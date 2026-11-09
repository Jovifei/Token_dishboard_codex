"""Attribute existing ccusage deltas; never recompute spend from thread counters."""
import argparse
import collections
import csv
import json
import ntpath
from pathlib import Path
import re
import sqlite3


METRICS = (
    "inputTokens", "cacheReadTokens", "cacheCreationTokens", "outputTokens",
    "reasoningOutputTokens", "totalTokens",
)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def canonical(path):
    value = str(path or "").replace("/", "\\")
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normpath(value).casefold() if value else ""


def match_project(path, roots):
    value = canonical(path)
    hits = [(len(root), pid) for root, pid in roots.items()
            if value == root or value.startswith(root.rstrip("\\") + "\\")]
    return max(hits)[1] if hits else None


def read_metadata(db):
    connection = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        threads = {row["id"]: dict(row) for row in connection.execute(
            "SELECT id,cwd,source,thread_source FROM threads"
        )}
        edges = dict(connection.execute(
            "SELECT child_thread_id,parent_thread_id FROM thread_spawn_edges"
        ))
    finally:
        connection.close()
    for tid, row in threads.items():
        try:
            spawn = json.loads(row["source"]).get("subagent", {}).get("thread_spawn", {})
            if spawn.get("parent_thread_id"):
                edges.setdefault(tid, spawn["parent_thread_id"])
        except (ValueError, AttributeError, TypeError):
            pass
    return threads, edges


def category(row, tid, edges):
    source = row.get("thread_source")
    if tid in edges or source == "subagent":
        return "child"
    if source == "guardian_review":
        return "guardian"
    if source == "agent_forked_thread":
        return "fork"
    if source in ("user", "agent_created_thread"):
        return "primary"
    return "unknown"


def aggregate(raw, registry, threads, edges, aliases=None):
    projects = {p["project_id"]: p for p in registry["projects"]}
    roots = {canonical(p["path"]): p["project_id"] for p in projects.values()}
    roots.update({canonical(path): pid for path, pid in (aliases or {}).items()
                  if pid in projects})
    resolved = {}

    def resolve(tid, visited=None):
        if tid in resolved:
            return resolved[tid]
        visited = set() if visited is None else visited
        if tid in visited:
            return None, "parent_cycle"
        visited.add(tid)
        row = threads.get(tid, {})
        direct = match_project(row.get("cwd"), roots)
        if direct:
            result = direct, "cwd_or_verified_worktree"
        elif tid in edges:
            parent, _ = resolve(edges[tid], visited)
            result = parent, "parent_edge" if parent else "unmapped"
        else:
            result = None, "unmapped"
        resolved[tid] = result
        return result

    buckets = {}
    for pid, project in {**projects, "UNMAPPED": {
        "project_id": "UNMAPPED", "label": "Unmapped local sessions",
        "path": "", "exists": None,
    }}.items():
        buckets[pid] = {
            "project_id": pid, "label": project["label"], "path": project["path"],
            "exists": project.get("exists"), "records": 0,
            **{k: 0 for k in METRICS}, "by_role": {},
        }
    mappings = []
    for row in raw["sessions"]:
        ids = UUID.findall(row["sessionId"])
        # Resumed filenames carry a second UUID; the first is the logical thread ID.
        tid = ids[0] if ids else ""
        meta = threads.get(tid, {})
        pid, method = resolve(tid) if meta else (None, "metadata_missing")
        role = category(meta, tid, edges)
        bucket = buckets[pid or "UNMAPPED"]
        bucket["records"] += 1
        role_bucket = bucket["by_role"].setdefault(role, {
            "records": 0, **{k: 0 for k in METRICS}
        })
        role_bucket["records"] += 1
        for key in METRICS:
            value = row[key]
            if type(value) is not int or value < 0:
                raise ValueError("Invalid ccusage token metric")
            bucket[key] += value
            role_bucket[key] += value
        mappings.append({
            "session_id": row["sessionId"], "thread_id": tid,
            "project_id": pid or "UNMAPPED", "role": role, "method": method,
            **{k: row[k] for k in METRICS},
        })
    totals = {key: sum(x[key] for x in buckets.values()) for key in METRICS}
    if any(totals[key] != raw["totals"][key] for key in METRICS):
        raise ValueError("Attribution does not conserve ccusage totals")
    if totals["totalTokens"] != sum(totals[key] for key in (
        "inputTokens", "cacheReadTokens", "cacheCreationTokens", "outputTokens"
    )):
        raise ValueError("Unexpected ccusage subset/addition semantics")
    return {
        "schema_version": 1, "unit": "ccusage_incremental_tokens_not_billing",
        "input_semantics": "inputTokens already excludes cacheRead/cacheCreation",
        "reasoning_semantics": "reasoningOutputTokens is a subset of outputTokens",
        "totals_conserved": True, "totals": totals,
        "projects": list(buckets.values()), "sessions": mappings,
        "method_counts": dict(collections.Counter(x["method"] for x in mappings)),
        "role_counts": dict(collections.Counter(x["role"] for x in mappings)),
    }


def write_report(report, prefix):
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = ["project_id", "label", "path", "exists", "records", *METRICS]
    fields += [role + "_totalTokens" for role in
               ("primary", "child", "guardian", "fork", "unknown")]
    with prefix.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for bucket in report["projects"]:
            row = {key: bucket[key] for key in fields if key in bucket}
            for role in ("primary", "child", "guardian", "fork", "unknown"):
                row[role + "_totalTokens"] = bucket["by_role"].get(role, {}).get("totalTokens", 0)
            writer.writerow(row)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("raw", "registry", "state-db", "output-prefix"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--aliases")
    args = parser.parse_args()
    load = lambda path: json.loads(Path(path).read_text(encoding="utf-8-sig"))
    metadata, parents = read_metadata(args.state_db)
    result = aggregate(load(args.raw), load(args.registry), metadata, parents,
                       load(args.aliases) if args.aliases else {})
    write_report(result, args.output_prefix)
    print(json.dumps({
        "records": len(result["sessions"]), "totals_conserved": True,
        "method_counts": result["method_counts"], "role_counts": result["role_counts"],
        "unmapped_tokens": result["projects"][-1]["totalTokens"],
    }))
