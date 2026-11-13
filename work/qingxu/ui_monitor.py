"""Tk views for local context pressure and a small optional desktop monitor.

All data is supplied by the app. These views never read a chat log, start a
collector, or infer which Codex window/chat is in the foreground.
"""
from datetime import datetime
import tkinter as tk
from tkinter import font as tkfont, ttk

from dashboard import ChatDashboard, Dashboard
from ui_billing import BillingPanel
from ui_brand import BrandMark, set_window_icon
from ui_motion import attach_motion_tree, reduced_motion, set_reduced_motion


from ui_quiet import CANVAS as BG, SURFACE as CARD, INK, MUTED, BLUE, GREEN, AMBER, RED
STATE_LABELS = {
    "ready": "可以继续", "approaching": "准备交接", "reached": "建议先交接",
    "stale": "最后已知值", "unknown": "等待数据", "compacted": "等待新测量",
    "error": "读取失败", "invalid": "检查提醒设置",
}
STATE_COLORS = {"ready": GREEN, "approaching": AMBER, "reached": RED,
                "invalid": AMBER, "error": RED}


def _styles(root, family):
    if getattr(root, "_qingxu_quiet", False):
        return
    style = ttk.Style(root)
    style.configure("MonitorLink.TButton", background=CARD, padding=(0, 1), anchor="w",
                    font=(family, 10), relief="flat")
    style.map("MonitorLink.TButton", background=[("active", "#f0f5ff"), ("!active", CARD)],
              foreground=[("active", BLUE), ("disabled", MUTED), ("!active", INK)])
    style.configure("Compact.MonitorLink.TButton", padding=0, font=(family, 9))
    style.configure("MonitorMode.TButton", padding=(6, 4), font=(family, 9))
    style.configure("Selected.MonitorMode.TButton", background=CARD, foreground=BLUE)
    style.map("Selected.MonitorMode.TButton", background=[("!disabled", CARD)], foreground=[("!disabled", BLUE)])
    style.configure("Mini.Monitor.TButton", padding=(3, 1), font=(family, 10),
                    relief="flat", shiftrelief=0)
    style.configure("FloatingTitle.TLabel", background=BG, foreground=INK,
                    font=(family, 13, "bold"))


def _recent_rank(row):
    value = row.get("record", {}).get("updated_at")
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, AttributeError):
            return 0


def presentation(row):
    """Shared display semantics; missing values never become a 0% progress bar."""
    row = row or {}
    state = row.get("state", "unknown")
    used, limit, progress = row.get("used"), row.get("limit"), row.get("progress_percent")
    actual = row.get("occupancy_percent")
    if actual is None:
        percent = "—"
    elif state == "stale":
        percent = f"上次 {actual:.1f}%"
    else:
        percent = f"{actual:.1f}%"
    amount = "尚无可用测量" if used is None else (
        f"{used:,} / {limit:,} tokens" if limit else f"{used:,} tokens · 容量未知")
    when = row.get("measured_at")
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            when = None
    stamp = when.astimezone().strftime("%m-%d %H:%M:%S") if isinstance(when, datetime) else "尚未测量"
    color = STATE_COLORS.get(state, MUTED)
    if state == "unknown" and used is not None:
        status = "提醒线未知"
    else:
        status = STATE_LABELS.get(state, "等待数据")
    if row.get("paused") or row.get("enabled") is False:
        status = "本会话提醒已暂停"
    elif row.get("snoozed_until"):
        until = row["snoozed_until"]
        if isinstance(until, datetime) and until.timestamp() > datetime.now().timestamp():
            status = "稍后提醒中"
    return {"percent": percent, "amount": amount, "stamp": stamp,
            "status": status, "color": color, "progress": progress,
            "threshold": row.get("threshold_percent"),
            "message": row.get("message", "选择项目和聊天后开始查看。")}


class ProgressLine(tk.Canvas):
    """A small accessible progress indicator with a separate reminder marker."""
    def __init__(self, parent, *, height=15, background=CARD):
        super().__init__(parent, height=height, background=background,
                         highlightthickness=0, borderwidth=0, takefocus=False)
        self.value, self.marker, self.color = None, None, BLUE
        self.bind("<Configure>", self._draw)

    def render(self, value=None, *, marker=None, color=BLUE):
        self.value, self.marker, self.color = value, marker, color
        self._draw()

    def _draw(self, _event=None):
        self.delete("all")
        width, height = max(20, self.winfo_width()), max(8, self.winfo_height())
        left, right, y = 5, width - 5, height / 2
        self.create_line(left, y, right, y, fill="#e8e8ed", width=6, capstyle="round")
        if self.value is not None:
            end = left + (right - left) * max(0, min(self.value, 100)) / 100
            if end > left:
                self.create_line(left, y, end, y, fill=self.color, width=6, capstyle="round")
        if self.marker is not None and 0 <= self.marker <= 100:
            x = left + (right - left) * self.marker / 100
            self.create_line(x, y - 6, x, y + 6, fill="#6e6e73", width=1)


class _ChatRow(ttk.Frame):
    def __init__(self, parent, on_select, *, compact=False):
        super().__init__(parent, style="Card.TFrame", padding=(8, 3) if compact else (10, 5))
        self.record, self.on_select = None, on_select
        self.columnconfigure(0, weight=1)
        self.title = ttk.Button(self, text="等待聊天", command=self._choose,
                                style="Compact.MonitorLink.TButton" if compact else "MonitorLink.TButton")
        self.title.grid(row=0, column=0, sticky="w")
        self.percent = ttk.Label(self, text="—", style="Card.TLabel")
        self.percent.grid(row=0, column=1, sticky="e", padx=(6, 0))
        self.line = ProgressLine(self, height=11)
        self.line.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.note = ttk.Label(self, text="", style="CardMuted.TLabel")
        self.note.grid(row=2, column=0, columnspan=2, sticky="w")
        self.compact = compact
        self.title_limit = 21 if compact else 52
        for widget in (self, self.percent, self.line, self.note):
            widget.bind("<Button-1>", lambda _event: self._choose())

    def _choose(self):
        if self.record is not None:
            self.on_select(self.record)

    def render(self, row):
        self.record = row.get("record")
        shown = presentation(row)
        title = row.get("title") or "未命名聊天"
        maximum = self.title_limit
        caption = title if len(title) <= maximum else title[:maximum - 1] + "…"
        self.title.configure(text=("● " if row.get("selected") else "") + caption,
                             state="normal" if self.record else "disabled")
        self.percent.configure(text=shown["percent"], foreground=shown["color"])
        self.line.render(shown["progress"], marker=shown["threshold"], color=shown["color"])
        project = row.get("project") or "未归类"
        if len(project) > 15 and self.compact:
            project = project[:14] + "…"
        self.note.configure(text=f"{project} · {shown['status']}")


class UsagePanel(ttk.Frame):
    def __init__(self, parent, app, billing_parent=None):
        super().__init__(parent, style="App.TFrame", padding=(4, 0))
        self.app, self.record, self.rows = app, None, []
        self._refresh_timer = None
        family = getattr(app, "font_family", tkfont.nametofont("TkDefaultFont").actual("family"))
        _styles(app.root, family)
        self.tabs = ttk.Notebook(self, style="Line.TNotebook")
        self.tabs.pack(fill="both", expand=True)
        overview = ttk.Frame(self.tabs, style="App.TFrame", padding=(0, 10, 0, 0))
        billing = billing_parent if billing_parent is not None else ttk.Frame(self.tabs, style="App.TFrame")
        detail = ttk.Frame(self.tabs, style="App.TFrame", padding=(0, 8, 0, 0))
        snapshot = ttk.Frame(self.tabs, style="App.TFrame", padding=(0, 8, 0, 0))
        views = [(overview, "上下文概览")]
        if billing_parent is None:
            views.append((billing, "计费统计"))
        views.extend(((detail, "详细记录"), (snapshot, "ccusage 快照")))
        for panel, label in views:
            self.tabs.add(panel, text=label)
        self.billing_panel = BillingPanel(billing, app)
        self.billing_panel.pack(fill="both", expand=True)
        self.billing_tab = billing if billing_parent is None else None
        if self.billing_tab is not None:
            self.tabs.bind("<<NotebookTabChanged>>", self._billing_selected, add="+")
        self.overview_canvas, body = self._scroll_page(overview)
        self.overview_body = body
        self.overview_columns = ttk.Frame(body, style="App.TFrame")
        self.overview_columns.pack(fill="x")
        self._overview_wide = None
        self.hero = hero = ttk.Frame(self.overview_columns, style="Panel.TFrame", padding=(22, 22))
        top = ttk.Frame(hero, style="Card.TFrame")
        top.pack(fill="x")
        self.hero_title = tk.StringVar(value="选择一次聊天，了解当前余量")
        self.hero_status = ttk.Label(top, text="等待数据", style="Card.TLabel", foreground=MUTED)
        self.hero_status.pack(side="right", padx=(10, 0))
        self.hero_title_label = ttk.Label(top, textvariable=self.hero_title, style="Card.TLabel", justify="left")
        self.hero_title_label.pack(side="left", fill="x", expand=True)
        number_row = ttk.Frame(hero, style="Card.TFrame")
        number_row.pack(fill="x", pady=(18, 10))
        self.hero_percent = ttk.Label(number_row, text="—", style="Card.TLabel",
                                      font=(family, 36 if getattr(app.root, "_qingxu_quiet", False) else 21, "bold"))
        self.hero_percent.pack(side="left")
        self.hero_amount = tk.StringVar(value="尚无可用测量")
        self.hero_amount_label = ttk.Label(number_row, textvariable=self.hero_amount,
                                           style="CardMuted.TLabel", justify="left")
        self.hero_amount_label.pack(side="left", padx=(12, 0), anchor="s", pady=(0, 4))
        self.hero_line = ProgressLine(hero, height=22)
        self.hero_line.pack(fill="x")
        self.actions = ttk.Frame(hero, style="Card.TFrame")
        self.actions.pack(fill="x", pady=(18, 14))
        self.prepare_button = ttk.Button(self.actions, text="整理并接续", style="Primary.TButton",
                                         command=self._prepare, state="disabled")
        self.floating_button = ttk.Button(self.actions, text="打开悬浮窗", style="Outline.TButton",
                                          command=self._open_floating)
        self.reminder_button = ttk.Button(self.actions, text="调整提醒", style="Ghost.TButton",
                                          command=lambda: app.show_page("settings"))
        self.refresh_button = ttk.Button(self.actions, text="刷新测量", style="Ghost.TButton",
                                         command=self._refresh)
        self.action_buttons = (self.prepare_button, self.floating_button,
                               self.reminder_button, self.refresh_button)
        self.actions.bind("<Configure>", self._layout_actions)
        self.hero_basis = tk.StringVar(value="最近请求总量参考 · 尚未测量")
        self.hero_rule = tk.StringVar(value="轻续提醒与 Codex 自动压缩设置分别显示。")
        self.hero_message = tk.StringVar(value="选择项目和聊天后开始查看。")
        self.hero_labels = []
        for variable in (self.hero_basis, self.hero_rule, self.hero_message):
            label = ttk.Label(hero, textvariable=variable, style="CardMuted.TLabel",
                              justify="left", wraplength=750)
            label.pack(anchor="w", pady=(2, 0))
            self.hero_labels.append(label)
        hero.bind("<Configure>", self._wrap_hero)
        self.recent_section = ttk.Frame(self.overview_columns, style="Panel.TFrame", padding=(20, 10))
        recent_header = ttk.Frame(self.recent_section, style="Card.TFrame")
        recent_header.pack(fill="x", pady=(0, 8))
        ttk.Label(recent_header, text="其他会话", style="CardTitle.TLabel").pack(side="left")
        ttk.Label(recent_header, text="最近 4 条", style="CardMuted.TLabel").pack(side="right")
        self.recent_frame = ttk.Frame(self.recent_section, style="Card.TFrame")
        self.recent_frame.pack(fill="x")
        self.recent_rows = [_ChatRow(self.recent_frame, app.choose_record) for _ in range(4)]
        self.empty = ttk.Label(self.recent_frame, text="还没有可显示的聊天。可在设置中选择 Codex 数据目录。",
                               style="CardMuted.TLabel", wraplength=650)
        self.empty.pack(anchor="w", pady=12)
        self.overview_note = ttk.Label(body, text="进度来自最近一次可读请求，不是精确实时上下文或费用。悬浮窗不跟踪 Codex 窗口焦点。",
                                       style="Muted.TLabel", wraplength=760)
        self.overview_note.pack(anchor="w", pady=(6, 3))
        body.bind("<Configure>", self._layout_overview, add="+")
        self.recent_section.bind("<Configure>", self._wrap_recent)
        self._layout_overview()
        self.chat_usage = ChatDashboard(detail, app.choose_record, self._scroll_page)
        self.chat_usage.pack(fill="both", expand=True)
        snapshot_header = ttk.Frame(snapshot, style="App.TFrame")
        snapshot_header.pack(fill="x", pady=(0, 5))
        ttk.Label(snapshot_header, text="保留 ccusage 的项目 / 主从 Agent 用量统计。",
                  style="Muted.TLabel").pack(side="left")
        ttk.Button(snapshot_header, text="选择快照位置", command=lambda: app.show_page("settings")).pack(side="right")
        ttk.Button(snapshot_header, text="读取快照", command=app.refresh_usage).pack(side="right", padx=6)
        self.dashboard = Dashboard(snapshot)
        self.dashboard.pack(fill="both", expand=True)
        self.bind("<Destroy>", self._destroyed, add="+")

    def _billing_selected(self, _event=None):
        if self.tabs.select() == str(self.billing_tab):
            self.billing_panel.activate()

    def _destroyed(self, event):
        if event.widget is self and self._refresh_timer:
            self.after_cancel(self._refresh_timer)
            self._refresh_timer = None

    def _layout_overview(self, event=None):
        width = event.width if event is not None else self.overview_body.winfo_width()
        scaling = float(self.tk.call("tk", "scaling"))
        wide = width >= max(860, round(570 * scaling))
        self.overview_note.configure(wraplength=max(240, width - 12))
        if wide == self._overview_wide:
            return
        self._overview_wide = wide
        columns = self.overview_columns
        columns.columnconfigure(0, weight=13 if wide else 1, uniform="overview" if wide else "")
        columns.columnconfigure(1, weight=9 if wide else 0, uniform="overview" if wide else "")
        self.hero.grid(row=0, column=0, sticky="new", padx=(0, 8) if wide else 0,
                       pady=0 if wide else (0, 8))
        self.recent_section.grid(row=0 if wide else 1, column=1 if wide else 0, sticky="new")
        recent = sorted(self.rows, key=_recent_rank, reverse=True)[:4]
        for index, widget in enumerate(self.recent_rows):
            widget.title_limit = 18 if wide else 52
            if index < len(recent):
                widget.render(recent[index])

    def _layout_actions(self, event):
        widths = [button.winfo_reqwidth() + 6 for button in self.action_buttons]
        columns = 4 if sum(widths) <= event.width else 2
        if getattr(self, "_action_columns", None) == columns:
            return
        self._action_columns = columns
        for index, button in enumerate(self.action_buttons):
            button.grid(row=index // columns, column=index % columns, sticky="w", padx=(0, 6), pady=(0, 4))

    def _wrap_hero(self, event):
        available = max(120, event.width - 82)
        for label in self.hero_labels:
            label.configure(wraplength=available)
        self.hero_title_label.configure(wraplength=max(130, available - self.hero_status.winfo_reqwidth() - 12))
        self.hero_amount_label.configure(wraplength=max(110, available - self.hero_percent.winfo_reqwidth() - 12))

    def _wrap_recent(self, event):
        width = max(120, event.width - 82)
        self.empty.configure(wraplength=width)
        for widget in self.recent_rows:
            widget.note.configure(wraplength=width)

    def _scroll_page(self, parent):
        if callable(getattr(self.app, "scroll_page", None)):
            return self.app.scroll_page(parent)
        canvas = tk.Canvas(parent, background=BG, highlightthickness=0, yscrollincrement=24)
        bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas, style="App.TFrame", padding=(0, 0, 6, 0))
        item = canvas.create_window(0, 0, anchor="nw", window=body)
        body.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))
        def wheel(event):
            if event.widget is canvas or str(event.widget).startswith(str(body) + "."):
                if not isinstance(event.widget, (tk.Text, ttk.Treeview, ttk.Combobox)):
                    delta = -1 if getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4 else 1
                    canvas.yview_scroll(delta, "units")
        root = self.winfo_toplevel()
        bindings = [(event, root.bind(event, wheel, add="+")) for event in ("<MouseWheel>", "<Button-4>", "<Button-5>")]
        def cleanup(event):
            if event.widget is canvas:
                for name, identity in bindings:
                    try:
                        root.unbind(name, identity)
                    except tk.TclError:
                        pass
        canvas.bind("<Destroy>", cleanup, add="+")
        return canvas, body

    def render_catalog(self, catalog):
        self.chat_usage.render_catalog(catalog)

    def set_record(self, record):
        self.billing_panel.selection_changed()
        self.record = record
        self.chat_usage.set_chat(record)
        self.prepare_button.configure(state="normal" if record else "disabled")
        self._hero(None)

    def _hero(self, row):
        shown = presentation(row)
        caption = (self.record.get("label") or "未命名聊天") if self.record else "选择一次聊天，了解当前余量"
        self.hero_title.set(caption[:62] + ("…" if len(caption) > 62 else ""))
        self.hero_status.configure(text=shown["status"], foreground=shown["color"])
        self.hero_percent.configure(text=shown["percent"], foreground=shown["color"])
        self.hero_amount.set(shown["amount"])
        self.hero_line.render(shown["progress"], marker=shown["threshold"], color=shown["color"])
        self.hero_basis.set(f"最近请求总量参考 · {row.get('basis', '容量未知') if row else '待读取'} · {shown['stamp']}")
        threshold = row.get("soft_limit") if row else None
        self.hero_rule.set((f"提醒线 {threshold:,} tokens · {row['soft_source']}" if threshold else
                            "提醒线未知 · 可在设置中选择轻续推荐或自定义提醒。"))
        self.hero_message.set(shown["message"] if row else ("正在读取所选聊天…" if self.record else shown["message"]))

    def render(self, rows):
        self.rows = list(rows)
        identity = self.record.get("chat_id") if self.record else None
        current = next((r for r in rows if r.get("chat_id") == identity), None)
        self._hero(current)
        recent = sorted(rows, key=_recent_rank, reverse=True)[:4]
        self.empty.pack_forget()
        for index, widget in enumerate(self.recent_rows):
            widget.pack_forget()
            if index < len(recent):
                widget.render(recent[index])
                widget.pack(fill="x", pady=(0, 2))
        if not recent:
            self.empty.pack(anchor="w", pady=12)
        self._refresh_ready()
        floating = getattr(self.app, "floating", None) or getattr(self.app, "floating_monitor", None)
        if floating:
            floating.render(rows)

    def render_snapshot(self, snapshot):
        if not snapshot:
            self.snapshot_disabled()
            return
        if "usage_snapshot" not in snapshot:
            snapshot = {"usage_snapshot": snapshot,
                        "observer": {"heartbeat": "UNKNOWN", "process_identity": "UNKNOWN"}, "STOP": None}
        try:
            self.dashboard.render(snapshot, self.app.relay.settings)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.snapshot_disabled("快照暂不可读 · " + str(exc))

    def snapshot_disabled(self, message=None):
        self.dashboard.clear()
        self.dashboard.info.set(message or "暂无 ccusage 快照。可在设置中选择已有快照文件；基础监测和交接仍可使用。")

    def _prepare(self):
        if self.record:
            self.app.show_page("handoff")
            self.app.set_status("已选定当前聊天。先复制旧聊收尾指令，再保存返回的摘要。")

    def _open_floating(self):
        floating = getattr(self.app, "floating", None) or getattr(self.app, "floating_monitor", None)
        if floating is None:
            floating = FloatingMonitor(self.app)
            self.app.floating = floating
        floating.render(self.rows)
        floating.show()

    def _refresh(self):
        self.refresh_button.configure(text="刷新中…", state="disabled")
        try:
            self.app.refresh_metrics()
            self.app.set_status("已请求刷新；显示结果会保留测量时间。")
        finally:
            if self._refresh_timer:
                self.after_cancel(self._refresh_timer)
            self._refresh_timer = self.after(1200, self._refresh_ready)

    def _refresh_ready(self):
        if self._refresh_timer:
            self.after_cancel(self._refresh_timer)
            self._refresh_timer = None
        self.refresh_button.configure(text="刷新测量", state="normal")


class FloatingMonitor:
    def __init__(self, app):
        self.app, self.window, self.rows = app, None, []
        self.mode = tk.StringVar(app.root, value="recent")
        self.visible, self.compact = False, False
        self._normal_geometry = "370x440"
        self._drag_start, self._dragged = None, False
        self._fit_job, self._fitting, self._last_width = None, False, None

    def _build(self):
        family = getattr(self.app, "font_family", tkfont.nametofont("TkDefaultFont").actual("family"))
        _styles(self.app.root, family)
        self.window = tk.Toplevel(self.app.root)
        self.window.title("轻续 · 上下文小窗")
        self.window.configure(background=BG)
        try:
            set_window_icon(self.window)
        except tk.TclError:
            pass
        x = max(16, self.app.root.winfo_screenwidth() - 370 - 24)
        y = max(16, self.app.root.winfo_screenheight() - 440 - 60)
        self._normal_geometry = f"370x440+{x}+{y}"
        self.window.geometry(self._normal_geometry)
        self.window.minsize(340, 390)
        self.window.attributes("-topmost", True)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _event: self.close())
        self.normal = ttk.Frame(self.window, style="App.TFrame", padding=12)
        self.normal.pack(fill="both", expand=True)
        top = ttk.Frame(self.normal, style="App.TFrame")
        top.pack(fill="x")
        BrandMark(top, size=28, background=BG).pack(side="left", padx=(0, 8))
        ttk.Label(top, text="会话余量", style="Section.TLabel").pack(side="left")
        ttk.Button(top, text="隐藏", width=4, command=self.close,
                   style="Mini.Monitor.TButton").pack(side="right", padx=(5, 0))
        ttk.Button(top, text="收起", width=4, command=self.toggle_compact,
                   style="Mini.Monitor.TButton").pack(side="right")
        choices = ttk.Frame(self.normal, style="App.TFrame")
        choices.pack(fill="x", pady=(9, 5))
        self.mode_buttons = {}
        for key, label in (("selected", "所选聊天"), ("recent", "最近活跃")):
            button = ttk.Button(choices, text=label, command=lambda k=key: self._set_mode(k))
            button.pack(side="left", expand=True, fill="x", padx=(0, 4))
            self.mode_buttons[key] = button
        self.notice_label = ttk.Label(self.normal, text="最近请求参考 · 不跟踪 Codex 窗口焦点。\n最近活跃按记录时间排序。",
                                      style="Muted.TLabel", wraplength=330)
        self.notice_label.pack(anchor="w", pady=(0, 7))
        self.list_frame = ttk.Frame(self.normal, style="App.TFrame")
        self.list_frame.pack(fill="both", expand=True)
        self.row_widgets = [_ChatRow(self.list_frame, self._choose, compact=True) for _ in range(4)]
        self.empty = ttk.Label(self.list_frame, text="先在轻续选择项目和聊天。", style="Muted.TLabel", wraplength=300)
        self.feedback = tk.StringVar(value="读取现有记录，不调用模型。")
        self.feedback_label = ttk.Label(self.normal, textvariable=self.feedback, style="Muted.TLabel", wraplength=330)
        self.feedback_label.pack(side="bottom", anchor="w", fill="x", pady=(5, 5), before=top)
        actions = ttk.Frame(self.normal, style="App.TFrame")
        # Reserve the action area before the expanding list takes its space.
        actions.pack(side="bottom", fill="x", before=self.feedback_label)
        self.prepare_button = ttk.Button(actions, text="整理并接续", style="Primary.TButton", command=self._prepare)
        self.prepare_button.pack(side="left")
        self.snooze_button = ttk.Button(actions, text="稍后 10 分钟", command=self._snooze)
        self.snooze_button.pack(side="right")
        self.mini = ttk.Frame(self.window, style="Card.TFrame", padding=(7, 4))
        self.mini_header = ttk.Frame(self.mini, style="Card.TFrame")
        self.mini_header.pack(anchor="center")
        self.mini_mark = BrandMark(self.mini_header, size=22, background=CARD)
        self.mini_mark.pack(side="left", padx=(0, 4))
        self.mini_value = ttk.Label(self.mini_header, text="轻续  —", style="Card.TLabel", font=(family, 10, "bold"))
        self.mini_value.pack(side="left")
        self.mini_line = ProgressLine(self.mini, height=9, background=CARD)
        self.mini_line.configure(width=1)  # Let the compact content determine width.
        self.mini_line.pack(fill="x", pady=(2, 0))
        self.mini_scope = ttk.Label(self.mini, text="等待聊天", style="CardMuted.TLabel",
                                   font=(family, 8), anchor="center")
        self.mini_scope.pack(fill="x")
        mini_actions = ttk.Frame(self.mini, style="Card.TFrame")
        mini_actions.pack(anchor="center", pady=(3, 0))
        ttk.Button(mini_actions, text="展开", command=self.toggle_compact, width=4,
                   style="Mini.Monitor.TButton").pack(side="left", padx=(0, 3))
        ttk.Button(mini_actions, text="隐藏", command=self.close, width=4,
                   style="Mini.Monitor.TButton").pack(side="left")
        self.menu = tk.Menu(self.window, tearoff=False)
        self.menu.add_command(label="等待聊天", state="disabled")
        self.menu.add_separator()
        self.menu.add_command(label="展开", command=lambda: self.toggle_compact() if self.compact else None)
        self.menu.add_command(label="隐藏悬浮窗", command=self.close)
        self.reduce_motion_var = tk.BooleanVar(self.window, value=reduced_motion(self.app.root))
        self.menu.add_checkbutton(label="减少动态效果（本次运行）", variable=self.reduce_motion_var,
            command=lambda: set_reduced_motion(self.app.root, self.reduce_motion_var.get()))
        for widget in (self.mini, self.mini_header, self.mini_mark, self.mini_value, self.mini_scope, self.mini_line):
            widget.bind("<ButtonPress-1>", self._drag_begin)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
            widget.bind("<Button-3>", self._popup_menu)
        self.feedback.trace_add("write", self._queue_fit)
        self.window.bind("<Configure>", self._window_configured, add="+")
        self.window.bind("<Destroy>", self._window_destroyed, add="+")
        attach_motion_tree(self.window)

    def _cancel_fit(self):
        if self._fit_job is not None and self.window is not None:
            try:
                self.window.after_cancel(self._fit_job)
            except tk.TclError:
                pass
            self._fit_job = None

    def _window_destroyed(self, event):
        if event.widget is self.window:
            self._cancel_fit()

    def _window_configured(self, event):
        if event.widget is not self.window or self.compact:
            return
        if event.width != self._last_width:
            self._last_width = event.width
            wrap = max(180, event.width - 24)
            self.notice_label.configure(wraplength=wrap)
            self.feedback_label.configure(wraplength=wrap)
            self.empty.configure(wraplength=max(180, wrap - 16))
            self._queue_fit()

    def _queue_fit(self, *_args):
        if self._fitting or self.window is None or not self.window.winfo_exists():
            return
        if self._fit_job is None:
            self._fit_job = self.window.after_idle(self._fit_compact if self.compact else self._fit_normal)

    def _fit_compact(self):
        """Size from the actual font and labels, including DPI changes."""
        self._fit_job = None
        if not self.compact or self.window is None or not self.window.winfo_exists():
            return
        self._fitting = True
        try:
            self.window.update_idletasks()
            width = max(120, self.mini.winfo_reqwidth() + 4)
            height = max(64, self.mini.winfo_reqheight() + 4)
            self.window.minsize(width, height)
            x, y = self.window.winfo_x(), self.window.winfo_y()
            self.window.geometry(f"{width}x{height}{x:+d}{y:+d}")
        finally:
            self._fitting = False

    def _fit_normal(self):
        self._fit_job = None
        if self.compact or self.window is None or not self.window.winfo_exists():
            return
        self._fitting = True
        try:
            # Resolve actual fonts, wrapping and current row count before sizing.
            self.window.update_idletasks()
            screen_width = self.app.root.winfo_screenwidth()
            screen_height = self.app.root.winfo_screenheight()
            available_height = max(200, screen_height - 84)
            minimum = min(available_height, max(390, self.normal.winfo_reqheight() + 4))
            width = max(340, self.window.winfo_width())
            height = min(available_height, max(440, minimum, self.window.winfo_height()))
            x = max(16, min(self.window.winfo_x(), screen_width - width - 24))
            y = max(16, min(self.window.winfo_y(), screen_height - height - 60))
            self.window.minsize(340, minimum)
            self._normal_geometry = f"{width}x{height}+{x}+{y}"
            if self.window.geometry() != self._normal_geometry:
                self.window.geometry(self._normal_geometry)
        finally:
            self._fitting = False

    def _popup_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def show(self):
        if self.window is None or not self.window.winfo_exists():
            self._build()
        self.visible = True
        self.window.deiconify()
        self.window.lift()
        self.window.attributes("-topmost", True)
        self.render(self.rows)

    def close(self):
        self._cancel_fit()
        if self.window is not None and self.window.winfo_exists():
            self.window.withdraw()
        self.visible = False

    def _set_mode(self, mode):
        self.mode.set(mode)
        self.render(self.rows)

    def _shown(self):
        if self.mode.get() == "selected":
            identity = self.app.chat.get("chat_id") if self.app.chat else None
            return [r for r in self.rows if r.get("chat_id") == identity][:1]
        return sorted(self.rows, key=_recent_rank, reverse=True)[:4]

    def _target(self):
        identity = self.app.chat.get("chat_id") if self.app.chat else None
        return next((r for r in self._shown() if r.get("chat_id") == identity), None)

    def render(self, rows):
        self.rows = list(rows)
        if self.window is None or not self.window.winfo_exists():
            return
        shown = self._shown()
        for key, button in self.mode_buttons.items():
            button.configure(style="Selected.MonitorMode.TButton" if self.mode.get() == key else "MonitorMode.TButton")
        self.empty.pack_forget()
        for index, widget in enumerate(self.row_widgets):
            widget.pack_forget()
            if index < len(shown):
                widget.render(shown[index])
                widget.pack(fill="x", pady=(0, 3))
        if not shown:
            self.empty.pack(anchor="w", pady=20)
        target = self._target()
        self.prepare_button.configure(state="normal" if target and target.get("record") else "disabled")
        self.snooze_button.configure(state="normal" if target else "disabled")
        mini_row = target or next(iter(shown), None)
        display = presentation(mini_row)
        actual = mini_row.get("occupancy_percent") if mini_row else None
        mini_percent = "—" if actual is None else f"{actual:.0f}%"
        if mini_row and mini_row.get("state") == "stale" and actual is not None:
            mini_percent += "·旧"
        self.mini_value.configure(text=mini_percent, foreground=display["color"])
        self.mini_line.render(display["progress"], marker=display["threshold"], color=display["color"])
        scope = "所选" if target is not None else "最近"
        project = (mini_row or {}).get("project") or "未归类"
        caption = project if len(project) <= 7 else project[:6] + "…"
        self.mini_scope.configure(text=f"{scope} · {caption}" if mini_row else "等待聊天")
        title = (mini_row or {}).get("title") or "未命名聊天"
        self.menu.entryconfigure(0, label=f"{project} · {title} · {display['status']}" if mini_row else "等待聊天")
        if shown and target is None:
            self.feedback.set("先点击一条聊天，再交接或暂缓提醒。")
        self._queue_fit()

    def _choose(self, record):
        self.app.choose_record(record)
        if self.app.chat and self.app.chat.get("chat_id") == record.get("chat_id"):
            self.feedback.set("已切换轻续所选聊天，正在更新测量。")
        else:
            self.feedback.set("保留原会话，尚未切换。")

    def _prepare(self):
        target = self._target()
        if target and target.get("record"):
            self.app.choose_record(target["record"])
            if not self.app.chat or self.app.chat.get("chat_id") != target.get("chat_id"):
                self.feedback.set("未切换会话，保留当前编辑。")
                return
            self.app.root.deiconify()
            self.app.root.lift()
            self.app.show_page("handoff")
            self.feedback.set("已打开交接页面。")

    def _snooze(self):
        target = self._target()
        if target:
            self.app.monitor.snooze(target["chat_id"], minutes=10)
            self.feedback.set("仅此会话暂缓提醒 10 分钟，进度继续更新。")
            self.app.set_status(self.feedback.get())

    def toggle_compact(self):
        if not self.compact:
            self._cancel_fit()
            self._fit_normal()
            self._normal_geometry = self.window.geometry()
            x, y = self.window.winfo_x(), self.window.winfo_y()
            self.normal.pack_forget()
            self.mini.pack(fill="both", expand=True)
            self.window.minsize(120, 64)
            self.window.overrideredirect(True)
            self.window.geometry(f"120x64{x:+d}{y:+d}")
        else:
            self.mini.pack_forget()
            self.normal.pack(fill="both", expand=True)
            self.window.overrideredirect(False)
            self.window.minsize(340, 390)
            self.window.geometry(self._normal_geometry)
        self.compact = not self.compact
        self.window.attributes("-topmost", True)
        self._queue_fit()

    def _drag_begin(self, event):
        self._dragged = False
        self._drag_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def _drag_move(self, event):
        if self._drag_start:
            px, py, x, y = self._drag_start
            dx, dy = event.x_root - px, event.y_root - py
            if abs(dx) + abs(dy) > 3:
                self._dragged = True
                self.window.geometry(f"{x + dx:+d}{y + dy:+d}")

    def _drag_end(self, _event):
        if not self._dragged and self.compact:
            self.toggle_compact()
        self._drag_start = None
