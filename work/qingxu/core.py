"""Conservative, offline TOML changes. Never reads sessions or credentials."""
import copy, datetime, hashlib, json, os, re, tempfile, tomllib
from pathlib import Path
TOKEN_KEYS = {'model_context_window', 'model_auto_compact_token_limit', 'tool_output_token_limit'}
MODEL = "model"
AGENT_MODEL = "agents.default_subagent_model"
AGENT_EFFORT = "agents.default_subagent_reasoning_effort"
AGENT_KEYS = {AGENT_MODEL, AGENT_EFFORT}
ALLOWED = TOKEN_KEYS | {MODEL, 'model_reasoning_effort', 'compact_prompt'} | AGENT_KEYS
EFFORTS = ('low','medium','high','xhigh','max','ultra')
class SafeError(ValueError): pass

def digest(data): return hashlib.sha256(data).hexdigest()
def parse(data):
    try: return tomllib.loads(data.decode('utf-8-sig'))
    except (UnicodeError,tomllib.TOMLDecodeError) as e: raise SafeError('不是有效 UTF-8 TOML：'+str(e)) from e

def config_value(data, key):
    if key not in AGENT_KEYS:
        return data.get(key)
    agents = data.get("agents")
    return agents.get(key.split(".", 1)[1]) if isinstance(agents, dict) else None

def validate(changes):
    if not changes or not set(changes) <= ALLOWED: raise SafeError('没有选择有效的修改项。')
    for k,v in changes.items():
        if v is None: continue
        if k in TOKEN_KEYS:
            if type(v) is not int or not 1 <= v <= 10000000: raise SafeError('token 数必须是 1–10000000 的整数（本工具输入保护，非官方推荐范围）。')
        elif k in ('model_reasoning_effort', AGENT_EFFORT) and v not in EFFORTS: raise SafeError('推理档位无效。')
        elif k in (MODEL, AGENT_MODEL) and (not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}", v)):
            raise SafeError(('主模型' if k == MODEL else '子 agent 模型') + '需填写有效模型 ID 或选「继承」。')
        elif k=='compact_prompt' and (not isinstance(v,str) or not v.strip() or len(v)>12000): raise SafeError('压缩规则须为 1–12000 字符。')

def propose(original, changes):
    validate(changes)
    before = parse(original)
    expected = copy.deepcopy(before)
    for key, value in changes.items():
        table, name = expected, key
        if key in AGENT_KEYS:
            if "agents" not in expected and value is None:
                continue
            table = expected.setdefault("agents", {})
            if not isinstance(table, dict):
                raise SafeError("agents 配置需要使用 TOML 表。")
            name = key.split(".", 1)[1]
        if value is None:
            table.pop(name, None)
        else:
            table[name] = value
    text = original.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    section, agent_slot, found, result = "", None, set(), []
    for line in text.splitlines(keepends=True):
        stripped=line.lstrip()
        if stripped.startswith("["):
            section = "agents" if re.fullmatch(r'\[\s*(?:agents|"agents"|\'agents\')\s*\]\s*(?:#.*)?', stripped.strip()) else "other"
            result.append(line)
            if section == "agents":
                agent_slot = len(result)
            continue
        match = re.match(r"^\s*((?:agents\s*\.\s*)?[A-Za-z_][\w-]*)\s*=", line)
        name = re.sub(r"\s+", "", match.group(1)) if match else None
        key = name if section == "" else f"agents.{name}" if section == "agents" and name else None
        if key in changes:
            rhs=line.split('=',1)[1].lstrip()
            if rhs.startswith(('"""', "'''")): raise SafeError(f'{key} 使用多行字符串；请用导出的片段手动审查，不自动覆盖。')
            if changes[key] is not None:
                result.append(f'{name} = {json.dumps(changes[key],ensure_ascii=False)}{newline}')
            found.add(key)
        else: result.append(line)
    missing = sorted(set(changes) - found)
    if any(config_value(before, key) is not None for key in missing):
        raise SafeError("目标项使用复杂或带引号的 TOML 语法。请整理为单行配置再重试。")
    prefix, agent_lines = [], []
    for key in missing:
        if changes[key] is None:
            continue
        if key in AGENT_KEYS and agent_slot is not None:
            agent_lines.append(f'{key.split(".", 1)[1]} = {json.dumps(changes[key], ensure_ascii=False)}{newline}')
        else:
            prefix.append(f'{key} = {json.dumps(changes[key], ensure_ascii=False)}{newline}')
    if agent_lines:
        if not result[agent_slot - 1].endswith("\n"):
            result[agent_slot - 1] += newline
        result[agent_slot:agent_slot] = agent_lines
    encoding = "utf-8-sig" if original.startswith(b"\xef\xbb\xbf") else "utf-8"
    candidate = "".join(prefix + result).encode(encoding)
    parsed = parse(candidate)
    if agent_slot is None and "agents" not in parsed and expected.get("agents") == {}:
        expected.pop("agents")
    if parsed != expected:
        raise SafeError("配置语义校验失败，原文件未修改。")
    return candidate

def read_target(path):
    p=Path(path).expanduser()
    if p.is_symlink(): raise SafeError('不支持符号链接配置文件。')
    if not p.is_file(): raise SafeError('请先选择已有的 config.toml 文件；本工具不自动创建配置。')
    if p.name!='config.toml' and not p.name.endswith('.config.toml'): raise SafeError('请选择 config.toml 或 *.config.toml。')
    if p.stat().st_size>2_000_000: raise SafeError('文件过大，拒绝读取。')
    data=p.read_bytes();parse(data);return data

def write_checked(path, expected_hash, candidate):
    p=Path(path).expanduser(); current=read_target(p)
    if digest(current)!=expected_hash: raise SafeError('配置已被其他程序改动，请重新读取并预览。')
    parse(candidate)
    if current==candidate: raise SafeError('没有实际变化。')
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup=p.with_name(p.name+'.codex-pocket-'+stamp+'.bak')
    backup_fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(backup_fd,'wb') as f: f.write(current)
    fd,tmp=tempfile.mkstemp(prefix='.codex-pocket-',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f: f.write(candidate);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,p.stat().st_mode & 0o777)
        if digest(read_target(p))!=expected_hash: raise SafeError('备份后检测到并发改动；取消应用。')
        os.replace(tmp,p)
        if p.read_bytes()!=candidate: raise SafeError('写后核验失败，请检查备份。')
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return backup


def export_new(path,text):
    p=Path(path).expanduser()
    if p.name.lower()=='config.toml' or p.name.lower().endswith('.config.toml'):
        raise SafeError('导出不能保存为 Codex 配置文件。请使用 .snippet.toml 或 .md。')
    try: fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError as e: raise SafeError('导出只允许新文件，不覆盖任何现有文件。请换一个文件名。') from e
    with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(text)

def read_backup(path,expected_hash):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size>2_000_000:raise SafeError('备份文件不安全或已不存在。')
    data=p.read_bytes()
    if digest(data)!=expected_hash:raise SafeError('备份自创建后已改变，拒绝恢复。')
    parse(data);return data
