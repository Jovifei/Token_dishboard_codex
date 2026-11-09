"""Presentation and real Tk control tests; Tk cases skip when no display exists."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import tkinter as tk
from tkinter import font as tkfont, ttk
import unittest

from monitor import MonitorService, assess
from ui_monitor import FloatingMonitor, UsagePanel, presentation


NOW = datetime.now(timezone.utc)
RECORD = {"chat_id": "first", "label": "蓝图 · 当前任务", "path": "/project/blueprint",
          "updated_at": 20, "app_project_id": "p1", "app_project_name": "示例蓝图"}
OTHER = RECORD | {"chat_id": "second", "label": "另一条任务", "updated_at": 21}


def row(record=RECORD, used=80, *, when=NOW):
    result = assess({"time": when, "usage": {"totalTokens": used}, "window": 200, "runtime_limit": 100}, now=NOW)
    return result | {"chat_id": record["chat_id"], "record": record, "title": record["label"],
                     "project": record["app_project_name"], "selected": record is RECORD}


class PresentationTests(unittest.TestCase):
    def test_unknown_and_real_zero_are_visibly_different(self):
        self.assertEqual(presentation(None)["percent"], "—")
        self.assertIsNone(presentation(None)["progress"])
        self.assertEqual(presentation(row(used=0))["percent"], "0.0%")

    def test_stale_progress_is_explicitly_last_known(self):
        value = presentation(row(when=NOW - timedelta(hours=1)))
        self.assertEqual(value["percent"], "上次 80.0%")
        self.assertEqual(value["status"], "最后已知值")

    def test_unknown_threshold_can_still_have_a_measured_ratio(self):
        value = presentation(row() | {"state": "unknown", "soft_limit": None})
        self.assertEqual(value["status"], "提醒线未知")
        self.assertEqual(value["percent"], "80.0%")

    def test_pause_is_visible_without_erasing_the_measurement(self):
        for state in ("ready", "unknown"):
            with self.subTest(state=state):
                value = presentation(row() | {"enabled": False, "state": state})
                self.assertEqual(value["percent"], "80.0%")
                self.assertIn("暂停", value["status"])


class FakeApp:
    def __init__(self, root):
        self.root, self.chat, self.prefs = root, RECORD, {}
        self.relay = SimpleNamespace(settings={"chat_db": "/not-used.sqlite", "usage": "/not-used.json"})
        self.monitor = MonitorService("/not-used.sqlite")
        self.data_root = "/not-used"
        self.status, self.page, self.refused = "", None, False
        self.refreshes, self.snapshot_refreshes = 0, 0
        self.floating = FloatingMonitor(self)

    def set_status(self, text):
        self.status = text

    def show_page(self, key):
        self.page = key

    def choose_record(self, record):
        if not self.refused:
            self.chat = record

    def refresh_metrics(self):
        self.refreshes += 1

    def refresh_usage(self):
        self.snapshot_refreshes += 1

    def open_path(self, path):
        self.opened = path


class TkControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest("无可用 Tk 显示环境：" + str(exc))
        cls.root.geometry("1000x720+20+20")
        style = ttk.Style(cls.root)
        style.theme_use("clam")
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        style.configure(".", font=(family, 11))
        style.configure("TButton", padding=(12, 7), font=(family, 10), borderwidth=0)
        style.configure("Primary.TButton", padding=(12, 7), font=(family, 10, "bold"))
        style.configure("Heading.TLabel", font=(family, 13, "bold"))
        style.configure("Muted.TLabel", font=(family, 10))
        style.configure("CardMuted.TLabel", font=(family, 10))

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.app = FakeApp(self.root)
        self.panel = UsagePanel(self.root, self.app)
        self.panel.pack(fill="both", expand=True)
        self.root.update_idletasks()

    def tearDown(self):
        if self.app.floating.window is not None:
            self.app.floating.window.destroy()
        self.panel.destroy()
        self.root.update_idletasks()

    def test_three_views_and_handoff_button_states(self):
        self.assertEqual(len(self.panel.tabs.tabs()), 3)
        self.assertFalse(hasattr(self.panel, "project_choice"))
        self.panel.set_record(None)
        self.assertTrue(self.panel.prepare_button.instate(["disabled"]))
        self.panel.set_record(RECORD)
        self.panel.prepare_button.invoke()
        self.assertEqual(self.app.page, "handoff")

    def test_old_result_cannot_fill_a_new_chat_progress(self):
        self.panel.set_record(RECORD)
        self.panel.render([row()])
        self.assertEqual(self.panel.hero_percent.cget("text"), "80.0%")
        self.panel.set_record(OTHER)
        self.panel.render([row()])
        self.assertEqual(self.panel.hero_percent.cget("text"), "—")

    def test_catalog_preserves_the_original_detailed_tree(self):
        catalog = {"app_projects": [{"app_project_id": "p1", "label": "示例蓝图"}], "chats": [RECORD, OTHER]}
        self.panel.render_catalog(catalog)
        self.panel.set_record(RECORD)
        self.assertEqual(self.panel.chat_usage.active, RECORD["chat_id"])
        self.assertEqual(len(self.panel.chat_usage.chats), 2)

    def test_refresh_disables_until_result_or_timeout(self):
        self.panel.refresh_button.invoke()
        self.assertEqual(self.app.refreshes, 1)
        self.assertTrue(self.panel.refresh_button.instate(["disabled"]))
        self.panel.render([])
        self.assertFalse(self.panel.refresh_button.instate(["disabled"]))

    def test_missing_snapshot_clears_old_values(self):
        self.panel.dashboard.cards["totalTokens"].set("123456")
        self.panel.snapshot_disabled()
        self.assertEqual(self.panel.dashboard.cards["totalTokens"].get(), "—")
        self.assertIn("基础监测和交接", self.panel.dashboard.info.get())

    def test_wide_overview_shows_four_rows_and_reflows_when_narrow(self):
        original = self.root.geometry()
        try:
            self.root.geometry("1024x560+20+20")
            records = [RECORD, OTHER, RECORD | {"chat_id": "third"}, RECORD | {"chat_id": "fourth"}]
            self.panel.set_record(RECORD)
            self.panel.render([row(record) for record in records])
            self.root.update()
            self.assertGreaterEqual(self.panel.overview_body.winfo_width(), 900)
            self.assertTrue(self.panel._overview_wide)
            self.assertGreater(self.panel.recent_section.winfo_x(), self.panel.hero.winfo_x())
            canvas = self.panel.overview_canvas
            bottom = canvas.winfo_rooty() + canvas.winfo_height()
            for widget in (*self.panel.recent_rows, self.panel.prepare_button):
                self.assertTrue(widget.winfo_ismapped())
                self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom)
            self.root.geometry("760x560+20+20")
            self.root.update()
            self.assertLess(self.panel.overview_body.winfo_width(), 900)
            self.assertFalse(self.panel._overview_wide)
            self.assertGreaterEqual(self.panel.recent_section.winfo_y(),
                                    self.panel.hero.winfo_y() + self.panel.hero.winfo_height())
            self.assertTrue(canvas.cget("scrollregion"))
            self.assertLess(canvas.yview()[1], 1.0)
            canvas.yview_moveto(1)
            self.root.update()
            self.assertEqual(canvas.yview()[1], 1.0)
            self.assertGreaterEqual(self.panel.recent_rows[-1].winfo_rooty(), canvas.winfo_rooty())
            self.assertLessEqual(self.panel.recent_rows[-1].winfo_rooty()
                                 + self.panel.recent_rows[-1].winfo_height(),
                                 canvas.winfo_rooty() + canvas.winfo_height())
        finally:
            self.root.geometry(original)
            self.root.update()

    def test_float_selection_recent_mode_and_declined_switch(self):
        floating = self.app.floating
        floating.render([row(), row(OTHER)])
        floating.show()
        self.assertEqual(len(floating._shown()), 2)
        floating._set_mode("selected")
        self.assertEqual([r["chat_id"] for r in floating._shown()], ["first"])
        self.app.refused = True
        floating._choose(OTHER)
        self.assertEqual(self.app.chat["chat_id"], "first")
        self.assertIn("未切换", floating.feedback.get())

    def test_float_hide_and_compact_do_not_close_root(self):
        floating = self.app.floating
        floating.render([row()])
        floating.show()
        self.root.update_idletasks()
        self.assertGreater(floating.window.winfo_x(), 0)
        identity = str(floating.window)
        floating.toggle_compact()
        self.root.update_idletasks()
        self.assertTrue(floating.compact)
        self.assertEqual(floating.window.winfo_width(), 120)
        floating.close()
        self.assertEqual(floating.window.state(), "withdrawn")
        self.assertTrue(self.root.winfo_exists())
        floating.show()
        self.assertEqual(str(floating.window), identity)
        floating.toggle_compact()
        self.assertFalse(floating.compact)

    def test_snooze_only_targets_the_selected_visible_chat(self):
        floating = self.app.floating
        floating.render([row(), row(OTHER)])
        floating.show()
        floating._snooze()
        self.assertIn("first", self.app.monitor._snoozed)
        self.assertNotIn("second", self.app.monitor._snoozed)
        self.app.chat = None
        floating.render([row(), row(OTHER)])
        self.assertTrue(floating.snooze_button.instate(["disabled"]))

    def test_float_four_rows_and_actions_fit(self):
        floating = self.app.floating
        records = [RECORD, OTHER, RECORD | {"chat_id": "third"}, RECORD | {"chat_id": "fourth"}]
        floating.render([row(record) for record in records])
        floating.show()
        self.root.update_idletasks()
        self.assertEqual(len(floating._shown()), 4)
        last = floating.row_widgets[3]
        self.assertTrue(last.winfo_ismapped())
        self.assertLessEqual(last.winfo_y() + last.winfo_height(), floating.list_frame.winfo_height())
        self.assert_float_actions_inside(floating)
        self.assertGreaterEqual(floating.window.winfo_height(), floating.normal.winfo_reqheight())
        floating.window.geometry("370x300")
        self.root.update_idletasks()
        self.assert_float_actions_inside(floating)
        self.assertGreaterEqual(floating.window.winfo_height(), floating.normal.winfo_reqheight())
        floating.toggle_compact()
        self.root.update_idletasks()
        floating.toggle_compact()
        self.root.update_idletasks()
        self.assert_float_actions_inside(floating)

    def assert_float_actions_inside(self, floating):
        top = floating.window.winfo_rooty()
        bottom = top + floating.window.winfo_height()
        for button in (floating.prepare_button, floating.snooze_button):
            self.assertTrue(button.winfo_ismapped())
            self.assertGreaterEqual(button.winfo_rooty(), top)
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), bottom)
            self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight())


if __name__ == "__main__":
    unittest.main()
