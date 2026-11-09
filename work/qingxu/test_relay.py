import json
import tempfile
import unittest
from pathlib import Path

try:
    from relay import Relay, build_prompt, observer_health
except ImportError:
    Relay = None


class RelayFixture:
    def setUp(self):
        self.assertIsNotNone(Relay, "selected-project relay adapter is missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "source"
        self.project.mkdir()
        self.registry = self.root / "registry.json"
        self.registry.write_text(json.dumps({"projects": [
            {"project_id": "one", "path": str(self.project), "label": "One"}]}))
        self.memory = self.root / "mapping.json"
        self.vault = self.root / "vault"
        self.pages = self.vault / "03-项目记忆" / "one"
        self.pages.mkdir(parents=True)
        for name in ("02-当前进度.md", "03-关键决策.md"):
            (self.pages / name).write_text("old", encoding="utf-8")
        self.write_mapping([{"source_root": str(self.project), "memory_id": "one"}])
        self.relay = Relay({"registry": str(self.registry), "mapping": str(self.memory),
                            "chat_db": str(self.root / "missing-chat-db.sqlite")})

    def write_mapping(self, mappings):
        self.memory.write_text(json.dumps({"active_profile": "home", "profiles": {
            "home": {"memory_root": str(self.vault),
                     "obsidian_vault_root": str(self.vault),
                     "approved_memory_roots": [str(self.vault)],
                     "project_mappings": mappings}}}), encoding="utf-8")


class RelayTests(RelayFixture, unittest.TestCase):
    def test_recent_chats_survive_missing_relay_registry(self):
        import sqlite3
        db = self.root / "chats.sqlite"
        with sqlite3.connect(db) as c:
            c.execute("CREATE TABLE threads(id TEXT,name TEXT,cwd TEXT,source TEXT,"
                      "archived INTEGER,updated_at INTEGER)")
            c.execute("INSERT INTO threads VALUES(?,?,?,?,?,?)",
                      ("chat", "Recent", str(self.project), "cli", 0, 1))
        c.close()
        self.relay.settings["chat_db"] = str(db)
        self.registry.unlink()
        result = self.relay.chats()
        self.assertEqual(result["chats"][0]["label"], "Recent")
        self.assertIsNone(result["chats"][0]["project_id"])
        self.assertTrue(result["registry_error"])
        self.assertFalse(self.registry.exists())

    def test_selected_only(self):
        self.assertEqual(self.relay.project("one")["path"], str(self.project))
        with self.assertRaises(ValueError):
            self.relay.project("unknown")

    def test_mapping_exact_pages(self):
        pages = self.relay.pages("one")
        self.assertEqual(pages["NOW"], self.pages / "02-当前进度.md")
        self.assertEqual(set(pages), {"NOW", "DECISIONS"})

    def test_mapping_missing_never_guessed(self):
        self.write_mapping([])
        with self.assertRaises(ValueError):
            self.relay.pages("one")

    def test_ambiguous_mapping_blocked(self):
        self.write_mapping([{"source_root": str(self.project), "memory_id": name}
                            for name in ("one", "two")])
        with self.assertRaises(ValueError):
            self.relay.pages("one")

    def test_mapping_traversal_blocked(self):
        self.write_mapping([{"source_root": str(self.project), "memory_id": "../escape"}])
        with self.assertRaises(ValueError):
            self.relay.pages("one")

    def test_prompt_has_exact_identity_and_destination(self):
        prompt = build_prompt("restore", self.relay.project("one"),
                              {"revision": 2}, Path("C:/private/state.json"),
                              {"runtime": "C:/runtime", "registry": str(self.registry),
                               "state_root": "C:/private"})
        self.assertIn(str(self.project), prompt)
        self.assertIn("2", prompt)
        self.assertIn("state.json", prompt)
        self.assertIn("semantic-read", prompt)
        self.assertNotIn("global memory", prompt)

    def test_save_prompt_reads_version_before_save_and_calls_for_readback(self):
        prompt = build_prompt("save", self.relay.project("one"), {"revision": 2},
                              Path("C:/private/state.json"),
                              {"runtime": "C:/runtime", "registry": str(self.registry),
                               "state_root": "C:/private"})
        self.assertLess(prompt.index("semantic-read --project-id 'one'"),
                        prompt.index("semantic-save --project-id 'one' --expected-revision 2"))
        self.assertIn("写后重跑", prompt)

    def test_observer_receipt_not_proof_of_process(self):
        result = observer_health({"running": True, "heartbeat_utc": "2026-01-01T00:00:00Z"})
        self.assertEqual(result["process_identity"], "UNKNOWN")
        self.assertEqual(result["heartbeat"], "STALE")

    def test_windowless_python_uses_console_command(self):
        prompt = build_prompt("restore", self.relay.project("one"), {"revision": 2},
                              Path("C:/private/state.json"),
                              {"python": "C:/Python/pythonw.exe", "runtime": "C:/runtime",
                               "registry": str(self.registry), "state_root": "C:/private"})
        self.assertIn("python.exe", prompt)
        self.assertNotIn("pythonw.exe", prompt)


if __name__ == "__main__":
    unittest.main()
