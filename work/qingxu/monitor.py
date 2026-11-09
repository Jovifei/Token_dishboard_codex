"""Bounded, read-only monitoring for Qingxu-selected and recently active chats.

No foreground-window inference is performed.  The progress measure is the latest
reported request total (an estimate for context pressure), never cumulative spend.
This module does not invoke Codex, write its configuration, or run a model.
"""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from itertools import islice
import math

from context_usage import FRESH_SECONDS, TAIL_BYTES, chat_metrics


DEFAULT_POLICY = {
    "mode": "recommended", "custom_kind": "percent", "custom_value": 70,
    "enabled": True,
}
MAX_VISIBLE_CHATS = 5
SOURCE_NOTICE = "跟随轻续选择或最近活动；无法识别 Codex 窗口当前点击的聊天。"
MEASUREMENT_LABEL = "最近请求总量参考"


def validate_policy(policy=None):
    """Validate local alert policy without touching any Codex configuration."""
    if policy is not None and not isinstance(policy, dict):
        raise ValueError("提醒设置必须是配置对象。")
    result = DEFAULT_POLICY | (policy or {})
    if result["mode"] not in ("official", "recommended", "custom"):
        raise ValueError("请选择官方参考、轻续推荐或自定义提醒。")
    if type(result["enabled"]) is not bool:
        raise ValueError("提醒启停必须是布尔值。")
    if result["custom_kind"] not in ("percent", "tokens"):
        raise ValueError("自定义提醒单位必须是 percent 或 tokens。")
    value = result["custom_value"]
    maximum = 100 if result["custom_kind"] == "percent" else 1_000_000_000
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("百分比须为 1–100 的整数。" if maximum == 100 else
                         "Token 提醒线须为 1–1,000,000,000 的整数。")
    return result


def _timestamp(value):
    try:
        value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(value, datetime) or value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _now(value):
    if value is None:
        return datetime.now(timezone.utc)
    result = _timestamp(value)
    if result is None:
        raise ValueError("监测时间须包含时区。")
    return result


def _positive(value):
    return value if type(value) is int and value > 0 else None


def _freshness(timestamp, now):
    if timestamp is None:
        return "unknown", None
    age = (now - timestamp).total_seconds()
    if age < 0:
        return "future", age
    return ("fresh" if age <= FRESH_SECONDS else "stale"), age


def assess(sample, compacted=None, *, config_limit=None, official_limit=None,
           policy=None, now=None, error=None):
    """Return one display row, with explicit provenance and honest unknowns.

    ``official_limit`` may only be supplied from an explicitly verified model
    default; this function never derives an official threshold from a window.
    ``config_limit`` is a file reference and does not establish runtime adoption.
    ``progress_percent`` is clamped for a progress widget; ``occupancy_percent``
    preserves an actual value greater than 100.  Neither is a billing metric.
    """
    policy = validate_policy(policy)
    now = _now(now)
    compacted = _timestamp(compacted)
    row = {
        "state": "unknown", "level": "unknown", "message": "等待本次聊天的上下文测量。",
        "measurement_label": MEASUREMENT_LABEL,
        "used": None, "limit": None, "limit_source": "unknown", "basis": "容量来源未知",
        "soft_limit": None, "soft_source": "提醒线未知", "soft_remaining_tokens": None,
        "progress_percent": None, "occupancy_percent": None, "remaining_tokens": None,
        "threshold_percent": None, "threshold_progress_percent": None,
        "window": None, "runtime_limit": None, "file_limit": _positive(config_limit),
        "official_limit": _positive(official_limit), "runtime_matches_file": None,
        "fresh": False, "freshness": "unknown", "age_seconds": None,
        "measured_at": None, "compacted_at": compacted, "policy_mode": policy["mode"],
        "enabled": policy["enabled"], "eligible": False, "notes": [],
        "at_compaction_limit": False,
    }
    if error is not None:
        row.update(state="error", level="unknown", message="读取失败 · " + str(error))
        return row
    measured = _timestamp(sample.get("time")) if isinstance(sample, dict) else None
    if compacted and (measured is None or compacted > measured):
        freshness, age = _freshness(compacted, now)
        row.update(state="compacted", level="info", freshness=freshness, age_seconds=age,
                   message="已记录上下文压缩，等待压缩后的新测量；旧进度不再使用。")
        if freshness == "stale":
            row["message"] += " 压缩记录已过时。"
        elif freshness == "future":
            row["message"] += " 记录时间晚于本机时钟。"
        return row
    if not isinstance(sample, dict) or measured is None:
        return row
    usage = sample.get("usage")
    used = usage.get("totalTokens") if isinstance(usage, dict) else None
    if type(used) is not int or used < 0:
        row["message"] = "未获得可核对的最近请求总量，未显示为零。"
        return row
    freshness, age = _freshness(measured, now)
    window = _positive(sample.get("window"))
    runtime = _positive(sample.get("runtime_limit"))
    configured, official = row["file_limit"], row["official_limit"]
    for label, value in (("运行时压缩线", runtime), ("配置文件参考线", configured),
                         ("官方参考线", official)):
        if value and window and value > window:
            row["notes"].append(label + "超过本次记录的窗口，暂不据此判断。")
            if label == "运行时压缩线":
                runtime = None
            elif label == "配置文件参考线":
                configured = None
            else:
                official = None
    if runtime:
        limit, source, basis = runtime, "runtime", "本会话报告的运行时压缩线"
    elif configured:
        limit, source, basis = configured, "config", "配置文件参考线（本会话采用情况未知）"
    elif official:
        limit, source, basis = official, "official", "已核对的模型官方默认值（本会话采用情况未知）"
    elif window:
        limit, source, basis = window, "window", "本次记录的窗口容量（压缩线未知）"
    else:
        limit, source, basis = None, "unknown", "窗口与压缩线未知"
    mode = policy["mode"]
    threshold, threshold_source = None, "提醒线未知"
    if mode == "official":
        # A user-written file override is not an official/default measurement.
        if runtime:
            threshold, threshold_source = runtime, "跟随本会话报告的压缩线"
        elif official:
            threshold, threshold_source = official, "已核对的模型官方默认压缩线"
        else:
            row["notes"].append("官方参考不可核验；可选择轻续推荐或自定义提醒。")
    elif mode == "recommended":
        if source in ("runtime", "config", "official"):
            threshold = max(1, limit * 80 // 100)
            threshold_source = "轻续推荐：参考压缩线的 80%（仅本工具预警）"
        elif window:
            threshold = max(1, window * 70 // 100)
            threshold_source = "轻续推荐：已记录窗口的 70%（压缩线未知，仅本工具预警）"
    elif policy["custom_kind"] == "tokens":
        threshold = policy["custom_value"]
        threshold_source = "用户自定义 Token 提醒线（仅本工具预警）"
    elif limit:
        threshold = max(1, limit * policy["custom_value"] // 100)
        threshold_source = f"用户自定义：当前参考容量的 {policy['custom_value']}%（仅本工具预警）"

    row.update(used=used, window=window, runtime_limit=runtime,
               runtime_matches_file=(runtime == configured) if runtime and configured else None,
               limit=limit, limit_source=source, basis=basis,
               soft_limit=threshold, soft_source=threshold_source,
               fresh=freshness == "fresh", freshness=freshness, age_seconds=age,
               measured_at=measured,
               at_compaction_limit=bool(runtime and used >= runtime))
    if threshold and limit and threshold > limit:
        row.update(state="invalid", level="unknown", eligible=False,
                   message="自定义提醒线高于当前参考容量，可能来不及提醒；请调低或核对来源。")
    elif threshold:
        row["state"] = "reached" if used >= threshold else (
            "approaching" if used * 100 >= threshold * 90 else "ready")
        row["level"] = {"reached": "alert", "approaching": "warning", "ready": "ready"}[row["state"]]
        row["eligible"] = row["fresh"] and policy["enabled"] and row["state"] in ("approaching", "reached")
    if limit is None and threshold:
        # A useful custom-target bar must explicitly name its different denominator.
        row.update(limit=threshold, limit_source="custom", basis="用户自定义提醒线（实际窗口未知）")
        limit = threshold
    if limit:
        ratio = used / limit * 100
        row.update(occupancy_percent=ratio, progress_percent=max(0.0, min(ratio, 100.0)),
                   remaining_tokens=max(0, limit - used))
    if threshold:
        row.update(soft_remaining_tokens=max(0, threshold - used),
                   threshold_progress_percent=used / threshold * 100,
                   threshold_percent=threshold / limit * 100 if limit else None)

    if row["state"] != "invalid":
        amount = f"{used:,} / {limit:,} tokens" if limit else f"{used:,} tokens"
        message = f"{MEASUREMENT_LABEL} {amount} · {row['basis']}。"
        if freshness == "stale":
            row.update(state="stale", level="history", eligible=False)
            message += " 最后已知测量已过时，等待新请求；不会据此弹出提醒。"
        elif freshness == "future":
            row.update(state="stale", level="history", eligible=False)
            message += " 测量时间晚于本机时钟，暂不触发提醒。"
        elif threshold is None:
            row.update(state="unknown", level="unknown", eligible=False)
            message += " 提醒阈值不可核验。"
        elif row["state"] == "reached":
            message += " 已达到轻续提醒线，建议先保存交接，避免继续追加大任务。"
        elif row["state"] == "approaching":
            message += " 即将达到轻续提醒线，可准备交接，先别追加大任务。"
        else:
            message += " 低于轻续提醒线，可继续当前阶段。"
        if compacted:
            message += " 当前显示压缩后的新测量。"
        row["message"] = message
    if not policy["enabled"]:
        row["message"] += " 提醒已暂停，仍更新数据。"
    row["signature"] = (mode, policy["custom_kind"], policy["custom_value"],
                        row["soft_limit"], row["limit_source"])
    return row


def select_records(records, selected_id=None, limit=4):
    """Choose at most five records, pinning Qingxu's selection before recency."""
    if type(limit) is not int or not 1 <= limit <= MAX_VISIBLE_CHATS:
        raise ValueError("悬浮窗最多显示 1–5 条聊天。")
    unique = {}
    # The existing discovery API already caps a catalog at 500 metadata rows.
    for record in islice(records, 500):
        if isinstance(record, dict) and isinstance(record.get("chat_id"), str):
            unique.setdefault(record["chat_id"], record)

    def recency(record):
        value = record.get("updated_at")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return value
        timestamp = _timestamp(value)
        return timestamp.timestamp() if timestamp else float("-inf")

    ordered = sorted(unique.values(), key=lambda r: (-recency(r), r["chat_id"]))
    selected = unique.get(selected_id)
    if selected:
        ordered = [selected, *(r for r in ordered if r["chat_id"] != selected_id)]
    return ordered[:limit]


class MonitorService:
    """One bounded polling source and per-chat notification episodes.

    Call ``poll`` in a worker, then render results on Tk's main thread.  The
    service is deliberately not internally threaded.  Serialize policy changes
    and polling, or create a new service after changing the data source.
    """
    def __init__(self, database, policy=None, *, metrics_reader=None):
        self.database = database
        self.policy = validate_policy(policy)
        self.reader = metrics_reader or chat_metrics
        self._episodes = {}
        self._snoozed = {}
        self._paused = set()
        self._last_good = OrderedDict()

    def set_policy(self, policy):
        policy = validate_policy(policy)
        if policy != self.policy:
            self._episodes.clear()
        self.policy = policy

    def poll(self, records, selected_id=None, *, config_limit=None, official_limit=None,
             now=None, limit=4):
        now = _now(now)
        policy = dict(self.policy)
        rows = []
        for record in select_records(records, selected_id, limit):
            identity = record["chat_id"]
            try:
                # No totals, body diagnostics, replay glob, or full-history scan.
                result = self.reader(self.database, identity, include_totals=False)
                row = assess(result.get("sample"), result.get("compacted"),
                             config_limit=config_limit, official_limit=official_limit,
                             policy=policy, now=now)
                if row["used"] is not None:
                    self._last_good[identity] = {key: row[key] for key in
                                                 ("used", "measured_at", "limit", "basis")}
                    self._last_good.move_to_end(identity)
                    while len(self._last_good) > 32:
                        self._last_good.popitem(last=False)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                row = assess(None, policy=policy, now=now, error=exc)
                row["last_known"] = self._last_good.get(identity)
            row.update(chat_id=identity, title=record.get("label") or identity,
                       project=record.get("app_project_name") or "未归类项目",
                       path=record.get("path") or "", record=record,
                       selected=identity == selected_id,
                       selection_source="qingxu_selected" if identity == selected_id else "recent_activity",
                       source_notice=SOURCE_NOTICE, paused=identity in self._paused,
                       snoozed_until=self._snoozed.get(identity),
                       max_scan_bytes=TAIL_BYTES)
            rows.append(row)
        return rows

    def snooze(self, chat_id, minutes=10, *, now=None):
        if type(minutes) is not int or not 1 <= minutes <= 1440:
            raise ValueError("稍后提醒时间须为 1–1440 分钟。")
        self._snoozed[chat_id] = _now(now) + timedelta(minutes=minutes)
        self._episodes.pop(chat_id, None)

    def pause(self, chat_id, paused=True):
        if type(paused) is not bool:
            raise ValueError("暂停状态必须是布尔值。")
        if paused:
            self._paused.add(chat_id)
        else:
            self._paused.discard(chat_id)
            self._episodes.pop(chat_id, None)

    def should_notify(self, row, *, now=None):
        """One notification per episode, plus one escalation; never stale data."""
        now = _now(now)
        identity = row.get("chat_id")
        if not identity:
            return False
        if row.get("state") in ("ready", "compacted"):
            self._episodes.pop(identity, None)
        if (not self.policy["enabled"] or identity in self._paused
                or now < self._snoozed.get(identity, now)
                or not row.get("eligible")):
            return False
        # Freshness must also hold at delivery time, not just at polling time.
        if _freshness(_timestamp(row.get("measured_at")), now)[0] != "fresh":
            return False
        signature = row.get("signature")
        if not signature or signature[:3] != tuple(self.policy[key] for key in
                                                   ("mode", "custom_kind", "custom_value")):
            return False
        severity = {"approaching": 1, "reached": 2}.get(row.get("state"), 0)
        if not severity:
            return False
        prior = self._episodes.get(identity)
        if prior and prior[0] == signature and prior[1] >= severity:
            return False
        self._episodes[identity] = (signature, severity)
        return True
