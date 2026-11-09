"""Explicit, bounded Markdown sync. No legacy apply scripts or automatic consent."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(path):
    p = Path(path)
    if not p.is_absolute() or ".." in p.parts or str(p).startswith("\\\\"):
        raise ValueError("只允许明确的本地绝对路径")
    for item in reversed((p, *p.parents)):
        try:
            s = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(s.st_mode) or getattr(s, "st_file_attributes", 0) & 0x400:
            raise ValueError("拒绝链接或重解析路径")
    return p


def read_file(path, maximum=2_000_000):
    p = safe_path(path)
    s = p.stat()
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size > maximum:
        raise ValueError("文件类型、大小或硬链接不安全")
    data = p.read_bytes()
    if len(data) > maximum:
        raise ValueError("文件过大")
    return data


def publish_new(path, content, maximum=2_000_000):
    """Publish a complete new file without replacing a competing writer."""
    path = safe_path(path)
    fd, temporary = tempfile.mkstemp(prefix=".qingxu-new-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(path)
        if os.name == "nt":
            os.rename(temporary, path)  # Windows rename fails if the target exists.
        else:
            os.link(temporary, path)
            os.unlink(temporary)
        if read_file(path, maximum) != content:
            raise ValueError("写后读回不一致，文件保留供检查。")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def replace_checked(path, expected_hash, content):
    p = safe_path(path)
    if sha(read_file(p)) != expected_hash:
        raise ValueError("文件已变化，请重新预览")
    fd, tmp = tempfile.mkstemp(prefix=".qingxu-", dir=p.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, p.stat().st_mode & 0o777)
        if sha(read_file(p)) != expected_hash:
            raise ValueError("写入前发生冲突")
        os.replace(tmp, p)
        if read_file(p) != content:
            raise ValueError("写后读回不一致")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def locks(paths):
    held = []
    try:
        for path in sorted(set(map(Path, paths)), key=lambda p: str(p).casefold()):
            lock = safe_path(path.with_name(path.name + ".qingxu.lock"))
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            held.append(lock)
            with os.fdopen(fd, "w") as stream:
                stream.write(str(os.getpid()))
        yield
    finally:
        for lock in reversed(held):
            lock.unlink()


def fingerprint(plan):
    return sha(json.dumps({k: v for k, v in plan.items() if k != "digest"},
                          sort_keys=True, ensure_ascii=False).encode("utf-8"))


class Store:
    def __init__(self, allowed, private_root):
        from privacy import ensure_private
        self.allowed = {safe_path(p) for p in allowed}
        self.root = safe_path(private_root)
        if any(self.root == p.parent or self.root.is_relative_to(p.parent) for p in self.allowed):
            raise ValueError("备份必须在目标知识库目录外")
        ensure_private(self.root)

    def check(self, path):
        p = safe_path(path)
        if p not in self.allowed:
            raise ValueError("路径不在当前项目精确允许列表")
        return p

    def preview(self, changes, context=""):
        ops = []
        for path, text in changes.items():
            p = self.check(path)
            if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 131072:
                raise ValueError("项目笔记应为非空、至多 128KiB UTF-8 文本")
            before = read_file(p)
            before.decode("utf-8-sig")
            if before != text.encode("utf-8"):
                ops.append({"path": str(p), "before_hash": sha(before),
                            "after_hash": sha(text.encode("utf-8")), "after": text})
        plan = {"context": context, "operations": ops}
        plan["digest"] = fingerprint(plan)
        return plan

    def journal(self, receipt):
        path = Path(receipt["receipt"])
        safe_path(path)
        data = json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".receipt-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def apply(self, plan, approval, stage_accepted, evidence="manual stage acceptance"):
        if not isinstance(approval, str) or approval != plan.get("digest"):
            return {"status": "WAITING_OR_DENIED", "written": [], "residual_paths": []}
        if stage_accepted is not True or not isinstance(evidence, str) or not evidence.strip():
            return {"status": "BLOCKED_STAGE", "written": [], "residual_paths": []}
        result = {"status": "BLOCKED", "written": [], "residual_paths": []}
        try:
            if fingerprint(plan) != approval:
                raise ValueError("预览已变化")
            ops = plan["operations"]
            if not ops:
                return {**result, "status": "NO_CHANGE"}
            paths = [self.check(op["path"]) for op in ops]
            if len(set(paths)) != len(paths):
                raise ValueError("重复目标")
            with locks(paths):
                for op, p in zip(ops, paths):
                    if sha(read_file(p)) != op["before_hash"]:
                        raise ValueError("目标文件已变动，未写任何目标")
                    if sha(op["after"].encode("utf-8")) != op["after_hash"]:
                        raise ValueError("候选内容校验失败")
                self.root.mkdir(parents=True, exist_ok=True)
                tx = self.root / uuid.uuid4().hex
                tx.mkdir(mode=0o700)
                result.update(receipt=str(tx / "receipt.json"), preview_hash=approval,
                              evidence=evidence, context=plan["context"], status="PREPARED")
                prepared = []
                for index, (op, p) in enumerate(zip(ops, paths)):
                    backup = tx / f"{index}.bak"
                    original = read_file(p)
                    if sha(original) != op["before_hash"]:
                        raise ValueError("备份前文件变化")
                    with backup.open("xb") as stream:
                        stream.write(original)
                        stream.flush()
                        os.fsync(stream.fileno())
                    prepared.append({k: v for k, v in op.items() if k != "after"} |
                                    {"backup": str(backup)})
                result["prepared"] = prepared
                self.journal(result)
                for op, item, p in zip(ops, prepared, paths):
                    # Journal intent before the replace, so an interruption remains recoverable.
                    result["written"].append(item)
                    result["residual_paths"].append(str(p))
                    self.journal(result)
                    replace_checked(p, op["before_hash"], op["after"].encode("utf-8"))
                    self.journal(result)
                result["status"] = "SYNCED"
        except (ValueError, OSError, KeyError, TypeError) as exc:
            result["error"] = str(exc)
            residual = []
            for item in result["written"]:
                try:
                    changed = sha(read_file(item["path"])) != item["before_hash"]
                except (ValueError, OSError):
                    changed = True
                if changed:
                    residual.append(item["path"])
            result["residual_paths"] = residual
            result["status"] = "PARTIAL" if residual else "BLOCKED"
        if "receipt" in result:
            self.journal(result)
        return result

    def load_receipt(self, path):
        p = safe_path(path)
        if not p.is_relative_to(self.root) or p.name != "receipt.json":
            raise ValueError("不是本工具私有同步回执")
        return json.loads(read_file(p).decode("utf-8"))

    def rollback(self, receipt, approved):
        if approved is not True:
            return {"status": "WAITING_OR_DENIED"}
        result = {"status": "ROLLED_BACK", "residual_paths": [], "errors": []}
        paths = [self.check(item["path"]) for item in receipt["written"]]
        try:
            with locks(paths):
                for item, p in zip(receipt["written"], paths):
                    try:
                        current = read_file(p)
                        if sha(current) == item["before_hash"]:
                            continue
                        backup = safe_path(item["backup"])
                        receipt_path = safe_path(receipt["receipt"])
                        if (not receipt_path.is_relative_to(self.root)
                                or backup.parent != receipt_path.parent
                                or backup.suffix != ".bak"):
                            raise ValueError("备份路径越界")
                        original = read_file(backup)
                        if sha(original) != item["before_hash"]:
                            raise ValueError("备份已变化")
                        replace_checked(p, item["after_hash"], original)
                    except (ValueError, OSError) as exc:
                        result["residual_paths"].append(str(p))
                        result["errors"].append(str(exc))
        except (ValueError, OSError) as exc:
            result["residual_paths"] = list(map(str, paths))
            result["errors"].append(str(exc))
        if result["residual_paths"]:
            result["status"] = "ROLLBACK_PARTIAL"
        if "receipt" in receipt:
            receipt["rollback"] = result
            self.journal(receipt)
        return result
