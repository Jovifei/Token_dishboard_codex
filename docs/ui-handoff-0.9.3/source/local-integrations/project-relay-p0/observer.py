"""One private, deterministic observer; no hooks, model calls, or task execution."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

import checkpoint as cp
import attribution


MAX_REPORT = 16 * 1024 * 1024
UTC8 = timezone(timedelta(hours=8))


def load_json(path, limit=cp.MAX_REGISTRY):
    return json.loads(cp.read_limited(cp.local_path(str(path)), limit).decode("utf-8-sig"))


def atomic_json(path, value):
    path = Path(path)
    cp.local_path(str(path.parent))
    try:
        prior = cp.read_limited(path, MAX_REPORT)
        json.loads(prior)
    except FileNotFoundError:
        prior = None
    encoded = cp.canonical(value) + b"\n"
    if len(encoded) > MAX_REPORT:
        raise cp.CheckpointError("report_too_large")
    fd, name = tempfile.mkstemp(prefix=".observer-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            latest = cp.read_limited(path, MAX_REPORT)
        except FileNotFoundError:
            latest = None
        if latest != prior:
            raise cp.CheckpointError("report_conflict")
        cp.local_path(str(path.parent))
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_settings(settings):
    for key in (
        "registry", "state_root", "stats_root", "state_db", "ccusage_cli",
        "node", "codex_home", "download_cache", "download_temp", "aliases_file",
    ):
        cp.local_path(settings[key])
    state = cp.local_path(settings["state_root"])
    stats = cp.local_path(settings["stats_root"])
    if cp.inside(state, stats) or cp.inside(stats, state):
        raise cp.CheckpointError("state_directories_overlap")
    for root in (state, stats):
        cp.load_registry(settings["registry"], root)
        if not root.is_dir():
            raise cp.CheckpointError("private_directory_not_provisioned")
    for key, minimum in (
        ("checkpoint_interval_seconds", 60), ("usage_interval_seconds", 600),
    ):
        if type(settings[key]) is not int or settings[key] < minimum:
            raise cp.CheckpointError("invalid_collection_interval")
    return state, stats


def capture_usage(settings, stats):
    day = datetime.now(UTC8).date().isoformat()
    cli = Path(settings["ccusage_cli"])
    package = load_json(cli.parents[1] / "package.json")
    if package.get("version") != settings["ccusage_version"]:
        raise cp.CheckpointError("ccusage_version_changed")
    command = [
        settings["node"], str(cli), "codex", "session",
        "--since", day, "--until", day, "--timezone", "Asia/Shanghai",
        "--json", "--offline",
    ]
    env = dict(os.environ, CODEX_HOME=settings["codex_home"], NO_COLOR="1",
               XDG_CACHE_HOME=settings["download_cache"],
               TEMP=settings["download_temp"], TMP=settings["download_temp"])
    started = cp.now()
    # Files capture tool output outside model history; only bounded JSON is retained.
    with tempfile.TemporaryFile(dir=stats) as output, tempfile.TemporaryFile(dir=stats) as error:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=output, stderr=error,
            env=env, cwd=Path(__file__).parent, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output.seek(0, os.SEEK_END)
        size = output.tell()
        if size > MAX_REPORT:
            raise cp.CheckpointError("usage_output_too_large")
        output.seek(0)
        raw = output.read()
        if result.returncode:
            raise cp.CheckpointError("ccusage_failed")
    usage = json.loads(raw)
    registry = load_json(settings["registry"])
    aliases = load_json(settings["aliases_file"])
    threads, edges = attribution.read_metadata(settings["state_db"])
    report = attribution.aggregate(usage, registry, threads, edges, aliases)
    report.update({
        "date": day, "timezone": "Asia/Shanghai", "started_utc": started,
        "finished_utc": cp.now(), "ccusage_version": settings["ccusage_version"],
        "raw_sha256": hashlib.sha256(raw).hexdigest(), "raw_bytes": size,
        "command_exit": result.returncode,
        "comparison": "observation_only_not_causal_savings",
        "alias_coverage": "verified_at_deployment_not_live_worktree_discovery",
    })
    atomic_json(stats / ("usage-" + day + ".json"), report)
    atomic_json(stats / "usage-latest.json", report)
    return {
        "result": "ok", "date": day, "records": len(report["sessions"]),
        "totals": report["totals"], "captured_utc": report["finished_utc"],
        "path": str(stats / "usage-latest.json"),
    }


def metadata_cycle(settings, state, paused):
    projects = cp.load_registry(settings["registry"], state)
    results = cp.collect(state, projects, paused)
    for result in results:
        if result["result"] == "error" and result.get("project_id") is not None:
            paused[result["project_id"]] = {
                "error": result["error"], "paused_at": cp.now(),
            }
            result["result"] = "paused"
    return {
        "observed_utc": cp.now(), "projects": len(results),
        "healthy": sum(x["result"] in ("created", "updated", "unchanged") for x in results),
        "paused": dict(paused),
        "metadata_scope": cp.METADATA_SCOPE,
        "business_progress": "unknown",
    }


def run(settings, once=False):
    state, stats = validate_settings(settings)
    paused = {}
    started = cp.now()
    run_id = uuid.uuid4().hex
    status = {
        "schema_version": 1, "run_id": run_id, "pid": os.getpid(),
        "python": sys.executable, "observer": str(Path(__file__).resolve()),
        "started_utc": started, "running": True,
        "purpose": "private_metadata_and_readonly_usage_only",
        "auto_start_on_login": False, "hooks_installed": False,
        "metadata": None, "usage": None,
    }
    next_meta = next_usage = 0.0
    with cp.Lock(state, "_observer_instance"):
        atomic_json(state / "observer-status.json", status)
        try:
            while not cp.stop_requested(state):
                tick = time.monotonic()
                if tick >= next_meta:
                    status["metadata"] = metadata_cycle(settings, state, paused)
                    next_meta = tick + settings["checkpoint_interval_seconds"]
                if tick >= next_usage:
                    try:
                        status["usage"] = capture_usage(settings, stats)
                    except (cp.CheckpointError, OSError, ValueError,
                            sqlite3.Error, subprocess.SubprocessError) as error:
                        previous = status.get("usage")
                        status["usage"] = {
                            "result": "error", "error": type(error).__name__,
                            "code": getattr(error, "code", None),
                            "last_good_utc": previous.get("captured_utc") if previous else None,
                            "observed_utc": cp.now(),
                        }
                    next_usage = tick + settings["usage_interval_seconds"]
                status["heartbeat_utc"] = cp.now()
                atomic_json(state / "observer-status.json", status)
                if once:
                    return 0 if status["usage"]["result"] == "ok" else 1
                until = min(next_meta, next_usage)
                while time.monotonic() < until and not cp.stop_requested(state):
                    time.sleep(min(1.0, max(0.0, until - time.monotonic())))
        finally:
            status.update(running=False, stopped_utc=cp.now())
            atomic_json(state / "observer-status.json", status)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        raise SystemExit(run(load_json(args.settings), args.once))
    except (cp.CheckpointError, OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"result": "error", "type": type(error).__name__,
                          "code": getattr(error, "code", None)}))
        raise SystemExit(1)
