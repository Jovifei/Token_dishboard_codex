"""Portable Qingxu preferences. Reading and validation never create directories.

This module owns app preferences only; Codex TOML writes stay in ConfigStore.
Directory changes rebind locations, never copy/move an existing user's data.
"""
from copy import deepcopy
import json
import os
from pathlib import Path, PureWindowsPath
import stat
import tempfile

from privacy import ensure_private
from monitor import validate_policy
from sync import locks, read_file, replace_checked, safe_path, sha


SCHEMA_VERSION = 1
MAX_BYTES = 131_072
PATH_ACCESS = {
    "codex_home": "read", "handoff_dir": "write", "knowledge_dir": "read",
    "snapshot_dir": "read", "export_dir": "write",
}
OPTIONAL_PATHS = {"codex_home", "knowledge_dir", "snapshot_dir"}
KNOWLEDGE_MODES = ("none", "folder", "obsidian")
KNOWLEDGE_POLICIES = ("none", "ask", "authorized")
_UNLOADED = object()


class PreferencesError(ValueError):
    """A validation, stale-edit, or persisted-data problem suitable for the UI."""


def _merge(base, patch):
    result = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _monitor_policy(value):
    # Missing fields receive defaults during the merge. An explicitly malformed
    # value (including null) must not silently become a valid default policy.
    if not isinstance(value, dict):
        raise PreferencesError("提醒设置必须是配置对象；原偏好文件未修改。")
    try:
        return validate_policy(value)
    except (TypeError, ValueError) as exc:
        raise PreferencesError("提醒设置无效：" + str(exc)) from exc


def _directory_syntax(value, key, optional=False):
    """Check only path syntax; unavailable drives and directories remain valid."""
    if not isinstance(value, str):
        raise PreferencesError(key + "：目录必须是文本路径。")
    if not value:
        if optional:
            return ""
        raise PreferencesError(key + "：请选择保存目录。")
    if not value.strip() or any(ord(char) < 32 for char in value):
        raise PreferencesError(key + "：目录不能含空白值或控制字符。")
    try:
        path = Path(value).expanduser()
    except (RuntimeError, TypeError, ValueError) as exc:
        raise PreferencesError(key + "：无法解析目录路径。") from exc
    if not path.is_absolute() or ".." in path.parts or str(path).startswith(("\\\\", "//")):
        raise PreferencesError(key + "：只允许明确的本地绝对目录，不能包含上级跳转。")
    if os.name == "nt" and (path.is_reserved() or
            any(any(char in part for char in '<>:"|?*') for part in path.parts[1:])):
        raise PreferencesError(key + "：目录包含 Windows 不支持的名称。")
    return str(path)


def _snapshot_filename(value):
    if (not isinstance(value, str) or not value or len(value) > 255
            or value in (".", "..") or value.endswith((".", " "))
            or any(ord(char) < 32 or char in '/\\<>:"|?*' for char in value)
            or Path(value).name != value or PureWindowsPath(value).is_reserved()):
        raise PreferencesError("快照文件名必须是当前目录内的一个有效文件名。")
    return value


def _validate_structure(values):
    """Validate known fields without querying any configured data directory.

    Unknown extension fields are retained. Filesystem permission and existence
    checks apply only when a caller actually changes or uses a directory.
    """
    if type(values.get("schema_version")) is not int or values["schema_version"] != SCHEMA_VERSION:
        raise PreferencesError("偏好来自不同版本或格式无效；当前程序不会覆盖原文件。")
    if values.get("knowledge_mode") not in KNOWLEDGE_MODES:
        raise PreferencesError("知识库模式需为关闭、普通文件夹或 Obsidian。")
    if values.get("knowledge_policy") not in KNOWLEDGE_POLICIES:
        raise PreferencesError("知识库更新策略无效。")
    for key in PATH_ACCESS:
        optional = key in OPTIONAL_PATHS
        if key == "knowledge_dir" and values["knowledge_mode"] != "none":
            optional = False
        values[key] = _directory_syntax(values.get(key), key, optional=optional)
    values["snapshot_filename"] = _snapshot_filename(values.get("snapshot_filename"))
    values["monitor"] = _monitor_policy(values.get("monitor"))
    return values


def _absolute(value):
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise PreferencesError("请选择明确的本地绝对目录。")
    return safe_path(Path(value).expanduser())


def validate_directory(value, access="read", optional=False):
    """Return a bounded permission check, not a promise that a future write works.

    No mkdir, write probe, recursive scan, or path normalization through symlinks
    occurs. A write directory is checked again by the actual save operation.
    """
    if access not in ("read", "write"):
        raise ValueError("access must be read or write")
    raw = "" if value is None else str(value)
    result = {"ok": False, "code": "INVALID", "path": raw,
              "message": "请选择有效目录。", "exists": False,
              "can_create": False, "access": access, "verification": "permission_check"}
    if not raw.strip():
        return result | {"ok": optional, "code": "DISABLED" if optional else "EMPTY",
                         "path": "", "message": "未配置，可稍后选择。" if optional else "请选择保存目录。"}
    try:
        p = _absolute(raw)
        result["path"] = str(p)
        try:
            info = p.stat()
        except FileNotFoundError:
            parent = p.parent
            can_create = (access == "write" and parent.is_dir()
                          and os.access(parent, os.W_OK | os.X_OK))
            return result | {"code": "MISSING", "can_create": can_create,
                             "message": "目录不存在；请重新选择，或明确创建这个保存目录。"}
        result["exists"] = True
        if not stat.S_ISDIR(info.st_mode):
            return result | {"code": "NOT_DIRECTORY", "message": "所选位置是文件，请选择目录。"}
        if not os.access(p, os.R_OK | os.X_OK):
            return result | {"code": "NOT_READABLE", "message": "当前用户不能读取此目录。"}
        # Reading just one entry proves list access without loading file contents.
        with os.scandir(p) as entries:
            next(entries, None)
        if access == "write" and not os.access(p, os.W_OK | os.X_OK):
            return result | {"code": "NOT_WRITABLE", "message": "此目录不可写，请更换保存位置。"}
        message = "目录可读取。" if access == "read" else "目录权限检查通过；实际保存后还会读回核验。"
        return result | {"ok": True, "code": "OK", "message": message}
    except PermissionError:
        return result | {"code": "PERMISSION_DENIED", "message": "访问被拒绝，请检查目录权限。"}
    except (OSError, ValueError, TypeError) as exc:
        return result | {"code": "INVALID", "message": str(exc)}


def default_preferences(data_root, legacy_settings=None):
    """Build defaults from this user's existing settings, without touching disk."""
    root = _absolute(data_root)
    legacy = legacy_settings or {}
    home = (legacy.get("codex_home") or
            (str(Path(legacy["chat_db"]).parent) if legacy.get("chat_db") else None) or
            os.environ.get("QINGXU_CODEX_HOME") or os.environ.get("CODEX_HOME") or
            str(Path.home() / ".codex"))
    home = str(Path(home).expanduser().absolute())
    usage = Path(legacy["usage"]) if legacy.get("usage") else None
    snapshot = str(usage.parent) if usage and usage.parent.is_dir() else ""
    result = {
        "schema_version": SCHEMA_VERSION,
        "codex_home": home,
        "handoff_dir": str(root / "handoffs"),
        "knowledge_dir": "", "knowledge_mode": "none", "knowledge_policy": "ask",
        "snapshot_dir": snapshot,
        "snapshot_filename": usage.name if usage else "usage-latest.json",
        "export_dir": str(root / "exports"),
        "monitor": {"mode": "recommended", "custom_kind": "percent",
                    "custom_value": 70, "enabled": True},
    }
    # Old reminder percentages used a different basis: retain them as evidence,
    # but never silently convert their meaning into the new threshold strategy.
    old_reminder = root / "chat-reminder.json"
    try:
        old = json.loads(read_file(old_reminder, 4096).decode("utf-8-sig"))
        if type(old.get("enabled")) is bool and type(old.get("percent")) is int and 1 <= old["percent"] <= 100:
            result["legacy_reminder"] = {"enabled": old["enabled"], "percent": old["percent"]}
            if old["enabled"] is False:
                result["monitor"]["enabled"] = False
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return result


def _migrate(raw):
    data = deepcopy(raw)
    # Support a previously exported directory map without discarding extension
    # fields or the original map. Do not parse knowledge-library private files.
    nested = data.get("paths")
    if "paths" in data and not isinstance(nested, dict):
        raise PreferencesError("旧偏好的 paths 必须是目录配置对象；原文件未修改。")
    if isinstance(nested, dict):
        for key in PATH_ACCESS:
            if key not in data and key in nested:
                data[key] = nested[key]
    for old, new in (("codex_dir", "codex_home"), ("handoff_root", "handoff_dir"),
                     ("knowledge_root", "knowledge_dir"), ("usage_dir", "snapshot_dir"),
                     ("exports_dir", "export_dir")):
        if new not in data and old in data:
            data[new] = data[old]
    for old in ("chat_db", "usage"):
        if old in data and not isinstance(data[old], str):
            raise PreferencesError(old + "：旧偏好的文件路径必须是文本；原文件未修改。")
    if "codex_home" not in data and data.get("chat_db"):
        data["codex_home"] = str(Path(data["chat_db"]).parent)
    if "snapshot_dir" not in data and data.get("usage"):
        data["snapshot_dir"] = str(Path(data["usage"]).parent)
        data.setdefault("snapshot_filename", Path(data["usage"]).name)
    if data.get("schema_version") is not None and type(data["schema_version"]) is not int:
        raise PreferencesError("偏好格式版本必须是整数；原文件未修改。")
    if data.get("schema_version") in (None, 0):
        data["schema_version"] = SCHEMA_VERSION
    return data


def relay_settings(preferences, original):
    """Rebind only the requested data roots; private registry identity is retained.

    Clearing snapshot_dir leaves snapshots disabled. The UI can inspect the
    snapshot_enabled flag instead of claiming the collector has stopped.
    """
    result = dict(original)
    home = preferences.get("codex_home", "")
    # Keep only runtime binding metadata while reading is disabled, so enabling
    # another home can still distinguish its default config from a custom file.
    old_database = original.get("chat_db") or original.get("_qingxu_bound_chat_db")
    old_home = Path(old_database).parent if old_database else None
    if home:
        folder = _absolute(home)
        database = Path(old_database or "state_5.sqlite").name
        result["chat_db"] = str(folder / database)
        result["_qingxu_bound_chat_db"] = result["chat_db"]
        # Keep a separately selected config/profile file. Only move the old
        # home-default config when it was actually bound to that old home.
        config = Path(original["config"]) if original.get("config") else None
        if config is None or (old_home is not None and config == old_home / "config.toml"):
            result["config"] = str(folder / "config.toml")
    else:
        result["chat_db"] = ""
        if old_database:
            result["_qingxu_bound_chat_db"] = old_database
    snapshot = preferences.get("snapshot_dir", "")
    filename = _snapshot_filename(preferences.get("snapshot_filename", "usage-latest.json"))
    result["usage"] = str(_absolute(snapshot) / filename) if snapshot else ""
    result["snapshot_enabled"] = bool(snapshot)
    return result


class PreferenceStore:
    """Merge/save preferences atomically, preserving unknown extension fields."""
    def __init__(self, data_root, legacy_settings=None):
        self.root = _absolute(data_root)
        self.path = self.root / "preferences.json"
        self.defaults = default_preferences(self.root, legacy_settings)
        self._last_hash = _UNLOADED

    @property
    def revision(self):
        return None if self._last_hash is _UNLOADED else self._last_hash

    def _read(self):
        try:
            raw = read_file(self.path, MAX_BYTES)
        except FileNotFoundError:
            return None, {}
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise PreferencesError("偏好文件无法解析；保留原文件，请先检查或备份后恢复默认。") from exc
        if not isinstance(data, dict):
            raise PreferencesError("偏好文件必须是 JSON 对象；原文件未修改。")
        return raw, data

    def load(self):
        raw, data = self._read()
        values = _validate_structure(_merge(self.defaults, _migrate(data)))
        self._last_hash = sha(raw) if raw is not None else None
        return values

    def save(self, updates, expected=None, create_dirs=False):
        if not isinstance(updates, dict):
            raise PreferencesError("偏好修改必须是对象。")
        original, raw_data = self._read()
        token = sha(original) if original is not None else None
        current = _validate_structure(_merge(self.defaults, _migrate(raw_data)))
        if expected is not None:
            matches = current == expected if isinstance(expected, dict) else token == expected
            if not matches:
                raise PreferencesError("偏好已由其他窗口修改，请重新读取后保存。")
        elif self._last_hash is not _UNLOADED and self._last_hash != token:
            raise PreferencesError("偏好已由其他窗口修改，请重新读取后保存。")
        # Enforce the same threshold rules for UI, imports, and future callers.
        # This runs before directory creation or an atomic preference write.
        values = _validate_structure(_merge(current, updates))
        changed = {key for key in PATH_ACCESS if values.get(key) != current.get(key)}
        if create_dirs:
            changed.update(key for key in ("handoff_dir", "export_dir") if key in updates)
        if values["knowledge_mode"] != "none" and (values["knowledge_mode"] != current["knowledge_mode"] or "knowledge_dir" in updates):
            changed.add("knowledge_dir")
        to_create = []
        for key in changed:
            if key == "knowledge_dir" and values["knowledge_mode"] == "none":
                # Turning off an offline/moved library must always remain possible.
                if not isinstance(values[key], str):
                    raise PreferencesError("知识目录必须是文本路径。")
                continue
            optional = key in OPTIONAL_PATHS and key != "knowledge_dir"
            check = validate_directory(values.get(key), PATH_ACCESS[key], optional=optional)
            if not check["ok"]:
                can_make = create_dirs and key in ("handoff_dir", "export_dir") and check["code"] == "MISSING" and check["can_create"]
                if not can_make:
                    raise PreferencesError(key + "：" + check["message"])
                to_create.append(Path(check["path"]))
            values[key] = check["path"]
        encoded = (json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise PreferencesError("偏好文件过大；请将正文和资料存放到交接文件中。")
        # root is the app's explicit private-data location, not a user data source.
        ensure_private(self.root)
        created = []
        try:
            with locks([self.path]):
                latest, _ = self._read()
                if latest != original:
                    raise PreferencesError("保存时偏好发生变化，请重新读取。")
                for folder in dict.fromkeys(to_create):
                    safe_path(folder)
                    if not folder.exists():
                        folder.mkdir(mode=0o700)
                        created.append(folder)
                if original is not None:
                    if encoded != original:
                        replace_checked(self.path, sha(original), encoded)
                else:
                    fd, temporary = tempfile.mkstemp(prefix=".qingxu-preferences-", dir=self.root)
                    try:
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(encoded)
                            stream.flush()
                            os.fsync(stream.fileno())
                        if self.path.exists():
                            raise PreferencesError("偏好文件刚被另一进程创建，请重新读取。")
                        os.replace(temporary, self.path)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
                if read_file(self.path, MAX_BYTES) != encoded:
                    raise PreferencesError("偏好写后读回不一致，请检查保存位置。")
        except Exception:
            for folder in reversed(created):
                try:
                    folder.rmdir()
                except OSError:
                    pass
            raise
        self._last_hash = sha(encoded)
        return deepcopy(values)
