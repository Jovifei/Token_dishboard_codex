"""Headless checks for settings form parsing and save/error state transitions."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from handoff import project_key
from preferences import PreferenceStore
from relay import KB_FILES
from ui_preferences import PreferencesPanel, parse_form


def form():
    return {
        "codex_home": "", "handoff_dir": "/tmp/handoffs", "export_dir": "/tmp/exports",
        "knowledge_dir": "", "snapshot_dir": "",
        "knowledge_mode": "不使用知识库", "knowledge_policy": "由旧会话询问后更新",
        "monitor_mode": "recommended", "custom_kind": "参考容量百分比",
        "custom_value": "70", "enabled": True,
    }


class FormTests(unittest.TestCase):
    def test_chinese_labels_become_backend_codes_without_model_calls(self):
        parsed = parse_form(form())
        self.assertEqual(parsed["knowledge_mode"], "none")
        self.assertEqual(parsed["knowledge_policy"], "ask")
        self.assertEqual(parsed["monitor"], {"mode": "recommended", "custom_kind": "percent",
                                            "custom_value": 70, "enabled": True})

    def test_valid_token_threshold_and_plain_folder(self):
        parsed = parse_form(form() | {"monitor_mode": "custom", "custom_kind": "固定 Token 数",
            "custom_value": "120000", "knowledge_mode": "普通 Markdown 文件夹",
            "knowledge_policy": "按已授权范围更新", "knowledge_dir": " /data/knowledge "})
        self.assertEqual(parsed["monitor"]["custom_value"], 120000)
        self.assertEqual(parsed["monitor"]["custom_kind"], "tokens")
        self.assertEqual(parsed["knowledge_mode"], "folder")
        self.assertEqual(parsed["knowledge_dir"], "/data/knowledge")

    def test_invalid_percent_is_rejected_without_clamping(self):
        for value in ("0", "101", "70.5", "", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_form(form() | {"monitor_mode": "custom", "custom_value": value})

    def test_switching_from_invalid_custom_to_official_remains_possible(self):
        parsed = parse_form(form() | {"monitor_mode": "official", "custom_value": "bad draft"})
        self.assertEqual(parsed["monitor"]["mode"], "official")
        self.assertEqual(parsed["monitor"]["custom_value"], 70)

    def test_unknown_choices_and_non_boolean_enable_are_rejected(self):
        for change in ({"monitor_mode": "guessed"}, {"knowledge_mode": "cloud-auto"},
                       {"knowledge_policy": "anything"}, {"enabled": "false"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_form(form() | change)

    def test_knowledge_disabled_and_reminder_paused_are_explicit(self):
        parsed = parse_form(form() | {"knowledge_policy": "不更新，只保存交接", "enabled": False})
        self.assertEqual(parsed["knowledge_policy"], "none")
        self.assertFalse(parsed["monitor"]["enabled"])

    def test_knowledge_categories_default_empty_and_reject_invalid_selection(self):
        self.assertEqual(parse_form(form())["knowledge_categories"], [])
        self.assertEqual(parse_form(form())["knowledge_project_key"], "")
        parsed = parse_form(form() | {"knowledge_categories": ["BUGS", "PLAN"]})
        self.assertEqual(parsed["knowledge_categories"], ["PLAN", "BUGS"])
        with self.assertRaises(ValueError):
            parse_form(form() | {"knowledge_categories": ["other"]})


class SaveStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qingxu-ui-preferences-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = PreferenceStore(root)
        self.prefs = self.store.load()
        self.raw = form() | {"codex_home": self.prefs["codex_home"],
                             "handoff_dir": self.prefs["handoff_dir"],
                             "export_dir": self.prefs["export_dir"]}
        self.app = SimpleNamespace(prefs=self.prefs, pref_store=self.store, set_status=Mock())

        def apply(updates, create_dirs=False):
            return self.store.save(updates, expected=self.app.prefs, create_dirs=create_dirs)
        self.app.apply_preferences = Mock(side_effect=apply)
        panel = object.__new__(PreferencesPanel)
        panel.app = self.app
        panel._original = deepcopy(self.prefs)
        panel._raw_form = lambda: deepcopy(self.raw)
        panel.save_button = Mock()
        panel._set_status = Mock()
        panel.refresh = Mock()
        panel.validate_path = Mock(return_value={"ok": True, "code": "OK", "can_create": False})
        self.panel = panel

    def test_save_creates_only_explicit_output_folders_and_reports_completion(self):
        self.assertTrue(self.panel.save())
        self.app.apply_preferences.assert_called_once()
        self.assertTrue(Path(self.prefs["handoff_dir"]).is_dir())
        self.assertTrue(Path(self.prefs["export_dir"]).is_dir())
        self.panel.refresh.assert_called_once()
        self.assertEqual(self.panel._set_status.call_args.args[1], "saved")

    def test_bad_form_never_invokes_app_write_and_keeps_user_text(self):
        self.raw.update(monitor_mode="custom", custom_value="not a number")
        before = deepcopy(self.raw)
        self.assertFalse(self.panel.save())
        self.app.apply_preferences.assert_not_called()
        self.panel.refresh.assert_not_called()
        self.assertEqual(self.raw, before)
        self.assertEqual(self.panel._set_status.call_args.args[1], "error")

    def test_failed_write_keeps_draft_and_allows_retry(self):
        self.app.apply_preferences.side_effect = OSError("simulated disk failure")
        self.assertFalse(self.panel.save())
        self.panel.refresh.assert_not_called()
        self.panel.save_button.configure.assert_called_with(state="normal")
        self.assertEqual(self.panel._set_status.call_args.args[1], "error")

    def test_directory_error_blocks_save_with_inline_message(self):
        self.raw["codex_home"] = str(Path(self.temp.name) / "unreadable")
        self.panel.validate_path.side_effect = lambda key: {
            "ok": key != "codex_home", "code": "PERMISSION_DENIED", "can_create": False,
            "message": "访问被拒绝。"}
        self.assertFalse(self.panel.save())
        self.app.apply_preferences.assert_not_called()
        self.assertIn("Codex 数据目录", self.panel._set_status.call_args.args[0])


class KnowledgeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qingxu-knowledge-settings-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.notes = self.base / "project-notes"
        self.notes.mkdir()
        self.record = {"path": str(self.base / "project-a"), "chat_id": "one", "label": "chat"}
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        (self.base / "appdata").mkdir()
        self.store = PreferenceStore(self.base / "appdata", {"chat_db": str(self.base / "codex" / "state.sqlite")})
        self.app = SimpleNamespace(root=self.root, chat=self.record, pref_store=self.store,
                                   data_root=self.base, prefs=self.store.load(), set_status=Mock())
        self.app.apply_preferences = lambda updates, create_dirs=False: self.store.save(
            updates, expected=self.app.prefs, create_dirs=create_dirs)
        self.panel = PreferencesPanel(self.root, self.app)
        self.panel.pack(fill="both", expand=True)

    def test_binding_requires_confirmation_and_limits_template_creation_to_current_project(self):
        self.panel.vars["knowledge_mode"].set("普通 Markdown 文件夹")
        with patch("ui_preferences.filedialog.askdirectory", return_value=str(self.notes)), \
             patch("ui_preferences.messagebox.askyesno", return_value=False):
            self.panel.browse_path("knowledge_dir")
        self.assertEqual(self.panel.vars["knowledge_dir"].get(), "")
        self.assertEqual(self.panel.vars["knowledge_project_key"].get(), "")
        self.assertEqual(list(self.notes.iterdir()), [])

        with patch("ui_preferences.filedialog.askdirectory", return_value=str(self.notes)), \
             patch("ui_preferences.messagebox.askyesno", return_value=True):
            self.panel.browse_path("knowledge_dir")
        self.assertEqual(self.panel.vars["knowledge_project_key"].get(), project_key(self.record))
        self.panel.knowledge_checks["PLAN"].set(True)
        self.assertTrue(self.panel.save(), self.panel.status_var.get())
        self.assertFalse(self.panel.knowledge_init_button.instate(["disabled"]))

        with patch("ui_preferences.messagebox.askyesno", return_value=False):
            self.panel.initialize_knowledge()
        self.assertEqual(list(self.notes.iterdir()), [])
        with patch("ui_preferences.messagebox.askyesno", return_value=True):
            self.panel.initialize_knowledge()
        self.assertTrue((self.notes / KB_FILES["PLAN"]).is_file())
        self.assertFalse((self.notes / KB_FILES["NOW"]).exists())

        self.app.chat = self.record | {"path": str(self.base / "project-b")}
        self.panel.refresh_project()
        self.assertTrue(self.panel.knowledge_init_button.instate(["disabled"]))
        self.panel.initialize_knowledge()
        self.assertEqual(len(list(self.notes.iterdir())), 1)

    def test_typing_another_folder_clears_previous_binding(self):
        self.panel.vars["knowledge_mode"].set("普通 Markdown 文件夹")
        with patch("ui_preferences.filedialog.askdirectory", return_value=str(self.notes)), \
             patch("ui_preferences.messagebox.askyesno", return_value=True):
            self.panel.browse_path("knowledge_dir")
        self.panel.vars["knowledge_dir"].set(str(self.base / "different"))
        self.assertEqual(self.panel.vars["knowledge_project_key"].get(), "")

    def test_project_change_during_confirmation_does_not_bind(self):
        self.panel.vars["knowledge_mode"].set("普通 Markdown 文件夹")

        def switch_project(*_args, **_kwargs):
            self.app.chat = self.record | {"path": str(self.base / "project-b")}
            return True

        with patch("ui_preferences.filedialog.askdirectory", return_value=str(self.notes)), \
             patch("ui_preferences.messagebox.askyesno", side_effect=switch_project):
            self.panel.browse_path("knowledge_dir")
        self.assertEqual(self.panel.vars["knowledge_dir"].get(), "")
        self.assertEqual(self.panel.vars["knowledge_project_key"].get(), "")
        self.assertEqual(list(self.notes.iterdir()), [])

    def test_settings_change_during_template_confirmation_blocks_creation(self):
        self.panel.vars["knowledge_mode"].set("普通 Markdown 文件夹")
        with patch("ui_preferences.filedialog.askdirectory", return_value=str(self.notes)), \
             patch("ui_preferences.messagebox.askyesno", return_value=True):
            self.panel.browse_path("knowledge_dir")
        self.panel.knowledge_checks["PLAN"].set(True)
        self.assertTrue(self.panel.save(), self.panel.status_var.get())

        def change_settings(*_args, **_kwargs):
            self.store.save({"knowledge_categories": []}, expected=self.app.prefs)
            return True

        with patch("ui_preferences.messagebox.askyesno", side_effect=change_settings):
            self.panel.initialize_knowledge()
        self.assertEqual(list(self.notes.iterdir()), [])
        self.assertIn("设置已更改", self.panel.status_var.get())


if __name__ == "__main__":
    unittest.main()
