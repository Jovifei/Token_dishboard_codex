"""Read numeric measurements for one Codex chat, without replaying its prompt."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import stat
import tomllib
from uuid import UUID

from chats import metadata_db
from sync import read_file, safe_path
from usage import METRICS, counts, token_text

TAIL_BYTES = 8 * 1024 * 1024
MAX_SCAN_BYTES = 512 * 1024 * 1024
LINE_BYTES = 256 * 1024
FRESH_SECONDS = 15 * 60
DEFAULT_REMINDER_PERCENT = 80
TOOL_WINDOW_MINUTES = 10
FREQUENT_CALLS = 10
LARGE_RESULT_BYTES = 64 * 1024
SOURCE_LABELS = {
    "messages": "对话消息",
    "instructions": "系统 / 开发者消息",
    "tools": "其他工具调用与结果",
    "mcp": "MCP 调用与结果",
    "orphan": "工具结果 · 未关联",
}
CALL_EVENTS = {"function_call", "custom_tool_call", "tool_search_call", "web_search_call"}
OUTPUT_EVENTS = {"function_call_output", "custom_tool_call_output"}


def _time(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _number(value):
    return value if type(value) is int and value > 0 else None


def normalize(raw):
    """Codex's reported total determines whether cache creation is already in input."""
    values = [raw.get(key, 0) for key in ("input_tokens", "cached_input_tokens",
                                         "cache_write_input_tokens", "output_tokens",
                                         "reasoning_output_tokens", "total_tokens")]
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("Codex token 字段无效")
    if not {"input_tokens", "output_tokens", "total_tokens"} <= raw.keys():
        raise ValueError("Codex 请求缺少 token 字段")
    input_tokens, read, created, output, reasoning, total = values
    input_with_cache = total - output
    if input_with_cache not in (input_tokens, input_tokens + created):
        raise ValueError("Codex 请求总量无法对账")
    return counts(dict(zip(METRICS, (input_with_cache - read - created, read, created,
                                     output, reasoning, total))))


def _event(line):
    try:
        item = json.loads(line)
        payload = item["payload"]
        if not isinstance(payload, dict):
            return None, None, {}
        kind = payload.get("type") if item["type"] == "event_msg" else item["type"]
        return kind, _time(item.get("timestamp")), payload
    except (ValueError, KeyError, TypeError):
        return None, None, {}


def _open_log(path, identity):
    stream = safe_path(path).open("rb")
    try:
        kind, _, meta = _event(stream.readline(TAIL_BYTES))
        if kind != "session_meta" or meta.get("id") != identity:
            raise ValueError("日志的聊天身份无法核对")
        return stream
    except Exception:
        stream.close()
        raise


def _log_path(path, roots):
    p = Path(str(path).removeprefix("\\\\?\\"))
    if p.suffix.lower() != ".jsonl" or not any(p.is_relative_to(root) for root in roots):
        raise ValueError("日志不在本机 Codex 会话目录")
    p = safe_path(p)
    status = p.stat()
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise ValueError("日志文件类型不受支持")
    return p


def _tool_identity(payload, event):
    parts = [value for key in ("namespace", "name") if isinstance(value := payload.get(key), str)
             and re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", value)]
    source = "mcp" if any(part.startswith("mcp__") for part in parts) else "tools"
    if parts:
        name = parts[-1] if parts[-1].startswith("mcp__") else ".".join(parts)
    else:
        name = event.removesuffix("_call") if event in ("tool_search_call", "web_search_call") else None
    return source, name


def _visible_sources(lines):
    totals = {key: [0, 0] for key in SOURCE_LABELS}
    calls, call_events, results = {}, [], []
    end = None

    def add(key, size):
        totals[key][0] += 1
        totals[key][1] += size

    for line in lines:
        if b'"response_item"' not in line[:220]:
            continue
        kind, timestamp, payload = _event(line)
        if kind != "response_item":
            continue
        if timestamp and (end is None or timestamp > end):
            end = timestamp
        event = payload.get("type")
        if not isinstance(event, str):
            continue
        role = payload.get("role")
        size = len(line.removesuffix(b"\r"))
        call_id = payload.get("call_id")
        if not isinstance(call_id, str):
            call_id = None
        if event == "message":
            if role in ("user", "assistant"):
                add("messages", size)
            elif role in ("system", "developer"):
                add("instructions", size)
        elif event in CALL_EVENTS:
            tool = _tool_identity(payload, event)
            add(tool[0], size)
            call_events.append((timestamp, call_id, tool))
            if call_id:
                calls[call_id] = tool if calls.get(call_id, tool) == tool else ("orphan", None)
        elif event in OUTPUT_EVENTS or event == "tool_search_output":
            results.append((timestamp, call_id, size, event))

    recent = {"tools": Counter(), "mcp": Counter()}
    seen = set()
    if end is not None:
        start = end - timedelta(minutes=TOOL_WINDOW_MINUTES)
        for timestamp, call_id, tool in call_events:
            source, name = tool
            if not timestamp or timestamp < start or not name:
                continue
            if call_id:
                if call_id in seen or calls[call_id] != tool:
                    continue
                seen.add(call_id)
            recent[source][name] += 1

    largest = None
    for timestamp, call_id, size, event in results:
        source, name = (("tools", "tool_search") if event == "tool_search_output" else
                        calls.get(call_id, ("orphan", None)))
        add(source, size)
        if largest is None or size > largest["bytes"]:
            suffix = call_id[-8:] if call_id and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", call_id) else None
            largest = {"time": timestamp, "tool": name, "source": source, "bytes": size, "call_suffix": suffix}
    return totals, {"window_end": end, "calls": recent, "largest_result": largest}


@lru_cache(maxsize=16)
def _tail(path, identity, _file_version, measure_sources=False):
    with _open_log(path, identity) as stream:
        end = stream.seek(0, 2)
        start = max(0, end - TAIL_BYTES)
        stream.seek(start)
        data = stream.read(TAIL_BYTES)
    lines = data.split(b"\n")[(1 if start else 0):-1]
    sample, compacted, started, window = None, None, None, None
    last_counter = None
    for line in lines:
        if not any(key in line[:320] for key in
                   (b'"token_count"', b'"compacted"', b'"context_compacted"', b'"task_started"')):
            continue
        kind, timestamp, payload = _event(line)
        if not timestamp:
            continue
        if kind == "task_started":
            started, window = timestamp, _number(payload.get("model_context_window"))
        elif kind in ("compacted", "context_compacted"):
            compacted = timestamp
        elif kind == "token_count":
            info = payload.get("info")
            if not isinstance(info, dict) or not isinstance(info.get("last_token_usage"), dict):
                continue
            try:
                usage = normalize(info["last_token_usage"])
            except ValueError:
                continue
            total = info.get("total_token_usage")
            counter = total.get("total_tokens") if isinstance(total, dict) else None
            counter = counter if type(counter) is int and counter >= 0 else None
            if counter is not None and counter == last_counter:
                continue
            # An explicit, reconciled zero is a measurement, not missing data.
            last_counter = counter
            sample = {"time": timestamp, "usage": usage,
                      "window": _number(info.get("model_context_window")) or window,
                      "runtime_limit": _number(info.get("model_auto_compact_token_limit"))}
    if sample and any(t and t > sample["time"] for t in (compacted, started)):
        sample = None
    sources = None
    if measure_sources:
        rows, diagnosis = _visible_sources(lines)
        sources = {"rows": rows, "diagnosis": diagnosis, "scanned_bytes": len(data), "truncated": bool(start)}
    return sample, compacted, sources


def _cursor_hash(stream, offset):
    size = min(offset, 4096)
    stream.seek(offset - size)
    return hashlib.sha256(stream.read(size)).digest()


def _request_rows(path, identity, stat_result, previous):
    version = (stat_result.st_ino, stat_result.st_size, stat_result.st_mtime_ns)
    if previous and previous[0] == version:
        return previous
    inode, size, _ = version
    with _open_log(path, identity) as stream:
        rows, unavailable = [], 0
        offset = stream.tell()
        # Codex appends complete records. A changed seam or a rewritten file is rescanned.
        if previous and previous[0][0] == inode and previous[0][1] < size:
            _, old_offset, anchor, old_rows, old_unavailable = previous
            if old_offset >= offset and _cursor_hash(stream, old_offset) == anchor:
                offset, rows, unavailable = old_offset, list(old_rows), old_unavailable
        stream.seek(offset)
        while offset < size:
            line = stream.readline(min(LINE_BYTES, size - offset))
            if not line:
                break
            measurement = b'"token_usage_record"' in line[:220]
            if not line.endswith(b"\n"):
                while line and not line.endswith(b"\n") and stream.tell() < size:
                    line = stream.readline(min(LINE_BYTES, size - stream.tell()))
                if not line.endswith(b"\n"):
                    break
                unavailable += int(measurement)
                offset = stream.tell()
                continue
            offset = stream.tell()
            if not measurement:
                continue
            kind, _, payload = _event(line)
            key = payload.get("response_id")
            if (kind != "token_usage_record" or payload.get("thread_id") != identity
                    or not isinstance(key, str) or not key):
                unavailable += 1
                continue
            try:
                rows.append((key, normalize(payload["usage"])))
            except (AttributeError, ValueError, KeyError, TypeError):
                unavailable += 1
        return version, offset, _cursor_hash(stream, offset), rows, unavailable


def _requests(paths, roots, identity, cache):
    totals = dict.fromkeys(METRICS, 0)
    seen = {}
    files, unavailable, repeats, budget = 0, 0, 0, MAX_SCAN_BYTES
    active_paths = set()
    for path in paths:
        try:
            path = _log_path(path, roots)
            status = path.stat()
            if status.st_size > budget:
                unavailable += 1
                continue
            budget -= status.st_size
            cache_key = (path, identity)
            entry = _request_rows(path, identity, status, cache.get(cache_key))
        except (OSError, ValueError):
            unavailable += 1
            continue
        cache[cache_key] = entry
        active_paths.add(cache_key)
        files += 1
        rows, missed = entry[3:]
        unavailable += missed
        for key, usage in rows:
            if key in seen:
                if seen[key] != usage:
                    raise ValueError("同一请求的用量记录发生冲突")
                repeats += 1
                continue
            seen[key] = usage
            for field in METRICS:
                totals[field] += usage[field]
    for key in cache.keys() - active_paths:
        del cache[key]
    return {"totals": totals if seen else None, "requests": len(seen),
            "measured_at": datetime.now(timezone.utc),
            "files": files, "unavailable": unavailable, "duplicates": repeats}


def chat_metrics(database, identity, *, include_totals=False, request_cache=None):
    try:
        if str(UUID(identity)) != identity:
            raise ValueError
    except (TypeError, AttributeError, ValueError) as exc:
        raise ValueError("没有可读的本机聊天日志 ID") from exc
    with metadata_db(database) as (conn, path):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
        if "rollout_path" not in cols:
            raise ValueError("当前 Codex 元数据没有日志地址")
        row = conn.execute("SELECT rollout_path FROM threads WHERE id=? AND archived=0", (identity,)).fetchone()
    if not row or not row[0]:
        raise ValueError("当前聊天没有可定位的本机日志")
    roots = path.parent / "sessions", path.parent / "archived_sessions"
    log = _log_path(row[0], roots)
    current = log.stat()
    sample, compacted, sources = _tail(log, identity, (current.st_ino, current.st_size, current.st_mtime_ns),
                                       measure_sources=include_totals)
    result = {"sample": sample, "compacted": compacted}
    if include_totals:
        result["sources"] = sources
        pattern = f"rollout-*{identity}*.jsonl"
        paths = {log, *roots[0].glob("*/*/*/" + pattern), *roots[1].glob(pattern)}
        try:
            cache = request_cache if request_cache is not None else {}
            result["requests"] = _requests([log, *sorted(paths - {log}, reverse=True)], roots, identity, cache)
        except ValueError as exc:
            result["requests_error"] = str(exc)
    return result


def file_limit(path):
    try:
        config = tomllib.loads(read_file(path, 500_000).decode("utf-8-sig"))
        return _number(config.get("model_auto_compact_token_limit"))
    except (OSError, ValueError):
        return None


def reminder(sample, compacted, reference, now=None, *, percent=DEFAULT_REMINDER_PERCENT):
    now = now or datetime.now(timezone.utc)

    def fresh(timestamp):
        return timestamp and 0 <= (now - timestamp).total_seconds() <= FRESH_SECONDS

    compaction = (f"{compacted.astimezone():%H:%M:%S} 记录到上下文压缩。" if fresh(compacted) else "")
    if not sample:
        if compaction:
            return "alert", compaction + "等待压缩后的新测量。可在当前工作段结束后保存交接。"
        return "unknown", "等待当前聊天的上下文测量。"
    runtime_limit, window = sample["runtime_limit"], sample["window"]
    if runtime_limit:
        limit, source = runtime_limit, "运行时压缩线"
    elif reference and (not window or reference <= window):
        limit, source = reference, "配置文件参考线（本聊天采用情况未知）"
    else:
        limit, source = window, "已记录窗口容量（运行时压缩线未知）"
    used = sample["usage"]["totalTokens"]
    text = (f"最近请求 {token_text(used)} / {token_text(limit)} tokens · {source}"
            f" · {used / limit:.1%}，提醒线 {percent}%" if limit else
            f"最近请求 {token_text(used)} tokens · 压缩线和窗口容量未知")
    if compaction:
        return "alert", compaction + text + "。可在当前工作段结束后保存交接。"
    if not fresh(sample["time"]):
        return "history", text + "。历史测量，等待新请求。"
    if not limit:
        return "unknown", text + "。"
    if used * 100 >= limit * percent:
        return "alert", text + "。可先保存交接，合适时换聊。"
    return "ready", text + "。低于提醒线。"
