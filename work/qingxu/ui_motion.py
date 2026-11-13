"""Geometry-stable, non-depressed feedback for native ttk buttons.

Only colors change. Original fonts, padding, focus metrics and keyboard/command
bindings remain intact. No polling, model calls or global monkeypatches.
"""
from __future__ import annotations

import itertools
import os
import time
import tkinter as tk
from tkinter import ttk

_counter = itertools.count(1)
BG = '#E9EEF5'


def _blend(widget, left, right, weight):
    try:
        a, b = widget.winfo_rgb(left), widget.winfo_rgb(right)
        weight = max(0.0, min(1.0, weight))
        return '#' + ''.join(f'{round((a[i]*(1-weight)+b[i]*weight)/257):02x}' for i in range(3))
    except (tk.TclError, TypeError, ValueError):
        return right


def reduced_motion(root):
    explicit = getattr(root, '_qingxu_reduce_motion', None)
    if explicit is not None:
        return bool(explicit)
    return os.environ.get('QINGXU_REDUCE_MOTION', '').strip().lower() in ('1', 'true', 'yes', 'on')


def set_reduced_motion(root, enabled):
    """A session-only preference: never touches Codex or project settings."""
    root._qingxu_reduce_motion = bool(enabled)
    for motion in tuple(getattr(root, '_qingxu_motion_live', ())):
        if motion.alive:
            motion.reduced = bool(enabled)
            motion._cancel()
            if motion._owns_style():
                motion._paint(motion._target() if motion.hover or motion.down else motion.base)


class _Motion:
    DURATION_MS = 120
    STEPS = 6

    def __init__(self, button):
        self.button = button
        self.root = button._root()
        self.style = ttk.Style(button)
        self.source = self.private = None
        self.base = self.current = BG
        self.job = None
        self.hover = self.down = False
        self.alive = True
        self.reduced = reduced_motion(self.root)
        self._owned = {}
        if not hasattr(self.root, '_qingxu_motion_live'):
            self.root._qingxu_motion_live = set()
            self.root._qingxu_motion_pool = {}
        self.root._qingxu_motion_live.add(self)
        for sequence, handler in (
            ('<Enter>', self.enter), ('<Leave>', self.leave),
            ('<ButtonPress-1>', self.press), ('<ButtonRelease-1>', self.release),
            ('<KeyPress-space>', self.press), ('<KeyRelease-space>', self.release),
            ('<FocusIn>', self.focus_in), ('<FocusOut>', self.focus_out),
            ('<Unmap>', self.unmap), ('<Destroy>', self.destroy),
        ):
            button.bind(sequence, handler, add='+')

    def _exists(self):
        if not self.alive:
            return False
        try:
            return bool(self.button.winfo_exists())
        except tk.TclError:
            return False

    def _owns_style(self):
        return self._exists() and self.private is not None and self.button.cget('style') == self.private

    def _source(self):
        active = self.button.cget('style') or 'TButton'
        if active != self.private:
            self.source = active
            states = tuple(s for s in self.button.state() if s not in ('active', 'pressed', 'disabled'))
            self.base = self.style.lookup(active, 'background', states) or BG
            self.current = self.base
        return self.source or 'TButton'

    def _setup(self):
        source = self._source()
        if source not in self._owned:
            pool = self.root._qingxu_motion_pool.setdefault(source, [])
            # Inherit the full style, not TButton. In particular, never change
            # focusthickness=2 to 1 on hover (the previous two-pixel jump).
            self._owned[source] = pool.pop() if pool else f'QingxuMotion{next(_counter)}.{source}'
        self.private = self._owned[source]
        self._paint(self.current)
        self.button.configure(style=self.private)

    def _paint(self, color):
        if self.private is None:
            return
        self.current = color
        disabled = self.style.lookup(self.source, 'background', ('disabled',)) or '#EFF0F2'
        self.style.configure(self.private, background=color, relief='flat', shiftrelief=0)
        # Inherit borderwidth, focusthickness, padding, font, foreground maps.
        self.style.map(self.private,
            background=[('disabled', disabled), ('!disabled', color)],
            lightcolor=[('disabled', disabled), ('!disabled', color)],
            darkcolor=[('disabled', disabled), ('!disabled', color)],
            relief=[('pressed', 'flat'), ('!disabled', 'flat')])

    def _target(self):
        if self.button.cget('style') != self.private:
            self._source()
        if self.source and ('Primary.' in self.source or self.base.lower() in ('#007aff', '#087cf7')):
            return _blend(self.button, self.base, '#FFFFFF', .12 if self.down else .07)
        return _blend(self.button, self.base, '#B9DDFE', .27 if self.down else .14)

    def _restore(self):
        if self._owns_style():
            self.button.configure(style=self.source)

    def _animate(self, destination, restore=False):
        self._cancel()
        if not self._exists():
            return
        if not self._enabled():
            self._restore()
            return
        if not self._owns_style():
            self._setup()
        start, owner, began = self.current, self.private, time.monotonic()
        self.reduced = reduced_motion(self.root)
        if self.reduced:
            self._paint(destination)
            if restore and not self.hover and not self.down:
                self._restore()
            return

        def frame():
            self.job = None
            if not self._exists() or self.button.cget('style') != owner:
                return  # A callback changed its variant; never undo that.
            if not self._enabled():
                self._restore()
                return
            t = min(1.0, (time.monotonic() - began) * 1000 / self.DURATION_MS)
            self._paint(_blend(self.button, start, destination, t*t*(3-2*t)))
            if t < 1:
                self.job = self.button.after(self.DURATION_MS // self.STEPS, frame)
            elif restore and not self.hover and not self.down:
                self._restore()
        frame()

    def _cancel(self):
        if self.job is not None:
            try:
                self.button.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None

    def _enabled(self):
        return self._exists() and not self.button.instate(['disabled'])

    def enter(self, _event=None):
        self.hover = True
        if self._enabled():
            self._animate(self._target())

    def leave(self, _event=None):
        self.hover = self.down = False
        if self._owns_style():
            self._animate(self.base, restore=True)

    def press(self, _event=None):
        if self._enabled():
            self.down = True
            self._animate(self._target())

    def release(self, _event=None):
        self.down = False
        if self._owns_style():
            self._animate(self._target() if self.hover else self.base, restore=not self.hover)

    def focus_in(self, _event=None):
        if self._enabled() and not self._owns_style():
            self._setup()

    def focus_out(self, _event=None):
        self.down = False
        if self._owns_style():
            self._animate(self._target() if self.hover else self.base, restore=not self.hover)

    def unmap(self, _event=None):
        self._cancel()
        self.hover = self.down = False
        self._restore()

    def destroy(self, _event=None):
        if not self.alive:
            return
        self._cancel()
        self.alive = False
        self.root._qingxu_motion_live.discard(self)
        # Tk styles cannot be removed. Reuse names for reopened dialogs.
        for source, private in self._owned.items():
            self.root._qingxu_motion_pool.setdefault(source, []).append(private)
        self._owned.clear()


def attach_soft_motion(button):
    if getattr(button._root(), '_qingxu_quiet', False):
        from ui_quiet import attach_quiet_clicks
        return attach_quiet_clicks(button)
    if isinstance(button, ttk.Button) and not hasattr(button, '_qingxu_motion'):
        style = button.cget('style')
        # These controls paint hover, press and focus with fixed-size images.
        if not style.endswith(('Outline.TButton', 'Ghost.TButton', 'Soft.TButton', 'Drop.TButton')):
            button._qingxu_motion = _Motion(button)
    return button


def attach_motion_tree(widget):
    if isinstance(widget, ttk.Button):
        attach_soft_motion(widget)
    for child in widget.winfo_children():
        attach_motion_tree(child)
