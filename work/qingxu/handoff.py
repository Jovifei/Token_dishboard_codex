"""Portable, local handoff files and prompts.

No Codex process, model, registry or knowledge-base connection is required.
The editable templates contain guidance only: project identity, the exchange
format and the file-verification rules are always added by this module.

Public UI API:
    store = HandoffStore(absolute_directory)
    target = store.new_path(chat_record)        # no filesystem write
    prompt = build_save_prompt(chat_record, target)
    saved = store.save(chat_record, pasted_markdown)
    saved = store.import_file(chat_record, chosen_path, allow_unbound=True)
    previous = store.latest(chat_record)
    prompt = build_restore_prompt(chat_record, saved=saved, mode="file")
    prompt = build_restore_prompt(chat_record, summary=pasted_markdown, mode="inline")
"""
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import uuid

from sync import publish_new, read_file, safe_path
from knowledge import validate_categories
from relay import KB_FILES

SCHEMA_VERSION = 1
MAX_SUMMARY_BYTES = 16 * 1024
MAX_TEMPLATE_BYTES = 8 * 1024
MAX_FILE_BYTES = 48 * 1024
POLICIES = ("none", "ask", "authorized")
POLICY_LABELS = {"none": "不更新知识库", "ask": "询问是否更新知识库",
                 "authorized": "按既有授权更新"}
HEADER = "<!-- qingxu-handoff:v1 "
SOURCE_HEADER = "<!-- qingxu-source:v1 "
SUMMARY_SECTIONS = (
    ("项目概览", "项目要解决什么问题、服务谁、总体目标和当前阶段。"),
    ("当前任务与验收", "本次目标、完成标准、还需要满足什么。"),
    ("已完成与证据", "已修改文件、可定位产物与已经验证的结果。"),
    ("未完成与下一步", "进行到哪里、阻塞原因、接下来最小且明确的一步。"),
    ("关键决策与约束", "重要决定、不得改变的约束、已排除方案的简短原因。"),
    ("工作区与验证", "目录/工作树、分支和 HEAD（无 Git 则注明）、未提交改动、运行中任务；通过/失败/未运行。"),
    ("按需资料索引", "必要资料的路径、用途和何时需要读取；没有则写无。"),
)
DEFAULT_TEMPLATES = {
    "save": (
        "请收束当前工作，生成一份足以接续的短交接。先遵守本项目必读规则，"
        "核对项目/工作树与已有改动。以当前事实为准，不把计划写成完成。\n"
        "先用几句话说明整个项目的目的、使用者与当前阶段，再记录本次任务。"
        "正文目标为 800—1500 个中文字符，重要状态不能为限字丢失；"
        "长细节留在原文件中，用路径和用途索引。\n"
        "只核对交接必需的现场，不重读全部历史或整个知识库。"
        "证据、未完成事项、失败/未运行的验证、关键决定和明确下一步必须保留。"
        "本轮完成收尾，不扩展新的大任务，不自动提交、切换工作树或清理改动。"
    ),
    "restore": (
        "请接续已有任务。先遵守本项目必读规则，再核对交接中的项目身份、工作区、"
        "分支/HEAD、未提交改动和关键产物；现场已变化时简要说明差异，以当前证据为准。\n"
        "先理解交接中的项目目的和当前阶段，再从中断处继续。"
        "只按需读取资料索引中的相关文件或片段，不默认加载全库、全仓库或全部旧聊天。\n"
        "用不超过五行说明已恢复的状态、目标和第一步，然后继续已授权工作。"
        "如果只有大目标，按验收缺口选择一个范围明确的下一阶段；"
        "关键事实缺失才提问，不猜测任务已经完成。保留已有改动，阶段完成后更新短交接。"
    ),
}


class HandoffError(ValueError):
    """Actionable local validation error, suitable for inline UI feedback."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp(value):
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError("timezone missing")
        return moment
    except (AttributeError, TypeError, ValueError) as exc:
        raise HandoffError("交接生成时间无效或缺少时区。") from exc


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _text(value, name, maximum=2048, *, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str) or "\x00" in value:
        raise HandoffError(f"{name}格式无效。")
    value = value.strip()
    if required and not value:
        raise HandoffError(f"缺少{name}，请先选择项目和聊天。")
    if len(value.encode("utf-8")) > maximum or "\n" in value or "\r" in value:
        raise HandoffError(f"{name}过长或包含换行。")
    return value


def _display_label(value, fallback):
    # Codex can use a full multiline first message as the chat title.
    text = value if isinstance(value, str) else fallback
    text = " ".join(text.replace("\x00", " ").splitlines()).strip() or fallback
    return text.encode("utf-8", errors="replace")[:2048].decode("utf-8", errors="ignore")


def _path_value(value, name):
    value = _text(str(value), name, 4096, required=True)
    if not (os.path.isabs(value) or ntpath.isabs(value)):
        raise HandoffError(f"{name}须为明确的绝对路径。")
    if ".." in value.replace("\\", "/").split("/"):
        raise HandoffError(f"{name}不能包含上级路径跳转。")
    return value


def _cwd_key(value):
    # Windows drive/UNC paths are case-insensitive. Preserve case on POSIX
    # so a cross-platform inspection never conflates distinct real directories.
    if re.match(r"^[A-Za-z]:", value) or value.startswith("\\"):
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return ntpath.normcase(ntpath.normpath(value.replace("/", "\\")))
    return os.path.normpath(value)


def _identity(record):
    if not isinstance(record, dict):
        raise HandoffError("请选择一个有效的聊天。")
    cwd = _path_value(record.get("path") or record.get("cwd") or "", "项目目录")
    thread = _text(record.get("chat_id") or record.get("thread_id"),
                   "来源会话 ID", required=True)
    app_id = _text(record.get("app_project_id") or record.get("native_project_id"),
                   "原生项目 ID")
    relay_id = _text(record.get("project_id") or record.get("registry_project_id"),
                     "登记项目 ID")
    title = _display_label(record.get("label") or record.get("title"), thread)
    project_name = _display_label(record.get("app_project_name") or record.get("project_name"),
                                  ntpath.basename(cwd.rstrip("/\\")) or cwd)
    key_input = json.dumps([_cwd_key(cwd), app_id, relay_id],
                           ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"project_key": _hash(key_input)[:24], "cwd": cwd, "thread_id": thread,
            "native_project_id": app_id, "registry_project_id": relay_id,
            "project_name": project_name, "title": title}


def project_key(record):
    """Return the same project and worktree identity used by handoff files."""
    return _identity(record)["project_key"]


def _metadata_record(metadata):
    if not isinstance(metadata, dict):
        raise HandoffError("交接来源身份格式无效。")
    return {"path": metadata.get("cwd"), "chat_id": metadata.get("thread_id"),
            "app_project_id": metadata.get("native_project_id"),
            "project_id": metadata.get("registry_project_id"),
            "app_project_name": metadata.get("project_name"),
            "label": metadata.get("title")}


def _check_identity(metadata, record):
    wanted = _identity(record)
    actual = _identity(_metadata_record(metadata))
    if _cwd_key(actual["cwd"]) != _cwd_key(wanted["cwd"]):
        raise HandoffError("交接属于其他项目或工作树，未导入。请重新选择正确聊天。")
    for field in ("native_project_id", "registry_project_id"):
        if actual[field] and wanted[field] and actual[field] != wanted[field]:
            raise HandoffError("交接的项目 ID 与所选项目不一致，未导入。")
    if metadata.get("project_key") and metadata["project_key"] != actual["project_key"]:
        raise HandoffError("交接项目标识校验失败。")
    warnings = []
    if actual["thread_id"] != wanted["thread_id"]:
        warnings.append("来源会话不同：这份交接来自同项目的另一条聊天，请核对任务。")
    if any(bool(actual[f]) != bool(wanted[f]) for f in
           ("native_project_id", "registry_project_id")):
        warnings.append("部分项目映射信息不同，已核对工作目录；恢复前请核对项目身份。")
    return warnings


def _summary(value):
    if not isinstance(value, str) or "\x00" in value:
        raise HandoffError("摘要须为有效的 UTF-8 文本。")
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        raise HandoffError("摘要为空。请先粘贴旧会话的摘要，或导入交接文件。")
    if len(value.encode("utf-8")) > MAX_SUMMARY_BYTES:
        raise HandoffError(f"摘要超过 {MAX_SUMMARY_BYTES // 1024} KiB，请精简正文并把长资料改成路径索引。")
    return value


def _summary_warnings(summary):
    warnings = []
    missing = [title for title, _ in SUMMARY_SECTIONS
               if not any(word in summary for word in (
                   {"项目概览": ("项目概览", "项目目的", "总体目标"),
                    "当前任务与验收": ("验收", "完成标准"),
                    "已完成与证据": ("已完成", "已验证", "证据"),
                    "未完成与下一步": ("未完成", "下一步", "待办"),
                    "关键决策与约束": ("决策", "约束"),
                    "工作区与验证": ("工作区", "HEAD", "分支", "未运行"),
                    "按需资料索引": ("索引", "资料路径", "参考文件")}[title]))]
    if missing:
        warnings.append("摘要可能缺少：" + "、".join(missing) + "。可保存，但请核对后再续接。")
    if len(summary.encode("utf-8")) > 8192:
        warnings.append("摘要较长；可把历史细节移到资料文件，仅保留路径和用途。")
    return warnings


def _policy(policy):
    if policy not in POLICIES:
        raise HandoffError("知识库策略须为 none、ask 或 authorized。")
    return policy


def _guidance(kind, template):
    value = DEFAULT_TEMPLATES[kind] if template is None else template
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise HandoffError("模板正文不能为空。")
    if len(value.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise HandoffError("模板正文超过 8 KiB，请保留必要指导。")
    return value.strip()


def _comment(prefix, metadata):
    value = json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e")
    return prefix + value + " -->"


def _decode(content):
    if len(content) > MAX_FILE_BYTES:
        raise HandoffError("交接文件过大，请仅导入短摘要文件。")
    try:
        value = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()
    except UnicodeDecodeError as exc:
        raise HandoffError("交接文件须为 UTF-8 文本。") from exc
    if value.startswith(HEADER) or value.startswith(SOURCE_HEADER):
        first, sep, body = value.partition("\n")
        prefix = HEADER if first.startswith(HEADER) else SOURCE_HEADER
        if not sep or not first.endswith(" -->"):
            raise HandoffError("交接文件头不完整，请检查旧会话的输出。")
        try:
            meta = json.loads(first[len(prefix):-4])
        except (ValueError, TypeError) as exc:
            raise HandoffError("交接元数据无法解析。") from exc
        if not isinstance(meta, dict):
            raise HandoffError("交接元数据须为对象。")
        _identity(_metadata_record(meta))
        body = _summary(body)
        if "source_identity" in meta and meta["source_identity"] is not None:
            _check_identity(meta["source_identity"], _metadata_record(meta))
        if prefix == HEADER:
            if meta.get("schema_version") != SCHEMA_VERSION:
                raise HandoffError("不支持这个交接文件版本。")
            if meta.get("summary_sha256") != _hash(body.encode("utf-8")):
                raise HandoffError("交接正文校验失败：文件可能被修改，请重新导入正确摘要。")
            _policy(meta.get("knowledge_policy"))
            identity = _identity(_metadata_record(meta))
            if meta.get("project_key") != identity["project_key"]:
                raise HandoffError("交接项目标识校验失败。")
            _timestamp(meta.get("generated_at"))
            if meta.get("source_generated_at"):
                _timestamp(meta["source_generated_at"])
        return meta, body, prefix == HEADER
    return None, _summary(value), False


def summary_skeleton():
    """Human-readable headings used by old-chat prompts; no project scan."""
    return "\n\n".join("## " + title + "\n" + description for title, description in SUMMARY_SECTIONS)


def default_templates():
    return dict(DEFAULT_TEMPLATES)


def _knowledge_instruction(policy, directory, kinds):
    _policy(policy)
    target = _path_value(directory, "知识资料目录") if directory else ""
    if policy == "none" or not kinds:
        return "本次仅保存任务交接，不更新长期知识库。"
    if not target:
        return "尚未确认本项目的知识文件夹。本次不更新知识库；先核实目标目录和授权。"
    join = ntpath.join if ntpath.splitdrive(target)[0] else os.path.join
    pages = "\n".join("- " + join(target, KB_FILES[kind]) for kind in kinds)
    scope = ("\n仅以下勾选分类的目标页属于本次范围：\n" + pages +
             "\n不得更新其他知识页。目标页缺失或项目、版本、授权不明确时先询问；写后读回核对。")
    if policy == "ask":
        return ("先完成交接，再列出值得长期保存的知识和目标文件，询问我是否更新；"
                "没有回答不代表同意，不因等待答复而丢失交接。"
                + scope)
    return ("仅在本会话已有明确授权范围内更新指定知识文件，写前核对目标与版本，写后读回；"
            "没有对应授权时只列候选，不能把这个选项视为新的无限写入许可。"
            + scope)


def build_save_prompt(record, destination=None, *, knowledge_policy="none",
                      knowledge_dir="", knowledge_kinds=None, template=None):
    identity = _identity(record)
    try:
        kinds = validate_categories(knowledge_kinds if knowledge_kinds is not None else [])
    except ValueError as exc:
        raise HandoffError(str(exc)) from exc
    marker = _comment(SOURCE_HEADER, identity)
    target = _path_value(destination, "交接输出路径") if destination else ""
    output = (f"将下面格式的 Markdown 新建保存到：{target}\n"
              "若路径已存在，另存一个带时间戳的新文件，不覆盖。写后读回并返回准确路径；"
              "若不能写文件，直接回复同一格式的全文，由我粘回轻续。\n"
              if target else "直接回复下面格式的 Markdown 全文，由我粘回轻续保存；不要只说“已总结”。\n")
    return (
        f"【轻续 · 旧会话收尾】\n项目：{identity['project_name']}\n"
        f"工作目录／工作树：{identity['cwd']}\n来源会话：{identity['title']}\n"
        f"来源会话 ID：{identity['thread_id']}\n\n"
        + _guidance("save", template) + "\n\n【交付格式】\n" + output
        + "保留下面第一行项目标记，随后按各小节填写真实内容，不保留说明占位文字。\n"
        + marker + "\n" + summary_skeleton()
        + f"\n\n正文最多 {MAX_SUMMARY_BYTES} UTF-8 字节；字符/字节不是 Token 计量。"
        + "\n\n【长期知识处理】\n"
        + _knowledge_instruction(knowledge_policy, knowledge_dir, kinds)
        + "\n\n【完成反馈】\n返回保存/未保存结果、文件路径（如有）、当前状态与下一步。"
          "复制提示词不代表已保存；生成摘要不代表上下文已经压缩。"
          "不删除或归档旧会话。之后由我在同项目真正的新会话续接。"
    )


def _canonical(metadata, body):
    return (_comment(HEADER, metadata) + "\n\n" + body + "\n").encode("utf-8")


def _publish_new(path, content):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        publish_new(path, content, MAX_FILE_BYTES)
    except FileExistsError as exc:
        raise HandoffError("目标交接已存在，未覆盖。请重新保存为新版本。") from exc
    except ValueError as exc:
        raise HandoffError(str(exc)) from exc


def _loaded(path, record=None):
    path = safe_path(Path(path))
    raw = read_file(path, MAX_FILE_BYTES)
    meta, body, canonical = _decode(raw)
    if not canonical:
        raise HandoffError("这是待导入的摘要，尚未由轻续保存校验。请先导入并保存。")
    warnings = _summary_warnings(body)
    if record is not None:
        warnings[:0] = _check_identity(meta, record)
        if meta.get("source_identity"):
            warnings[:0] = _check_identity(meta["source_identity"], record)
    source_time = meta.get("source_generated_at") or meta["generated_at"]
    age = (datetime.now(timezone.utc) - _timestamp(source_time)).total_seconds()
    if age > 24 * 60 * 60:
        warnings.append("交接已超过一天；继续前核对工作区与关键产物是否变化。")
    elif age < -300:
        warnings.append("交接时间晚于本机时间，请核对系统时钟。")
    return {"status": "SAVED_VERIFIED", "path": str(path), "summary": body,
            "metadata": meta, "sha256": _hash(raw),
            "age_seconds": age, "warnings": list(dict.fromkeys(warnings))}


class HandoffStore:
    def __init__(self, root):
        # Construction does not create or change the user-selected directory.
        self.root = safe_path(Path(root).expanduser().absolute())

    def new_path(self, record):
        identity = _identity(record)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return self.root / identity["project_key"] / f"handoff-{stamp}-{uuid.uuid4().hex[:12]}.md"

    def save(self, record, summary, *, knowledge_policy="none", source="paste"):
        identity = _identity(record)
        # Accept a pasted old-chat exchange block and verify its binding.
        metadata, body, _ = _decode(str(summary).encode("utf-8")) if isinstance(summary, str) else (
            None, _summary(summary), False)
        warnings = _check_identity(metadata, record) if metadata else []
        policy = _policy(knowledge_policy)
        now = _now()
        meta = {**identity, "schema_version": SCHEMA_VERSION, "generated_at": now,
                "summary_sha256": _hash(body.encode("utf-8")),
                "knowledge_policy": policy,
                "source": _text(source, "摘要来源", 128, required=True),
                "source_generated_at": ((metadata.get("source_generated_at")
                                          or metadata.get("generated_at")) if metadata else None),
                "source_identity": ((metadata.get("source_identity") or
                                     _identity(_metadata_record(metadata))) if metadata else identity)}
        target = self.new_path(record)
        _publish_new(target, _canonical(meta, body))
        result = self.load(target, record)
        result["warnings"] = list(dict.fromkeys(warnings + result["warnings"]))
        return result

    def import_file(self, record, path, *, allow_unbound=False, knowledge_policy="none"):
        chosen = safe_path(Path(path).expanduser().absolute())
        raw = read_file(chosen, MAX_FILE_BYTES)
        metadata, body, canonical = _decode(raw)
        if metadata:
            _check_identity(metadata, record)
        elif not allow_unbound:
            raise HandoffError("普通摘要没有项目标记。请核对当前项目后，明确确认按所选聊天导入。")
        result = self.save(record, raw.decode("utf-8-sig"),
                           knowledge_policy=knowledge_policy, source="file_import")
        result["imported_from"] = str(chosen)
        if metadata is None:
            result["warnings"].insert(0, "普通摘要由用户确认绑定到当前项目；来源身份未自动验证。")
        return result

    def load(self, path, record=None):
        return _loaded(Path(path).expanduser().absolute(), record)

    def list(self, record, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise HandoffError("交接列表数量须为 1—100。")
        identity = _identity(record)
        folder = safe_path(self.root / identity["project_key"])
        if not folder.exists():
            return []
        paths = sorted(folder.glob("handoff-*.md"), key=lambda p: p.name, reverse=True)
        result = []
        for path in paths[:100]:
            try:
                result.append(self.load(path, record))
            except (OSError, ValueError) as exc:
                # Keep corrupt/partial history visible to the UI; never label it
                # usable or silently fall back to a different project.
                result.append({"status": "INVALID", "path": str(path),
                               "summary": "", "metadata": {}, "warnings": [str(exc)]})
            if len(result) >= limit:
                break
        return result

    def latest(self, record):
        identity = _identity(record)
        records = self.list(record, limit=100)
        # Never auto-select another chat's summary merely because it is newer.
        return next((item for item in records
                     if item["status"] == "SAVED_VERIFIED"
                     and item["metadata"].get("thread_id") == identity["thread_id"]), None)


def build_restore_prompt(record, summary="", *, saved=None, mode="file",
                         next_goal="", template=None):
    identity = _identity(record)
    if mode not in ("file", "inline"):
        raise HandoffError("续接方式须为 file 或 inline。")
    provenance = "用户粘贴摘要；未验证本地保存，内容需在新会话核对。"
    source_identity = identity
    if saved is not None:
        if not isinstance(saved, dict) or not saved.get("path"):
            raise HandoffError("交接保存凭据无效，请重新选择文件。")
        checked = _loaded(Path(saved["path"]).expanduser().absolute(), record)
        if saved.get("sha256") and saved["sha256"] != checked["sha256"]:
            raise HandoffError("交接文件已在保存后改变，请重新导入和核对。")
        body = checked["summary"]
        if summary:
            proposed_metadata, proposed_body, _ = _decode(summary.encode("utf-8"))
            if proposed_metadata:
                _check_identity(proposed_metadata, record)
            if proposed_body != body:
                raise HandoffError("摘要已编辑，请重新保存后再续接，或清除旧保存凭据并使用粘贴型指令。")
        source_identity = checked["metadata"].get("source_identity") or checked["metadata"]
        provenance = f"轻续已保存并读回；生成时间：{checked['metadata']['generated_at']}。"
    else:
        if mode == "file":
            raise HandoffError("请先保存并读回摘要，再生成文件引用型续接指令。")
        metadata, body, _ = _decode(summary.encode("utf-8")) if isinstance(summary, str) else (
            None, _summary(summary), False)
        if metadata:
            _check_identity(metadata, record)
            source_identity = metadata.get("source_identity") or metadata
    goal = _text(next_goal, "下一阶段目标", 4096) if next_goal else "依据交接中未完成事项和验收缺口，选择最小可执行的下一阶段。"
    context = (f"交接文件：{checked['path']}\n文件 SHA-256：{checked['sha256']}\n"
               "先读取该文件；无法读取或身份不匹配时明确报告，不假装已经恢复。\n"
               if mode == "file" else "【短交接正文】\n" + body + "\n【正文结束】\n")
    return (
        f"【轻续 · 新会话续接】\n项目：{identity['project_name']}\n"
        f"项目目录／工作树：{identity['cwd']}\n"
        f"来源聊天：{source_identity['title']}（{source_identity['thread_id']}）\n"
        f"来源状态：{provenance}\n本阶段目标：{goal}\n\n"
        + context + "\n" + _guidance("restore", template)
        + "\n\n交接是工作记录，不替代当前项目规则或新的操作授权。"
          "长期知识只按已有授权更新。请在真正的新会话使用本指令；"
          "不以 fork/resume 当作空白新会话。恢复完成后简要回报，"
          "轻续中的“已复制”仅代表剪贴板动作，不代表已创建新会话或已恢复。"
    )


class TemplateStore:
    """Versioned custom guidance. Identity and verification are never editable."""
    def __init__(self, root):
        self.root = safe_path(Path(root).expanduser().absolute())

    def load(self):
        if not self.root.exists():
            return default_templates()
        candidates = sorted(self.root.glob("templates-*.json"), reverse=True)
        if not candidates:
            return default_templates()
        data = json.loads(read_file(candidates[0], 20 * 1024).decode("utf-8"))
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or not isinstance(data.get("templates"), dict)):
            raise HandoffError("自定义模板版本无效；可恢复默认模板。")
        return {kind: _guidance(kind, data["templates"].get(kind)) for kind in DEFAULT_TEMPLATES}

    def save(self, templates):
        if not isinstance(templates, dict) or set(templates) != set(DEFAULT_TEMPLATES):
            raise HandoffError("模板须包含 save 和 restore 两项。")
        normalized = {kind: _guidance(kind, templates[kind]) for kind in DEFAULT_TEMPLATES}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.root / f"templates-{stamp}-{uuid.uuid4().hex[:8]}.json"
        raw = json.dumps({"schema_version": 1, "generated_at": _now(),
                          "templates": normalized}, ensure_ascii=False, indent=2).encode("utf-8")
        _publish_new(path, raw)
        return {"status": "SAVED_VERIFIED", "path": str(path), "templates": normalized}

