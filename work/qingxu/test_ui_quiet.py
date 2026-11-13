"""Quiet UI regression checks, synthetic Tk display only."""
import struct
import tkinter as tk
from tkinter import ttk
import unittest

from ui_controls import install_controls
from ui_motion import attach_motion_tree
from ui_quiet import install_quiet, decorate_panels, surface_png, BLUE, LINE, SURFACE


class SurfaceTests(unittest.TestCase):
    def test_surface_is_valid_small_png(self):
        raw=surface_png(SURFACE,LINE,10)
        self.assertEqual(raw[:8],b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II',raw[16:24]),(25,25))

    def test_paint_assets_are_cached(self):
        self.assertIs(surface_png(SURFACE,LINE),surface_png(SURFACE,LINE))

    def test_focus_is_not_the_default_surface(self):
        self.assertNotEqual(surface_png(SURFACE,LINE),surface_png(SURFACE,BLUE,border=2))


class QuietControlsTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.geometry('540x300+0+0')
        self.errors=[]
        self.root.report_callback_exception=lambda *a:self.errors.append(a)
        self.style=ttk.Style(self.root);self.style.theme_use('clam')
        install_controls(self.root);install_quiet(self.root,'DejaVu Sans')
        self.calls=[]
        self.button=ttk.Button(self.root,text='Continue',style='Primary.TButton',command=lambda:self.calls.append(1))
        self.button.pack(padx=20,pady=20)
        attach_motion_tree(self.root)
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.errors,[])

    def bounds(self,b=None):
        b=b or self.button
        return b.winfo_x(),b.winfo_y(),b.winfo_width(),b.winfo_height(),b.winfo_reqwidth(),b.winfo_reqheight()

    def test_all_states_hold_geometry(self):
        before=self.bounds()
        for state in ('active','pressed','focus','disabled'):
            self.button.state([state]);self.root.update_idletasks()
            self.assertEqual(self.bounds(),before,state)
            self.button.state(['!'+state])

    def test_entire_pointer_cycle_invokes_once(self):
        before=self.bounds()
        for event in ('<Enter>','<ButtonPress-1>','<ButtonRelease-1>','<Leave>'):
            self.button.event_generate(event,x=12,y=12)
            self.root.update()
            self.assertEqual(self.bounds(),before)
        self.assertEqual(self.calls,[1])

    def test_keyboard_space_invokes_once_without_changing_geometry(self):
        self.button.focus_force();self.root.update()
        before=self.bounds()
        self.button.event_generate('<KeyPress-space>')
        self.button.event_generate('<KeyRelease-space>')
        self.root.update()
        self.assertEqual(self.calls,[1])
        self.assertEqual(before,self.bounds())

    def test_pressed_and_disabled_corners_do_not_inherit_clam_beige(self):
        for state in ('pressed','disabled','active','focus'):
            self.assertEqual(self.style.lookup('Primary.TButton','background',(state,)),SURFACE)

    def test_dragging_out_cancels_native_click(self):
        self.button.event_generate('<Enter>',x=10,y=10)
        self.button.event_generate('<ButtonPress-1>',x=10,y=10)
        self.button.event_generate('<Leave>',x=-5,y=-5)
        self.button.event_generate('<ButtonRelease-1>',x=-5,y=-5)
        self.root.update()
        self.assertEqual(self.calls,[])

    def test_disabled_is_non_interactive(self):
        self.button.state(['disabled']);self.button.invoke()
        self.button.event_generate('<ButtonPress-1>',x=10,y=10)
        self.button.event_generate('<ButtonRelease-1>',x=10,y=10)
        self.root.update();self.assertEqual(self.calls,[])

    def test_legacy_motion_does_not_wrap_new_controls(self):
        attach_motion_tree(self.root)
        self.assertFalse(hasattr(self.button,'_qingxu_motion'))
        self.assertEqual(self.button.cget('style'),'Primary.TButton')

    def test_primary_uses_native_image_surface_not_clam_bevel(self):
        layout=str(self.style.layout('Primary.TButton'))
        self.assertIn('Quiet.',layout)
        self.assertNotIn('Button.border',layout)
        self.assertEqual(str(self.style.lookup('Primary.TButton','relief',('pressed',))),'flat')
        self.assertEqual(int(self.style.lookup('Primary.TButton','shiftrelief')),0)

    def test_styles_install_once_per_root(self):
        count=len(self.root._quiet_themes['clam'])
        install_quiet(self.root,'DejaVu Sans')
        self.assertEqual(len(self.root._quiet_themes['clam']),count)

    def test_panel_decoration_does_not_negotiate_geometry(self):
        panel=ttk.Frame(self.root,style='Panel.TFrame',padding=16)
        panel.pack();ttk.Label(panel,text='Native contents').pack()
        self.root.update_idletasks();before=self.bounds(panel)
        decorate_panels(panel);self.root.update_idletasks()
        self.assertEqual(before,self.bounds(panel))
        self.assertEqual(panel._quiet_surface.winfo_manager(),'place')
        self.assertFalse(int(panel._quiet_surface.cget('takefocus')))
        first=panel._quiet_surface;decorate_panels(panel)
        self.assertIs(panel._quiet_surface,first)

    def test_destroyed_panel_cancels_no_application_work(self):
        panel=ttk.Frame(self.root,style='Panel.TFrame');panel.pack()
        decorate_panels(panel);self.root.update();panel.destroy();self.root.update()
        self.assertTrue(self.root.winfo_exists())


if __name__=='__main__':unittest.main()
