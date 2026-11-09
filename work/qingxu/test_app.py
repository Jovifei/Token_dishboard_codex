import json
from datetime import datetime, timezone
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from app import App
    from config_store import ConfigStore
except ImportError:
    App = ConfigStore = None

from test_relay import RelayFixture
from test_dashboard import fixture


class AppTests(RelayFixture, unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(App, "desktop UI is missing")
        super().setUp()
        self.tk = tk.Tk()
        clipboard = []
        self.enterContext(patch.object(self.tk, "clipboard_clear", clipboard.clear))
        self.enterContext(patch.object(self.tk, "clipboard_append", clipboard.append))
        self.enterContext(patch.object(self.tk, "clipboard_get", lambda: "".join(clipboard)))
        self.tk.withdraw()
        self.addCleanup(self.tk.destroy)
        self.ui = App(self.tk, self.relay, self.root / "appdata")

    def test_three_workspaces(self):
        names = [self.ui.tabs.tab(tab, "text") for tab in self.ui.tabs.tabs()]
        self.assertEqual(names, ["交接与知识库", "聊天用量", "配置与恢复"])

    def test_usage_tree_uses_all_catalog_chats_and_restores_selection(self):
        first = {"chat_id": "a", "label": "目标聊天", "path": str(self.project),
                 "project_id": "one", "app_project_id": "native",
                 "app_project_name": "原生项目", "updated_at": 2}
        second = first | {"chat_id": "b", "app_project_id": None, "app_project_name": None}
        result = {"app_projects": [{"app_project_id": "native", "label": "原生项目"},
                                   {"app_project_id": "empty", "label": "空项目"}],
                  "chats": [first, second], "total": 2}
        with patch.object(self.relay, "chats", return_value=result):
            self.ui.refresh_catalog()
            self.assertEqual(list(self.ui.chat_usage.projects), ["native", "empty", None])
            self.ui.choose_usage_chat(first)
            self.assertEqual(self.ui.chat_usage.tree.selection(), ("a",))
            self.assertEqual(self.ui.chat["chat_id"], "a")
            self.ui.reload_choices()
            self.assertEqual(self.ui.chat["chat_id"], "a")
            self.assertEqual(self.ui.chat_usage.tree.selection(), ("a",))
            other = tk.Toplevel(self.tk)
            try:
                app = App(other, self.relay, self.ui.data_root)
                self.assertEqual(app.chat["chat_id"], "a")
            finally:
                other.destroy()

    def test_taskbar_flashes_once_per_alert_episode(self):
        from context_usage import normalize
        from test_context_usage import USAGE
        usage = normalize(USAGE)
        current = {"time": datetime.now(timezone.utc), "usage": usage, "window": 200, "runtime_limit": 100}
        result = {"sample": current, "compacted": None}
        with patch("app.flash_taskbar") as flash:
            self.ui.show_chat_metrics("a", result, False)
            self.ui.show_chat_metrics("a", result, False)
            self.assertEqual(flash.call_count, 1)
            self.assertIn("运行时压缩线", self.ui.context_notice.get())
            self.assertEqual(self.ui.context_banner.cget("style"), "Alert.TLabel")
            low = result | {"sample": current | {"runtime_limit": 200}}
            self.ui.show_chat_metrics("a", low, False)
            self.ui.show_chat_metrics("a", result, False)
            self.assertEqual(flash.call_count, 2)
            self.assertEqual(len(self.ui.chat_usage.context.table.get_children()), 3)

    def test_reminder_threshold_persists_and_pause_disables_flash(self):
        from context_usage import normalize
        from test_context_usage import USAGE
        ui = self.ui
        self.assertIn("80%", ui.reminder_caption.get())
        with self.assertRaises(ValueError):
            ui.save_reminder(True, 101)
        self.assertFalse(ui.reminder_file.exists())
        result = {"compacted": None, "sample": {"time": datetime.now(timezone.utc), "window": 200,
                  "runtime_limit": 100, "usage": normalize(USAGE)}}
        with patch("app.flash_taskbar") as flash:
            ui.save_reminder(True, 81)
            ui.show_chat_metrics("a", result, False)
            self.assertIn("提醒线 81%", ui.context_notice.get())
            self.assertEqual(ui.context_banner.cget("style"), "Notice.TLabel")
            ui.save_reminder(False, 80)
            ui.show_chat_metrics("a", result, False)
            self.assertIn("已暂停", ui.context_notice.get())
            flash.assert_not_called()
            self.assertEqual(ui.load_reminder(), (False, 80))
            ui.save_reminder(True, 80)
            ui.show_chat_metrics("a", result, False)
            flash.assert_called_once_with(self.tk)


    def test_content_sources_shares_use_bytes_and_clear_on_switch(self):
        from context_usage import SOURCE_LABELS
        panel = self.ui.chat_usage
        rows = dict.fromkeys(SOURCE_LABELS, (0, 0)) | {"messages": (5, 20), "tools": (1, 60)}
        now = datetime.now(timezone.utc)
        diagnosis = {"window_end": now, "calls": {"tools": {"exec": 11}, "mcp": {"mcp__lookup": 10}},
                     "largest_result": {"time": now, "tool": "exec", "bytes": 70000, "call_suffix": "1234"}}
        panel.show_sources({"rows": rows, "scanned_bytes": 130, "truncated": False, "diagnosis": diagnosis})
        self.assertEqual(panel.source_table.item("messages", "values"), ("5", "20", "25.0%"))
        self.assertEqual(panel.source_table.item("tools", "values"), ("1", "60", "75.0%"))
        self.assertIn("调用较频繁 · 共 11 次 · exec 11 次", panel.diagnosis_notes["tools"].cget("text"))
        self.assertIn("mcp__lookup 10 次", panel.diagnosis_notes["mcp"].cget("text"))
        self.assertIn("70,000 字节", panel.diagnosis_notes["output"].cget("text"))
        panel.set_chat(None)
        self.assertEqual(panel.source_table.get_children(), ())
        self.assertEqual(panel.diagnosis_notes["output"].cget("text"), "待测量")

    def test_project_defaults_to_latest_without_reading_notes(self):
        first = {"chat_id": "a", "label": "Latest", "path": str(self.project),
                 "updated_at": 4, "project_id": "one", "app_project_id": "native"}
        older = first | {"chat_id": "b", "label": "Older", "updated_at": 2}
        result = {"chats": [first, older], "total": 2,
                  "app_projects": [{"app_project_id": "native", "label": "Real project"}]}
        with patch.object(self.relay, "chats", return_value=result), \
                patch.object(self.relay, "snapshot") as checkpoint, \
                patch.object(self.relay, "knowledge_mapping") as knowledge:
            self.ui.reload_choices()
            self.assertEqual(self.ui.choice_records, {})
            self.ui.category_choice.set("Real project")
            self.ui.filter_chats()
            self.assertEqual(self.ui.chat["chat_id"], "a")
            self.assertIn("Latest", self.ui.project_choice.get())
            self.ui.project_choice.current(1)
            self.ui.select_project()
            self.assertEqual(self.ui.chat["chat_id"], "b")
            self.ui.chat_usage.tree.selection_set(self.ui.chat_usage.projects["native"])
            self.ui.chat_usage.pick()
            self.assertEqual(self.ui.chat["chat_id"], "a")
            checkpoint.assert_not_called()
            knowledge.assert_not_called()

    def test_project_then_chat_and_recent_with_unsaved_cancellation(self):
        first = {"chat_id": "a", "label": "Recent", "path": str(self.project),
                 "updated_at": 3, "project_id": "one", "mapping": "EXACT",
                 "app_project_id": "native", "app_mapping": "EXACT_ROOT"}
        unknown = first | {"chat_id": "b", "label": "Unknown",
                           "app_project_id": None, "app_mapping": "AMBIGUOUS_ROOT"}
        result = {"chats": [first, unknown], "total": 2,
                  "app_projects": [{"app_project_id": "native", "label": "Real name"},
                                   {"app_project_id": "empty", "label": "No chats"}]}
        with patch.object(self.relay, "chats", return_value=result):
            self.ui.source_kind.set("按项目选聊天")
            self.ui.reload_choices()
            self.assertEqual(self.ui.choice_records, {})
            category = next(k for k, v in self.ui.category_map.items() if v == "native")
            self.ui.category_choice.set(category)
            self.ui.filter_chats()
            self.assertEqual(len(self.ui.choice_records), 1)
            self.ui.project_choice.current(0)
            self.ui.select_project()
            self.assertEqual(self.ui.chat["chat_id"], "a")
            self.ui.summary_text.insert("end", "keep edits")
            self.ui.category_choice.set("未归类")
            with patch("app.messagebox.askyesno", return_value=False):
                self.ui.filter_chats()
            self.assertEqual(self.ui.category_choice.get(), category)
            self.assertEqual(self.ui.chat["chat_id"], "a")
            self.assertEqual(self.ui.summary_text.get("1.0", "end-1c"), "keep edits")
            self.ui.source_kind.set("Codex 最近聊天")
            with patch("app.messagebox.askyesno", return_value=False):
                self.ui.reload_choices()
            self.assertEqual(self.ui.source_kind.get(), "Codex 最近聊天")
            self.assertEqual(self.ui.chat["chat_id"], "a")
            self.assertEqual(self.ui.summary_text.get("1.0", "end-1c"), "keep edits")
            self.ui.source_kind.set("按项目选聊天")
            self.ui.reload_choices()
            self.ui.put(self.ui.summary_text, "")
            self.ui.category_choice.set("未归类")
            self.ui.filter_chats()
            self.assertEqual(next(iter(self.ui.choice_records.values()))["chat_id"], "b")
            empty = next(k for k, v in self.ui.category_map.items() if v == "empty")
            self.ui.category_choice.set(empty)
            self.ui.filter_chats()
            self.assertEqual(self.ui.choice_records, {})
            self.ui.source_kind.set("Codex 最近聊天")
            self.ui.reload_choices()
            self.assertEqual(len(self.ui.choice_records), 2)
            self.assertFalse(self.ui.category_choice.winfo_manager())

    def test_missing_metadata_at_start_does_not_block_with_modal(self):
        with patch("app.messagebox.showerror") as error:
            second = tk.Toplevel(self.tk)
            try:
                ui = App(second, self.relay, self.root / "otherdata")
                self.assertIn("聊天列表暂不可读", ui.status.get())
                self.assertFalse(error.called)
                self.assertEqual(ui.choice_records, {})
            finally:
                second.destroy()

    def test_presets_keep_custom_and_preview_only(self):
        from app import PRESETS
        self.ui.preset.set(PRESETS[1])
        self.ui.select_preset()
        self.assertIsNone(self.ui.config_changes()["model_context_window"])
        self.assertIsNone(self.ui.config_changes()["model_auto_compact_token_limit"])
        self.assertIsNone(self.ui.config_changes()["tool_output_token_limit"])
        self.assertNotIn("model_reasoning_effort", self.ui.config_changes())
        self.ui.config_items["tool_output_token_limit"][1].set("7000")
        self.assertEqual(self.ui.preset.get(), PRESETS[2])
        self.ui.save_custom()
        self.ui.preset.set(PRESETS[0])
        self.ui.select_preset()
        self.assertTrue(all(v is None for v in self.ui.config_changes().values()))
        self.ui.preset.set(PRESETS[1])
        self.ui.select_preset()
        self.assertIsNone(self.ui.config_changes()["tool_output_token_limit"])
        self.ui.preset.set(PRESETS[2])
        self.ui.select_preset()
        self.assertIsNone(self.ui.config_changes()["model_context_window"])
        self.assertEqual(self.ui.config_changes()["tool_output_token_limit"], 7000)

    def test_recommended_preview_and_apply_to_copy(self):
        import core
        from app import PRESETS

        path = self.root / "config.toml"
        original = (b'model_context_window=1000000\nmodel_auto_compact_token_limit=96000\n'
                    b'model_reasoning_effort="high"\n[agents]\ndefault_subagent_reasoning_effort="low"\n'
                    b'[profiles.work]\nmodel_context_window=750000\n')
        path.write_bytes(original)
        self.ui.config_path.set(str(path))
        self.ui.load_config()
        checked, draft = self.ui.config_items[core.AGENT_EFFORT]
        checked.set(True)
        draft.set("high")
        self.ui.preset.set(PRESETS[1])
        self.ui.select_preset()
        self.assertEqual(set(self.ui.config_changes()), core.TOKEN_KEYS)
        self.ui.preview_config()
        self.assertEqual(path.read_bytes(), original)
        with patch("app.messagebox.askyesno", return_value=True):
            self.ui.apply_config()
        result = core.parse(path.read_bytes())
        self.assertNotIn("model_context_window", result)
        self.assertNotIn("model_auto_compact_token_limit", result)
        self.assertNotIn("tool_output_token_limit", result)
        self.assertEqual(result["model_reasoning_effort"], "high")
        self.assertEqual(core.config_value(result, core.AGENT_EFFORT), "low")
        self.assertEqual(result["profiles"]["work"]["model_context_window"], 750000)

    def test_custom_thresholds_are_model_specific(self):
        from app import FILE_MODEL, MODEL_CHOICES

        path = self.root / "config.toml"
        path.write_bytes(b'model="gpt-6.1-terra"\n')
        self.ui.config_path.set(str(path))
        self.ui.load_config()
        enabled, value = self.ui.config_items["model_auto_compact_token_limit"]
        enabled.set(True)
        value.set("420000")
        self.ui.save_custom()
        astra = next(label for label, model in MODEL_CHOICES.items() if model == "gpt-6-astra")
        self.ui.model_view.set(astra)
        self.assertFalse(enabled.get())
        enabled.set(True)
        value.set("225000")
        self.ui.save_custom()
        with self.assertRaisesRegex(ValueError, "型号"):
            self.ui.preview_config()
        self.ui.model_view.set(FILE_MODEL)
        self.assertTrue(enabled.get())
        self.assertEqual(value.get(), "420000")

    def test_official_mode_apply_to_copy_and_restore(self):
        from app import PRESETS
        path = self.root / "config.toml"
        original = b'model="keep"\ntool_output_token_limit=123\n[profiles.work]\nx=1\n'
        path.write_bytes(original)
        self.ui.config_path.set(str(path))
        self.ui.load_config()
        self.ui.preset.set(PRESETS[0])
        self.ui.select_preset()
        self.ui.preview_config()
        self.assertIn("123  →  继承", self.ui.config_text.get("1.0", "end"))
        with patch("app.messagebox.askyesno", return_value=True):
            self.ui.apply_config()
        import core
        result = core.parse(path.read_bytes())
        self.assertNotIn("tool_output_token_limit", result)
        self.assertEqual(result["model"], "keep")
        receipts = list(self.ui.config_store.root.glob("*/receipt.json"))
        self.assertEqual(len(receipts), 1)
        self.ui.config_store.restore(receipts[0])
        self.assertEqual(path.read_bytes(), original)

    def test_dashboard_and_failed_refresh_clear_stale_values(self):
        data = fixture()
        result = {"usage_snapshot": data, "observer": {"heartbeat": "UNKNOWN",
                  "process_identity": "UNKNOWN"}, "STOP": False}
        with patch.object(self.relay, "status", return_value=result):
            self.ui.load_status()
        dashboard = self.ui.dashboard
        tree = dashboard.trees["projects"]
        self.assertEqual(dashboard.cards["totalTokens"].get(), "100")
        project, = tree.get_children()
        self.assertEqual(tree.item(project, "text"), "未归类")
        self.assertEqual(len(tree.get_children(project)), 1)
        self.assertIn("项目未归类 100", dashboard.info.get())
        data["totals"] = dict(data["totals"], totalTokens=999)
        with patch.object(self.relay, "status", return_value=result):
            with self.assertRaises(ValueError):
                self.ui.load_status()
        self.assertEqual(dashboard.cards["totalTokens"].get(), "—")
        self.assertEqual(len(tree.get_children()), 0)
        self.assertIn("读取失败", dashboard.info.get())

    def test_sort_numeric_not_lexical_and_zero_usage(self):
        dashboard = self.ui.dashboard
        tree = dashboard.trees["projects"]
        tree.insert("", "end", text="Small", values=("9",))
        large = tree.insert("", "end", text="Large", values=("1,000",))
        child = tree.insert(large, "end", text="Title", values=("1,000",))
        dashboard.sort(tree, "totalTokens")
        self.assertEqual(tree.get_children()[0], large)
        self.assertEqual(tree.parent(child), large)
        dashboard.sort(tree, "totalTokens")
        self.assertEqual(tree.item(tree.get_children()[0], "text"), "Small")
        data = fixture()
        for row in [data["totals"], *data["projects"], *data["sessions"]]:
            for key in data["totals"]:
                row[key] = 0
        dashboard.render({"usage_snapshot": data, "observer": {"heartbeat": "UNKNOWN",
                         "process_identity": "UNKNOWN"}, "STOP": True}, self.relay.settings)
        self.assertEqual(dashboard.cards["totalTokens"].get(), "0")
        self.assertIn("STOP True", dashboard.health.get())

    def test_generate_and_copy_restore(self):
        self.ui.pid = "one"
        self.ui.snapshot = {"revision": 2, "summary": "unfinished"}
        self.ui.put(self.ui.summary_text, "unfinished")
        with patch.object(self.relay, "snapshot", return_value=self.ui.snapshot):
            self.ui.make_prompt("restore")
        self.ui.copy_prompt()
        self.assertIn(str(self.project), self.tk.clipboard_get())
        self.assertIn("新聊天", self.ui.relay_feedback.get())

    def test_restore_reads_checkpoint_saved_while_window_stays_open(self):
        self.ui.pid = "one"
        self.ui.snapshot = {"revision": 2, "summary": ""}
        latest = {"revision": 3, "summary": "unfinished work saved by old chat"}
        with patch.object(self.relay, "snapshot", return_value=latest) as read:
            self.ui.make_prompt("restore")
        read.assert_called_once_with("one")
        self.assertEqual(self.ui.snapshot, latest)
        self.assertEqual(self.ui.summary_text.get("1.0", "end-1c"), latest["summary"])
        self.assertIn("3", self.ui.prompt_text.get("1.0", "end-1c"))

    def test_unregistered_chat_copies_manual_summary_without_writes(self):
        self.ui.chat = {"chat_id": "x", "path": str(self.project)}
        with patch.object(self.relay, "snapshot") as snapshot:
            self.ui.copy_generated("save")
        snapshot.assert_not_called()
        self.assertIn("回复中给出可粘贴的短摘要", self.tk.clipboard_get())
        self.assertEqual(self.ui.relay_tabs.select(), str(self.ui.prompt_pane))

    def test_unregistered_chat_can_restore_user_pasted_summary_without_writes(self):
        self.ui.chat = {"chat_id": "x", "path": str(self.project)}
        with self.assertRaisesRegex(ValueError, "请先把旧聊天生成"):
            self.ui.make_prompt("restore")
        self.ui.summary_text.insert("1.0", "unfinished: verify the project")
        with patch.object(self.relay, "snapshot") as snapshot, patch.object(self.relay, "save") as save:
            self.ui.copy_generated("restore")
        snapshot.assert_not_called()
        save.assert_not_called()
        prompt = self.tk.clipboard_get()
        self.assertIn(str(self.project), prompt)
        self.assertIn("unfinished: verify the project", prompt)
        self.assertIn("不读取全部旧聊天、整个 Obsidian 或全局记忆", prompt)
        self.assertIn("新聊天", self.ui.relay_feedback.get())

    def test_unregistered_restore_rejects_oversize_summary(self):
        self.ui.chat = {"chat_id": "x", "path": str(self.project)}
        self.ui.summary_text.insert("1.0", "x" * 8193)
        with self.assertRaisesRegex(ValueError, "8192"):
            self.ui.make_prompt("restore")

    def test_read_config_populates_current_values_without_enabling_changes(self):
        path = self.root / "config.toml"
        path.write_text('tool_output_token_limit=4321\nmodel_reasoning_effort="high"\n')
        self.ui.config_path.set(str(path))
        self.ui.load_config()
        self.assertEqual(self.ui.config_items["tool_output_token_limit"][1].get(), "4321")
        self.assertEqual(self.ui.config_items["model_reasoning_effort"][1].get(), "high")
        self.assertFalse(any(enabled.get() for enabled, _ in self.ui.config_items.values()))
        self.assertIn("读取成功", self.ui.config_status.get())

    def test_pasted_summary_does_not_write_private_checkpoint(self):
        self.ui.pid = "one"
        self.ui.put(self.ui.summary_text, "pasted summary")
        with patch.object(self.relay, "save") as save:
            self.ui.make_prompt("restore")
        self.assertIn("pasted summary", self.ui.prompt_text.get("1.0", "end-1c"))
        save.assert_not_called()

    def test_knowledge_prompt_is_scoped_editable_and_never_writes(self):
        self.ui.pid = "one"
        targets, _ = self.relay.knowledge_mapping("one")
        before = {path: path.read_bytes() for path in targets.values() if path.is_file()}
        self.ui.kb_checks["PLAN"].set(True)
        self.tk.clipboard_clear()
        self.tk.clipboard_append("original clipboard")
        self.ui.generate_knowledge_prompt()
        self.assertEqual(self.tk.clipboard_get(), "original clipboard")
        text = self.ui.prompt_text.get("1.0", "end-1c")
        self.assertIn(str(targets["PLAN"]), text)
        self.assertNotIn(str(targets["REQUIREMENTS"]), text)
        self.assertEqual(self.ui.relay_tabs.select(), str(self.ui.prompt_pane))
        self.ui.prompt_text.insert("end", "\nmanual edit")
        self.ui.copy_prompt()
        self.assertTrue(self.tk.clipboard_get().endswith("manual edit"))
        self.ui.kb_checks["REQUIREMENTS"].set(True)
        with self.assertRaisesRegex(ValueError, "重新生成"):
            self.ui.copy_prompt()
        self.assertEqual({path: path.read_bytes() for path in targets.values() if path.is_file()}, before)

    def test_append_knowledge_keeps_the_handoff_editor_edits(self):
        self.ui.pid = "one"
        with patch.object(self.relay, "snapshot", return_value={"revision": 2}):
            self.ui.copy_generated("save")
        self.ui.prompt_text.insert("end", "\nmanual handoff edit")
        self.ui.kb_checks["PLAN"].set(True)
        self.ui.kb_mode.set("附到旧聊交接")
        self.ui.generate_knowledge_prompt()
        self.ui.kb_checks["PLAN"].set(False)
        self.ui.kb_checks["REQUIREMENTS"].set(True)
        self.ui.generate_knowledge_prompt()
        text = self.ui.prompt_text.get("1.0", "end-1c")
        self.assertIn("manual handoff edit", text)
        self.assertEqual(text.count("【项目知识库更新】"), 1)
        self.assertIn("00-项目概览.md", text)
        self.assertNotIn("01-总体计划.md", text)
        self.ui.copy_prompt()
        self.assertEqual(self.tk.clipboard_get(), text)

    def test_cancel_replacement_preserves_manual_prompt_edit(self):
        self.ui.pid = "one"
        self.ui.prompt_text.insert("end", "keep this draft")
        with patch("app.messagebox.askyesno", return_value=False):
            self.ui.copy_generated("save")
        self.assertEqual(self.ui.prompt_text.get("1.0", "end-1c"), "keep this draft")

    def test_write_child_defaults_to_copy_and_show_readback(self):
        import core
        path = self.root / "fixture.config.toml"
        terra, luna = "gpt-6.1-terra", "gpt-6-luna"
        catalog = {"models": [
            {"slug": model, "supported_reasoning_levels": [{"effort": level} for level in efforts]}
            for model, efforts in ((terra, core.EFFORTS), (luna, core.EFFORTS[:-1]))]}
        path.with_name("models_cache.json").write_text(json.dumps(catalog), encoding="utf-8")
        self.relay.settings["config"] = str(path)
        original = b'model="gpt-6.1-terra"\n[agents]\nmax_threads=2\ndefault_subagent_reasoning_effort="low"\n'
        path.write_bytes(original)
        self.ui.config_path.set(str(path))
        self.ui.load_config()
        self.assertEqual(self.ui.config_items[core.AGENT_MODEL][1].get(), "继承")
        self.assertEqual(self.ui.config_items[core.AGENT_EFFORT][1].get(), "low")
        model_checked, model_value = self.ui.config_items[core.AGENT_MODEL]
        effort_checked, effort_value = self.ui.config_items[core.AGENT_EFFORT]
        effort_entry = self.ui.config_entries[core.AGENT_EFFORT]
        model_value.set(luna)
        self.assertEqual(effort_entry.cget("values"), ("继承", *core.EFFORTS))
        model_checked.set(True)
        self.assertEqual(effort_entry.cget("values"), ("继承", *core.EFFORTS[:-1]))
        effort_checked.set(True)
        effort_value.set("ultra")
        with self.assertRaisesRegex(ValueError, "gpt-6-luna.*ultra"):
            self.ui.preview_config()
        self.assertEqual(path.read_bytes(), original)
        model_value.set(terra)
        self.assertEqual(effort_entry.cget("values"), ("继承", *core.EFFORTS))
        self.ui.preview_config()
        self.assertEqual(path.read_bytes(), original)
        with patch("app.messagebox.askyesno", return_value=True):
            self.ui.apply_config()
        self.assertEqual(core.parse(path.read_bytes()), {"model": terra, "agents": {
            "max_threads": 2, "default_subagent_model": terra,
            "default_subagent_reasoning_effort": "ultra"}})
        self.assertEqual(self.ui.config_result_title.get(), "写后读回一致")
        self.assertIn("ultra", self.ui.config_text.get("1.0", "end-1c"))

    def test_decline_switch_preserves_edits_and_project(self):
        self.ui.pid = "one"
        self.ui.summary_text.insert("end", "keep me")
        self.ui.project_choice.set("not a project")
        with patch("app.messagebox.askyesno", return_value=False):
            self.ui.select_project()
        self.assertEqual(self.ui.pid, "one")
        self.assertEqual(self.ui.summary_text.get("1.0", "end-1c"), "keep me")

    def test_decline_close_keeps_window(self):
        self.ui.summary_text.insert("end", "keep me")
        with patch("app.messagebox.askyesno", return_value=False):
            self.ui.close()
        self.assertTrue(self.tk.winfo_exists())

    def test_decline_switch_keeps_exact_chat_with_shared_project(self):
        self.ui.pid = "one"
        first = {"chat_id": "a", "project_id": "one", "path": str(self.project)}
        second = first | {"chat_id": "b"}
        self.ui.chat = second
        self.ui.choice_records = {"First": first, "Second": second}
        self.ui.summary_text.insert("end", "unsaved")
        self.ui.project_choice.set("First")
        with patch("app.messagebox.askyesno", return_value=False):
            self.ui.select_project()
        self.assertEqual(self.ui.project_choice.get(), "Second")
        self.assertEqual(self.ui.chat["chat_id"], "b")



class ConfigTests(unittest.TestCase):
    def test_prepared_receipt_survives_interrupted_status_update(self):
        from sync import replace_checked
        from core import AGENT_MODEL, AGENT_EFFORT, parse
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "config.toml"
            original = b'model="keep"\n'
            path.write_bytes(original)
            store = ConfigStore(root / "private")

            def interrupt_receipt(target, expected, candidate):
                if target.name == "receipt.json":
                    raise OSError("simulated interruption")
                return replace_checked(target, expected, candidate)

            with patch("config_store.replace_checked", side_effect=interrupt_receipt):
                receipt = store.apply(path, original, {AGENT_MODEL: "gpt-6-luna", AGENT_EFFORT: "high"})
            self.assertEqual(parse(path.read_bytes())["agents"]["default_subagent_model"], "gpt-6-luna")
            self.assertEqual(json.loads(Path(receipt).read_bytes())["status"], "PREPARED")
            self.assertEqual(ConfigStore(root / "private").restore(receipt), "RESTORED")
            self.assertEqual(path.read_bytes(), original)

    def test_persistent_backup_reopen_restore(self):
        self.assertIsNotNone(ConfigStore, "persistent configuration recovery is missing")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "config.toml"
            path.write_bytes(b'model="keep"\n')
            store = ConfigStore(root / "private")
            receipt = store.apply(path, b'model="keep"\n',
                                  {"tool_output_token_limit": 6000})
            store = ConfigStore(root / "private")
            store.restore(receipt)
            self.assertEqual(path.read_bytes(), b'model="keep"\n')

    def test_config_conflict_and_foreign_values_preserved(self):
        self.assertIsNotNone(ConfigStore, "configuration recovery is missing")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "config.toml"
            original = b'model="keep"\n[unknown]\nx=1\n'
            path.write_bytes(original)
            store = ConfigStore(root / "private")
            r = store.apply(path, original, {"tool_output_token_limit": 6000})
            newer = path.read_bytes() + b'y=2\n'
            path.write_bytes(newer)
            with self.assertRaises(ValueError):
                store.restore(r)
            self.assertEqual(path.read_bytes(), newer)

    def test_config_preflight_and_receipt_tampering(self):
        self.assertIsNotNone(ConfigStore)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "config.toml"
            path.write_bytes(b'model="keep"\n')
            store = ConfigStore(root / "private")
            with self.assertRaises(ValueError):
                store.apply(path, b"# stale", {"tool_output_token_limit": 6000})
            receipt = store.apply(path, path.read_bytes(), {"tool_output_token_limit": 6000})
            record = json.loads(Path(receipt).read_text(encoding="utf-8"))
            record["backup"] = str(path)
            Path(receipt).write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaises(ValueError):
                store.restore(receipt)


if __name__ == "__main__":
    unittest.main()
