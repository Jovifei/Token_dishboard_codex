#!/usr/bin/env python3
"""Private, local-only Git metadata checkpoints with an explicit summary API."""

import argparse
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone


FORMAT = "project-relay-private-checkpoint-v1"
METADATA_SCOPE = "NOT_SEMANTIC_HANDOFF"
SUMMARY_SCOPE = "CALLER_SUPPLIED_UNVERIFIED_SUMMARY"
MAX_REGISTRY = 1024 * 1024
MAX_STATE = 128 * 1024
MAX_SUMMARY = 8 * 1024
MAX_STATUS = 2 * 1024 * 1024
MAX_DIFF = 2 * 1024 * 1024
MAX_RECEIPT = 1024 * 1024
PROJECT_SECONDS = 20.0
COMMAND_SECONDS = 6.0


class CheckpointError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def local_path(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CheckpointError("invalid_path")
    p = Path(value)
    if not p.is_absolute() or ".." in p.parts:
        raise CheckpointError("invalid_path")
    if os.name == "nt":
        drive = p.drive
        if not drive or drive.startswith("\\\\"):
            raise CheckpointError("network_or_device_path")
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")
        if drive_type not in (2, 3, 6):
            raise CheckpointError("network_or_device_path")
    p = Path(os.path.abspath(p))
    for component in reversed((p, *p.parents)):
        try:
            entry = os.lstat(component)
        except FileNotFoundError:
            break
        except OSError:
            raise CheckpointError("path_access_failed") from None
        if stat.S_ISLNK(entry.st_mode) or getattr(entry, "st_file_attributes", 0) & 0x400:
            raise CheckpointError("link_or_reparse_point")
    return p


def obviously_nonlocal(value):
    if os.name != "nt":
        return False
    drive = Path(value).drive
    return (not drive or drive.startswith("\\\\")
            or ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") not in (2, 3, 6))


def inside(child, parent):
    try:
        c = os.path.normcase(os.path.abspath(child))
        p = os.path.normcase(os.path.abspath(parent))
        return os.path.commonpath((c, p)) == p
    except ValueError:
        return False


def valid_id(value):
    return (isinstance(value, str) and 0 < len(value) <= 128 and ".." not in value
            and value not in (".", "..") and not value.endswith(".")
            and all(c.isalnum() or c in "_.-" for c in value))


def read_limited(path, maximum):
    try:
        entry = os.lstat(path)
        if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1
                or getattr(entry, "st_file_attributes", 0) & 0x400):
            raise CheckpointError("unsafe_file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            current = os.fstat(stream.fileno())
            if (current.st_dev, current.st_ino) != (entry.st_dev, entry.st_ino):
                raise CheckpointError("state_conflict")
            data = stream.read(maximum + 1)
    except FileNotFoundError:
        raise
    except OSError:
        raise CheckpointError("file_access_failed") from None
    if len(data) > maximum:
        raise CheckpointError("file_too_large")
    return data


def load_registry(registry_path, state_root):
    registry_path = local_path(registry_path)
    try:
        registry = json.loads(read_limited(registry_path, MAX_REGISTRY).decode("utf-8-sig"))
    except (FileNotFoundError, UnicodeError, ValueError):
        raise CheckpointError("invalid_registry") from None
    if (not isinstance(registry, dict) or not isinstance(registry.get("projects"), list)
            or len(registry["projects"]) > 1000):
        raise CheckpointError("invalid_registry")
    projects = registry["projects"]
    root_real = os.path.realpath(state_root)
    for project in projects:
        raw = project.get("path") if isinstance(project, dict) else None
        if (isinstance(raw, str) and "\x00" not in raw and os.path.isabs(raw)
                and not obviously_nonlocal(raw)):
            # Protect even invalid or linked registry entries before any state write.
            if any(inside(root, candidate) for root in (state_root, root_real)
                   for candidate in (raw, os.path.realpath(raw))):
                raise CheckpointError("state_under_project")
    return projects


def ensure_state_root(path):
    local_path(str(path))
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError:
        raise CheckpointError("state_directory_failed") from None
    local_path(str(path))
    try:
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            raise CheckpointError("state_directory_failed")
    except OSError:
        raise CheckpointError("state_directory_failed") from None


def names(root, project_id):
    key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    return root / (key + ".json"), root / (key + ".lock")


class Lock:
    def __init__(self, root, project_id):
        self.root = root
        self.path = names(root, project_id)[1]
        self.identity = None

    def __enter__(self):
        local_path(str(self.root))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.path, flags, 0o600)
        except FileExistsError:
            raise CheckpointError("lock_contended") from None
        except OSError:
            raise CheckpointError("lock_failed") from None
        try:
            entry = os.fstat(fd)
            self.identity = (entry.st_dev, entry.st_ino)
            os.write(fd, (str(os.getpid()) + "\n").encode("ascii"))
            os.fsync(fd)
        except OSError:
            raise CheckpointError("lock_failed") from None
        finally:
            os.close(fd)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            local_path(str(self.root))
            entry = os.lstat(self.path)
            if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1
                    or getattr(entry, "st_file_attributes", 0) & 0x400
                    or (entry.st_dev, entry.st_ino) != self.identity):
                raise CheckpointError("lock_conflict")
            os.unlink(self.path)
        except OSError:
            raise CheckpointError("lock_release_failed") from None


def load_checkpoint(root, project_id):
    path = names(root, project_id)[0]
    local_path(str(root))
    try:
        raw = read_limited(path, MAX_STATE)
    except FileNotFoundError:
        return None, None
    try:
        obj = json.loads(raw.decode("utf-8"))
        metadata = obj["metadata"]
        summary = obj["semantic_summary"]
        source_hash = obj["summary_source_metadata_hash"]
        source_revision = obj["summary_source_revision"]
        if (not isinstance(obj, dict) or obj["format"] != FORMAT or obj["project_id"] != project_id
                or type(obj["revision"]) is not int or obj["revision"] < 1
                or not isinstance(metadata, dict) or obj["metadata_hash"] != digest(metadata)
                or obj["metadata_scope"] != METADATA_SCOPE or obj["business_progress"] != "unknown"
                or obj["summary_scope"] != SUMMARY_SCOPE or not isinstance(obj["updated_at"], str)
                or not (summary is None or isinstance(summary, str) and len(summary.encode("utf-8")) <= MAX_SUMMARY)
                or (summary is None and (source_hash is not None or source_revision is not None))
                or (summary is not None and (not isinstance(source_hash, str) or len(source_hash) != 64
                    or any(c not in "0123456789abcdef" for c in source_hash)
                    or type(source_revision) is not int or not 1 <= source_revision < obj["revision"]))
                or obj["summary_hash"] != (None if summary is None else hashlib.sha256(summary.encode("utf-8")).hexdigest())):
            raise ValueError
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError):
        raise CheckpointError("invalid_state") from None
    return obj, hashlib.sha256(raw).hexdigest()


def token(obj, raw_hash):
    return None if obj is None else (obj["revision"], obj["metadata_hash"], obj["summary_hash"], raw_hash)


def commit(root, project_id, expected, obj):
    current, current_raw_hash = load_checkpoint(root, project_id)
    if token(current, current_raw_hash) != expected:
        raise CheckpointError("state_conflict")
    encoded = canonical(obj) + b"\n"
    if len(encoded) > MAX_STATE:
        raise CheckpointError("state_too_large")
    path = names(root, project_id)[0]
    local_path(str(root))
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".checkpoint-", suffix=".tmp", dir=root)
        with os.fdopen(fd, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        local_path(str(root))
        latest, latest_raw_hash = load_checkpoint(root, project_id)
        if token(latest, latest_raw_hash) != expected:
            raise CheckpointError("state_conflict")
        os.replace(tmp, path)
        tmp = None
    except OSError:
        raise CheckpointError("state_write_failed") from None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            except OSError:
                raise CheckpointError("state_write_failed") from None


def git_env():
    keep = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1", "GIT_PAGER": "cat", "LC_ALL": "C"})
    return env


class StatusCounter:
    def __init__(self):
        self.prefix = bytearray()
        self.rename_path = False
        self.count = 0
        self.valid = True

    def feed(self, chunk):
        for byte in chunk:
            if byte == 0:
                if self.rename_path:
                    self.rename_path = False
                elif len(self.prefix) != 3 or self.prefix[2] != 32:
                    self.valid = False
                else:
                    self.count += 1
                    self.rename_path = b"R" in self.prefix[:2] or b"C" in self.prefix[:2]
                self.prefix.clear()
            elif not self.rename_path and len(self.prefix) < 3:
                self.prefix.append(byte)

    def complete(self):
        return self.valid and not self.prefix and not self.rename_path


def git_stream(project, args, deadline, limit, capture=False, counter=None):
    left = deadline - time.monotonic()
    if left <= 0:
        return {"status": "timeout"}
    cmd = ["git", "-C", str(project), "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
           "-c", "protocol.allow=never", "-c", "color.ui=false", *args]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=git_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        raise CheckpointError("git_unavailable") from None
    result = {"hash": hashlib.sha256(), "size": 0, "chunks": [] if capture else None, "overflow": False, "error": False}

    def consume():
        try:
            while True:
                chunk = proc.stdout.read(min(65536, limit - result["size"] + 1))
                if not chunk:
                    break
                if result["size"] + len(chunk) > limit:
                    result["overflow"] = True
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    break
                result["size"] += len(chunk)
                result["hash"].update(chunk)
                if capture:
                    result["chunks"].append(chunk)
                if counter is not None:
                    counter.feed(chunk)
        except (OSError, ValueError):
            result["error"] = True
            try:
                proc.kill()
            except OSError:
                pass

    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=max(0.001, min(COMMAND_SECONDS, deadline - time.monotonic())))
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            raise CheckpointError("git_termination_failed") from None
    reader.join(timeout=2)
    if reader.is_alive():
        raise CheckpointError("git_reader_failed")
    proc.stdout.close()
    if timed_out:
        return {"status": "timeout"}
    if result["error"]:
        raise CheckpointError("git_read_failed")
    if result["overflow"]:
        return {"status": "size_limit"}
    return {"status": "ok", "code": proc.returncode, "hash": result["hash"].hexdigest(),
            "data": b"".join(result["chunks"]) if capture else None}


def required_git(project, args, deadline, limit=32768):
    result = git_stream(project, args, deadline, limit, capture=True)
    if result["status"] != "ok":
        raise CheckpointError("git_" + result["status"])
    return result


def git_admin_path(project, pointer):
    path = Path(pointer)
    if len(path.parts) > 128 or (path.drive and not path.is_absolute()):
        raise CheckpointError("unsafe_git")
    current = Path(path.anchor) if path.is_absolute() else project
    parts = path.parts[1:] if path.is_absolute() else path.parts
    # Git submodule pointers may use ".."; check each component before collapsing it.
    for part in parts:
        current = current.parent if part == ".." else current / part
        local_path(str(current))
    return local_path(str(current))


def observe(project, deadline=None):
    if deadline is None:
        deadline = time.monotonic() + PROJECT_SECONDS
    project = local_path(str(project))
    try:
        root_stat = os.stat(project, follow_symlinks=False)
    except FileNotFoundError:
        raise CheckpointError("project_missing") from None
    except OSError:
        raise CheckpointError("project_access_failed") from None
    if not stat.S_ISDIR(root_stat.st_mode):
        raise CheckpointError("project_not_directory")
    root_metadata = {"root": str(project), "root_device": root_stat.st_dev, "root_inode": root_stat.st_ino, "exists": True}
    git_marker = project / ".git"
    try:
        marker = os.lstat(git_marker)
    except FileNotFoundError:
        return {**root_metadata, "git_status": "not_repository", "head": None, "branch": None,
                "porcelain_sha256": None, "porcelain_count": None, "porcelain_coverage": "not_applicable",
                "tracked_diff": None, "fingerprint_coverage": "ROOT_METADATA_ONLY"}
    except OSError:
        raise CheckpointError("project_access_failed") from None
    if (stat.S_ISLNK(marker.st_mode) or getattr(marker, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(marker.st_mode) or stat.S_ISREG(marker.st_mode))):
        raise CheckpointError("unsafe_git")
    if stat.S_ISREG(marker.st_mode):
        try:
            marker_text = read_limited(git_marker, 4096).decode("utf-8").strip()
            if not marker_text.startswith("gitdir: "):
                raise CheckpointError("unsafe_git")
            gitdir = marker_text[8:]
            if not git_admin_path(project, gitdir).is_dir():
                raise CheckpointError("unsafe_git")
        except (OSError, UnicodeError):
            raise CheckpointError("unsafe_git") from None
    top = required_git(project, ["rev-parse", "--show-toplevel"], deadline)
    if top["code"] != 0:
        raise CheckpointError("not_git_root")
    try:
        git_root = top["data"].decode("utf-8").rstrip("\r\n")
        if os.path.normcase(os.path.realpath(git_root)) != os.path.normcase(os.path.realpath(project)):
            raise CheckpointError("not_git_root")
        head_result = required_git(project, ["rev-parse", "--verify", "--quiet", "HEAD"], deadline)
        branch_result = required_git(project, ["symbolic-ref", "--quiet", "--short", "HEAD"], deadline)
        if head_result["code"] not in (0, 1) or branch_result["code"] not in (0, 1):
            raise CheckpointError("git_failed")
        head = head_result["data"].decode("ascii").strip() if head_result["code"] == 0 else None
        branch = branch_result["data"].decode("utf-8").rstrip("\r\n") if branch_result["code"] == 0 else None
        if head is not None and (len(head) not in (40, 64) or any(c not in "0123456789abcdef" for c in head)):
            raise CheckpointError("git_invalid_output")
    except UnicodeError:
        raise CheckpointError("git_invalid_output") from None
    counter = StatusCounter()
    status = git_stream(project, ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"],
                        deadline, MAX_STATUS, counter=counter)
    if status["status"] == "timeout":
        raise CheckpointError("git_timeout")
    if status["status"] == "ok" and (status["code"] != 0 or not counter.complete()):
        raise CheckpointError("git_status_failed")
    diffs = {}
    for name, cached in (("staged", ["--cached"]), ("unstaged", [])):
        result = git_stream(project, ["diff", *cached, "--no-ext-diff", "--no-textconv", "--binary", "--full-index",
                                      "--no-renames", "--ignore-submodules=all", "--no-color", "--"], deadline, MAX_DIFF)
        if result["status"] == "ok" and result["code"] != 0:
            raise CheckpointError("git_diff_failed")
        diffs[name] = {"sha256": result.get("hash") if result["status"] == "ok" else None,
                       "coverage": result["status"]}
    return {**root_metadata, "git_status": "repository", "head": head, "branch": branch,
            "porcelain_sha256": status.get("hash") if status["status"] == "ok" else None,
            "porcelain_count": counter.count if status["status"] == "ok" else None,
            "porcelain_coverage": status["status"], "tracked_diff": diffs,
            "fingerprint_coverage": "INCOMPLETE_IGNORED_UNTRACKED_SUBMODULES_AND_LIMITS"}


def new_checkpoint(project_id, old, metadata, summary, bind_summary=False):
    if summary is None:
        source_hash, source_revision = None, None
    elif bind_summary and old is not None:
        source_hash, source_revision = digest(metadata), old["revision"]
    elif old is not None and old["semantic_summary"] == summary:
        source_hash, source_revision = old["summary_source_metadata_hash"], old["summary_source_revision"]
    else:
        raise CheckpointError("summary_source_missing")
    return {"format": FORMAT, "project_id": project_id, "revision": 1 if old is None else old["revision"] + 1,
            "metadata": metadata, "metadata_hash": digest(metadata), "metadata_scope": METADATA_SCOPE,
            "business_progress": "unknown", "semantic_summary": summary,
            "summary_hash": None if summary is None else hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            "summary_source_metadata_hash": source_hash, "summary_source_revision": source_revision,
            "summary_scope": SUMMARY_SCOPE, "updated_at": now()}


def summary_stale(obj):
    if obj["semantic_summary"] is None:
        return None
    return obj["summary_source_metadata_hash"] != obj["metadata_hash"]


def project_list(projects):
    counts = {}
    for item in projects:
        pid = item.get("project_id") if isinstance(item, dict) else None
        if valid_id(pid):
            counts[pid] = counts.get(pid, 0) + 1
    for item in projects:
        pid = item.get("project_id") if isinstance(item, dict) else None
        try:
            if not valid_id(pid):
                raise CheckpointError("invalid_project_id")
            if counts[pid] != 1:
                raise CheckpointError("duplicate_project_id")
            yield pid, local_path(item.get("path")), None
        except CheckpointError as error:
            yield pid if valid_id(pid) else None, None, error.code


def collect_one(root, project_id, project):
    deadline = time.monotonic() + PROJECT_SECONDS
    with Lock(root, project_id):
        old, old_raw_hash = load_checkpoint(root, project_id)
        metadata = observe(project, deadline)
        if old is not None and any(old["metadata"].get(key) != metadata[key]
                                   for key in ("root", "root_device", "root_inode")):
            raise CheckpointError("project_identity_changed")
        if (old is not None and old["metadata"].get("git_status") == "repository"
                and metadata["git_status"] == "not_repository"):
            raise CheckpointError("repository_marker_missing")
        if old is not None and old["metadata_hash"] == digest(metadata):
            obj = old
            outcome = "unchanged"
        else:
            obj = new_checkpoint(project_id, old, metadata, None if old is None else old["semantic_summary"])
            commit(root, project_id, token(old, old_raw_hash), obj)
            outcome = "created" if old is None else "updated"
    return {"project_id": project_id, "result": outcome, "revision": obj["revision"], "metadata_hash": obj["metadata_hash"],
            "metadata": obj["metadata"], "checkpoint_updated_at": obj["updated_at"], "observed_at": now(),
            "metadata_scope": METADATA_SCOPE, "business_progress": "unknown", "summary_stale": summary_stale(obj)}


def collect(root, projects, paused=None):
    results = []
    for pid, path, invalid in project_list(projects):
        try:
            if invalid:
                raise CheckpointError(invalid)
            if paused is not None and pid in paused:
                results.append({"project_id": pid, "result": "paused", "error": paused[pid]["error"],
                                "observed_at": now(), "metadata_scope": METADATA_SCOPE, "business_progress": "unknown"})
                continue
            results.append(collect_one(root, pid, path))
        except CheckpointError as error:
            results.append({"project_id": pid, "result": "error", "error": error.code,
                            "observed_at": now(), "metadata_scope": METADATA_SCOPE, "business_progress": "unknown"})
        except OSError:
            results.append({"project_id": pid, "result": "error", "error": "project_access_failed",
                            "observed_at": now(), "metadata_scope": METADATA_SCOPE, "business_progress": "unknown"})
    return results


def selected(projects, project_id):
    matches = [(pid, path, error) for pid, path, error in project_list(projects) if pid == project_id]
    if len(matches) != 1 or matches[0][2]:
        raise CheckpointError(matches[0][2] if matches and matches[0][2] else "project_not_allowlisted")
    return matches[0][1]


def semantic_read(root, project_id):
    obj, _ = load_checkpoint(root, project_id)
    if obj is None:
        raise CheckpointError("checkpoint_missing")
    return {"project_id": project_id, "revision": obj["revision"], "summary": obj["semantic_summary"],
            "summary_scope": SUMMARY_SCOPE, "summary_stale": summary_stale(obj),
            "summary_source_metadata_hash": obj["summary_source_metadata_hash"],
            "summary_source_revision": obj["summary_source_revision"],
            "metadata_scope": METADATA_SCOPE, "business_progress": "unknown"}


def semantic_save(root, project_id, project, revision, summary):
    if not isinstance(summary, str) or not summary or len(summary.encode("utf-8")) > MAX_SUMMARY:
        raise CheckpointError("invalid_summary")
    deadline = time.monotonic() + PROJECT_SECONDS
    with Lock(root, project_id):
        old, old_raw_hash = load_checkpoint(root, project_id)
        if old is None:
            raise CheckpointError("checkpoint_missing")
        if old["revision"] != revision:
            raise CheckpointError("state_conflict")
        current = observe(project, deadline)
        meta = old["metadata"]
        if any(current[key] != meta.get(key) for key in ("root", "root_device", "root_inode")):
            raise CheckpointError("project_identity_changed")
        if digest(current) != old["metadata_hash"]:
            raise CheckpointError("state_conflict")
        if old["semantic_summary"] == summary and not summary_stale(old):
            obj, outcome = old, "unchanged"
        else:
            obj = new_checkpoint(project_id, old, meta, summary, bind_summary=True)
            commit(root, project_id, token(old, old_raw_hash), obj)
            outcome = "updated"
    return {"project_id": project_id, "result": outcome, "revision": obj["revision"], "summary_scope": SUMMARY_SCOPE,
            "summary_source_metadata_hash": obj["summary_source_metadata_hash"],
            "summary_source_revision": obj["summary_source_revision"], "summary_stale": summary_stale(obj)}


def emit(value):
    print(json.dumps(value, ensure_ascii=True, separators=(",", ":")), flush=True)


def stop_requested(root):
    local_path(str(root))
    path = root / "STOP"
    try:
        entry = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(entry.st_mode) or getattr(entry, "st_file_attributes", 0) & 0x400:
        raise CheckpointError("unsafe_stop_sentinel")
    return True


def write_watch_receipt(root, run_id, paused):
    receipt = root / ("watch-receipt-" + run_id + ".json")
    data = canonical({"format": "project-relay-watch-receipt-v1", "run_id": run_id,
                      "updated_at": now(), "paused_projects": paused}) + b"\n"
    if len(data) > MAX_RECEIPT:
        raise CheckpointError("receipt_too_large")
    local_path(str(root))
    if receipt.exists():
        read_limited(receipt, MAX_RECEIPT)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".receipt-", suffix=".tmp", dir=root)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        local_path(str(root))
        os.replace(tmp, receipt)
        tmp = None
    except OSError:
        raise CheckpointError("receipt_write_failed") from None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            except OSError:
                raise CheckpointError("receipt_write_failed") from None
    return receipt.name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--state-root", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("collect")
    watcher = commands.add_parser("watch")
    watcher.add_argument("--interval", type=float, default=600.0)
    reader = commands.add_parser("semantic-read")
    reader.add_argument("--project-id", required=True)
    writer = commands.add_parser("semantic-save")
    writer.add_argument("--project-id", required=True)
    writer.add_argument("--expected-revision", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "watch" and (not math.isfinite(args.interval) or args.interval <= 0):
            raise CheckpointError("invalid_interval")
        root = local_path(args.state_root)
        projects = load_registry(args.registry, root)
        ensure_state_root(root)
        if args.command == "semantic-read":
            selected(projects, args.project_id)
            emit(semantic_read(root, args.project_id))
        elif args.command == "semantic-save":
            project = selected(projects, args.project_id)
            data = sys.stdin.buffer.read(MAX_SUMMARY + 1)
            if len(data) > MAX_SUMMARY:
                raise CheckpointError("invalid_summary")
            try:
                summary = data.decode("utf-8")
            except UnicodeError:
                raise CheckpointError("invalid_summary") from None
            emit(semantic_save(root, args.project_id, project, args.expected_revision, summary))
        else:
            paused = {}
            receipt_id = uuid.uuid4().hex
            receipt_dirty = False
            while True:
                if args.command == "watch" and stop_requested(root):
                    emit({"result": "stopped", "reason": "stop_sentinel"})
                    return 0
                # Reload the allowlist before every cycle; removed projects are never collected.
                projects = load_registry(args.registry, root)
                results = collect(root, projects, paused if args.command == "watch" else None)
                conflicts = {"lock_contended", "lock_failed", "lock_conflict", "lock_release_failed",
                             "state_conflict", "invalid_state", "unsafe_file", "file_too_large",
                             "state_write_failed", "project_identity_changed", "repository_marker_missing", "unsafe_git"}
                new_pause = False
                if args.command == "watch":
                    for result in results:
                        pid = result.get("project_id")
                        if pid is not None and result.get("error") in conflicts and pid not in paused:
                            paused[pid] = {"error": result["error"], "paused_at": now()}
                            result["result"] = "paused"
                            new_pause = True
                receipt_dirty = receipt_dirty or new_pause
                if receipt_dirty:
                    try:
                        receipt = write_watch_receipt(root, receipt_id, paused)
                        receipt_dirty = False
                        emit({"result": "pause_receipt", "file": receipt, "paused_count": len(paused)})
                    except CheckpointError as error:
                        emit({"result": "error", "error": error.code})
                for result in results:
                    emit(result)
                failed = any(result["result"] == "error" for result in results)
                if args.command == "collect":
                    return 1 if failed else 0
                until = time.monotonic() + args.interval
                while time.monotonic() < until:
                    if stop_requested(root):
                        emit({"result": "stopped", "reason": "stop_sentinel"})
                        return 0
                    time.sleep(max(0.0, min(1.0, until - time.monotonic())))
        return 0
    except CheckpointError as error:
        emit({"result": "error", "error": error.code})
        return 1
    except KeyboardInterrupt:
        emit({"result": "stopped", "reason": "interrupted"})
        return 130


if __name__ == "__main__":
    sys.exit(main())
