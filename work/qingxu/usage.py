"""Validate the existing ccusage snapshot, not cumulative thread counters."""
from datetime import datetime, timezone

METRICS = ("inputTokens", "cacheReadTokens", "cacheCreationTokens",
           "outputTokens", "reasoningOutputTokens", "totalTokens")
COMPONENTS = (("非缓存输入", "inputTokens"), ("缓存读取", "cacheReadTokens"),
              ("缓存创建", "cacheCreationTokens"), ("输出", "outputTokens"))


def token_text(value):
    return f"{value:,}"


def counts(value):
    result = {}
    for key in METRICS:
        v = value.get(key)
        if type(v) is not int or v < 0:
            raise ValueError("统计包含缺失/无效 token 字段：" + key)
        result[key] = v
    if (sum(result[k] for _, k in COMPONENTS) != result["totalTokens"]
            or result["reasoningOutputTokens"] > result["outputTokens"]):
        raise ValueError("统计不守恒或 reasoning 超出 output；未显示为有效用量")
    return result


def session_identity(row):
    key = row.get("thread_id") or row.get("session_id")
    return key if isinstance(key, str) and key else None


def group_sessions(sessions, metadata):
    projects = {}

    def add(bucket, session):
        for key in METRICS:
            bucket[key] += session[key]
        bucket["records"] += 1

    for session in sessions:
        identity = session_identity(session)
        record = metadata.get(identity, {})
        pid = record.get("project_id")
        project = projects.setdefault(pid, dict.fromkeys(METRICS, 0) | {
            "title": record.get("project_name", "未归类"), "chats": {}, "records": 0,
            "unclassified": {}})
        chat_id = record.get("chat_id", identity)
        title = record.get("title", "标题未读到" if identity else "无聊天 ID")
        chat = project["chats"].setdefault(chat_id, dict.fromkeys(METRICS, 0) | {
            "title": title, "id": chat_id, "records": 0, "child_records": 0,
            "unclassified": {}})
        if session.get("role") == "child":
            chat["child_records"] += 1
        add(chat, session)
        add(project, session)
        if pid is None:
            reason = record.get("mapping") or ("NO_DB_MATCH" if identity else "NO_ID")
            for row in (project, chat):
                reasons = row["unclassified"]
                reasons[reason] = reasons.get(reason, 0) + session["totalTokens"]
    return projects


def summarize(data):
    if (data.get("unit") != "ccusage_incremental_tokens_not_billing"
            or data.get("schema_version") != 1):
        raise ValueError("不是受支持的 ccusage 增量快照；不把累计计数当成花费")
    totals = counts(data["totals"])
    projects, sessions = data["projects"], data["sessions"]
    for rows in (projects, sessions):
        verified = [counts(row) for row in rows]
        if any(sum(r[k] for r in verified) != totals[k] for k in METRICS):
            raise ValueError("项目/会话范围与汇总不一致；请检查现有采集回执")
    roles = {}
    for session in sessions:
        role = session.get("role", "unknown")
        bucket = roles.setdefault(role, dict.fromkeys(METRICS, 0) | {"records": 0})
        for key in METRICS:
            bucket[key] += session[key]
        bucket["records"] += 1
    age = None
    try:
        captured = datetime.fromisoformat(data["finished_utc"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - captured).total_seconds()
    except (KeyError, ValueError, TypeError):
        pass
    return {"total": totals["totalTokens"], "totals": totals,
            "components": [(label, totals[key]) for label, key in COMPONENTS],
            "unmapped": sum(p["totalTokens"] for p in projects if p["project_id"] == "UNMAPPED"),
            "records": len(sessions), "roles": roles, "age_seconds": age,
            "date": data.get("date", "未知"), "captured": data.get("finished_utc", "未知"),
            "timezone": data.get("timezone", "未知"), "projects": projects, "sessions": sessions}
