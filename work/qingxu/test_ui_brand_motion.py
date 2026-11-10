"""Synthetic Tk checks for the Apple-style icon and button motion."""
import os
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from pathlib import Path
from ui_brand import icon_png, icon_svg, write_ico, BrandMark, set_window_icon
from ui_motion import attach_soft_motion, attach_motion_tree


class BrandTests(unittest.TestCase):
    def test_png_and_svg(self):
        self.assertEqual(icon_png(32)[:8],b'\x89PNG\r\n\x1a\n')
        self.assertIn('<svg',icon_svg())
        self.assertRaises(ValueError,icon_png,512)

    def test_ico(self):
        with tempfile.TemporaryDirectory() as path:
            ico=write_ico(Path(path)/'qingxu.ico')
            self.assertEqual(ico.read_bytes()[:4],b'\0\0\x01\0')
            self.assertGreater(ico.stat().st_size,500)


@unittest.skipIf(not(os.environ.get('DISPLAY') or os.name=='nt'),'Tk display unavailable')
class MotionTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk()
        self.root.geometry('320x180')
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def test_no_depth_or_geometry_change(self):
        style=ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Primary.TButton',background='#087CF7',relief='flat',borderwidth=0)
        button=attach_soft_motion(ttk.Button(self.root,text='保存',style='Primary.TButton'))
        button.pack(padx=10,pady=10)
        self.root.update()
        x,y,w,h=button.winfo_x(),button.winfo_y(),button.winfo_width(),button.winfo_height()
        button.event_generate('<Enter>')
        self.root.update()
        button.event_generate('<ButtonPress-1>')
        button.event_generate('<ButtonRelease-1>')
        self.root.update()
        self.assertEqual((x,y,w,h),(button.winfo_x(),button.winfo_y(),button.winfo_width(),button.winfo_height()))
        motion=button._qingxu_motion
        self.assertEqual(str(style.lookup(motion.private,'relief')),'flat')
        button.event_generate('<Leave>')
        self.root.update()
        self.assertTrue(button.winfo_exists())

    def test_brand_window_and_tree(self):
        frame=ttk.Frame(self.root)
        frame.pack()
        mark=BrandMark(frame,size=32)
        mark.pack()
        button=ttk.Button(frame,text='打开')
        button.pack()
        attach_motion_tree(frame)
        self.assertTrue(hasattr(button,'_qingxu_motion'))
        set_window_icon(self.root)
        self.assertTrue(hasattr(self.root,'_qingxu_icon_photo'))


if __name__=='__main__':unittest.main()
