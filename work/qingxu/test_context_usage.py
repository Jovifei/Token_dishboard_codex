from contextlib import closing
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from context_usage import chat_metrics, normalize, reminder


IDENTITY = "a1111111-1111-4111-8111-111111111111"
OTHER = "b2222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
USAGE = {"input_tokens": 70, "cached_input_tokens": 40, "cache_write_input_tokens": 10,
         "output_tokens": 10, "reasoning_output_tokens": 5, "total_tokens": 80}


def event(kind, body, when=NOW):
    return {"type": kind, "timestamp": when.isoformat(), "payload": body}


def request(response, identity=IDENTITY):
    return event("token_usage_record", {"thread_id": identity, "response_id": response, "usage": USAGE,
                                        "thread_token_usage": USAGE | {"total_tokens": 200000}})


def sample():
    return event("event_msg", {"type": "token_count", "info": {
        "model_context_window": 200, "last_token_usage": USAGE}})


class MeasurementsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.database = self.root / "state.sqlite"
        self.folder = self.root / "sessions" / "2026" / "11" / "08"
        self.folder.mkdir(parents=True)
        self.log = self.folder / f"rollout-main-{IDENTITY}.jsonl"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TABLE threads(id TEXT, rollout_path TEXT, archived INTEGER)")
            db.execute("INSERT INTO threads VALUES (?,?,0)", (IDENTITY, str(self.log)))
            db.commit()

    @staticmethod
    def write_log(path, events, identity=IDENTITY):
        lines = [event("session_meta", {"id": identity}), *events]
        path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")

    def test_read_one_chat_own_requests_and_replay(self):
        self.write_log(self.log, [request("same"), request("second"), sample()])
        self.write_log(self.folder / f"rollout-old-{IDENTITY}.jsonl", [request("same")])
        self.write_log(self.folder / f"rollout-other-{OTHER}.jsonl", [request("other", OTHER)], OTHER)
        result = chat_metrics(self.database, IDENTITY, include_totals=True)
        self.assertEqual(result["sample"]["window"], 200)
        self.assertEqual(result["sample"]["usage"]["totalTokens"], 80)
        self.assertEqual(result["requests"]["totals"]["totalTokens"], 160)
        self.assertEqual(result["requests"]["totals"]["inputTokens"], 40)
        self.assertEqual(result["requests"]["totals"]["reasoningOutputTokens"], 10)
        self.assertEqual(result["requests"]["duplicates"], 1)
        self.assertEqual(result["requests"]["files"], 2)

    def test_newer_compaction_invalidates_last_request(self):
        self.write_log(self.log, [sample(), event("compacted", {}, NOW + timedelta(seconds=2))])
        result = chat_metrics(self.database, IDENTITY)
        self.assertIsNone(result["sample"])
        self.assertEqual(reminder(None, result["compacted"], 100, NOW + timedelta(seconds=3))[0], "alert")
        self.assertEqual(reminder(None, result["compacted"], 100, NOW + timedelta(hours=2))[0], "unknown")
        after = {"time": NOW + timedelta(seconds=3), "usage": normalize(USAGE), "window": 200, "runtime_limit": 100}
        level, detail = reminder(after, result["compacted"], None, NOW + timedelta(seconds=4), percent=90)
        self.assertEqual(level, "alert")
        self.assertIn("80.0%，提醒线 90%", detail)

    def test_new_turn_waits_for_own_measurement(self):
        self.write_log(self.log, [sample(), event("event_msg", {"type": "task_started",
                        "model_context_window": 400}, NOW + timedelta(seconds=1))])
        self.assertIsNone(chat_metrics(self.database, IDENTITY)["sample"])

    def test_duplicate_counter_does_not_freshen_an_old_sample(self):
        initial = sample()
        initial["payload"]["info"]["total_token_usage"] = {"total_tokens": 300}
        self.write_log(self.log, [initial, initial | {"timestamp": (NOW + timedelta(hours=2)).isoformat()}])
        self.assertEqual(chat_metrics(self.database, IDENTITY)["sample"]["time"], NOW)

    def test_incremental_requests_wait_for_complete_appended_record(self):
        self.write_log(self.log, [request("one"), sample()])
        cache = {}
        def measure():
            return chat_metrics(self.database, IDENTITY, include_totals=True, request_cache=cache)
        self.assertEqual(measure()["requests"]["totals"]["totalTokens"], 80)
        record = json.dumps(request("two")).encode("utf-8") + b"\n"
        with self.log.open("ab") as stream:
            stream.write(record[:len(record) // 2])
        self.assertEqual(measure()["requests"]["requests"], 1)
        with self.log.open("ab") as stream:
            stream.write(record[len(record) // 2:])
            stream.write(record)
        self.assertEqual(measure()["requests"]["totals"]["totalTokens"], 160)
        self.assertEqual(measure()["requests"]["duplicates"], 1)
        self.write_log(self.log, [request("replacement")])
        self.assertEqual(measure()["requests"]["requests"], 1)

    def test_visible_sources_pair_mcp_outputs_and_keep_orphans(self):
        records = [
            event("response_item", {"type": "message", "role": "user", "content": [{"text": "示例"}]}),
            event("response_item", {"type": "message", "role": "developer"}),
            event("response_item", {"type": "function_call_output", "call_id": "m"}),
            event("response_item", {"type": "function_call", "call_id": "m", "namespace": "mcp__example"}),
            event("response_item", {"type": "function_call", "call_id": "s", "name": "exec_command"}),
            event("response_item", {"type": "function_call_output", "call_id": "s"}),
            event("response_item", {"type": "function_call_output", "call_id": "unseen"}),
            event("response_item", {"type": ["malformed"]}),
            event("event_msg", {"type": "item_completed", "call_id": "m"}),
        ]
        self.write_log(self.log, records)
        quick = chat_metrics(self.database, IDENTITY)
        self.assertNotIn("sources", quick)
        source = chat_metrics(self.database, IDENTITY, include_totals=True)["sources"]
        self.assertEqual({key: n for key, (n, _) in source["rows"].items()},
                         {"messages": 1, "instructions": 1, "mcp": 2, "tools": 2, "orphan": 1})
        self.assertEqual(source["rows"]["mcp"][1],
                         sum(len(json.dumps(record).encode("utf-8")) for record in records[2:4]))
        self.assertFalse(source["truncated"])

    def test_diagnosis_deduplicates_recent_calls_and_keeps_older_large_result(self):
        def record(kind, when=NOW, **body):
            return event("response_item", {"type": kind, **body}, when)

        old = NOW - timedelta(minutes=30)
        largest = record("custom_tool_call_output", old, call_id="call_bigresult", output="x" * 70000)
        exec_call = record("custom_tool_call", call_id="recent", name="exec")
        self.write_log(self.log, [
            record("custom_tool_call", old, call_id="call_bigresult", name="exec"), largest,
            exec_call, exec_call,
            record("function_call", call_id="m1", namespace="mcp__catalog", name="find"),
            record("function_call_output", call_id="m1", output="small"),
        ])
        diagnosis = chat_metrics(self.database, IDENTITY, include_totals=True)["sources"]["diagnosis"]
        self.assertEqual(diagnosis["window_end"], NOW)
        self.assertEqual(diagnosis["calls"], {"tools": {"exec": 1}, "mcp": {"mcp__catalog.find": 1}})
        self.assertEqual(diagnosis["largest_result"], {"time": old, "tool": "exec", "source": "tools",
                         "bytes": len(json.dumps(largest).encode("utf-8")), "call_suffix": "igresult"})

    def test_wrong_log_location_and_identity_are_rejected(self):
        self.write_log(self.log, [sample()], identity=OTHER)
        with self.assertRaisesRegex(ValueError, "身份"):
            chat_metrics(self.database, IDENTITY)
        other = self.root / "elsewhere.jsonl"
        self.write_log(other, [sample()])
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE threads SET rollout_path=?", (str(other),))
            db.commit()
        with self.assertRaisesRegex(ValueError, "会话目录"):
            chat_metrics(self.database, IDENTITY)

    def test_reminder_sources_and_staleness(self):
        self.write_log(self.log, [sample()])
        last = chat_metrics(self.database, IDENTITY)["sample"]
        self.assertEqual(reminder(last, None, 100, NOW)[0], "alert")
        self.assertIn("配置文件参考线", reminder(last, None, 100, NOW)[1])
        level, detail = reminder(last, None, 100, NOW, percent=81)
        self.assertEqual(level, "ready")
        self.assertIn("80.0%，提醒线 81%", detail)
        self.assertEqual(reminder(last, None, None, NOW)[0], "ready")
        self.assertEqual(reminder(last, None, 100, NOW + timedelta(minutes=16))[0], "history")
        self.assertIn("运行时压缩线", reminder(last | {"runtime_limit": 96}, None, 70, NOW)[1])

    def test_cache_and_reasoning_are_not_counted_twice(self):
        self.assertEqual(normalize(USAGE)["inputTokens"], 20)
        self.assertEqual(normalize(USAGE | {"input_tokens": 60})["inputTokens"], 20)
        with self.assertRaises(ValueError):
            normalize(USAGE | {"cached_input_tokens": 90})


if __name__ == "__main__":
    unittest.main()
