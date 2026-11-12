"""Compact native Tk controls shared by the workspaces."""

import ctypes
from ctypes import wintypes
from math import isqrt
import os
import tkinter as tk
from tkinter import font, ttk


WHITE = "#FFFFFF"
INK = "#191B20"
MUTED = "#565A65"
LINE = "#DFE1E7"
HOVER = "#F0F1F4"
PRESSED = "#E7E9EE"
FOCUS = "#176BC0"


def rounded_image(master, fill, outline, radius=6):
    """A small stretchable surface. The center can grow around native text."""
    size = radius * 2 + 3
    image = tk.PhotoImage(master=master, width=size, height=size)
    for y in range(size):
        dy = max(radius - y, y - (size - radius - 1), 0)
        start = radius - isqrt(radius * radius - dy * dy)
        image.put(outline, to=(start, y, size - start, y + 1))
        if dy < radius:
            inner = radius - isqrt((radius - 1) ** 2 - dy * dy)
            image.put(fill, to=(inner, y, size - inner, y + 1))
    return image


def install_controls(widget):
    root = widget._root()
    style = ttk.Style(widget)
    theme = style.theme_use()
    installed = getattr(root, "_qingxu_controls", {})
    if theme in installed:
        return
    images = installed[theme] = []
    root._qingxu_controls = installed

    def surface(fill, outline, radius=6):
        picture = rounded_image(root, fill, outline, radius)
        images.append(picture)
        return picture

    def element(name, image, *states, **options):
        style.element_create(name, "image", image, *states, **options)

    for name, normal in (("Outline", "#CFD2DA"), ("Ghost", WHITE), ("Soft", HOVER)):
        base = WHITE if name != "Soft" else HOVER
        element("Qingxu" + name + ".border", surface(base, normal),
                ("disabled", surface("#F7F7F9", LINE)),
                ("focus", surface(WHITE, FOCUS)),
                ("pressed", surface(PRESSED, PRESSED)),
                ("active", surface(HOVER, normal)),
                border=7, sticky="nsew")
        style.layout(name + ".TButton", [
            ("Qingxu" + name + ".border", {"sticky": "nsew", "children": [
                ("Button.padding", {"sticky": "nsew", "children": [
                    ("Button.label", {"sticky": "nsew"})]})]})])
        style.configure(name + ".TButton", background=base, foreground=INK, padding=(9, 4),
                        shiftrelief=0, borderwidth=0, width=0)
        style.map(name + ".TButton", foreground=[("disabled", "#8B8E97")])

    chevron = tk.PhotoImage(master=root, width=17, height=9)
    for x in range(9):
        y = 2 + min(x, 8 - x)
        chevron.put(MUTED, (7 + x, y))
    images.append(chevron)
    element("QingxuDrop.chevron", chevron)
    style.layout("Drop.TButton", [
        ("QingxuOutline.border", {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [
                ("QingxuDrop.chevron", {"side": "right", "sticky": ""}),
                ("Button.label", {"sticky": "nsew"})]})]})])
    style.configure("Drop.TButton", anchor="w", padding=(9, 4), shiftrelief=0, foreground=INK, width=0)
    style.map("Drop.TButton", foreground=[("disabled", "#8B8E97")])

    element("QingxuSegment.border", surface(HOVER, HOVER),
            ("focus", surface(WHITE, FOCUS)),
            ("selected", surface(WHITE, LINE)),
            ("pressed", surface(PRESSED, PRESSED)),
            ("active", surface("#E9EAF0", "#E9EAF0")), border=7, sticky="nsew")
    style.layout("Toolbutton", [
        ("QingxuSegment.border", {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [
                ("Button.label", {"sticky": "nsew"})]})]})])
    style.configure("Toolbutton", padding=(8, 4), shiftrelief=0, width=0)
    style.map("Toolbutton", foreground=[("disabled", "#8B8E97"), ("selected", INK), ("!selected", MUTED)])

    def rule(color, edge, fill=WHITE):
        image = tk.PhotoImage(master=root, width=7, height=7)
        image.put(fill, to=(0, 0, 7, 7))
        image.put(color, to=(0, 5, 7, 7) if edge == "bottom" else (0, 0, 7, 1))
        images.append(image)
        return image

    element("QingxuTab.tab", rule(LINE, "bottom"),
            ("selected", "focus", rule(FOCUS, "bottom")),
            ("focus", rule(FOCUS, "bottom", HOVER)),
            ("selected", rule(INK, "bottom")),
            ("active", rule(LINE, "bottom", "#F8F8FA")),
            border=(2, 1, 2, 2), sticky="nsew")
    style.layout("Line.TNotebook.Tab", [
        ("QingxuTab.tab", {"sticky": "nsew", "children": [
            ("Notebook.padding", {"sticky": "nsew", "children": [
                ("Notebook.label", {"sticky": "nsew"})]})]})])
    element("QingxuTab.client", rule(LINE, "top"), border=(0, 1, 0, 0), sticky="nsew")
    style.layout("Line.TNotebook", [("QingxuTab.client", {"sticky": "nsew"})])
    element("QingxuShell.client", rule(WHITE, "top"), sticky="nsew")
    style.layout("Shell.TNotebook", [("QingxuShell.client", {"sticky": "nsew"})])
    style.configure("Line.TNotebook", background=WHITE, borderwidth=0, tabmargins=0)
    style.configure("Line.TNotebook.Tab", padding=(11, 5), foreground=MUTED, font="TkDefaultFont")
    style.map("Line.TNotebook.Tab", foreground=[("selected", INK), ("active", INK)])

    element("QingxuHeading.cell", rule(LINE, "bottom"),
            ("active", rule(LINE, "bottom", HOVER)), border=(0, 0, 0, 2), sticky="nsew")
    element("QingxuTree.row", rule("#E7E8EC", "bottom"),
            ("selected", rule("#D5E2F3", "bottom", "#E9F0FA")), border=(0, 0, 0, 2), sticky="nsew")
    style.layout("Usage.Treeview.Heading", [
        ("QingxuHeading.cell", {"sticky": "nsew", "children": [
            ("Treeheading.padding", {"sticky": "nsew", "children": [
                ("Treeheading.text", {"sticky": "we"})]})]})])
    style.layout("Usage.Treeview.Row", [("QingxuTree.row", {"sticky": "nsew"})])
    style.configure("Usage.Treeview", background=WHITE, fieldbackground=WHITE, borderwidth=0)
    style.configure("Usage.Treeview.Heading", font="TkDefaultFont", foreground=MUTED, padding=(5, 5))
    style.map("Usage.Treeview", background=[("selected", "#E9F0FA")], foreground=[("selected", INK)])

    for name, fill, outline in (("DateField", WHITE, LINE), ("ActiveDateField", WHITE, "#F97316"),
                                ("DateCalendar", "#F0F0F3", LINE)):
        element("Qingxu" + name + ".border", surface(fill, outline, radius=9), border=10, sticky="nsew")
        style.layout(name + ".TFrame", [("Qingxu" + name + ".border", {"sticky": "nsew"})])
        style.configure(name + ".TFrame", background=fill)

    for axis in ("Vertical", "Horizontal"):
        prefix = "Qingxu" + axis
        track = tk.PhotoImage(master=root, width=8, height=8)
        track.put(WHITE, to=(0, 0, 8, 8))
        images.append(track)
        element(prefix + ".trough", track, sticky="nsew")
        element(prefix + ".thumb", surface("#CCCED5", "#CCCED5", 2),
                ("active", surface("#AAAEB8", "#AAAEB8", 2)), border=3, sticky="nsew")
        style.layout("Slim." + axis + ".TScrollbar", [
            (prefix + ".trough", {"sticky": "nsew", "children": [
                (prefix + ".thumb", {"expand": True, "sticky": "ns" if axis == "Vertical" else "ew"})]})])


class DropButton(ttk.Button):
    """A single text trigger with its chevron inside the same hit target."""

    def __init__(self, parent, **options):
        install_controls(parent)
        options.setdefault("style", "Drop.TButton")
        options.setdefault("takefocus", True)
        super().__init__(parent, **options)
        for key in ("<Return>", "<Down>", "<Alt-Down>"):
            self.bind(key, self._open_key)

    def _open_key(self, _event):
        self.invoke()
        return "break"


def work_area(anchor):
    if os.name == "nt":
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user = ctypes.windll.user32
        from_window = user.MonitorFromWindow
        from_window.argtypes = (wintypes.HWND, wintypes.DWORD)
        from_window.restype = wintypes.HMONITOR
        monitor = from_window(anchor.winfo_toplevel().winfo_id(), 2)
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        if monitor and user.GetMonitorInfoW(wintypes.HMONITOR(monitor), ctypes.byref(info)):
            box = info.rcWork
            return box.left, box.top, box.right, box.bottom
    return 0, 0, anchor.winfo_screenwidth(), anchor.winfo_screenheight()


class Popover(tk.Toplevel):
    """Keep a popup by its trigger and inside the current monitor's work area."""

    def __init__(self, anchor):
        super().__init__(anchor, background=WHITE, highlightbackground=LINE, highlightthickness=1)
        self.withdraw()
        self.overrideredirect(True)
        self.transient(anchor.winfo_toplevel())
        self.anchor = anchor
        self._bounds = work_area(anchor)
        self._focus_job = None
        self._previous_grab = anchor.grab_current()
        self._previous_status = self._previous_grab.grab_status() if self._previous_grab else None
        self._anchor_binding = anchor.bind("<Unmap>", self._anchor_unmapped, add="+")
        self.bind("<Escape>", lambda _event: self.close())
        self.bind("<ButtonPress-1>", self._outside_click, add="+")
        self.bind("<FocusOut>", self._focus_out, add="+")

    def capacity(self):
        left, top, right, bottom = self._bounds
        anchor_y = self.anchor.winfo_rooty()
        height = max(anchor_y - top, bottom - anchor_y - self.anchor.winfo_height())
        return max(1, right - left - 16), max(1, height - 8)

    def present(self, focus, *, align="left", width=None, height=None):
        self.update_idletasks()
        left, top, right, bottom = self._bounds
        max_width, max_height = self.capacity()
        width = min(width or self.winfo_reqwidth(), max_width)
        height = min(height or self.winfo_reqheight(), max_height)
        x = self.anchor.winfo_rootx()
        if align == "right":
            x += self.anchor.winfo_width() - width
        x = max(left + 4, min(x, right - width - 4))
        y = self.anchor.winfo_rooty() + self.anchor.winfo_height() + 4
        if y + height > bottom:
            y = max(top + 4, self.anchor.winfo_rooty() - height - 4)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.deiconify()
        self.lift()
        self.update_idletasks()
        self.grab_set()
        focus.focus_set()

    def _outside_click(self, event):
        x, y = self.winfo_rootx(), self.winfo_rooty()
        if not (x <= event.x_root < x + self.winfo_width() and y <= event.y_root < y + self.winfo_height()):
            return self.close()

    def _focus_out(self, _event):
        if self._focus_job is None and self.winfo_exists():
            self._focus_job = self.after_idle(self._check_focus)

    def _check_focus(self):
        self._focus_job = None
        focused = self.focus_displayof()
        if focused is None or focused.winfo_toplevel() != self:
            self.close(restore_focus=False)

    def _anchor_unmapped(self, _event):
        self.close(restore_focus=False)

    def close(self, *, restore_focus=True):
        anchor = self.anchor
        if not self.winfo_exists():
            return "break"
        self.destroy()
        previous = self._previous_grab
        if previous is not None and previous.winfo_viewable() and anchor.grab_current() is None:
            if self._previous_status == "global":
                previous.grab_set_global()
            else:
                previous.grab_set()
        if restore_focus and anchor.winfo_viewable():
            anchor.focus_set()
        return "break"

    def destroy(self):
        if self._focus_job is not None:
            self.after_cancel(self._focus_job)
            self._focus_job = None
        if self.anchor.winfo_exists():
            self.anchor.unbind("<Unmap>", self._anchor_binding)
        super().destroy()


class ChoiceButton(DropButton):
    """A read-only choice with the value and selection event used by the forms."""

    def __init__(self, parent, *, values=(), textvariable=None, caption=None, state="readonly", **options):
        self.variable = textvariable if textvariable is not None else tk.StringVar(parent)
        self._values = tuple(values)
        self.popup = None
        if caption is None:
            options["textvariable"] = self.variable
        else:
            options["text"] = caption
        super().__init__(parent, command=self.open,
                         state="disabled" if state == "disabled" else "normal", **options)

    def configure(self, cnf=None, **options):
        if isinstance(cnf, dict):
            options = {**cnf, **options}
            cnf = None
        changed = False
        if "values" in options:
            values = tuple(options.pop("values"))
            changed = values != self._values
            self._values = values
        if (changed or options.get("state") == "disabled") and self.popup is not None and self.popup.winfo_exists():
            self.popup.close()
        if options.get("state") == "readonly":
            options["state"] = "normal"
        return super().configure(cnf, **options)

    config = configure

    def cget(self, key):
        if key == "values":
            return self._values
        return super().cget(key)

    __getitem__ = cget

    def get(self):
        return self.variable.get()

    def set(self, value):
        self.variable.set(value)

    def open(self):
        if self.instate(("disabled",)) or not self._values:
            return
        if self.popup is not None and self.popup.winfo_exists():
            self.popup.close()
            return
        self.popup = popup = Popover(self)
        options = tk.Variable(popup, value=self._values)
        popup.options = options
        width_limit, height_limit = popup.capacity()
        face = font.nametofont("TkDefaultFont", root=self)
        desired = max(self.winfo_width(), max(face.measure(value) for value in self._values) + 30)
        width = min(desired, width_limit, max(680, self.winfo_width()))
        horizontal = desired > width
        margin = 18 if horizontal else 9
        visible = max(1, min(10, len(self._values), (height_limit - margin) // (face.metrics("linespace") + 2)))
        rows = tk.Listbox(popup, listvariable=options, height=visible, font=face,
                          background=WHITE, foreground=INK, borderwidth=0, highlightthickness=0,
                          selectbackground="#E8EDF6", selectforeground=INK, selectborderwidth=1,
                          activestyle="none", exportselection=False)
        if horizontal:
            scroll = ttk.Scrollbar(popup, orient="horizontal", command=rows.xview, style="Slim.Horizontal.TScrollbar")
            scroll.pack(side="bottom", fill="x", padx=6, pady=(0, 3))
            rows.configure(xscrollcommand=scroll.set)
        if len(self._values) > visible:
            scroll = ttk.Scrollbar(popup, orient="vertical", command=rows.yview, style="Slim.Vertical.TScrollbar")
            scroll.pack(side="right", fill="y", padx=(0, 3), pady=4)
            rows.configure(yscrollcommand=scroll.set)
        rows.pack(side="left", fill="both", expand=True, padx=6, pady=4)
        index = self._values.index(self.get()) if self.get() in self._values else 0
        rows.selection_set(index)
        rows.activate(index)
        rows.see(index)
        rows.bind("<ButtonRelease-1>", lambda event: self._choose_row(rows, event))
        rows.bind("<Return>", lambda _event: self._choose_row(rows))
        rows.bind("<space>", lambda _event: self._choose_row(rows))
        rows.bind("<KeyPress>", lambda event: self._seek_row(rows, event))
        popup.present(rows, width=width)

    def _seek_row(self, rows, event):
        if not event.char or not event.char.isalnum() or event.state & 0xC:
            return None
        first = rows.index("active") + 1
        order = (*range(first, len(self._values)), *range(first))
        for index in order:
            if self._values[index].casefold().startswith(event.char.casefold()):
                rows.selection_clear(0, "end")
                rows.selection_set(index)
                rows.activate(index)
                rows.see(index)
                break
        return "break"

    def _choose_row(self, rows, event=None):
        if event is None:
            selected = rows.curselection()
            if not selected:
                return "break"
            index = selected[0]
        else:
            index = rows.nearest(event.y)
            bbox = rows.bbox(index)
            if not bbox or not (bbox[1] <= event.y < bbox[1] + bbox[3]):
                return "break"
        self.set(self._values[index])
        self.popup.close()
        self.event_generate("<<ComboboxSelected>>", when="tail")
        return "break"
