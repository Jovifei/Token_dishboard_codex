"""P0/P1 checks use isolated directories; never inspect the user's Codex home."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import core
from config_store import ConfigStore, model_defaults
from test_path_support import directory_alias
from handoff import HandoffStore
from preferences import PreferenceStore, PreferencesError, relay_settings, validate_directory


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qingxu-preferences-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "appdata"
        self.codex = self.root / "codex"
        self.codex.mkdir()
        self.legacy = {"chat_db": str(self.codex / "state_5.sqlite"),
                       "config": str(self.codex / "config.toml"),
                       "usage": str(self.root / "missing-snapshot" / "usage-latest.json"),
                       "registry": str(self.root / "registry.json")}
        self.store = PreferenceStore(self.data, self.legacy)

    def test_load_defaults_is_read_only_and_has_no_library_requirement(self):
        result = self.store.load()
        self.assertFalse(self.data.exists())
        self.assertEqual(result["codex_home"], str(self.codex))
        self.assertEqual(result["knowledge_mode"], "none")
        self.assertEqual(result["knowledge_dir"], "")
        self.assertEqual(result["snapshot_dir"], "")
        self.assertEqual(result["monitor"]["mode"], "recommended")

    def test_validation_distinguishes_disabled_missing_and_file(self):
        self.assertEqual(validate_directory("", optional=True)["code"], "DISABLED")
        path = self.root / "not-created"
        checked = validate_directory(path, access="write")
        self.assertEqual(checked["code"], "MISSING")
        self.assertTrue(checked["can_create"])
        self.assertFalse(path.exists())
        path.write_text("file", encoding="utf-8")
        self.assertEqual(validate_directory(path)["code"], "NOT_DIRECTORY")

    def test_reject_relative_and_symlink_paths(self):
        self.assertFalse(validate_directory("relative/folder")["ok"])
        link = self.root / "link"
        directory_alias(link, self.codex)
        self.assertFalse(validate_directory(link)["ok"])
        self.assertFalse(validate_directory(self.root / ".." / self.root.name)["ok"])

    def test_non_writable_directory_explains_failure_without_probing_files(self):
        real_access = __import__("os").access
        with patch("preferences.os.access", side_effect=lambda p, mode: False if mode & 2 else real_access(p, mode)):
            checked = validate_directory(self.codex, access="write")
        self.assertEqual(checked["code"], "NOT_WRITABLE")
        self.assertEqual(list(self.codex.iterdir()), [])

    def test_selecting_a_missing_read_source_never_creates_it(self):
        before = self.store.load()
        missing = self.root / "codex-typo"
        with self.assertRaises(PreferencesError):
            self.store.save({"codex_home": str(missing)}, expected=before, create_dirs=True)
        self.assertFalse(missing.exists())
        self.assertFalse(self.data.exists())

    def test_missing_write_directory_needs_explicit_create(self):
        chosen = self.root / "chosen-handoffs"
        with self.assertRaises(PreferencesError):
            self.store.save({"handoff_dir": str(chosen)})
        self.assertFalse(chosen.exists())
        saved = self.store.save({"handoff_dir": str(chosen)}, create_dirs=True)
        self.assertTrue(chosen.is_dir())
        self.assertEqual(saved["handoff_dir"], str(chosen))
        self.assertEqual(PreferenceStore(self.data, self.legacy).load(), saved)

    def test_explicit_create_also_handles_unmodified_default_destination(self):
        initial = self.store.load()
        self.data.mkdir()
        self.store.save({"handoff_dir": initial["handoff_dir"]}, create_dirs=True)
        self.assertTrue(Path(initial["handoff_dir"]).is_dir())

    def test_unknown_fields_and_nested_extensions_survive_updates(self):
        self.data.mkdir()
        self.store.path.write_text(json.dumps({"schema_version": 1,
            "plugin": {"custom": ["keep"]},
            "monitor": {"mode": "custom", "extension": {"keep": 123}}}), encoding="utf-8")
        loaded = self.store.load()
        saved = self.store.save({"monitor": {"enabled": False}}, expected=loaded)
        self.assertEqual(saved["plugin"], {"custom": ["keep"]})
        self.assertEqual(saved["monitor"]["extension"], {"keep": 123})
        self.assertEqual(saved["monitor"]["mode"], "custom")
        self.assertFalse(saved["monitor"]["enabled"])

    def test_unavailable_saved_library_can_be_disabled_without_reading_it(self):
        self.data.mkdir()
        self.store.path.write_text(json.dumps({"knowledge_mode": "folder",
            "knowledge_dir": str(self.root / "offline-vault")}), encoding="utf-8")
        self.store.load()
        saved = self.store.save({"knowledge_mode": "none"})
        self.assertEqual(saved["knowledge_mode"], "none")
        self.assertFalse((self.root / "offline-vault").exists())

    def test_enabling_library_requires_readable_selected_folder(self):
        with self.assertRaises(PreferencesError):
            self.store.save({"knowledge_mode": "folder"})
        library = self.root / "markdown"
        library.mkdir()
        saved = self.store.save({"knowledge_mode": "folder", "knowledge_dir": str(library)})
        self.assertEqual(saved["knowledge_dir"], str(library))
        self.assertEqual(list(library.iterdir()), [])

    def test_concurrent_edit_preserves_newer_preferences(self):
        self.store.save({"extension": 1})
        second = PreferenceStore(self.data, self.legacy)
        second.load()
        self.store.save({"extension": 2})
        with self.assertRaises(PreferencesError):
            second.save({"extension": 3})
        self.assertEqual(self.store.load()["extension"], 2)

    def test_expected_snapshot_detects_stale_ui(self):
        initial = self.store.load()
        self.store.save({"extension": 2})
        with self.assertRaises(PreferencesError):
            self.store.save({"extension": 3}, expected=initial)

    def test_backend_rejects_invalid_monitor_before_creating_any_output(self):
        invalid = (None, [], "recommended", {"mode": "automatic"},
                   {"enabled": 1}, {"custom_kind": "characters"},
                   {"custom_value": 0}, {"custom_value": 101},
                   {"custom_value": True}, {"custom_value": 1.5},
                   {"custom_kind": "tokens", "custom_value": 1_000_000_001})
        for policy in invalid:
            with self.subTest(policy=policy), self.assertRaises(PreferencesError):
                self.store.save({"monitor": policy,
                    "handoff_dir": str(self.root / "output")}, create_dirs=True)
            self.assertFalse(self.data.exists())
            self.assertFalse((self.root / "output").exists())

    def test_invalid_monitor_update_preserves_existing_preferences(self):
        self.store.save({"extension": "preserve"})
        original = self.store.path.read_bytes()
        with self.assertRaises(PreferencesError):
            self.store.save({"monitor": {"mode": "custom", "custom_value": 120}})
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_load_invalid_monitor_fails_without_rewriting_it(self):
        self.data.mkdir()
        for policy in (None, [], {"enabled": "false"}, {"custom_value": -1}):
            raw = json.dumps({"monitor": policy}).encode("utf-8")
            self.store.path.write_bytes(raw)
            with self.subTest(policy=policy), self.assertRaises(PreferencesError):
                self.store.load()
            self.assertEqual(self.store.path.read_bytes(), raw)

    def test_monitor_partial_updates_and_unknown_fields_survive_validation(self):
        saved = self.store.save({"monitor": {"custom_value": 90,
            "extension": {"sound": "soft"}}})
        self.assertEqual(saved["monitor"]["mode"], "recommended")
        saved = self.store.save({"monitor": {"enabled": False}}, expected=saved)
        self.assertFalse(saved["monitor"]["enabled"])
        self.assertEqual(saved["monitor"]["custom_value"], 90)
        self.assertEqual(self.store.load()["monitor"]["extension"], {"sound": "soft"})

    def test_monitor_accepts_percentage_and_token_boundaries(self):
        for kind, limit in (("percent", 100), ("tokens", 1_000_000_000)):
            for value in (1, limit):
                with self.subTest(kind=kind, value=value):
                    saved = self.store.save({"monitor": {"mode": "custom",
                        "custom_kind": kind, "custom_value": value}})
                    self.assertEqual(saved["monitor"]["custom_value"], value)

    def test_invalid_json_and_newer_schema_are_not_overwritten(self):
        self.data.mkdir()
        self.store.path.write_bytes(b'{"broken":')
        with self.assertRaises(PreferencesError):
            self.store.save({"extension": 1})
        self.assertEqual(self.store.path.read_bytes(), b'{"broken":')
        self.store.path.write_text('{"schema_version":99}', encoding="utf-8")
        with self.assertRaises(PreferencesError):
            self.store.save({"extension": 1})
        self.assertEqual(json.loads(self.store.path.read_text())["schema_version"], 99)

    def test_bad_directory_types_and_syntax_are_rejected_on_load_without_rewrite(self):
        self.data.mkdir()
        bad = (None, [], {}, 123, True, "relative-folder", "../parent",
               "   ", str(self.root / ".." / "outside"), "/bad\x00path",
               r"\\server\shared\folder")
        for key in ("codex_home", "handoff_dir", "export_dir", "knowledge_dir", "snapshot_dir"):
            for value in bad:
                raw = json.dumps({key: value}).encode("utf-8")
                self.store.path.write_bytes(raw)
                with self.subTest(key=key, value=value):
                    with self.assertRaisesRegex(PreferencesError, key):
                        self.store.load()
                    self.assertEqual(self.store.path.read_bytes(), raw)

    def test_bad_snapshot_filename_is_blocked_on_load_and_save(self):
        bad = (None, [], 12, "", ".", "..", "../usage.json", r"..\usage.json",
               "/usage.json", "usage\x00.json", "usage\n.json", "C:usage.json",
               "usage.json ", "CON.json")
        self.data.mkdir()
        for value in bad:
            raw = json.dumps({"snapshot_filename": value}).encode("utf-8")
            self.store.path.write_bytes(raw)
            with self.subTest(value=value):
                with self.assertRaisesRegex(PreferencesError, "快照文件名"):
                    self.store.load()
                with self.assertRaises(PreferencesError):
                    self.store.save({"monitor": {"enabled": False}})
                self.assertEqual(self.store.path.read_bytes(), raw)

    def test_invalid_structure_updates_never_create_or_save_preferences(self):
        invalid = ({"codex_home": "relative"}, {"handoff_dir": None},
                   {"export_dir": ""}, {"snapshot_filename": ".."},
                   {"knowledge_mode": "automatic"}, {"knowledge_policy": "write-all"},
                   {"schema_version": True}, {"schema_version": 99})
        for update in invalid:
            with self.subTest(update=update), self.assertRaises(PreferencesError):
                self.store.save(update, create_dirs=True)
            self.assertFalse(self.data.exists())

    def test_bad_legacy_structures_raise_preferences_error_instead_of_typeerror(self):
        self.data.mkdir()
        invalid = ({"paths": []}, {"paths": "relative"}, {"chat_db": ["file.sqlite"]},
                   {"usage": {"filename": "usage.json"}}, {"schema_version": False},
                   {"schema_version": "1"}, {"knowledge_mode": []},
                   {"knowledge_policy": False}, {"paths": {"codex_home": []}})
        for value in invalid:
            raw = json.dumps(value).encode("utf-8")
            self.store.path.write_bytes(raw)
            with self.subTest(value=value), self.assertRaises(PreferencesError):
                self.store.load()
            self.assertEqual(self.store.path.read_bytes(), raw)

    def test_offline_data_directories_allow_loading_and_unrelated_policy_changes(self):
        self.data.mkdir()
        offline = self.root / "offline-drive"
        values = {key: str(offline / key) for key in
                  ("codex_home", "handoff_dir", "export_dir", "knowledge_dir", "snapshot_dir")}
        values.update(knowledge_mode="obsidian", snapshot_filename="usage-latest.json")
        self.store.path.write_text(json.dumps(values), encoding="utf-8")
        original_stat, original_lstat = Path.stat, Path.lstat

        def checked_stat(path, *args, **kwargs):
            if path.is_relative_to(offline):
                self.fail("Loading preferences must not stat an offline data source")
            return original_stat(path, *args, **kwargs)

        def checked_lstat(path, *args, **kwargs):
            if path.is_relative_to(offline):
                self.fail("Loading preferences must not inspect an offline data source")
            return original_lstat(path, *args, **kwargs)

        with patch.object(Path, "stat", checked_stat), patch.object(Path, "lstat", checked_lstat):
            with patch("preferences.validate_directory", side_effect=AssertionError("unexpected directory access")):
                loaded = self.store.load()
                saved = self.store.save({"monitor": {"enabled": False}}, expected=loaded)
        self.assertFalse(saved["monitor"]["enabled"])
        self.assertEqual(saved["knowledge_dir"], values["knowledge_dir"])
        self.assertFalse(offline.exists())

    def test_bad_preference_load_leaves_defaults_usable_for_startup_recovery(self):
        self.data.mkdir()
        raw = b'{"codex_home":"relative","snapshot_filename":"../bad.json"}'
        self.store.path.write_bytes(raw)
        with self.assertRaises(PreferencesError):
            self.store.load()
        recovered = self.store.defaults
        settings = relay_settings(recovered, self.legacy)
        store = HandoffStore(recovered["handoff_dir"])
        self.assertEqual(settings["chat_db"], self.legacy["chat_db"])
        self.assertEqual(store.root, self.data / "handoffs")
        self.assertFalse(store.root.exists())
        self.assertEqual(self.store.path.read_bytes(), raw)

    def test_failed_atomic_replace_keeps_original_and_removes_temporary_file(self):
        self.store.save({"extension": "old"})
        before = self.store.path.read_bytes()
        with patch("preferences.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.store.save({"extension": "new"})
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual([p.name for p in self.data.iterdir()], ["preferences.json"])

    def test_legacy_reminder_pause_kept_without_reinterpreting_old_percent(self):
        self.data.mkdir()
        (self.data / "chat-reminder.json").write_text('{"enabled":false,"percent":85}', encoding="utf-8")
        loaded = PreferenceStore(self.data, self.legacy).load()
        self.assertEqual(loaded["legacy_reminder"], {"enabled": False, "percent": 85})
        self.assertFalse(loaded["monitor"]["enabled"])
        self.assertEqual(loaded["monitor"]["custom_value"], 70)
        self.assertFalse(self.store.path.exists())

    def test_legacy_path_alias_migration_preserves_extra_fields(self):
        self.data.mkdir()
        self.store.path.write_text(json.dumps({"schema_version": 0,
            "codex_dir": str(self.codex), "extra": "keep"}), encoding="utf-8")
        loaded = self.store.load()
        self.assertEqual(loaded["schema_version"], 1)
        saved = self.store.save({"knowledge_policy": "none"})
        self.assertEqual(saved["codex_home"], str(self.codex))
        self.assertEqual(saved["extra"], "keep")

    def test_rebind_preserves_private_registry_and_separate_config_file(self):
        new = self.root / "other-home"
        new.mkdir()
        values = self.store.load() | {"codex_home": str(new), "snapshot_dir": ""}
        bound = relay_settings(values, self.legacy)
        self.assertEqual(bound["chat_db"], str(new / "state_5.sqlite"))
        self.assertEqual(bound["config"], str(new / "config.toml"))
        self.assertEqual(bound["registry"], self.legacy["registry"])
        self.assertFalse(bound["snapshot_enabled"])
        self.assertEqual(bound["usage"], "")
        original = self.legacy | {"config": str(self.root / "custom.config.toml")}
        self.assertEqual(relay_settings(values, original)["config"], original["config"])

    def test_snapshot_filename_cannot_escape_selected_directory(self):
        values = self.store.load() | {"snapshot_filename": "../other.json"}
        with self.assertRaises(PreferencesError):
            relay_settings(values, self.legacy)

    def test_disabling_and_reenabling_codex_home_restores_database_filename(self):
        values = self.store.load()
        disabled = relay_settings(values | {"codex_home": ""}, self.legacy)
        self.assertEqual(disabled["chat_db"], "")
        rebound = relay_settings(values, disabled)
        self.assertEqual(rebound["chat_db"], str(self.codex / "state_5.sqlite"))

    def test_reenabling_different_home_keeps_default_config_and_database_bound(self):
        values = self.store.load()
        original = self.legacy | {"chat_db": str(self.codex / "state_9.sqlite")}
        disabled = relay_settings(values | {"codex_home": ""}, original)
        new_home = self.root / "another-codex"
        rebound = relay_settings(values | {"codex_home": str(new_home)}, disabled)
        self.assertEqual(rebound["chat_db"], str(new_home / "state_9.sqlite"))
        self.assertEqual(rebound["config"], str(new_home / "config.toml"))
        custom = original | {"config": str(self.root / "custom-config.toml")}
        disabled = relay_settings(values | {"codex_home": ""}, custom)
        rebound = relay_settings(values | {"codex_home": str(new_home)}, disabled)
        self.assertEqual(rebound["config"], custom["config"])


class ModelConfigurationTests(unittest.TestCase):
    def test_main_model_write_preserves_unselected_scope_and_inheritance(self):
        original = b'model="old-model"\n# leave other fields\n[profiles.work]\nmodel="other-model"\n'
        changed = core.propose(original, {core.MODEL: "provider/new-model"})
        parsed = core.parse(changed)
        self.assertEqual(parsed["model"], "provider/new-model")
        self.assertEqual(parsed["profiles"]["work"]["model"], "other-model")
        self.assertNotIn("model", core.parse(core.propose(changed, {core.MODEL: None})))

    def test_main_model_rejects_bad_values_and_security_keys_stay_forbidden(self):
        for value in ("", " bad", "model\nnext", True, [], 42):
            with self.subTest(value=value), self.assertRaises(core.SafeError):
                core.propose(b"", {core.MODEL: value})
        with self.assertRaises(core.SafeError):
            core.propose(b"", {"approval_policy": "never"})

    def test_main_model_uses_existing_backup_and_conflict_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            original = b'model="old"\n'
            config.write_bytes(original)
            store = ConfigStore(root / "backups")
            receipt = store.apply(config, original, {core.MODEL: "new"})
            self.assertEqual(core.parse(config.read_bytes())["model"], "new")
            self.assertEqual(store.restore(receipt), "RESTORED")
            self.assertEqual(config.read_bytes(), original)

    def test_unknown_client_preserves_explicit_catalog_compaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models_cache.json"
            path.write_text(json.dumps({"client_version": "0.999.0", "models": [{
                "slug": "test-model", "context_window": 100000,
                "auto_compact_token_limit": 72000,
                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]}]}), encoding="utf-8")
            defaults = model_defaults(path, "test-model")
            self.assertEqual(defaults["model_auto_compact_token_limit"], 72000)
            self.assertEqual(defaults["efforts"], ("low", "high"))

    def test_unknown_client_does_not_invent_missing_compaction_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models_cache.json"
            path.write_text(json.dumps({"client_version": "future", "models": [{
                "slug": "test-model", "context_window": 100000}]}), encoding="utf-8")
            self.assertIsNone(model_defaults(path, "test-model")["model_auto_compact_token_limit"])

    def test_known_legacy_client_keeps_verified_window_cap_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models_cache.json"
            path.write_text(json.dumps({"client_version": "0.167.0", "models": [{
                "slug": "test-model", "context_window": 100000,
                "auto_compact_token_limit": 95000}]}), encoding="utf-8")
            self.assertEqual(model_defaults(path, "test-model")["model_auto_compact_token_limit"], 90000)


if __name__ == "__main__":
    unittest.main()
