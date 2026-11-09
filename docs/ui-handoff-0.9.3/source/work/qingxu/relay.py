"""Adapters to the installed private relay; never scans chat history or Vault."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

from sync import read_file, safe_path, sha
from chats import ALL_PROJECTS, discover

def default_settings():
    home = Path(os.environ.get("QINGXU_CODEX_HOME") or os.environ.get("CODEX_HOME")
                or Path.home() / ".codex").expanduser().absolute()
    data = Path(__file__).resolve().parent.parent / "Codex" / "data"
    if getattr(sys, "frozen", False) or not data.is_dir():
        data = home / "qingxu-relay"
    runtime = data / "project-relay-p0"
    state = data / "AgentState"
    return {
        "runtime": str(runtime),
        "registry": str(runtime / "registry.json"),
        "state_root": str(state / "project-relay-v1"),
        "mapping": str(Path.home() / ".codex-memory" / "config.yaml"),
        "config": str(home / "config.toml"),
        "usage": str(state / "usage-private" / "usage-latest.json"),
        "chat_db": str(home / "state_5.sqlite"),
    }

# Exact filenames in the established project-memory layout.
KB_FILES = {
    "REQUIREMENTS": "00-项目概览.md",
    "PLAN": "01-总体计划.md",
    "NOW": "02-当前进度.md",
    "DECISIONS": "03-关键决策.md",
    "LESSONS": "04-工作流与知识.md",
    "RESEARCH": "05-参考文件索引.md",
    "BUGS": "06-Bug与版本索引.md",
}


def read_json(path):
    return json.loads(read_file(path, 12_000_000).decode("utf-8-sig"))


def inside(child, parent):
    return Path(child).is_relative_to(Path(parent))


def observer_health(receipt):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(
            receipt["heartbeat_utc"].replace("Z", "+00:00"))).total_seconds()
        heartbeat = "RECENT" if 0 <= age < 1500 else "STALE"
    except (KeyError, ValueError, TypeError):
        heartbeat = "UNKNOWN"
    # A JSON flag or recycled PID alone is never live process identity evidence.
    return {"heartbeat": heartbeat, "process_identity": "UNKNOWN",
            "receipt_running_claim": receipt.get("running"),
            "pid": receipt.get("pid"), "heartbeat_utc": receipt.get("heartbeat_utc")}


def psquote(value):
    return "'" + str(value).replace("'", "''") + "'"


def build_prompt(kind, project, snapshot, checkpoint, settings):
    pid, path = project["project_id"], project["path"]
    rev = snapshot.get("revision", "待刷新")
    python = Path(settings.get("python", os.sys.executable))
    if python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")
    command = ("& " + psquote(python)
               + " " + psquote(Path(settings["runtime"]) / "checkpoint.py")
               + " --registry " + psquote(settings["registry"])
               + " --state-root " + psquote(settings["state_root"]))
    read_command = command + " semantic-read --project-id " + psquote(pid)
    identity = f"项目：{path}\nproject_id：{pid}\n私有短断点：{checkpoint}\n当前 revision：{rev}\n"
    rules = ("只读当前项目规则、短断点和相关现场证据。摘要与最新代码冲突时先核实，"
             "不跨项目混读，不默认读全局记忆、整个 Obsidian 或全部旧聊天。"
             "不覆盖他人改动，不写凭据；业务 STOP 和人工门槛继续有效。")
    if kind == "restore":
        return (identity + "请继续这个项目未完成的任务，先核项目身份、Git/工作区及断点是否过期。"
                "不要把恢复检查当业务执行授权。\n" + rules + "\n只读断点命令：\n"
                + read_command)
    return (identity + "请生成并保存不超过 8192 UTF-8 字节的语义断点：目标、已完成、未完成、"
            "验收条件、分支/SHA、工作区改动、关键证据、决策、失败尝试和下一步。"
            "不能用采集元数据代替任务摘要。\n" + rules
            + "\n保存前运行下列只读命令获取 revision；发生冲突即停，不强行重试覆盖。\n"
            + read_command
            + "\n将摘要作为 UTF-8 文本从 stdin 传入（非 JSON），用刚读到的 revision 替换参数值：\n"
            + command + " semantic-save --project-id " + psquote(pid)
            + " --expected-revision " + str(rev)
            + "\n写后重跑同一条只读命令核对 revision 和正文；若不能安全保存，返回可粘贴的摘要。"
            "这条交接指令仅授权保存本机任务摘要。")


class Relay:
    def __init__(self, settings=None):
        self.settings = default_settings() | (settings or {})
        self._checkpoint = None

    def projects(self):
        if getattr(sys, "frozen", False) and not self.settings.get("python"):
            raise ValueError("分享版可使用手动交接。私有接力需单独部署。")
        projects = read_json(self.settings["registry"])["projects"]
        ids = [p["project_id"] for p in projects]
        if len(ids) != len(set(ids)):
            raise ValueError("登记表有重复身份")
        return projects

    def chats(self, limit=200, *, app_project=ALL_PROJECTS, offset=0):
        # The app's chat metadata remains useful if the independent relay registry
        # is missing or invalid.  Never infer an authorization mapping in that case.
        try:
            projects, registry_error = self.projects(), None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            projects, registry_error = [], str(exc)
        result = discover(self.settings["chat_db"], projects, limit,
                          app_project=app_project, offset=offset)
        result["registry_error"] = registry_error
        return result

    def project(self, pid):
        matches = [p for p in self.projects() if p["project_id"] == pid]
        if len(matches) != 1:
            raise ValueError("未登记或重复的项目身份")
        safe_path(matches[0]["path"])
        return matches[0]

    def engine(self):
        if self._checkpoint is None:
            path = safe_path(Path(self.settings["runtime"]) / "checkpoint.py")
            spec = importlib.util.spec_from_file_location("qingxu_checkpoint", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._checkpoint = module
        return self._checkpoint

    def selected(self, pid):
        self.project(pid)
        module = self.engine()
        root = module.local_path(self.settings["state_root"])
        projects = module.load_registry(self.settings["registry"], root)
        project = module.selected(projects, pid)
        return module, root, project

    def checkpoint_path(self, pid):
        self.project(pid)
        return Path(self.settings["state_root"]) / (hashlib.sha256(pid.encode()).hexdigest() + ".json")

    def snapshot(self, pid, refresh=False):
        module, root, project = self.selected(pid)
        if refresh:
            module.collect_one(root, pid, project)
        if not self.checkpoint_path(pid).exists():
            return {"revision": None, "summary": "", "availability": "NOT_COLLECTED"}
        return module.semantic_read(root, pid)

    def save(self, pid, revision, text):
        module, root, project = self.selected(pid)
        return module.semantic_save(root, pid, project, revision, text)

    def knowledge_mapping(self, pid):
        project = safe_path(self.project(pid)["path"])
        raw = read_file(self.settings["mapping"], 12_000_000)
        config = json.loads(raw.decode("utf-8-sig"))
        profile = config["profiles"][config["active_profile"]]
        matches = []
        for m in profile["project_mappings"]:
            source = safe_path(m["source_root"])
            if inside(project, source):
                matches.append((len(source.parts), m))
        if not matches:
            raise ValueError("此项目未映射知识库；不按名称猜测")
        longest = max(n for n, _ in matches)
        matches = [m for n, m in matches if n == longest]
        if len(matches) != 1:
            raise ValueError("知识库映射有歧义")
        memory_id = matches[0]["memory_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", memory_id):
            raise ValueError("memory_id 非法")
        root = safe_path(profile["memory_root"])
        vault = safe_path(profile["obsidian_vault_root"])
        approved = [safe_path(p) for p in profile["approved_memory_roots"]]
        if root not in approved or not inside(root, vault):
            raise ValueError("知识库根目录未获授权或超出 Vault")
        folder = safe_path(root / "03-项目记忆" / memory_id)
        pages = {kind: folder / filename for kind, filename in KB_FILES.items()}
        return pages, f"{sha(raw)}|{pid}|{folder}"

    def pages(self, pid):
        pages, _ = self.knowledge_mapping(pid)
        return {kind: pages[kind] for kind in ("NOW", "DECISIONS")}

    def status(self):
        try:
            receipt = read_json(Path(self.settings["state_root"]) / "observer-status.json")
        except (OSError, ValueError):
            receipt = {}
        try:
            usage = read_json(self.settings["usage"])
        except FileNotFoundError as exc:
            raise ValueError("本机暂无日期快照。可在「当前聊天」查看用量，日期快照需另行采集。") from exc
        return {"observer": observer_health(receipt),
                "STOP": (Path(self.settings["state_root"]) / "STOP").exists(),
                "metadata": receipt.get("metadata"), "usage_date": usage.get("date"),
                "usage_captured": usage.get("finished_utc"), "unit": usage.get("unit"),
                "totals": usage.get("totals"), "projects": usage.get("projects"),
                "usage_snapshot": usage,
                "input_semantics": usage.get("input_semantics"),
                "reasoning_semantics": usage.get("reasoning_semantics"),
                "auto_start": False, "runtime_config_adoption": "UNKNOWN",
                "causal_savings": "UNPROVEN"}
