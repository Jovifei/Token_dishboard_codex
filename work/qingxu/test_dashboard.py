import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

import core
from chats import discover, path_key, usage_metadata
from config_store import model_defaults
from usage import group_sessions, summarize, token_text


class ChatTests(unittest.TestCase):
    def test_sidebar_hides_stale_project_and_resolves_project_threads(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.sqlite"
            with closing(sqlite3.connect(db)) as c, c:
                c.executescript(
                    "CREATE TABLE projects(id TEXT,name TEXT);"
                    "CREATE TABLE project_roots(project_id TEXT,path TEXT);"
                    "CREATE TABLE threads(id TEXT,name TEXT,title TEXT,cwd TEXT,source TEXT,"
                    "archived INTEGER,updated_at INTEGER,project_id TEXT);"
                    "CREATE TABLE thread_spawn_edges(child_thread_id TEXT,parent_thread_id TEXT);")
                c.executemany("INSERT INTO projects VALUES(?,?)",
                              [("canonical", "MateLink"), ("hidden", "Stale duplicate")])
                c.executemany("INSERT INTO project_roots VALUES(?,?)",
                              [("canonical", r"X:\demo-a"), ("hidden", r"X:\demo-a")])
                c.executemany("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)", [
                    ("assigned", "示例任务二", "Old title", r"E:\elsewhere", "cli", 0, 10, "hidden"),
                    ("alias", "", "示例界面二", r"E:\worktree", "cli", 0, 9, "canonical"),
                    ("hint", "示例界面一", None, r"E:\worktree", "cli", 0, 8, None),
                    ("root", "示例任务一", None, r"X:\demo-a", "cli", 0, 7, None),
                    ("loose", "Projectless", None, r"X:\demo-a", "cli", 0, 6, None),
                    ("remote", "Remote", None, r"X:\demo-a", "cli", 0, 5, None),
                    ("child", None, None, r"E:\worktree", "subagent", 1, 4, None),
                ])
                c.execute("INSERT INTO thread_spawn_edges VALUES('child','assigned')")
            state_path = db.with_name(".codex-global-state.json")
            state_path.write_text(json.dumps({
                "project-order": ["another", "mate", "remote"],
                "local-projects": {"mate": {"name": "示例项目甲", "rootPaths": [r"X:\demo-a"]},
                                   "another": {"name": "Other", "rootPaths": [r"E:\other"]}},
                "thread-project-assignments": {"assigned": {"projectKind": "local", "projectId": "mate"}},
                "thread-project-membership-host-ids": {"remote": "other-host"},
                "thread-workspace-root-hints": {"hint": r"X:\demo-a"},
                "projectless-thread-ids": ["loose"],
                "app-server-project-id-by-legacy-project-id-by-host": {
                    "local:" + str(db.parent): {"mate": "canonical"}},
            }), encoding="utf-8")
            before = (db.read_bytes(), state_path.read_bytes())
            result = discover(db, [], app_project="mate")
            self.assertEqual([p["label"] for p in result["app_projects"]], ["Other", "示例项目甲"])
            self.assertEqual([row["label"] for row in result["chats"]],
                             ["示例任务二", "示例界面二", "示例界面一", "示例任务一"])
            recent = {row["chat_id"]: row for row in discover(db, [])["chats"]}
            self.assertEqual(recent["loose"]["app_mapping"], "PROJECTLESS")
            self.assertEqual(recent["remote"]["app_mapping"], "OTHER_HOST")
            self.assertNotIn("child", recent)
            metadata = usage_metadata(db, [{"thread_id": "child", "role": "child"}])
            self.assertEqual(metadata["child"]["chat_id"], "assigned")
            self.assertEqual(metadata["child"]["title"], "示例任务二")
            self.assertEqual(metadata["child"]["project_name"], "示例项目甲")
            self.assertEqual((db.read_bytes(), state_path.read_bytes()), before)

    def test_project_filter_precedes_pagination_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.sqlite"
            with closing(sqlite3.connect(db)) as c, c:
                c.executescript(
                    "CREATE TABLE projects(id TEXT,name TEXT);"
                    "CREATE TABLE project_roots(project_id TEXT,path TEXT);"
                    "CREATE TABLE threads(id TEXT,name TEXT,cwd TEXT,source TEXT,"
                    "archived INTEGER,updated_at INTEGER,project_id TEXT);")
                c.executemany("INSERT INTO projects VALUES(?,?)", [("old", "Old"), ("new", "New")])
                c.executemany("INSERT INTO project_roots VALUES(?,?)",
                              [("old", r"E:\old"), ("new", r"E:\new")])
                c.executemany("INSERT INTO threads VALUES(?,?,?,?,?,?,?)", [
                    (f"new{i}", "New", r"E:\new", "cli", 0, 1000 + i, "new")
                    for i in range(230)] + [
                    (f"old{i}", "Old", r"E:\old", "cli", 0, i, "old")
                    for i in range(3)])
            before = db.read_bytes()
            first = discover(db, [], limit=2, app_project="old")
            second = discover(db, [], limit=2, app_project="old", offset=2)
            self.assertEqual([x["chat_id"] for x in first["chats"]], ["old2", "old1"])
            self.assertTrue(first["has_more"])
            self.assertEqual([x["chat_id"] for x in second["chats"]], ["old0"])
            self.assertFalse(second["has_more"])
            self.assertEqual(first["total"], 233)
            self.assertEqual(db.read_bytes(), before)

    def test_native_projects_membership_shared_roots_and_no_parent_guess(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.sqlite"
            with closing(sqlite3.connect(db)) as c, c:
                c.executescript(
                    "CREATE TABLE projects(id TEXT,name TEXT);"
                    "CREATE TABLE project_roots(project_id TEXT,path TEXT);"
                    "CREATE TABLE threads(id TEXT,name TEXT,cwd TEXT,source TEXT,"
                    "archived INTEGER,updated_at INTEGER,project_id TEXT);")
                c.executemany("INSERT INTO projects VALUES(?,?)",
                              [("p", "Native"), ("q", "Shared"), ("r", "Shared 2"),
                               ("empty", "No chats")])
                c.executemany("INSERT INTO project_roots VALUES(?,?)",
                              [("p", r"E:\unique"), ("q", r"E:\shared"),
                               ("r", r"E:\shared")])
                c.executemany("INSERT INTO threads VALUES(?,?,?,?,?,?,?)", [
                    ("a", "Explicit", r"E:\shared", "cli", 0, 6, "q"),
                    ("b", "Ambiguous", r"E:\shared", "cli", 0, 5, None),
                    ("c", "Unique", r"\\?\E:\unique", "cli", 0, 4, None),
                    ("d", "Nested", r"E:\unique\child", "cli", 0, 3, None),
                    ("e", "Dangling", r"E:\unique", "cli", 0, 2, "gone")])
            before = db.read_bytes()
            result = discover(db, [{"project_id": "relay", "path": r"E:\unique"}])
            chats = {r["chat_id"]: r for r in result["chats"]}
            self.assertEqual(len(result["app_projects"]), 4)
            self.assertEqual(chats["a"]["app_project_id"], "q")
            self.assertEqual(chats["b"]["app_mapping"], "AMBIGUOUS_ROOT")
            self.assertIsNone(chats["b"]["app_project_id"])
            self.assertEqual(chats["c"]["app_project_id"], "p")
            self.assertEqual(chats["c"]["project_id"], "relay")
            self.assertIsNone(chats["d"]["app_project_id"])
            self.assertEqual(chats["e"]["app_mapping"], "UNKNOWN_PROJECT")
            self.assertIsNone(chats["e"]["app_project_id"])
            self.assertEqual(db.read_bytes(), before)

    def test_named_human_chats_exact_mapping_and_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state_5.sqlite"
            with closing(sqlite3.connect(db)) as c, c:
                c.execute("CREATE TABLE threads(id TEXT,name TEXT,cwd TEXT,source TEXT,"
                          "thread_source TEXT,archived INTEGER,updated_at INTEGER)")
                c.executemany("INSERT INTO threads VALUES(?,?,?,?,?,?,?)", [
                    ("a", "Actual label", r"\\?\E:\repo", "vscode", "user", 0, 3),
                    ("b", "Nested", r"E:\repo\child", "cli", "user", 0, 2),
                    ("c", "Worker", r"E:\repo", '{"subagent":{}}', "user", 0, 5),
                    ("d", "Archived", r"E:\repo", "cli", "user", 1, 6)])
            before = db.read_bytes()
            result = discover(db, [{"project_id": "p", "path": r"e:\repo"}], limit=1)
            self.assertEqual(result["app_projects"], [])
            self.assertEqual(result["total"], 2)
            self.assertEqual(result["chats"][0]["label"], "Actual label")
            self.assertEqual(result["chats"][0]["project_id"], "p")
            self.assertEqual(db.read_bytes(), before)
            self.assertIsNone(discover(db, [], 10)["chats"][0]["project_id"])
            self.assertIsNone(discover(db, [{"project_id": "p", "path": r"E:\repo"}], 10)
                              ["chats"][1]["project_id"])

    def test_no_create_missing_database(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "missing.sqlite"
            with self.assertRaises(ValueError):
                discover(db, [])
            self.assertFalse(db.exists())

    def test_ambiguous_path_not_guessed(self):
        self.assertEqual(path_key(r"\\?\E:\repo\\"), path_key("e:/repo"))
        self.assertEqual(path_key(r"\\?\UNC\host\share\x"), path_key(r"\\host\share\x"))


def fixture():
    tokens = {"inputTokens": 10, "cacheReadTokens": 70, "cacheCreationTokens": 5,
              "outputTokens": 15, "reasoningOutputTokens": 4, "totalTokens": 100}
    return {"schema_version": 1, "unit": "ccusage_incremental_tokens_not_billing",
            "date": "2026-11-08", "finished_utc": "2026-11-08T04:00:00Z",
            "timezone": "Asia/Shanghai", "totals": tokens,
            "projects": [tokens | {"project_id": "UNMAPPED", "label": "Unmapped",
                                   "records": 1, "by_role": {"child": tokens}}],
            "sessions": [tokens | {"role": "child", "thread_id": "a"}]}


class UsageTests(unittest.TestCase):
    def test_group_by_project_and_title_preserving_child_and_unclassified_totals(self):
        tokens = fixture()["totals"]
        sessions = [tokens | {"thread_id": "main", "role": "primary"},
                    tokens | {"thread_id": "worker", "role": "child"},
                    tokens | {"thread_id": "unknown", "role": "primary"}]
        main = {"project_id": "p", "project_name": "Project", "chat_id": "main", "title": "A chat"}
        groups = group_sessions(sessions, {"main": main, "worker": main})
        chat = groups["p"]["chats"]["main"]
        self.assertEqual((chat["title"], chat["totalTokens"], chat["child_records"]), ("A chat", 200, 1))
        self.assertEqual(groups[None]["title"], "未归类")
        self.assertEqual(sum(group["totalTokens"] for group in groups.values()), 300)

    def test_no_reasoning_double_count_and_unmapped_coverage(self):
        result = summarize(fixture())
        self.assertEqual(result["total"], 100)
        self.assertEqual(sum(v for _, v in result["components"]), 100)
        self.assertEqual(result["roles"]["child"]["totalTokens"], 100)
        self.assertEqual(result["unmapped"], 100)
        self.assertEqual(result["records"], 1)

    def test_reject_bad_counts_and_conservation(self):
        for key, value in (("outputTokens", -1), ("totalTokens", 104),
                           ("reasoningOutputTokens", 18), ("inputTokens", True)):
            data = json.loads(json.dumps(fixture()))
            data["totals"][key] = value
            with self.assertRaises(ValueError):
                summarize(data)

    def test_reject_scope_and_schema_mismatch(self):
        for mutate in (lambda d: d.update(unit="billing"),
                       lambda d: d.update(sessions=[]),
                       lambda d: d["projects"][0].update(totalTokens=99)):
            data = json.loads(json.dumps(fixture()))
            mutate(data)
            with self.assertRaises(ValueError):
                summarize(data)

    def test_format_tokens(self):
        self.assertEqual(token_text(1234567), "1,234,567")


class DefaultTests(unittest.TestCase):
    def test_model_default_threshold_differs_from_capacity_and_tool_limit(self):
        with tempfile.TemporaryDirectory() as d:
            catalog = Path(d) / "models_cache.json"
            entry = {"slug": "terra", "context_window": 500000, "max_context_window": 872000,
                     "truncation_policy": {"mode": "tokens", "limit": 10000}}
            data = {"client_version": "0.167.0", "models": [entry]}
            catalog.write_text(json.dumps(data), encoding="utf-8")
            defaults = model_defaults(catalog, "terra")
            self.assertEqual(defaults["model_context_window"], 500000)
            self.assertEqual(defaults["maximum"], 872000)
            self.assertEqual(defaults["model_auto_compact_token_limit"], 450000)
            self.assertEqual(defaults["tool_output_token_limit"], 10000)
            entry["auto_compact_token_limit"] = 200000
            catalog.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(model_defaults(catalog, "terra")["model_auto_compact_token_limit"], 200000)
            data["client_version"] = "0.168.0"
            catalog.write_text(json.dumps(data), encoding="utf-8")
            # Explicit catalog evidence survives an unknown client version.
            self.assertEqual(model_defaults(catalog, "terra")["model_auto_compact_token_limit"], 200000)
            # Without that evidence, do not infer a threshold from its window.
            del entry["auto_compact_token_limit"]
            catalog.write_text(json.dumps(data), encoding="utf-8")
            self.assertIsNone(model_defaults(catalog, "terra")["model_auto_compact_token_limit"])

    def test_unset_only_selected_root_fields(self):
        source = (b'model="keep"\ntool_output_token_limit=6000\n'
                  b'[profiles.work]\ntool_output_token_limit=123\n[mcp_servers.keep]\nx=1\n')
        result = core.parse(core.propose(source, {"tool_output_token_limit": None}))
        self.assertNotIn("tool_output_token_limit", result)
        self.assertEqual(result["model"], "keep")
        self.assertEqual(result["profiles"]["work"]["tool_output_token_limit"], 123)
        self.assertEqual(result["mcp_servers"]["keep"]["x"], 1)

    def test_unset_missing_is_noop_and_complex_still_fails_closed(self):
        self.assertEqual(core.propose(b'model="keep"\n', {"compact_prompt": None}),
                         b'model="keep"\n')
        with self.assertRaises(core.SafeError):
            core.propose(b'compact_prompt="""keep\nthis"""\n', {"compact_prompt": None})
        with self.assertRaises(core.SafeError):
            core.propose(b'"compact_prompt"="keep"\n', {"compact_prompt": None})


if __name__ == "__main__":
    unittest.main()
