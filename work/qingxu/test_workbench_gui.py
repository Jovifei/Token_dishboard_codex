"""Integration tests on synthetic data. Requires a real Tk display."""
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "tools/fixtures"))
import demo
import core
from app import PRESETS
from relay import Relay
from workbench import Workbench


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qingxu-new-ui-")
        self.addCleanup(self.tmp.cleanup)
        self.fixture = demo.create(Path(self.tmp.name) / "fixture")
        self.tk = tk.Tk()
        self.tk.withdraw()
        self.app = Workbench(self.tk, Relay(self.fixture["settings"]), self.fixture["prefs"])
        self.addCleanup(self.finish)
        self.normal = next(r for r in self.app.catalog["chats"] if r["chat_id"] == demo.IDS["normal"])
        self.alert = next(r for r in self.app.catalog["chats"] if r["chat_id"] == demo.IDS["alert"])
        self.app.choose_record(self.normal)

    def finish(self):
        self.app.closed = True
        self.app.metric_worker.shutdown(wait=True, cancel_futures=True)
        self.tk.destroy()

    def config(self):
        self.app.show_page("settings")
        self.app.load_config()
        return self.app

    def test_three_pages_share_project_selection(self):
        for page in ("usage", "handoff", "settings"):
            self.app.show_page(page)
            self.assertEqual(self.app.chat["chat_id"], self.normal["chat_id"])
            self.assertEqual(self.app.picker.winfo_manager(), "pack")
        self.assertEqual(len(self.app.tabs.tabs()), 3)

    def test_main_model_write_is_previewed_applied_and_recoverable(self):
        ui = self.config()
        original = ui.original_config
        for key, value in ((core.MODEL, "gpt-6-luna"), ("model_reasoning_effort", "medium")):
            ui.config_items[key][0].set(True)
            ui.config_items[key][1].set(value)
        ui.preview_config()
        self.assertEqual(ui.config_preview[3]["model"], "gpt-6-luna")
        with patch("app.messagebox.askyesno", return_value=True):
            ui.apply_config()
        target = Path(ui.config_path.get())
        self.assertEqual(core.parse(target.read_bytes())["model"], "gpt-6-luna")
        receipts = list(ui.config_store.root.glob("*/receipt.json"))
        self.assertEqual(len(receipts), 1)
        ui.config_store.restore(receipts[0])
        self.assertEqual(target.read_bytes(), original)

    def test_changing_model_checks_unedited_effort(self):
        ui = self.config()
        target = Path(ui.config_path.get())
        target.write_bytes(core.propose(target.read_bytes(), {"model_reasoning_effort": "ultra"}))
        ui.load_config()
        ui.config_items[core.MODEL][0].set(True)
        ui.config_items[core.MODEL][1].set("gpt-6-luna")
        self.assertFalse(ui.config_items["model_reasoning_effort"][0].get())
        with self.assertRaises(ValueError):
            ui.preview_config()
        self.assertIsNone(ui.config_preview)

    def test_child_inheritance_uses_new_main_model(self):
        ui = self.config()
        target = Path(ui.config_path.get())
        target.write_bytes(core.propose(target.read_bytes(), {core.AGENT_MODEL: None, core.AGENT_EFFORT: "ultra"}))
        ui.load_config()
        ui.config_items[core.MODEL][0].set(True)
        ui.config_items[core.MODEL][1].set("gpt-6-luna")
        with self.assertRaises(ValueError):
            ui.preview_config()

    def test_numeric_preset_preserves_agent_selection(self):
        ui = self.config()
        for key in (core.MODEL, core.AGENT_MODEL):
            ui.config_items[key][0].set(True)
            ui.config_items[key][1].set("gpt-6-luna")
        ui.preset.set(PRESETS[0])
        ui.select_preset()
        for key in (core.MODEL, core.AGENT_MODEL):
            self.assertTrue(ui.config_items[key][0].get())
            self.assertEqual(ui.config_items[key][1].get(), "gpt-6-luna")

    def test_config_external_change_blocks_preview(self):
        ui = self.config()
        ui.config_items[core.MODEL][0].set(True)
        ui.config_items[core.MODEL][1].set("gpt-6-luna")
        with Path(ui.config_path.get()).open("ab") as stream:
            stream.write(b"\n# external edit\n")
        with self.assertRaises(ValueError):
            ui.preview_config()
        self.assertIsNone(ui.config_preview)

    def test_switch_cancel_keeps_exact_draft_and_chat(self):
        panel = self.app.handoff_panel
        panel.summary_text.insert("1.0", "当前项目草稿，尚未保存。")
        with patch("workbench.messagebox.askyesno", return_value=False):
            self.app.choose_record(self.alert)
        self.assertEqual(self.app.chat["chat_id"], self.normal["chat_id"])
        self.assertIn("当前项目草稿", panel.summary_text.get("1.0", "end"))

    def test_directory_and_policy_save_rebinds_monitor(self):
        original = self.app.monitor
        self.app.apply_preferences({"monitor": {"mode": "custom", "custom_kind": "percent", "custom_value": 65, "enabled": False}})
        self.assertIsNot(self.app.monitor, original)
        saved = json.loads((self.fixture["prefs"] / "preferences.json").read_text())
        self.assertEqual(saved["monitor"]["custom_value"], 65)
        self.assertFalse(saved["monitor"]["enabled"])

    def test_unconnected_snapshot_does_not_read_collector(self):
        self.app.relay.settings["snapshot_enabled"] = False
        with patch.object(self.app.relay, "status") as read:
            self.app.refresh_usage()
        read.assert_not_called()

    def test_cancel_source_change_has_no_persisted_or_runtime_change(self):
        ui = self.app
        before = dict(ui.relay.settings)
        generation = ui.metric_generation
        directory = Path(self.tmp.name) / "new-home"
        directory.mkdir()
        ui.handoff_panel.summary_text.insert("1.0", "未保存的摘要")
        with patch("workbench.messagebox.askyesno", return_value=False):
            with self.assertRaises(ValueError):
                ui.apply_preferences({"codex_home": str(directory)})
        self.assertEqual(before, ui.relay.settings)
        self.assertEqual(generation, ui.metric_generation)
        self.assertFalse(ui.pref_store.path.exists())
        self.assertEqual(ui.chat["chat_id"], self.normal["chat_id"])

    def test_empty_verified_effort_list_does_not_offer_unsupported_values(self):
        ui = self.config()
        with patch.object(ui, "model_reference", return_value={"efforts": (), "version": "fixture"}):
            ui.show_model_defaults()
        self.assertEqual(tuple(ui.config_entries["model_reasoning_effort"]["values"]), ("继承",))

    def test_floating_selected_mode_follows_qingxu_only(self):
        ui = self.app
        rows, detail, catalog = ui.collect_metrics(dict(ui.relay.settings), ui.monitor, ui.catalog["chats"], self.normal["chat_id"], {})
        ui.floating.render(rows)
        ui.floating.show()
        ui.floating._set_mode("selected")
        self.assertEqual(ui.floating._shown()[0]["chat_id"], self.normal["chat_id"])
        ui.choose_record(self.alert)
        self.assertEqual(ui.floating._shown(), [])
        rows, _, _ = ui.collect_metrics(dict(ui.relay.settings), ui.monitor, ui.catalog["chats"], self.alert["chat_id"], {})
        ui.floating.render(rows)
        self.assertEqual(ui.floating._shown()[0]["chat_id"], self.alert["chat_id"])
        ui.floating.close()
        self.assertTrue(ui.root.winfo_exists())
        self.assertEqual(ui.floating.window.state(), "withdrawn")

    def test_old_worker_result_is_discarded_on_new_selection(self):
        from concurrent.futures import Future
        ui = self.app
        future = Future()
        future.set_result(([{"stale_test": True}], None, None))
        ui.metric_job = (ui.metric_generation - 1, "old", future)
        with patch.object(ui, "render_metrics") as render:
            ui.update_metrics()
        render.assert_not_called()

    def test_missing_detail_does_not_erase_recent_rows(self):
        ui = self.app
        with patch("workbench.chat_metrics", side_effect=ValueError("selected file unavailable")):
            rows, detail, catalog = ui.collect_metrics(dict(ui.relay.settings), ui.monitor, ui.catalog["chats"], self.normal["chat_id"], {})
        self.assertTrue(rows)
        self.assertIsNone(detail["sample"])
        self.assertIn("unavailable", detail["requests_error"])

    def test_background_identity_change_blocks_new_prompts_and_preserves_draft(self):
        ui = self.app
        ui.handoff_panel.summary_text.insert("1.0", "尚未保存的现场摘要")
        changed = dict(self.normal, path=str(Path(self.tmp.name) / "other-project"))
        catalog = dict(ui.catalog, chats=[changed])
        ui.render_metrics([], None, catalog)
        self.assertIn("归属已变化", ui.handoff_panel.binding_warning)
        self.assertIn("尚未保存", ui.handoff_panel.summary_text.get("1.0", "end"))
        with self.assertRaises(ValueError):
            ui.handoff_panel.generate_old()

    def test_picker_stays_usable_at_high_dpi_and_minimum_width(self):
        self.tk.tk.call("tk", "scaling", 2.0)
        self.app.configure_theme()
        self.tk.geometry("980x680")
        self.tk.deiconify()
        self.tk.update()
        for combo in (self.app.category_choice, self.app.project_choice):
            self.assertTrue(combo.winfo_ismapped())
            self.assertGreaterEqual(combo.winfo_width(), 120)
            self.assertLessEqual(combo.winfo_rootx() + combo.winfo_width(),
                                 self.tk.winfo_rootx() + self.tk.winfo_width())
        self.assertIn(self.app.project_choice.get(), self.app.project_choice["values"])

    def test_handoff_primary_actions_fit_actual_shell_at_minimum_size(self):
        ui = self.app
        self.tk.geometry("980x680")
        self.tk.deiconify()
        ui.show_page("handoff")
        self.tk.update()
        for step, button in ((1, ui.handoff_panel.copy_old_button), (2, ui.handoff_panel.save_button), (3, ui.handoff_panel.copy_new_button)):
            with self.subTest(step=step):
                ui.handoff_panel.show_step(step)
                self.tk.update()
                self.assertTrue(button.winfo_ismapped())
                self.assertGreaterEqual(button.winfo_rootx(), self.tk.winfo_rootx())
                self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), self.tk.winfo_rootx() + self.tk.winfo_width())
                self.assertGreaterEqual(button.winfo_rooty(), self.tk.winfo_rooty())
                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), self.tk.winfo_rooty() + self.tk.winfo_height())
                position = button.winfo_rootx(), button.winfo_rooty()
                ui.handoff_panel.canvas.yview_moveto(1)
                self.tk.update()
                self.assertTrue(button.winfo_ismapped())
                self.assertEqual((button.winfo_rootx(), button.winfo_rooty()), position)

    def test_floating_actions_fit_actual_shell_styles(self):
        ui = self.app
        rows, _, _ = ui.collect_metrics(dict(ui.relay.settings), ui.monitor, ui.catalog["chats"], self.normal["chat_id"], {})
        ui.floating.render(rows)
        ui.floating.show()
        self.tk.update_idletasks()
        floating = ui.floating
        bottom = floating.window.winfo_rooty() + floating.window.winfo_height()
        for widget in (floating.prepare_button, floating.snooze_button, floating.row_widgets[3]):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom)


if __name__ == "__main__":
    unittest.main()
