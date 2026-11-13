"""Quiet UI: one native ttk design system, with no mechanical press animation.

Image-backed 9-slice borders replace clam bevels. All states use the same
geometry. Commands, focus, navigation and accessibility remain native ttk.
Only the small paint assets are generated; no third-party runtime dependency.
"""
from __future__ import annotations
import base64
from functools import lru_cache
import math
import struct
import tkinter as tk
from tkinter import ttk
import zlib

CANVAS = '#F4F6F9'
SURFACE = '#FFFFFF'
SIDEBAR = '#F8F9FC'
INK = '#17202F'
MUTED = '#626F83'
LINE = '#DEE4EE'
BLUE = '#2563EB'
TINT = '#EAF1FF'
GREEN = '#237653'
AMBER = '#966318'
RED = '#B83A4B'


def _rgb(color):
    return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))


def _chunk(kind, data):
    return (struct.pack('>I', len(data)) + kind + data +
            struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))


@lru_cache(maxsize=160)
def surface_png(fill, stroke, radius=10, border=1):
    """Antialiased alpha-rounded tile. Small corners stretch without blur."""
    size = radius * 2 + 5
    rgb, edge = _rgb(fill), _rgb(stroke)
    rgba = bytearray()
    # Analytic signed distance, pixel-centre coverage; constant 1px border.
    half = size / 2
    def coverage(x, y, inset):
        r = max(0, radius-inset)
        qx = abs(x-half) - (half-inset-r)
        qy = abs(y-half) - (half-inset-r)
        d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
        return max(0., min(1., .5-d))
    for y in range(size):
        for x in range(size):
            outer = coverage(x+.5, y+.5, 0)
            inner = coverage(x+.5, y+.5, border)
            mix = inner / outer if outer else 0
            rgba.extend(round(edge[i]*(1-mix) + rgb[i]*mix) for i in range(3))
            rgba.append(round(255*outer))
    raw = b''.join(b'\0'+rgba[y*size*4:(y+1)*size*4] for y in range(size))
    return (b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)) +
            _chunk(b'IDAT', zlib.compress(raw)) + _chunk(b'IEND', b''))


def install_quiet(widget, family=None):
    """Install after the legacy base styles, once per Tcl interpreter/theme."""
    root = widget._root()
    root._qingxu_quiet = True
    style = ttk.Style(root)
    key = style.theme_use()
    if key in getattr(root, '_quiet_themes', {}):
        return
    family = family or 'TkDefaultFont'
    root._quiet_themes = getattr(root, '_quiet_themes', {})
    images = root._quiet_themes[key] = []
    def image(fill, edge, r=10, border=1):
        p = tk.PhotoImage(master=root, data=base64.b64encode(surface_png(fill, edge, r, border)))
        images.append(p)
        return p
    def button(name, fill, edge, hover, hover_edge, *, text=INK, radius=9, selected=None, padding=(14, 8)):
        element = 'Quiet.' + name.replace('.', '_')
        # Disabled always wins. Pressed keeps the hover fill: only its outline
        # changes. No white flash, label movement, timer, scale or fake depth.
        states = [('disabled', image('#F1F3F7', '#E5E9F0', radius)),
                  ('pressed', image(hover, BLUE if fill != BLUE else '#194CB9', radius)),
                  ('focus', image(fill, '#102D64' if fill == BLUE else BLUE, radius, border=2))]
        if selected is not None:
            states.append(('selected', image(selected, selected, radius)))
        states.append(('active', image(hover, hover_edge, radius)))
        style.element_create(element, 'image', image(fill, edge, radius), *states,
                             border=radius+1, padding=0, sticky='nsew')
        style.layout(name, [(element, {'sticky':'nsew', 'children':[
            ('Button.padding', {'sticky':'nsew', 'children':[
                ('Button.label', {'sticky':'nsew'})]})]})])
        style.configure(name, background=(SIDEBAR if name.startswith('Sidebar.') else CANVAS if name=='Ghost.TButton' else SURFACE), foreground=text, padding=padding,
                        relief='flat', shiftrelief=0, borderwidth=0, width=0,
                        font=(family, 10), anchor='center', focusthickness=0)
        backdrop = SIDEBAR if name.startswith('Sidebar.') else CANVAS if name=='Ghost.TButton' else SURFACE
        style.map(name, foreground=[('disabled', '#94A0B1'), ('!disabled', text)],
                  background=[('disabled', backdrop), ('!disabled', backdrop)],
                  relief=[('pressed', 'flat'), ('!disabled', 'flat')])
        return element

    style.configure('.', background=CANVAS, foreground=INK, font=(family, 10))
    for name, color in [('TFrame', CANVAS), ('App.TFrame', CANVAS), ('Card.TFrame', SURFACE), ('Side.TFrame', SIDEBAR)]:
        style.configure(name, background=color, relief='flat', borderwidth=0)
    for name, bg, color, size, weight in (
        ('TLabel', CANVAS, INK, 10, 'normal'), ('Title.TLabel', CANVAS, INK, 23, 'bold'),
        ('Section.TLabel', CANVAS, INK, 13, 'bold'), ('Heading.TLabel', SURFACE, INK, 13, 'bold'),
        ('CardTitle.TLabel', SURFACE, INK, 12, 'bold'), ('Card.TLabel', SURFACE, INK, 10, 'normal'),
        ('Muted.TLabel', CANVAS, MUTED, 9, 'normal'), ('CardMuted.TLabel', SURFACE, MUTED, 9, 'normal'),
        ('Side.TLabel', SIDEBAR, MUTED, 9, 'normal'), ('Eyebrow.TLabel', CANVAS, MUTED, 8, 'bold'),
        ('SideSection.TLabel', SIDEBAR, '#8792A2', 8, 'normal'), ('Number.TLabel', SURFACE, INK, 32, 'bold')):
        style.configure(name, background=bg, foreground=color, font=(family, size, weight))

    button('TButton', SURFACE, LINE, '#F5F8FF', '#C8D6EC')
    button('Outline.TButton', SURFACE, LINE, '#F5F8FF', '#C8D6EC')
    button('Primary.TButton', BLUE, BLUE, BLUE, BLUE, text='white')
    button('Ghost.TButton', CANVAS, CANVAS, '#EAF0F9', '#EAF0F9', text=MUTED)
    button('Soft.TButton', TINT, TINT, '#E2ECFF', '#E2ECFF', text=BLUE)
    button('Sidebar.Ghost.TButton', SIDEBAR, SIDEBAR, '#EEF1F7', '#EEF1F7', padding=(15, 12))
    button('Sidebar.Soft.TButton', TINT, TINT, TINT, TINT, text=BLUE, padding=(15, 12))
    for name in ('Sidebar.Ghost.TButton', 'Sidebar.Soft.TButton'):
        style.configure(name, anchor='w', font=(family, 10, 'bold' if '.Soft.' in name else 'normal'))

    outline = button('Drop.TButton', SURFACE, LINE, '#F5F8FF', '#C8D6EC', padding=(12, 7))
    style.layout('Drop.TButton', [(outline, {'sticky':'nsew', 'children':[
        ('Button.padding', {'sticky':'nsew', 'children':[
            ('QingxuDrop.chevron', {'side':'right','sticky':''}), ('Button.label', {'sticky':'nsew'})]})]})])
    style.configure('Drop.TButton', anchor='w')

    button('Toolbutton', '#EDF1F7', '#EDF1F7', '#E5EBF5', '#E5EBF5', selected=SURFACE, padding=(12, 7), text=MUTED)
    style.map('Toolbutton', foreground=[('disabled','#94A0B1'), ('selected', BLUE), ('!selected', MUTED)])
    button('HandoffStep.TButton', CANVAS, CANVAS, '#EAF0F9', '#EAF0F9', text=MUTED, padding=(12, 12))
    button('Selected.HandoffStep.TButton', TINT, TINT, TINT, TINT, text=BLUE, padding=(12, 12))
    button('MonitorMode.TButton', CANVAS, CANVAS, '#EAF0F9', '#EAF0F9', padding=(10, 6))
    button('Selected.MonitorMode.TButton', TINT, TINT, TINT, TINT, text=BLUE, padding=(10, 6))
    button('Mini.Monitor.TButton', SURFACE, SURFACE, '#F1F5FB', '#F1F5FB', padding=(4, 2), radius=6, text=MUTED)
    button('MonitorLink.TButton', SURFACE, SURFACE, '#F5F8FF', '#F5F8FF', padding=(2, 3), radius=6)
    style.configure('MonitorLink.TButton', anchor='w')
    style.configure('Compact.MonitorLink.TButton', padding=(2, 2), font=(family, 9))

    tile = image(SURFACE, LINE, 14)
    style.element_create('Quiet.Panel.border', 'image', tile, border=15, padding=0, sticky='nsew')
    style.layout('Panel.TFrame', [('Frame.border', {'sticky':'nsew'})])
    style.configure('Panel.TFrame', background=CANVAS, padding=18, relief='flat', borderwidth=0)
    tile = image(CANVAS, CANVAS, 2, 0)
    style.element_create('Quiet.Notebook.client', 'image', tile, sticky='nsew')
    style.layout('Shell.TNotebook', [('Quiet.Notebook.client', {'sticky':'nsew'})])
    style.layout('Shell.TNotebook.Tab', [])

    style.element_create('Quiet.tab', 'image', image(CANVAS, CANVAS, 8),
                         ('selected','focus',image(TINT,BLUE,8)),
                         ('selected',image(TINT,TINT,8)), ('focus',image(CANVAS,BLUE,8)),
                         ('active',image('#E9EDF5','#E9EDF5',8)), border=9, padding=0, sticky='nsew')
    tab_layout=[('Quiet.tab', {'sticky':'nsew','children':[
        ('Notebook.padding', {'sticky':'nsew','children':[('Notebook.label',{'sticky':'nsew'})]})]})]
    for name in ('TNotebook','Line.TNotebook'):
        style.layout(name, [('Quiet.Notebook.client',{'sticky':'nsew'})])
        style.configure(name, background=CANVAS, borderwidth=0, tabmargins=(0, 0, 0, 12))
        style.layout(name+'.Tab', tab_layout)
        style.configure(name+'.Tab', padding=(14,8), font=(family,10), foreground=MUTED)
        style.map(name+'.Tab', foreground=[('selected',BLUE),('!selected',MUTED)])
    style.layout('Shell.TNotebook', [('Quiet.Notebook.client', {'sticky':'nsew'})])
    style.layout('Shell.TNotebook.Tab', [])

    # Eliminate the native focus/pressed bevel on inputs too. Keep the standard
    # entry/combobox editing element so IME, selection and clipboard still work.
    style.element_create('Quiet.entry', 'image', image(SURFACE,LINE,8),
                         ('disabled',image('#F1F3F7',LINE,8)), ('focus',image(SURFACE,BLUE,8)),
                         border=9,sticky='nsew')
    style.layout('TEntry',[('Quiet.entry',{'sticky':'nsew','children':[
        ('Entry.padding',{'sticky':'nsew','children':[('Entry.textarea',{'sticky':'nsew'})]})]})])
    style.configure('TEntry',fieldbackground=SURFACE,foreground=INK,padding=(10,7),borderwidth=0)
    style.configure('Treeview',rowheight=36,background=SURFACE,fieldbackground=SURFACE,
                    foreground=INK,borderwidth=0,relief='flat')
    style.configure('Usage.Treeview',rowheight=36,background=SURFACE,fieldbackground=SURFACE)
    style.configure('Treeview.Heading',font=(family,9),background='#EEF2F8',foreground=MUTED,padding=(8,10),relief='flat')
    style.configure('Usage.Treeview.Heading',font=(family,9),foreground=MUTED,padding=(8,10))
    for name in ('Treeview','Usage.Treeview'):
        style.map(name,background=[('selected',TINT)],foreground=[('selected',INK)])
    style.configure('Horizontal.TProgressbar',background=BLUE,troughcolor='#E8EDF5',borderwidth=0)
    style.configure('Notice.TLabel',background=TINT,foreground='#31548B',padding=(14,10))
    style.configure('Alert.TLabel',background='#FFF4E4',foreground='#865716',padding=(14,10))


def attach_quiet_clicks(button):
    """Cancel pointer releases outside; keep native in-bounds/keyboard actions."""
    if not isinstance(button, ttk.Button) or hasattr(button, '_quiet_release_guard'):
        return button
    def release(event):
        if button.instate(['disabled']):
            return 'break'
        if not (0 <= event.x < button.winfo_width() and 0 <= event.y < button.winfo_height()):
            button.state(['!pressed', '!active'])
            return 'break'
    button._quiet_release_guard=button.bind('<ButtonRelease-1>',release,add='+')
    return button


def decorate_panels(widget):
    """Paint rounded surfaces without participating in Tk geometry negotiation.

    The decorative canvas uses place, never pack/grid. Native controls remain
    the foreground children, with their existing parent and focus semantics.
    """
    attach_quiet_clicks(widget)
    if isinstance(widget, ttk.Frame) and widget.cget('style') == 'Panel.TFrame' and not hasattr(widget, '_quiet_surface'):
        canvas = tk.Canvas(widget, background=CANVAS, borderwidth=0, highlightthickness=0, takefocus=False)
        canvas.place(x=0,y=0,relwidth=1,relheight=1,bordermode='ignore')
        canvas.tk.call('lower', canvas._w)
        widget._quiet_surface = canvas
        def draw(e):
            w,h=e.width,e.height
            r=min(16,max(2,w/4),max(2,h/4))
            canvas.delete('surface')
            canvas.create_polygon(1,r,1,1,r,1,w-r,1,w-1,1,w-1,r,w-1,h-r,w-1,h-1,w-r,h-1,
                                  r,h-1,1,h-1,1,h-r,smooth=True,splinesteps=20,
                                  fill=SURFACE,outline=LINE,tags='surface')
        canvas.bind('<Configure>',draw)
    for child in widget.winfo_children():
        if child is not getattr(widget, '_quiet_surface', None):
            decorate_panels(child)
