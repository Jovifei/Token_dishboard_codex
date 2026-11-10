"""Real Tk regressions for stable motion and the branded compact monitor.

Synthetic data only. Font scaling checks are not native Windows DPI evidence.
"""
import os
from pathlib import Path
import struct
import tempfile
import time
import tkinter as tk
from tkinter import font, ttk
import unittest
from unittest.mock import patch
import zlib

from ui_brand import BrandMark, _rgba, icon_png, set_window_icon, write_ico
from ui_motion import attach_motion_tree, attach_soft_motion, reduced_motion, set_reduced_motion
from ui_monitor import FloatingMonitor
from test_ui_monitor import FakeApp, RECORD, OTHER, row


class IconPolishTests(unittest.TestCase):
    def test_small_icon_edges_have_partial_alpha(self):
        data = _rgba(22)
        self.assertTrue(any(0 < a < 255 for a in data[3::4]))
        self.assertEqual(data[3], 0)

    def test_png_dimensions_and_crc(self):
        for size in (16, 22, 28, 40, 64, 128, 256):
            with self.subTest(size=size):
                data = icon_png(size)
                self.assertEqual(struct.unpack('>II', data[16:24]), (size, size))
                offset = 8
                while offset < len(data):
                    length = struct.unpack('>I', data[offset:offset+4])[0]
                    kind = data[offset+4:offset+8]
                    payload = data[offset+8:offset+8+length]
                    crc = struct.unpack('>I', data[offset+8+length:offset+12+length])[0]
                    self.assertEqual(crc, zlib.crc32(kind + payload) & 0xffffffff)
                    offset += length + 12
                self.assertEqual(offset, len(data))

    def test_invalid_sizes_fail_explicitly(self):
        for size in (0, 15, 257, True, 22.5, '32'):
            with self.subTest(size=size), self.assertRaises(ValueError):
                icon_png(size)

    def test_ico_has_six_valid_png_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            data = write_ico(Path(folder) / 'candidate.ico').read_bytes()
        self.assertEqual(struct.unpack('<HHH', data[:6]), (0, 1, 6))
        for i, size in enumerate((16, 32, 48, 64, 128, 256)):
            w, h, _, _, planes, depth, length, offset = struct.unpack('<BBBBHHII', data[6+16*i:22+16*i])
            self.assertEqual((w or 256, h or 256, planes, depth), (size, size, 1, 32))
            self.assertEqual(data[offset:offset+length], icon_png(size))


class TkPolishTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('620x420+20+20')
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(str(args))
        self.style = ttk.Style(self.root)
        self.style.theme_use('clam')
        self.style.configure('TButton', padding=(12, 7), font=('DejaVu Sans', 10),
                             relief='flat', borderwidth=0, focusthickness=2)
        self.style.configure('Primary.TButton', background='#007AFF', foreground='white')
        self.style.map('Primary.TButton', foreground=[('disabled', '#8A93A0'), ('!disabled', 'white')])
        self.calls = []
        self.button = attach_soft_motion(ttk.Button(self.root, text='Copy handoff',
            style='Primary.TButton', command=lambda: self.calls.append('click')))
        self.button.pack(pady=18)
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def pump(self, seconds=.15):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.root.update()
            time.sleep(.005)
        self.root.update()

    def geometry(self):
        b = self.button
        return b.winfo_x(), b.winfo_y(), b.winfo_width(), b.winfo_height(), b.winfo_reqwidth(), b.winfo_reqheight()

    def test_geometry_unchanged_through_entire_pointer_cycle(self):
        before = self.geometry()
        for event in ('<Enter>', '<ButtonPress-1>', '<ButtonRelease-1>', '<Leave>'):
            self.button.event_generate(event, x=15, y=15)
            self.pump()
            self.assertEqual(self.geometry(), before, event)

    def test_full_source_style_metrics_and_foreground_inherit(self):
        original = {k: self.style.lookup('Primary.TButton', k)
                    for k in ('padding', 'font', 'focusthickness', 'borderwidth', 'foreground')}
        self.button._qingxu_motion.enter()
        private = self.button.cget('style')
        self.assertTrue(private.endswith('.Primary.TButton'))
        for key, value in original.items():
            self.assertEqual(self.style.lookup(private, key), value, key)
        self.assertEqual(self.style.lookup(private, 'foreground', ('disabled',)), '#8A93A0')

    def test_press_is_flat_and_does_not_shift(self):
        motion = self.button._qingxu_motion
        motion.press()
        self.assertEqual(self.style.lookup(motion.private, 'relief', ('pressed',)), 'flat')
        self.assertEqual(int(self.style.lookup(motion.private, 'shiftrelief')), 0)

    def test_color_transition_finishes_and_stops_scheduling(self):
        motion = self.button._qingxu_motion
        motion.enter()
        start = motion.current
        target = motion._target()
        self.pump(.055)
        middle = motion.current
        self.pump(.13)
        self.assertNotEqual(start, middle)
        self.assertNotEqual(middle, target)
        self.assertEqual(motion.current, target)
        self.assertIsNone(motion.job)

    def test_motion_handlers_do_not_invoke_commands(self):
        motion = self.button._qingxu_motion
        motion.enter(); motion.press(); motion.release(); motion.leave()
        self.pump()
        self.assertEqual(self.calls, [])

    def test_pointer_invokes_native_command_exactly_once(self):
        self.button.event_generate('<Enter>')
        self.button.event_generate('<ButtonPress-1>', x=12, y=12)
        self.button.event_generate('<ButtonRelease-1>', x=12, y=12)
        self.pump()
        self.assertEqual(self.calls, ['click'])

    def test_keyboard_space_keeps_native_single_activation(self):
        self.root.focus_force(); self.button.focus_force(); self.root.update()
        self.button.event_generate('<KeyPress-space>')
        self.button.event_generate('<KeyRelease-space>')
        self.pump()
        self.assertEqual(self.calls, ['click'])

    def test_disabled_button_never_activates_or_schedules(self):
        self.button.state(['disabled'])
        self.button._qingxu_motion.enter()
        self.button._qingxu_motion.press()
        self.button.invoke()
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.button._qingxu_motion.job)

    def test_disabling_mid_animation_restores_source(self):
        motion = self.button._qingxu_motion
        motion.enter(); self.button.state(['disabled']); self.pump()
        self.assertIsNone(motion.job)
        self.assertEqual(self.button.cget('style'), 'Primary.TButton')

    def test_command_changed_variant_is_not_overwritten(self):
        self.button._qingxu_motion.enter()
        self.style.configure('Selected.Nav.TButton', background='#FFFFFF')
        self.button.configure(style='Selected.Nav.TButton')
        self.pump()
        self.assertEqual(self.button.cget('style'), 'Selected.Nav.TButton')

    def test_destroy_cancels_timer_and_registry_entry(self):
        motion = self.button._qingxu_motion
        motion.enter(); self.button.destroy(); self.pump()
        self.assertIsNone(motion.job)
        self.assertNotIn(motion, self.root._qingxu_motion_live)

    def test_unmap_cancels_timer(self):
        motion = self.button._qingxu_motion
        motion.enter(); self.button.pack_forget(); self.root.update()
        self.assertIsNone(motion.job)
        self.assertEqual(self.button.cget('style'), 'Primary.TButton')

    def test_reopened_dialog_reuses_private_style(self):
        first = tk.Toplevel(self.root)
        one = attach_soft_motion(ttk.Button(first, text='Save', style='Primary.TButton'))
        one.pack(); self.root.update(); one._qingxu_motion.enter()
        private = one.cget('style'); first.destroy(); self.root.update()
        second = tk.Toplevel(self.root)
        two = attach_soft_motion(ttk.Button(second, text='Save', style='Primary.TButton'))
        two.pack(); self.root.update(); two._qingxu_motion.enter()
        self.assertEqual(two.cget('style'), private)
        second.destroy()

    def test_attachment_is_idempotent(self):
        motion = self.button._qingxu_motion
        attach_motion_tree(self.root); attach_motion_tree(self.root)
        self.assertIs(self.button._qingxu_motion, motion)
        self.assertEqual(len(self.root._qingxu_motion_live), 1)

    def test_reduce_motion_environment_is_instant(self):
        with patch.dict(os.environ, {'QINGXU_REDUCE_MOTION': '1'}):
            self.button._qingxu_motion.enter()
            self.assertIsNone(self.button._qingxu_motion.job)
            self.assertTrue(reduced_motion(self.root))

    def test_session_toggle_stops_inflight_motion(self):
        motion = self.button._qingxu_motion
        motion.enter(); set_reduced_motion(self.root, True)
        self.assertIsNone(motion.job)
        self.assertTrue(reduced_motion(self.root))

    def test_logo_photo_is_shared_by_widgets_and_dialogs(self):
        a, b = BrandMark(self.root, size=28), BrandMark(self.root, size=28)
        self.assertIs(a.image, b.image)
        win = tk.Toplevel(self.root)
        main = set_window_icon(self.root)
        self.assertIs(set_window_icon(win), main)
        win.destroy()
        self.assertEqual(main.width(), 64)

    def test_compact_long_percentage_and_scope_fit(self):
        app = FakeApp(self.root)
        f = app.floating
        f.render([row() | {'occupancy_percent': 100, 'progress_percent': 100, 'state': 'stale'}])
        f.show(); self.root.update(); f.toggle_compact(); self.root.update()
        self.assertEqual(f.mini_value.cget('text'), '100%·旧')
        self.assertGreaterEqual(f.mini_value.winfo_width(), f.mini_value.winfo_reqwidth())
        self.assertIn('所选', f.mini_scope.cget('text'))
        self.assertIn('最后已知值', f.menu.entrycget(0, 'label'))
        f.window.destroy()

    def test_compact_adapts_to_larger_font(self):
        app = FakeApp(self.root); f = app.floating
        f.render([row() | {'occupancy_percent': 100, 'state': 'stale'}])
        f.show(); self.root.update(); f.toggle_compact(); self.root.update()
        width = f.window.winfo_width()
        f.mini_value.configure(font=('Noto Sans CJK SC', 22, 'bold'))
        f.render(f.rows); self.root.update()
        self.assertGreater(f.window.winfo_width(), width)
        self.assertGreaterEqual(f.mini_value.winfo_width(), f.mini_value.winfo_reqwidth())
        self.assertGreaterEqual(f.window.winfo_height(), f.mini.winfo_reqheight())
        f.window.destroy()

    def test_compact_unknown_is_not_zero(self):
        app = FakeApp(self.root); f = app.floating
        f.show(); f.toggle_compact(); f.render([]); self.root.update()
        self.assertEqual(f.mini_value.cget('text'), '—')
        self.assertIsNone(f.mini_line.value)
        f.render([row(used=0)]); self.root.update()
        self.assertEqual(f.mini_value.cget('text'), '0%')
        self.assertEqual(f.mini_line.value, 0)
        f.window.destroy()

    def test_recent_fallback_discloses_scope_and_disables_handoff(self):
        app = FakeApp(self.root); f = app.floating
        f.render([row(OTHER)]); f.show(); f.toggle_compact(); self.root.update()
        self.assertIn('最近', f.mini_scope.cget('text'))
        self.assertTrue(f.prepare_button.instate(['disabled']))
        self.assertIn(OTHER['label'], f.menu.entrycget(0, 'label'))
        f.window.destroy()

    def test_hide_and_reopen_reuses_window(self):
        app = FakeApp(self.root); f = app.floating
        f.render([row()]); f.show(); self.root.update()
        f.toggle_compact(); self.root.update(); identity = str(f.window)
        f.close(); self.root.update(); self.assertIsNone(f._fit_job)
        f.show(); self.root.update()
        self.assertEqual(str(f.window), identity)
        self.assertTrue(self.root.winfo_exists())
        f.window.destroy()

    def test_menu_reduced_motion_does_not_change_application_preferences(self):
        app = FakeApp(self.root); f = app.floating
        before = dict(app.prefs)
        f.show(); f.menu.invoke(f.menu.index('end')); self.root.update()
        self.assertTrue(reduced_motion(self.root))
        self.assertEqual(app.prefs, before)
        f.window.destroy()


if __name__ == '__main__':
    unittest.main()
