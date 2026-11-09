"""Monitoring acceptance tests: provenance, bounded work, and notification episodes."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from context_usage import chat_metrics, normalize
from monitor import assess, MonitorService, select_records, validate_policy


NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
IDENTITY = "a1111111-1111-4111-8111-111111111111"


def sample(used=80, window=200, runtime=100, when=NOW):
    raw = {"input_tokens": used, "output_tokens": 0, "total_tokens": used}
    return {"time": when, "usage": normalize(raw), "window": window, "runtime_limit": runtime}


def alert_row(chat="one", used=80, when=NOW):
    return assess(sample(used, when=when), now=when) | {"chat_id": chat}


class ThresholdTests(unittest.TestCase):
    def test_runtime_file_and_recommendation_are_distinct(self):
        value = assess(sample(80), config_limit=90, now=NOW)
        self.assertEqual((value["limit"], value["soft_limit"], value["limit_source"]), (100, 80, "runtime"))
        self.assertEqual(value["file_limit"], 90)
        self.assertFalse(value["runtime_matches_file"])
        self.assertEqual(value["state"], "reached")
        self.assertIn("大任务", value["message"])
        fallback = assess(sample(70, runtime=None), config_limit=100, now=NOW)
        self.assertEqual((fallback["limit_source"], fallback["soft_limit"]), ("config", 80))
        self.assertIn("采用情况未知", fallback["basis"])

    def test_official_does_not_treat_file_override_as_official(self):
        value = assess(sample(runtime=None), config_limit=100, policy={"mode": "official"}, now=NOW)
        self.assertIsNone(value["soft_limit"])
        self.assertEqual(value["state"], "unknown")
        self.assertFalse(value["eligible"])
        self.assertEqual(value["limit_source"], "config")
        verified = assess(sample(runtime=None), official_limit=160,
                          policy={"mode": "official"}, now=NOW)
        self.assertEqual(verified["soft_limit"], 160)
        self.assertIn("采用情况未知", verified["basis"])

    def test_window_fallback_is_a_local_rule_not_an_official_threshold(self):
        value = assess(sample(140, runtime=None), now=NOW)
        self.assertEqual(value["soft_limit"], 140)
        self.assertEqual(value["limit_source"], "window")
        self.assertIsNone(value["official_limit"])
        self.assertIn("70%", value["soft_source"])
        self.assertIn("压缩线未知", value["basis"])

    def test_unknown_is_not_zero_and_explicit_zero_is_ready(self):
        unknown = assess(None, now=NOW)
        zero = assess(sample(0), now=NOW)
        self.assertIsNone(unknown["used"])
        self.assertIsNone(unknown["progress_percent"])
        self.assertEqual((zero["state"], zero["used"], zero["progress_percent"]), ("ready", 0, 0.0))

    def test_stale_and_future_samples_never_notify(self):
        for when in (NOW - timedelta(minutes=16), NOW + timedelta(seconds=1)):
            with self.subTest(when=when):
                value = assess(sample(100, when=when), now=NOW)
                self.assertEqual(value["state"], "stale")
                self.assertFalse(value["eligible"])
                self.assertEqual(value["used"], 100)

    def test_compaction_clears_old_progress_then_accepts_new_measurement(self):
        compacted = NOW + timedelta(seconds=1)
        before = assess(sample(), compacted, now=compacted)
        self.assertEqual(before["state"], "compacted")
        self.assertIsNone(before["used"])
        self.assertFalse(before["eligible"])
        after = assess(sample(20, when=compacted + timedelta(seconds=1)), compacted,
                       now=compacted + timedelta(seconds=2))
        self.assertEqual((after["state"], after["used"]), ("ready", 20))
        self.assertIn("压缩后的新测量", after["message"])

    def test_custom_modes_and_invalid_policy(self):
        percent = {"mode": "custom", "custom_kind": "percent", "custom_value": 90}
        self.assertEqual(assess(sample(), policy=percent, now=NOW)["soft_limit"], 90)
        tokens = {"mode": "custom", "custom_kind": "tokens", "custom_value": 60}
        self.assertEqual(assess(sample(), policy=tokens, now=NOW)["soft_limit"], 60)
        no_window = assess(sample(50, window=None, runtime=None), policy=tokens, now=NOW)
        self.assertEqual(no_window["limit_source"], "custom")
        self.assertIn("实际窗口未知", no_window["basis"])
        too_high = assess(sample(), policy=tokens | {"custom_value": 300}, now=NOW)
        self.assertEqual(too_high["state"], "invalid")
        self.assertFalse(too_high["eligible"])
        for change in ({"mode": "invented"}, {"enabled": 1}, {"custom_value": True},
                       {"custom_value": 0}, {"custom_value": 101}, {"custom_kind": "bytes"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_policy(change)

    def test_inconsistent_limit_does_not_produce_false_official_claim(self):
        value = assess(sample(150, window=200, runtime=300), config_limit=400, now=NOW)
        self.assertEqual((value["limit_source"], value["soft_limit"]), ("window", 140))
        self.assertIsNone(value["runtime_limit"])
        self.assertEqual(len(value["notes"]), 2)

    def test_warning_and_exceeded_values_have_different_semantics(self):
        warning = assess(sample(72), now=NOW)
        exceeded = assess(sample(120), now=NOW)
        self.assertEqual(warning["state"], "approaching")
        self.assertEqual(exceeded["progress_percent"], 100.0)
        self.assertEqual(exceeded["occupancy_percent"], 120.0)
        self.assertTrue(exceeded["at_compaction_limit"])


class PollingTests(unittest.TestCase):
    def test_bound_recent_records_pin_selection_and_never_read_totals(self):
        records = [{"chat_id": str(i), "updated_at": i, "label": "会话" + str(i)} for i in range(20)]
        calls = []
        def reader(database, identity, **options):
            calls.append((database, identity, options))
            return {"sample": sample(), "compacted": None}
        monitor = MonitorService("example.sqlite", metrics_reader=reader)
        rows = monitor.poll(records, "0", now=NOW, limit=4)
        self.assertEqual([r["chat_id"] for r in rows], ["0", "19", "18", "17"])
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(c[2] == {"include_totals": False} for c in calls))
        self.assertEqual(rows[0]["selection_source"], "qingxu_selected")
        self.assertTrue(all(r["selection_source"] == "recent_activity" for r in rows[1:]))
        self.assertIn("无法识别", rows[0]["source_notice"])
        self.assertEqual(len(select_records(records, limit=5)), 5)
        with self.assertRaises(ValueError):
            select_records(records, limit=6)

    def test_error_preserves_explicit_last_known_without_reusing_progress(self):
        results = iter([{"sample": sample(), "compacted": None}, OSError("目录离线")])
        def reader(*args, **kwargs):
            item = next(results)
            if isinstance(item, Exception):
                raise item
            return item
        monitor = MonitorService("unused", metrics_reader=reader)
        records = [{"chat_id": "a", "updated_at": 1}]
        monitor.poll(records, now=NOW)
        row = monitor.poll(records, now=NOW)[0]
        self.assertEqual(row["state"], "error")
        self.assertIsNone(row["progress_percent"])
        self.assertEqual(row["last_known"]["used"], 80)
        self.assertIn("目录离线", row["message"])

    def test_alerts_are_per_chat_deduplicated_and_can_escalate(self):
        monitor = MonitorService("unused")
        warning, reached = alert_row(used=72), alert_row(used=80)
        self.assertTrue(monitor.should_notify(warning, now=NOW))
        self.assertFalse(monitor.should_notify(warning, now=NOW))
        self.assertTrue(monitor.should_notify(reached, now=NOW))
        self.assertFalse(monitor.should_notify(reached, now=NOW))
        self.assertTrue(monitor.should_notify(alert_row("other"), now=NOW))
        self.assertFalse(monitor.should_notify(alert_row(used=10), now=NOW))
        self.assertTrue(monitor.should_notify(reached, now=NOW))

    def test_snooze_and_pause_never_leak_to_another_chat(self):
        monitor = MonitorService("unused")
        monitor.snooze("one", 10, now=NOW)
        self.assertFalse(monitor.should_notify(alert_row(), now=NOW))
        self.assertTrue(monitor.should_notify(alert_row("two"), now=NOW))
        later = NOW + timedelta(minutes=11)
        self.assertTrue(monitor.should_notify(alert_row(when=later), now=later))
        monitor.pause("one")
        self.assertFalse(monitor.should_notify(alert_row(when=later), now=later))
        monitor.pause("one", False)
        self.assertTrue(monitor.should_notify(alert_row(when=later), now=later))

    def test_delayed_delivery_and_disabled_policy_never_notify(self):
        monitor = MonitorService("unused")
        self.assertFalse(monitor.should_notify(alert_row(), now=NOW + timedelta(minutes=16)))
        monitor.set_policy({"enabled": False})
        self.assertFalse(monitor.should_notify(alert_row(), now=NOW))

    def test_completed_worker_result_cannot_alert_after_policy_changed(self):
        monitor = MonitorService("unused")
        previous = alert_row()
        monitor.set_policy({"mode": "official"})
        self.assertFalse(monitor.should_notify(previous, now=NOW))


class RealLogZeroTest(unittest.TestCase):
    def test_zero_is_read_from_local_log_and_duplicate_does_not_refresh_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logdir = root / "sessions" / "2026" / "11" / "09"
            logdir.mkdir(parents=True)
            log = logdir / "rollout-zero.jsonl"
            database = root / "state.sqlite"
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("CREATE TABLE threads(id TEXT, rollout_path TEXT, archived INTEGER)")
                conn.execute("INSERT INTO threads VALUES(?,?,0)", (IDENTITY, str(log)))
                conn.commit()
            info = {"model_context_window": 200, "last_token_usage": {
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "total_token_usage": {"total_tokens": 0}}
            lines = [
                {"type": "session_meta", "timestamp": NOW.isoformat(), "payload": {"id": IDENTITY}},
                {"type": "event_msg", "timestamp": NOW.isoformat(), "payload": {"type": "token_count", "info": info}},
                {"type": "event_msg", "timestamp": (NOW + timedelta(hours=1)).isoformat(),
                 "payload": {"type": "token_count", "info": info}},
            ]
            log.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
            result = chat_metrics(database, IDENTITY)
            self.assertEqual(result["sample"]["usage"]["totalTokens"], 0)
            self.assertEqual(result["sample"]["time"], NOW)
            self.assertEqual(assess(result["sample"], now=NOW)["state"], "ready")


if __name__ == "__main__":
    unittest.main()
