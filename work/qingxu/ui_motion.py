"""Tk motion feedback that never depresses, scales or shifts a button.

Uses private ttk styles so the original button class, command, tab order,
keyboard activation, state and layout remain intact. Turn transitions off with
QINGXU_REDUCE_MOTION=1 (colors still change instantly).
"""
from __future__ import annotations

import itertools
import os
import tkinter as tk
from tkinter import ttk

_counter = itertools.count(1)
BG = '#E9EEF5'


def _blend(widget, left, right, weight):
    try:
        a=widget.winfo_rgb(left)
        b=widget.winfo_rgb(right)
        return '#'+''.join(f'{round((a[i]*(1-weight)+b[i]*weight)/257):02x}' for i in range(3))
    except (tk.TclError, TypeError, ValueError):
        return right


class _Motion:
    DURATION_MS = 120
    STEPS = 6

    def __init__(self, button):
        self.button=button
        self.style=ttk.Style(button)
        self.private=f'QingxuMotion{next(_counter)}.TButton'
        self.source=None
        self.base=BG
        self.current=BG
        self.job=None
        self.hover=False
        self.down=False
        self.reduced=os.environ.get('QINGXU_REDUCE_MOTION','').strip().lower() in ('1','true','yes','on')
        button.bind('<Enter>',self.enter,add='+')
        button.bind('<Leave>',self.leave,add='+')
        button.bind('<ButtonPress-1>',self.press,add='+')
        button.bind('<ButtonRelease-1>',self.release,add='+')
        button.bind('<Destroy>',self.destroy,add='+')

    def _source(self):
        active=self.button.cget('style') or 'TButton'
        if active!=self.private:
            self.source=active
            self.base=self.style.lookup(active,'background') or BG
            self.current=self.base
        return self.source or 'TButton'

    def _setup(self):
        source=self._source()
        self.style.layout(self.private,self.style.layout(source) or self.style.layout('TButton'))
        self.style.configure(self.private,
            background=self.current,
            foreground=self.style.lookup(source,'foreground') or '#1D1D1F',
            font=self.style.lookup(source,'font') or 'TkDefaultFont',
            padding=self.style.lookup(source,'padding') or (12,7),
            anchor=self.style.lookup(source,'anchor') or 'center',
            relief='flat',borderwidth=0,shiftrelief=0,focusthickness=1)
        self._paint(self.current)
        self.button.configure(style=self.private)

    def _paint(self,color):
        self.current=color
        self.style.configure(self.private,background=color,relief='flat',shiftrelief=0,borderwidth=0)
        self.style.map(self.private,
            background=[('disabled','#F0F1F4'),('!disabled',color)],
            relief=[('pressed','flat'),('!disabled','flat')],
            foreground=[('disabled','#979DA8')])

    def _target(self):
        if self.button.cget('style') != self.private:
            self._source()
        if self.source and ('Primary.' in self.source or self.base.lower() in ('#007aff','#087cf7')):
            return _blend(self.button,self.base,'#FFFFFF',.17 if self.down else .11)
        return _blend(self.button,self.base,'#B9DDFE',.34 if self.down else .17)

    def _animate(self,destination,restore=False):
        self._cancel()
        if not self.button.winfo_exists():
            return
        if self.button.cget('style')!=self.private:
            self._setup()
        start=self.current
        steps=1 if self.reduced else self.STEPS
        def frame(i):
            if not self.button.winfo_exists() or self.button.cget('style')!=self.private:
                self.job=None
                return
            t=i/steps
            eased=t*t*(3-2*t)
            self._paint(_blend(self.button,start,destination,eased))
            if i<steps:
                self.job=self.button.after(self.DURATION_MS//steps,frame,i+1)
            else:
                self.job=None
                if restore and not self.hover and not self.down and self.button.cget('style')==self.private:
                    self.button.configure(style=self.source)
        frame(1)

    def _cancel(self):
        if self.job is not None:
            try:self.button.after_cancel(self.job)
            except tk.TclError:pass
            self.job=None

    def _enabled(self):
        return self.button.winfo_exists() and not self.button.instate(['disabled'])

    def enter(self,_event=None):
        self.hover=True
        if self._enabled():
            self._animate(self._target())

    def leave(self,_event=None):
        self.hover=self.down=False
        if self.button.winfo_exists() and self.button.cget('style')==self.private:
            self._animate(self.base,restore=True)

    def press(self,_event=None):
        if self._enabled():
            self.down=True
            self._animate(self._target())

    def release(self,_event=None):
        self.down=False
        if self.button.winfo_exists() and self.button.cget('style')==self.private:
            self._animate(self._target() if self.hover else self.base,restore=not self.hover)

    def destroy(self,_event=None):
        self._cancel()


def attach_soft_motion(button):
    """Opt-in animation for a standard ttk.Button; returns it unchanged."""
    if isinstance(button,ttk.Button) and not hasattr(button,'_qingxu_motion'):
        button._qingxu_motion=_Motion(button)
    return button


def attach_motion_tree(widget):
    """Attach to existing buttons without reparenting or rewriting commands."""
    for child in widget.winfo_children():
        if isinstance(child,ttk.Button):
            attach_soft_motion(child)
        else:
            attach_motion_tree(child)
