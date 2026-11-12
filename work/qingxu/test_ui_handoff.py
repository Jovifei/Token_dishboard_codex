"""Tk behavior tests for the three-step handoff.

Run in a real desktop: PYTHONPATH=source/work/qingxu:tests/work/qingxu
python -B -m unittest test_ui_handoff -v
Tests use temporary files and intercepted clipboard/file dialogs.
"""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest import mock

from handoff import HandoffStore, TemplateStore, HandoffError, POLICY_LABELS, project_key
from ui_handoff import HandoffPanel


class HandoffPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            probe = tk.Tk()
            probe.destroy()
        except tk.TclError as exc:
            raise unittest.SkipTest("需要可连接的 Tk 桌面：" + str(exc))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.geometry("1040x760")
        self.addCleanup(self.root.destroy)
        self.messages = []
        self.clipboard = []
        self.app = SimpleNamespace(
            root=self.root, data_root=self.base,
            prefs={"handoff_dir": str(self.base / "handoffs"), "knowledge_dir": "",
                   "knowledge_mode": "none", "knowledge_policy": "ask"},
            handoff_store=HandoffStore(self.base / "handoffs"),
            template_store=TemplateStore(self.base / "templates"),
            set_status=self.messages.append, show_knowledge_settings=mock.Mock(), open_path=mock.Mock(),
        )
        for name, color in (("App.TFrame", "#F5F5F7"), ("Card.TFrame", "white")):
            ttk.Style(self.root).configure(name, background=color)
        self.panel = HandoffPanel(self.root, self.app)
        self.panel.pack(fill="both", expand=True)
        self.panel._copy = self.clipboard.append
        self.record = {"path": str(self.base / "project"), "chat_id": "chat-one",
                       "label": "当前任务", "app_project_id": "project",
                       "app_project_name": "示例工程"}
        self.summary = "项目概览：制作工具。\n验收：可续聊。\n已完成：解析。\n下一步：接界面。\n约束：保留文件。\n工作区：未运行 GUI。\n索引：RUN.md"
        self.root.update()

    def select(self):
        self.panel.set_record(self.record)
        self.root.update()

    def enter_summary(self, value=None):
        self.panel.summary_text.delete("1.0", "end")
        self.panel.summary_text.insert("1.0", value or self.summary)
        self.root.update()

    def test_no_selection_and_copy_only_have_truthful_states(self):
        self.assertEqual(str(self.panel.copy_old_button["state"]), "disabled")
        self.select()
        self.assertIn("chat-one", self.panel._get(self.panel.old_text))
        self.assertEqual(self.clipboard, [])
        self.assertFalse(self.app.handoff_store.root.exists())
        self.assertFalse(self.panel.has_unsaved())
        self.panel.copy_old_button.invoke()
        self.assertEqual(len(self.clipboard), 1)
        self.assertIn("chat-one", self.clipboard[0])
        self.assertIsNone(self.panel.saved)
        self.assertIn("已复制", self.panel.feedback.get())
        self.assertFalse(self.app.handoff_store.root.exists())

    def test_save_roundtrip_idempotence_and_reference_restore(self):
        self.select()
        self.enter_summary()
        self.assertTrue(self.panel.has_unsaved())
        first = self.panel.save_summary()
        second = self.panel.save_summary()
        self.assertEqual(first["path"], second["path"])
        self.assertFalse(self.panel.has_unsaved())
        self.panel.mode.set("file")
        self.panel.show_step(3)
        self.panel.copy_new_button.invoke()
        self.assertIn(first["path"], self.clipboard[-1])
        self.assertNotIn(self.summary, self.clipboard[-1])
        self.assertIn("待新会话执行", self.panel.feedback.get())
        self.assertEqual(len(list(self.app.handoff_store.root.rglob("*.md"))), 1)

    def test_edited_summary_disables_file_mode_but_allows_honest_inline(self):
        self.select()
        self.enter_summary()
        self.panel.save_summary()
        self.enter_summary(self.summary + "\n新增未保存内容")
        self.panel.mode.set("file")
        self.assertEqual(str(self.panel.copy_new_button["state"]), "disabled")
        self.panel.mode.set("inline")
        self.assertEqual(str(self.panel.copy_new_button["state"]), "normal")
        self.panel.copy_new_button.invoke()
        self.assertIn("新增未保存内容", self.clipboard[-1])
        self.assertIn("未验证本地保存", self.clipboard[-1])
        self.assertTrue(self.panel.has_unsaved())

    def test_missing_saved_file_is_revalidated_before_repeated_copy(self):
        self.select()
        self.enter_summary()
        saved = self.panel.save_summary()
        self.panel.mode.set("file")
        self.panel.copy_new()
        count = len(self.clipboard)
        Path(saved["path"]).unlink()
        self.panel.copy_new_button.invoke()
        self.assertEqual(len(self.clipboard), count)
        self.assertTrue(any("No such file" in m or "找不到" in m for m in self.messages))

    def test_pasted_summary_can_continue_without_saving(self):
        self.select()
        self.panel.show_step(2)
        with mock.patch.object(self.root, "clipboard_get", return_value=self.summary):
            self.panel.paste_button.invoke()
        self.assertEqual(self.panel.mode.get(), "inline")
        self.assertIsNone(self.panel.saved)
        self.assertEqual(str(self.panel.copy_new_button["state"]), "normal")
        self.panel.show_step(3)
        self.panel.copy_new_button.invoke()
        self.assertIn(self.summary, self.clipboard[-1])
        self.assertIn("未验证本地保存", self.clipboard[-1])
        self.assertFalse(self.app.handoff_store.root.exists())

    def test_optional_goal_is_collapsed_and_keeps_edits_when_closed(self):
        self.select()
        self.enter_summary()
        self.panel.show_step(3)
        self.root.update()
        self.assertFalse(self.panel.goal_options.winfo_ismapped())
        toggle = next(child for child in self.panel.pages[2].winfo_children()
                      if isinstance(child, ttk.Checkbutton) and "下一阶段目标" in child.cget("text"))
        toggle.invoke()
        self.root.update()
        self.assertTrue(self.panel.goal_options.winfo_ismapped())
        self.panel.goal.set("核对下一步")
        toggle.invoke()
        self.assertFalse(self.panel.goal_options.winfo_ismapped())
        self.panel.copy_new_button.invoke()
        self.assertIn("核对下一步", self.clipboard[-1])

    def test_additional_controls_are_available_when_expanded(self):
        self.select()
        for step, label, control in ((1, "知识处理与模板", self.panel.policy_box),
                                     (2, "更多摘要选项", self.panel.recent_button)):
            with self.subTest(step=step):
                self.panel.show_step(step)
                self.root.update()
                self.assertFalse(control.winfo_ismapped())
                toggle = next(child for child in self.panel.pages[step - 1].winfo_children()
                              if isinstance(child, ttk.Checkbutton) and label in child.cget("text"))
                toggle.invoke()
                self.root.update()
                self.assertTrue(control.winfo_ismapped())

    def test_selection_and_restart_never_reuse_wrong_thread_summary(self):
        self.select()
        self.enter_summary()
        saved = self.panel.save_summary()
        other = {**self.record, "chat_id": "chat-two", "label": "另一任务"}
        self.panel.set_record(other)
        self.assertEqual(self.panel._summary_value(), "")
        self.assertIsNone(self.panel.saved)
        self.panel.set_record(self.record)
        self.assertEqual(self.panel.saved["path"], saved["path"])
        self.assertEqual(self.panel._summary_value(), self.summary)

    def test_direct_old_chat_file_can_be_read_without_file_picker(self):
        self.select()
        prompt = self.panel.generate_old()
        import handoff
        marker = next(line for line in prompt.splitlines() if line.startswith(handoff.SOURCE_HEADER))
        target = self.panel._draft_path
        target.parent.mkdir(parents=True)
        target.write_text(marker + "\n\n" + self.summary, encoding="utf-8")
        self.panel.read_old_file()
        self.assertEqual(self.panel._summary_value(), self.summary)
        self.assertEqual(self.panel.saved["status"], "SAVED_VERIFIED")
        self.assertNotEqual(self.panel.saved["path"], str(target))

    def test_plain_file_needs_confirmation_and_wrong_project_is_rejected(self):
        self.select()
        plain = self.base / "plain.md"
        plain.write_text(self.summary, encoding="utf-8")
        with mock.patch("ui_handoff.messagebox.askyesno", return_value=False) as ask:
            self.panel.import_summary(plain)
        ask.assert_called_once()
        self.assertIsNone(self.panel.saved)
        with mock.patch("ui_handoff.messagebox.askyesno", return_value=True):
            self.panel.import_summary(plain)
        self.assertIsNotNone(self.panel.saved)
        old_summary = self.panel._summary_value()
        wrong = self.app.handoff_store.save(
            {**self.record, "path": str(self.base / "different")}, self.summary)
        with self.assertRaises(HandoffError):
            self.panel.import_summary(wrong["path"])
        self.assertEqual(self.panel._summary_value(), old_summary)

    def test_copy_custom_old_prompt_preserves_user_edit(self):
        self.select()
        self.panel.generate_old()
        self.panel.old_text.insert("end", "\n额外要求：先核对验收。")
        self.assertTrue(self.panel.has_unsaved())
        self.panel.copy_old()
        self.assertTrue(self.clipboard[-1].endswith("额外要求：先核对验收。"))
        self.assertFalse(self.panel.has_unsaved())

    def test_knowledge_scope_follows_bound_project_and_checked_categories(self):
        self.select()
        self.assertEqual(self.panel.policy.get(), "none")
        notes = self.base / "notes"
        notes.mkdir()
        self.app.prefs.update(knowledge_mode="folder", knowledge_dir=str(notes),
                              knowledge_categories=["PLAN"])
        self.panel.refresh_preferences()
        self.assertEqual(self.panel.policy.get(), "none")
        self.app.prefs["knowledge_project_key"] = project_key(self.record)
        self.panel.refresh_preferences()
        self.assertEqual(self.panel.policy.get(), "ask")
        prompt = self.panel.generate_old()
        self.assertIn("询问我是否更新", prompt)
        self.assertIn("01-总体计划.md", prompt)
        self.assertNotIn("02-当前进度.md", prompt)
        self.assertEqual(list(notes.iterdir()), [])
        self.panel.set_record(dict(self.record, path=str(self.base / "another project")))
        self.assertEqual(self.panel.policy.get(), "none")
        self.assertNotIn(str(notes), self.panel.generate_old())

    def test_knowledge_dropdown_keyboard_updates_the_prompt_policy(self):
        self.select()
        notes = self.base / "notes"
        notes.mkdir()
        self.app.prefs.update(knowledge_mode="folder", knowledge_dir=str(notes),
                              knowledge_categories=["PLAN"],
                              knowledge_project_key=project_key(self.record))
        self.panel.refresh_preferences()
        self.panel.old_options_visible.set(True)
        self.panel._toggle_old_options()
        self.root.update()
        box = self.panel.policy_box
        box.focus_force()
        box.event_generate("<Down>")
        self.root.update()
        popup = box.popup
        self.assertTrue(popup.winfo_viewable())
        rows = next(child for child in popup.winfo_children() if isinstance(child, tk.Listbox))
        rows.selection_clear(0, "end")
        rows.selection_set(0)
        rows.focus_force()
        rows.event_generate("<Return>")
        self.root.update()
        self.assertEqual(self.panel.policy_label.get(), POLICY_LABELS["none"])
        self.assertEqual(self.panel.policy.get(), "none")
        self.assertFalse(popup.winfo_exists())

    def test_template_editor_saves_guidance_and_preserves_identity(self):
        self.select()
        self.panel.edit_templates()
        box = self.panel._template_boxes["save"]
        box.delete("1.0", "end")
        box.insert("1.0", "用简短列表记录项目目的与下一步。")
        window = self.panel._template_dialog
        def descendants(parent):
            for child in parent.winfo_children():
                yield child
                yield from descendants(child)
        save = next(w for w in descendants(window)
                    if isinstance(w, ttk.Button) and w.cget("text") == "保存模板")
        save.invoke()
        self.assertIsNone(self.panel._template_dialog)
        self.assertEqual(self.app.template_store.load()["save"], "用简短列表记录项目目的与下一步。")
        prompt = self.panel.generate_old()
        self.assertIn(self.record["path"], prompt)
        self.assertIn("用简短列表", prompt)

    def test_narrow_layout_buttons_remain_reachable_with_scroll(self):
        self.select()
        self.root.geometry("650x560")
        self.panel.show_step(2)
        self.root.update()
        self.panel.canvas.yview_moveto(1)
        self.root.update()
        self.assertTrue(self.panel._narrow_mode)
        self.assertLessEqual(self.panel.save_button.winfo_rootx() + self.panel.save_button.winfo_width(),
                             self.root.winfo_rootx() + self.root.winfo_width())
        self.assertLessEqual(self.panel.save_button.winfo_rooty() + self.panel.save_button.winfo_height(),
                             self.root.winfo_rooty() + self.root.winfo_height())
        self.assertTrue(self.panel.save_button.winfo_ismapped())
        self.assertEqual(self.panel.save_button.master.master, self.panel.footer)
        bounds = self.panel.canvas.bbox("all")
        self.assertIsNotNone(bounds)
        if bounds[3] - bounds[1] > self.panel.canvas.winfo_height():
            # The body only needs to scroll when it exceeds the available area.
            self.assertGreater(self.panel.canvas.yview()[0], 0)
        self.assertAlmostEqual(self.panel.canvas.yview()[1], 1.0, places=2)

    def test_primary_actions_remain_fixed_while_body_scrolls(self):
        self.select()
        self.root.geometry("980x680")
        self.enter_summary()
        self.panel.save_summary()
        for step, button in ((1, self.panel.copy_old_button),
                             (2, self.panel.save_button),
                             (3, self.panel.copy_new_button)):
            with self.subTest(step=step):
                self.panel.show_step(step)
                self.root.update()
                self.assertTrue(button.winfo_ismapped())
                y_before = button.winfo_rooty()
                self.panel.canvas.yview_moveto(1)
                self.root.update()
                self.assertEqual(button.winfo_rooty(), y_before)
                self.assertGreaterEqual(button.winfo_rooty(), self.root.winfo_rooty())
                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                     self.root.winfo_rooty() + self.root.winfo_height())
                self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                                     self.root.winfo_rootx() + self.root.winfo_width())
                self.assertEqual(button.master.master, self.panel.footer)

    def test_each_step_shows_editor_and_primary_action_at_desktop_scales(self):
        self.select()
        self.enter_summary()
        self.root.geometry("760x430")
        for dpi in (96, 144, 192):
            self.root.tk.call("tk", "scaling", dpi / 72)
            for step, editor, button in ((1, self.panel.old_text, self.panel.copy_old_button),
                                         (2, self.panel.summary_text, self.panel.save_button),
                                         (3, self.panel.new_text, self.panel.copy_new_button)):
                with self.subTest(dpi=dpi, step=step):
                    self.panel.show_step(step)
                    self.root.update()
                    canvas_top = self.panel.canvas.winfo_rooty()
                    canvas_bottom = canvas_top + self.panel.canvas.winfo_height()
                    editor_top = editor.winfo_rooty()
                    self.assertTrue(editor.winfo_ismapped())
                    self.assertGreaterEqual(editor_top, canvas_top)
                    self.assertLess(editor_top + 24, canvas_bottom)
                    self.assertTrue(button.winfo_ismapped())
                    self.assertGreaterEqual(button.winfo_rooty(), self.root.winfo_rooty())
                    self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                         self.root.winfo_rooty() + self.root.winfo_height())


if __name__ == "__main__":
    unittest.main()

