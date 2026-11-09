"""Conservative configuration writes with durable, private recovery receipts."""
import json
import os
from pathlib import Path
import uuid

import core
from privacy import ensure_private
from sync import locks, read_file, replace_checked, safe_path, sha


def model_defaults(catalog, model):
    """Read model defaults from Codex's local catalog, without merging config layers."""
    data = json.loads(read_file(catalog, 4_000_000).decode("utf-8-sig"))
    match = next((item for item in data["models"] if item["slug"] == model), None)
    if match is None:
        return {}

    def limit(value):
        return value if type(value) is int and value > 0 else None

    maximum = limit(match.get("max_context_window"))
    window = limit(match.get("context_window")) or maximum
    compact = limit(match.get("auto_compact_token_limit"))
    # ModelInfo::auto_compact_token_limit in Codex 0.167.0 uses 90% of the resolved window.
    if window and data.get("client_version") == "0.167.0":
        window_limit = window * 9 // 10
        compact = min(compact, window_limit) if compact else window_limit
    # In other client versions a missing default remains unknown, but an explicit
    # catalog value is still evidence. Do not erase it just because window exists.
    policy = match.get("truncation_policy") or {}
    levels = match.get("supported_reasoning_levels")
    efforts = None
    if isinstance(levels, list):
        available = {level.get("effort") for level in levels if isinstance(level, dict)}
        efforts = tuple(effort for effort in core.EFFORTS if effort in available)
    return {"model_context_window": window, "model_auto_compact_token_limit": compact,
            "tool_output_token_limit": limit(policy.get("limit")) if policy.get("mode") == "tokens" else None,
            "model_reasoning_effort": match.get("default_reasoning_level"), "efforts": efforts,
            "maximum": maximum, "version": data.get("client_version")}


class ConfigStore:
    def __init__(self, root):
        self.root = ensure_private(root)

    def apply(self, path, original, changes):
        p = safe_path(path)
        if core.read_target(p) != original or read_file(p) != original:
            raise ValueError("配置已变化，请重新读取和预览")
        candidate = core.propose(original, changes)
        if core.parse(candidate) == core.parse(original):
            raise ValueError("没有实际变化")
        with locks([p]):
            if read_file(p) != original:
                raise ValueError("配置发生并发变化")
            self.root.mkdir(parents=True, exist_ok=True)
            tx = self.root / uuid.uuid4().hex
            tx.mkdir(mode=0o700)
            backup = tx / "original-config.toml"
            with backup.open("xb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
            receipt = tx / "receipt.json"
            record = {"target": str(p), "backup": str(backup),
                      "before_hash": sha(original), "after_hash": sha(candidate),
                      "changed_keys": sorted(changes), "status": "PREPARED"}
            prepared = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
            with receipt.open("xb") as stream:
                stream.write(prepared)
                stream.flush()
                os.fsync(stream.fileno())
            # The prepared receipt remains usable if the process stops after replace.
            replace_checked(p, sha(original), candidate)
            record["status"] = "APPLIED"
            applied = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
            try:
                replace_checked(receipt, sha(prepared), applied)
            except (OSError, ValueError) as exc:
                try:
                    recovery = read_file(receipt)
                except (OSError, ValueError):
                    recovery = None
                if recovery not in (prepared, applied):
                    raise ValueError(f"配置已写入并读回，备份回执需要人工核对。{receipt}") from exc
        return str(receipt)

    def load(self, receipt):
        p = safe_path(receipt)
        if (not p.is_relative_to(self.root) or p.name != "receipt.json"
                or p.parent.parent != self.root):
            raise ValueError("不属于本工具配置恢复目录")
        record = json.loads(read_file(p).decode("utf-8"))
        backup = safe_path(record["backup"])
        if backup != p.parent / "original-config.toml":
            raise ValueError("备份路径越界")
        original = read_file(backup)
        if sha(original) != record["before_hash"]:
            raise ValueError("备份已变化")
        core.parse(original)
        target = safe_path(record["target"])
        core.read_target(target)
        return record, original, target

    def restore(self, receipt):
        record, original, target = self.load(receipt)
        with locks([target]):
            if sha(read_file(target)) == record["before_hash"]:
                return "ALREADY_ORIGINAL"
            replace_checked(target, record["after_hash"], original)
        return "RESTORED"
