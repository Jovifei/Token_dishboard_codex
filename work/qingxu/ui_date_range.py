"""Minute-accurate date range picker for the usage workspace."""

import calendar
from datetime import date, datetime, time, timedelta, timezone
import re
import tkinter as tk
from tkinter import ttk

from billing import fixed_timezone, period_bounds
from ui_controls import DropButton, Popover, install_controls


def _parse_day(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期请使用 YYYY-MM-DD 格式。")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("日期无效，请检查年月日。") from exc


def _parse_clock(value):
    if not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ValueError("时间请使用 24 小时制 HH:MM 格式。")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("时间无效，请检查小时和分钟。") from exc


def _to_utc(day, clock, tz, *, latest=False):
    local = datetime.combine(day, clock)
    candidates = []
    for fold in (0, 1):
        try:
            candidate = local.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)
            if candidate.astimezone(tz).replace(tzinfo=None) == local:
                candidates.append(candidate)
        except (OSError, OverflowError, ValueError):
            continue
    if not candidates:
        raise ValueError("所选时间在该时区不存在，请检查夏令时切换。")
    return max(candidates) if latest else min(candidates)


def custom_bounds(start, end, start_time, end_time, now, tz_label, follow_now=False):
    """Return local day bounds and UTC bounds with the final minute included."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("当前时间必须带时区。")
    tz = fixed_timezone(tz_label)
    first = _parse_day(start)
    beginning = _to_utc(first, _parse_clock(start_time), tz)
    if follow_now:
        ending = now.astimezone(timezone.utc)
        last = ending.astimezone(tz).date()
    else:
        last = _parse_day(end)
        try:
            ending = _to_utc(last, _parse_clock(end_time), tz, latest=True) + timedelta(minutes=1)
        except OverflowError as exc:
            raise ValueError("结束时间超出可用范围。") from exc
    if first > last or (last - first).days > 3660:
        raise ValueError("日期范围需按先后排列且不超过十年。")
    if beginning >= ending:
        raise ValueError("开始时间必须早于结束时间。")
    return first, last, beginning, ending


class DateRangeButton(DropButton):
    def __init__(self, parent, *, presets, period, start, end, start_time, end_time,
                 follow_now, timezone_label, on_apply, on_error):
        self.presets = tuple(value for value in presets if value != "自定义")
        self.period, self.start, self.end = period, start, end
        self.start_time, self.end_time = start_time, end_time
        self.follow_now, self.timezone_label = follow_now, timezone_label
        self.on_apply, self.on_error = on_apply, on_error
        self.popup = None
        super().__init__(parent, textvariable=period, command=self.open)

    def open(self):
        if self.instate(("disabled",)):
            return
        if self.popup is not None and self.popup.winfo_exists():
            self.popup.close()
            return
        self.popup = popup = DateRangePopup(self)
        scale = float(self.tk.call("tk", "scaling")) / (96 / 72)
        first_choice = next(iter(popup.preset_buttons.values()), popup.start_entry)
        width = min(round(620 * scale), max(380, self.winfo_toplevel().winfo_width() - 24))
        popup.present(first_choice, align="right", width=width, height=round(328 * scale))


class DateRangePopup(Popover):
    def __init__(self, button):
        super().__init__(button)
        install_controls(self)
        self.button = button
        self._setting = False
        self._calendar_after = None
        self._active_endpoint = "start"
        self._endpoint_fields = {}
        self.draft_period = tk.StringVar(self, value=button.period.get())
        self.draft_start = tk.StringVar(self, value=button.start.get())
        self.draft_end = tk.StringVar(self, value=button.end.get())
        self.draft_start_time = tk.StringVar(self, value=button.start_time.get())
        self.draft_end_time = tk.StringVar(self, value=button.end_time.get())
        self.draft_follow_now = tk.BooleanVar(self, value=button.follow_now.get())
        self.error_text = tk.StringVar(self)
        try:
            selected = _parse_day(self.draft_start.get())
        except ValueError:
            selected = datetime.now(timezone.utc).astimezone(fixed_timezone(button.timezone_label.get())).date()
        self.month = selected.replace(day=1)
        self._build()
        for variable in (self.draft_start, self.draft_end, self.draft_start_time,
                         self.draft_end_time, self.draft_follow_now):
            variable.trace_add("write", self._edited)
        if self.draft_period.get() in button.presets:
            self.choose_preset(self.draft_period.get())
        else:
            self._sync_follow()
            self._calendar()

    def _build(self):
        style = ttk.Style(self)
        style.configure("Popup.TFrame", background="#FFFFFF")
        style.configure("Popup.TLabel", background="#FFFFFF", foreground="#626772")
        style.configure("Popup.TCheckbutton", background="#FFFFFF", foreground="#626772")
        style.map("Popup.TCheckbutton", background=[("active", "#FFFFFF")])
        style.configure("Calendar.TFrame", background="#F0F0F3")
        style.configure("Calendar.TLabel", background="#F0F0F3", foreground="#303238")
        style.configure("DateApply.TButton", background="#F97316", foreground="#FFFFFF", padding=(16, 6), width=12)
        style.map("DateApply.TButton", background=[("active", "#EA6810"), ("pressed", "#D85E0C")],
                  foreground=[("!disabled", "#FFFFFF")])

        footer = ttk.Frame(self, style="Popup.TFrame", padding=(12, 6, 12, 10))
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="取消", style="Ghost.TButton", command=self.cancel).pack(side="left", padx=(8, 15))
        ttk.Button(footer, text="确定", style="DateApply.TButton", command=self.apply).pack(side="left", padx=(0, 12))
        ttk.Label(footer, textvariable=self.error_text, style="Popup.TLabel", foreground="#B42318", wraplength=300).pack(side="left", fill="x", expand=True)

        viewport = ttk.Frame(self, style="Popup.TFrame")
        viewport.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(viewport, background="#FFFFFF", highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=self.canvas.yview,
                                  style="Slim.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.body = ttk.Frame(self.canvas, style="Popup.TFrame", padding=(12, 10))
        window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.body.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window, width=event.width))
        self.bind("<MouseWheel>", self._wheel, add="+")
        self.bind("<FocusIn>", self._show_focus, add="+")

        self.preset_bar = ttk.Frame(self.body, style="Popup.TFrame")
        self.preset_bar.pack(fill="x", pady=(0, 10))
        self.preset_buttons = {}
        for index, kind in enumerate(self.button.presets):
            option = ttk.Button(self.preset_bar, text=kind, style="Outline.TButton",
                                command=lambda value=kind: self.choose_preset(value))
            option.grid(row=0, column=index, sticky="w", padx=(0, 5), pady=(0, 4))
            self.preset_buttons[kind] = option
        self.preset_bar.bind("<Configure>", self._layout_presets)
        ttk.Separator(self.body).pack(fill="x", pady=(0, 8))

        self.columns = ttk.Frame(self.body, style="Popup.TFrame")
        self.columns.pack(fill="both", expand=True)
        self.fields = ttk.Frame(self.columns, style="Popup.TFrame", padding=(0, 0, 10, 0))
        ttk.Label(self.fields, text="支持日期与时间", style="Popup.TLabel").pack(anchor="w", pady=(0, 6))
        self.start_entry, self.start_clock_entry = self._field("开始时间", self.draft_start, self.draft_start_time, "start")
        self.end_entry, self.end_clock_entry = self._field("结束时间", self.draft_end, self.draft_end_time, "end")
        ttk.Checkbutton(self.fields, text="结束时间跟随当前时刻", variable=self.draft_follow_now, style="Popup.TCheckbutton",
                        command=self._sync_follow).pack(anchor="w", pady=(3, 0))

        self.calendar = ttk.Frame(self.columns, style="DateCalendar.TFrame", padding=(8, 8))
        header = ttk.Frame(self.calendar, style="Calendar.TFrame")
        header.pack(fill="x")
        tk.Button(header, text="‹", command=lambda: self._move_month(-1), background="#F0F0F3",
                  activebackground="#DFE1E5", relief="flat", borderwidth=0, cursor="hand2").pack(side="left")
        self.month_label = ttk.Label(header, style="Calendar.TLabel", anchor="center")
        self.month_label.pack(side="left", fill="x", expand=True)
        tk.Button(header, text="›", command=lambda: self._move_month(1), background="#F0F0F3",
                  activebackground="#DFE1E5", relief="flat", borderwidth=0, cursor="hand2").pack(side="right")
        self.calendar_grid = ttk.Frame(self.calendar, style="Calendar.TFrame")
        self.calendar_grid.pack(fill="both", expand=True, pady=(6, 0))
        for column, name in enumerate(("日", "一", "二", "三", "四", "五", "六")):
            ttk.Label(self.calendar_grid, text=name, style="Calendar.TLabel", anchor="center").grid(
                row=0, column=column, sticky="ew", pady=(0, 3))
            self.calendar_grid.columnconfigure(column, weight=1, uniform="days")
        self._day_cells = []
        for index in range(42):
            cell = tk.Button(self.calendar_grid, width=3, activebackground="#FFD1AF", activeforeground="#303238",
                             relief="flat", borderwidth=0, highlightthickness=1, cursor="hand2")
            cell.grid(row=index // 7 + 1, column=index % 7, sticky="nsew", padx=1, pady=1)
            self._day_cells.append(cell)
        self.columns.bind("<Configure>", self._layout_columns)

    def _field(self, label, day, clock, endpoint):
        group = ttk.Frame(self.fields, style="ActiveDateField.TFrame" if endpoint == "start" else "DateField.TFrame",
                          padding=(9, 7))
        self._endpoint_fields[endpoint] = group
        group.pack(fill="x", pady=(0, 7))
        ttk.Label(group, text=label, style="Popup.TLabel").pack(anchor="w", pady=(0, 4))
        row = ttk.Frame(group, style="Popup.TFrame")
        row.pack(fill="x")
        day_entry = ttk.Entry(row, textvariable=day, width=11)
        day_entry.pack(side="left", fill="x", expand=True, padx=(0, 7))
        clock_entry = ttk.Entry(row, textvariable=clock, width=5)
        clock_entry.pack(side="left")
        for entry in (day_entry, clock_entry):
            entry.bind("<FocusIn>", lambda _event, value=endpoint: self._set_active(value), add="+")
            entry.bind("<Return>", lambda _event: self.apply())
        return day_entry, clock_entry

    def _layout_presets(self, event):
        controls = list(self.preset_buttons.values())
        if not controls:
            return
        columns = len(controls)
        if sum(control.winfo_reqwidth() + 5 for control in controls) > event.width:
            columns = max(1, min(3, event.width // max(control.winfo_reqwidth() + 5 for control in controls)))
        if columns == getattr(self, "_preset_columns", None):
            return
        self._preset_columns = columns
        for index, control in enumerate(controls):
            control.grid(row=index // columns, column=index % columns, sticky="w", padx=(0, 5), pady=(0, 4))

    def _layout_columns(self, event):
        needed = self.fields.winfo_reqwidth() + self.calendar.winfo_reqwidth() + 12
        wide = event.width >= needed
        if wide == getattr(self, "_wide", None):
            return
        self._wide = wide
        self.columns.columnconfigure(0, weight=1)
        self.columns.columnconfigure(1, weight=1 if wide else 0)
        self.fields.grid(row=0, column=0, sticky="nsew")
        self.calendar.grid(row=0 if wide else 1, column=1 if wide else 0, sticky="nsew")

    def _wheel(self, event):
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

    def _show_focus(self, event):
        widget = event.widget
        if not str(widget).startswith(str(self.body) + "."):
            return
        top = widget.winfo_rooty() - self.canvas.winfo_rooty()
        bottom = top + widget.winfo_height()
        if top < 0 or bottom > self.canvas.winfo_height():
            shift = top if top < 0 else bottom - self.canvas.winfo_height()
            position = self.canvas.canvasy(0) + shift
            self.canvas.yview_moveto(position / max(1, self.body.winfo_height()))

    def _set_active(self, endpoint):
        self._active_endpoint = endpoint
        for name, group in self._endpoint_fields.items():
            group.configure(style="ActiveDateField.TFrame" if name == endpoint else "DateField.TFrame")

    def _sync_follow(self):
        state = "disabled" if self.draft_follow_now.get() else "normal"
        self.end_entry.configure(state=state)
        self.end_clock_entry.configure(state=state)

    def _edited(self, *_args):
        if self._setting:
            return
        self.draft_period.set("自定义")
        self.error_text.set("")
        self._sync_follow()
        self._style_presets()
        if self._calendar_after:
            self.after_cancel(self._calendar_after)
        self._calendar_after = self.after_idle(self._calendar)

    def _style_presets(self):
        for kind, button in self.preset_buttons.items():
            button.configure(style="Soft.TButton" if kind == self.draft_period.get() else "Outline.TButton")

    def choose_preset(self, kind):
        now = datetime.now(timezone.utc)
        start, end = period_bounds(kind, now, self.button.timezone_label.get())
        self._setting = True
        try:
            self.draft_period.set(kind)
            self.draft_follow_now.set(False)
            if start is not None and end is not None:
                tz = fixed_timezone(self.button.timezone_label.get())
                first, last = start.astimezone(tz), end.astimezone(tz)
                self.draft_start.set(first.date().isoformat())
                self.draft_end.set(last.date().isoformat())
                self.draft_start_time.set(first.strftime("%H:%M"))
                self.draft_end_time.set(last.strftime("%H:%M"))
                self.month = last.date().replace(day=1)
            else:
                today = now.astimezone(fixed_timezone(self.button.timezone_label.get())).date()
                self.draft_start.set(today.isoformat())
                self.draft_end.set(today.isoformat())
                self.draft_start_time.set("00:00")
                self.draft_end_time.set("23:59")
                self.month = today.replace(day=1)
        finally:
            self._setting = False
        self.error_text.set("")
        self._sync_follow()
        self._calendar()

    def _move_month(self, offset):
        index = self.month.year * 12 + self.month.month - 1 + offset
        year, month = divmod(index, 12)
        if 1 <= year <= 9999:
            self.month = date(year, month + 1, 1)
            self._calendar()

    def _calendar(self):
        if self._calendar_after:
            self.after_cancel(self._calendar_after)
        self._calendar_after = None
        self._style_presets()
        self.month_label.configure(text=f"{self.month.year}年{self.month.month}月")
        try:
            first = _parse_day(self.draft_start.get())
            last = _parse_day(self.draft_end.get())
        except ValueError:
            first = last = None
        days = list(calendar.Calendar(firstweekday=6).itermonthdays3(self.month.year, self.month.month))
        for index, cell in enumerate(self._day_cells):
            if index >= len(days) or not 1 <= days[index][0] <= 9999:
                cell.grid_remove()
                continue
            day = date(*days[index])
            endpoint = day == first or day == last
            in_range = first is not None and last is not None and first <= day <= last
            background = "#F97316" if endpoint else "#E2E3E8" if in_range else "#F0F0F3"
            foreground = "#FFFFFF" if endpoint else "#303238" if day.month == self.month.month else "#90939B"
            cell.configure(text=str(day.day), background=background, foreground=foreground,
                           command=lambda value=day: self.select_day(value))
            cell.grid()

    def select_day(self, selected):
        if isinstance(selected, str):
            selected = _parse_day(selected)
        if self._active_endpoint == "start":
            self.draft_start.set(selected.isoformat())
            self._set_active("end")
        else:
            try:
                first = _parse_day(self.draft_start.get())
            except ValueError:
                first = selected
            if selected < first:
                self.draft_start.set(selected.isoformat())
                self.draft_end.set(first.isoformat())
            else:
                self.draft_end.set(selected.isoformat())
            self.draft_follow_now.set(False)
            self._sync_follow()
            self._set_active("start")
        self.month = selected.replace(day=1)
        self._calendar()

    def apply(self):
        kind = self.draft_period.get()
        if kind == "自定义":
            try:
                custom_bounds(self.draft_start.get(), self.draft_end.get(), self.draft_start_time.get(),
                              self.draft_end_time.get(), datetime.now(timezone.utc),
                              self.button.timezone_label.get(), self.draft_follow_now.get())
            except (ValueError, OverflowError) as exc:
                message = str(exc)
                self.error_text.set(message)
                self.button.on_error(message)
                return "break"
        self.button.period.set(kind)
        self.button.start.set(self.draft_start.get())
        self.button.end.set(self.draft_end.get())
        self.button.start_time.set(self.draft_start_time.get())
        self.button.end_time.set(self.draft_end_time.get())
        self.button.follow_now.set(self.draft_follow_now.get())
        self.close()
        self.button.on_apply()
        return "break"

    def cancel(self):
        return self.close()

    def destroy(self):
        if self._calendar_after:
            self.after_cancel(self._calendar_after)
            self._calendar_after = None
        super().destroy()
