"""Create a small, synthetic Codex home and an ordinary Markdown knowledge folder."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import sys

BUNDLE = Path(__file__).resolve().parents[1]
APP_SOURCE = BUNDLE / "source" / "work" / "qingxu"
CHECKPOINT = BUNDLE / "source" / "local-integrations" / "project-relay-p0" / "checkpoint.py"
IDS = {name: f"{i:08x}-1111-4111-8111-111111111111" for i, name in enumerate(
    ("normal", "history", "unknown", "alert", "manual", "child", "fork"), 1)}
PROJECT = "demo-blueprint"
SUMMARY = ("目标：完成示例蓝图的筛选器。\n已完成：确认可用的筛选字段。\n"
           "待办：先读示例项目目录和现有说明，再核查筛选器的边界。\n"
           "验收：示例列表能区分空结果与读取失败。")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def event(kind, timestamp, **payload):
    return {"type": kind, "timestamp": timestamp.isoformat(), "payload": payload}


def append_events(path, *records):
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        for item in records:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")


def measurement(now, total, window=None, limit=None):
    return event("event_msg", now, type="token_count", info={
        "model_context_window": window, "model_auto_compact_token_limit": limit,
        "last_token_usage": {"input_tokens": total - 120, "cached_input_tokens": total // 2,
                             "output_tokens": 120, "reasoning_output_tokens": 48, "total_tokens": total},
        "total_token_usage": {"total_tokens": total}})


def snapshot_row(key, role, values):
    return {"session_id": f"demo-{IDS[key]}", "thread_id": IDS[key], "role": role, "project_id": PROJECT,
            **dict(zip(("inputTokens", "cacheReadTokens", "cacheCreationTokens", "outputTokens",
                        "reasoningOutputTokens", "totalTokens"), values))}


def create(root, blank=False):
    from relay import KB_FILES

    root = Path(root).absolute()
    root.mkdir()
    home, prefs, state = (root / name for name in ("Codex 示例数据", "轻续 私有偏好", "AgentState"))
    for path in (home, prefs, state):
        path.mkdir()
    settings = {"config": str(home / "config.toml"), "chat_db": str(home / "state_5.sqlite"),
                "mapping": str(root / "demo-mapping.json"), "registry": str(root / "registry.json"),
                "runtime": str(root / "可选接力引擎"), "state_root": str(state),
                "usage": str(root / "usage-latest.json"), "python": "python.exe"}
    result = {"root": root, "prefs": prefs, "settings": settings, "logs": {}}
    if blank:
        return result

    Path(settings["runtime"]).mkdir()
    shutil.copy2(CHECKPOINT, Path(settings["runtime"]) / "checkpoint.py")

    alpha, beta, notes = (root / name for name in ("示例工程 蓝图", "示例工程 行程", "普通 Markdown 文件夹"))
    for path in (alpha, beta, notes):
        path.mkdir()
    (alpha / "README.md").write_text("# 示例蓝图\n这是用于交接包的合成项目。\n", encoding="utf-8")
    page_root = notes / "03-项目记忆" / PROJECT
    page_root.mkdir(parents=True)
    for kind, filename in KB_FILES.items():
        (page_root / filename).write_text(f"# 合成示例 · {kind}\n\n示例条目，供检查映射。\n", encoding="utf-8")
    (notes / "INDEX.md").write_text("# 资料索引\n\n- 蓝图：[项目概览](03-项目记忆/demo-blueprint/00-项目概览.md)\n",
                                    encoding="utf-8")
    write_json(Path(settings["registry"]), {"projects": [
        {"project_id": PROJECT, "path": str(alpha), "label": "示例蓝图"}]})
    write_json(Path(settings["mapping"]), {"active_profile": "demo", "profiles": {"demo": {
        "memory_root": str(notes), "obsidian_vault_root": str(notes), "approved_memory_roots": [str(notes)],
        "project_mappings": [{"source_root": str(alpha), "memory_id": PROJECT}]}}})
    Path(settings["config"]).write_bytes(b'model = "gpt-6.1-sol"\nmodel_reasoning_effort = "medium"\n\n'
                                        b'[agents]\ndefault_subagent_model = "gpt-6.1-terra"\n'
                                        b'default_subagent_reasoning_effort = "medium"\n')
    write_json(home / "models_cache.json", {"client_version": "0.167.0", "models": [
        {"slug": slug, "context_window": window, "max_context_window": 872000, "default_reasoning_level": effort,
         "truncation_policy": {"mode": "tokens", "limit": 10000},
         "supported_reasoning_levels": [{"effort": level} for level in levels]}
        for slug, window, effort, levels in (
            ("gpt-6.1-sol", 500000, "low", ("low", "medium", "high", "xhigh", "max", "ultra")),
            ("gpt-6.1-terra", 500000, "low", ("low", "medium", "high", "xhigh", "max", "ultra")),
            ("gpt-6-astra", 272000, "medium", ("low", "medium", "high", "xhigh", "max", "ultra")),
            ("gpt-6-luna", 272000, "medium", ("low", "medium", "high", "xhigh", "max"))) ]})

    now = datetime.now(timezone.utc)
    local = now.astimezone(timezone(timedelta(hours=8)))
    logs = home / "sessions" / local.strftime("%Y/%m/%d")
    logs.mkdir(parents=True)
    entries = (("normal", "蓝图 · 正常测量", alpha, "p-a", now, 128000, 475000, 450000),
               ("history", "蓝图 · 历史测量", alpha, "p-a", now - timedelta(minutes=35), 170000, 475000, 450000),
               ("unknown", "蓝图 · 容量未知", alpha, "p-a", now, 26000, None, None),
               ("alert", "蓝图 · 接近压缩线", alpha, "p-a", now, 386000, 475000, 450000),
               ("manual", "行程 · 手动交接", beta, "p-b", now, 51000, 475000, None))
    with closing(sqlite3.connect(settings["chat_db"])) as con:
        con.execute("CREATE TABLE projects(id TEXT,name TEXT)")
        con.execute("CREATE TABLE project_roots(project_id TEXT,path TEXT)")
        con.execute("CREATE TABLE threads(id TEXT,name TEXT,cwd TEXT,source TEXT,archived INTEGER,"
                    "updated_at INTEGER,project_id TEXT,rollout_path TEXT,thread_source TEXT,agent_role TEXT)")
        con.executemany("INSERT INTO projects VALUES (?,?)", [("p-a", "示例蓝图"), ("p-b", "示例行程")])
        con.executemany("INSERT INTO project_roots VALUES (?,?)", [("p-a", str(alpha)), ("p-b", str(beta))])
        for i, (key, title, cwd, project, stamp, used, window, limit) in enumerate(entries):
            ident = IDS[key]
            path = logs / f"rollout-demo-{ident}.jsonl"
            result["logs"][key] = path
            con.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (ident, title, str(cwd), "cli", 0, int(now.timestamp()) - i * 60, project, str(path), "user", None))
            usage = {"input_tokens": 8000, "cached_input_tokens": 4500, "output_tokens": 420,
                     "reasoning_output_tokens": 200, "total_tokens": 8420}
            request = event("token_usage_record", stamp, thread_id=ident, response_id=f"request-{key}", usage=usage)
            append_events(path, event("session_meta", stamp, id=ident), request, request,
                          measurement(stamp, used, window, limit))
        con.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (IDS["child"], "蓝图 · 子任务", str(alpha), "subagent", 0, int(now.timestamp()),
                     "p-a", None, "subagent", "worker"))
        con.commit()
    normal = result["logs"]["normal"]
    for i in range(11):
        append_events(normal, event("response_item", now, type="custom_tool_call", name="exec", call_id=f"demo-{i}"))
    append_events(normal,
                  event("response_item", now, type="function_call", namespace="mcp__demo", name="find", call_id="demo-mcp"),
                  event("response_item", now, type="function_call_output", call_id="demo-mcp", output="合成结果"),
                  event("response_item", now, type="custom_tool_call_output", call_id="demo-10", output="x" * 69000))
    sessions = [snapshot_row("normal", "primary", (3500, 4500, 0, 420, 200, 8420)),
                snapshot_row("child", "child", (300, 100, 0, 60, 20, 460)),
                snapshot_row("fork", "fork", (80, 40, 0, 30, 12, 150))]
    totals = {key: sum(row[key] for row in sessions) for key in (
        "inputTokens", "cacheReadTokens", "cacheCreationTokens", "outputTokens", "reasoningOutputTokens", "totalTokens")}
    write_json(Path(settings["usage"]), {"schema_version": 1, "unit": "ccusage_incremental_tokens_not_billing",
        "date": local.date().isoformat(), "timezone": "Asia/Shanghai", "finished_utc": (now - timedelta(minutes=100)).isoformat(),
        "totals": totals, "projects": [totals | {"project_id": PROJECT}], "sessions": sessions})
    result.update({"project": alpha, "notes": notes})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="new directory for synthetic data")
    parser.add_argument("--blank", action="store_true")
    parser.add_argument("--launch", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(APP_SOURCE))
    from relay import Relay

    demo = create(args.root, args.blank)
    if args.launch:
        import tkinter as tk
        from app import App

        root = tk.Tk()
        App(root, Relay(demo["settings"]), demo["prefs"])
        root.mainloop()
    else:
        print(demo["root"])


if __name__ == "__main__":
    main()
