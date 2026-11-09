"""Qingxu 0.10: three task-oriented workspaces around the conservative 0.9 core.

The GUI does not send messages to Codex or assume its focused conversation.
I/O runs on one bounded worker; its results are discarded after a source switch.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tkinter as tk
from tkinter import filedialog, font, messagebox, simpledialog, ttk

import core
from app import App, TITLE, VERSION, INHERIT, PRESETS, FILE_MODEL, MODEL_CHOICES, CONFIG_FIELDS, flash_taskbar
from chats import path_key
from config_store import ConfigStore
from context_usage import chat_metrics, file_limit
from handoff import HandoffStore, TemplateStore
from monitor import MonitorService
from preferences import PreferenceStore, relay_settings
from privacy import ensure_private
from relay import Relay
from sync import read_file, sha
from ui_handoff import HandoffPanel
from ui_monitor import UsagePanel, FloatingMonitor
from ui_preferences import PreferencesPanel

BG, SURFACE, INK, SECONDARY, BLUE, BORDER = "#F5F5F7", "#FFFFFF", "#1D1D1F", "#6E6E73", "#007AFF", "#D9DCE2"


def final_models(parsed, selection):
    """Resolve the configuration that would exist, including inherited children."""
    main = selection.get(core.MODEL, core.config_value(parsed, core.MODEL))
    effort = selection.get("model_reasoning_effort", core.config_value(parsed, "model_reasoning_effort"))
    child = selection.get(core.AGENT_MODEL, core.config_value(parsed, core.AGENT_MODEL)) or main
    child_effort = selection.get(core.AGENT_EFFORT, core.config_value(parsed, core.AGENT_EFFORT))
    return main, effort, child, child_effort


class Workbench(App):
    def __init__(self, root, relay=None, data_root=None):
        self.root, self.relay = root, relay or Relay()
        self.data_root = ensure_private(Path(data_root or Path(__file__).parent / "data").absolute())
        self.pref_store = PreferenceStore(self.data_root, legacy_settings=self.relay.settings)
        self.preferences_error = None
        try:
            self.prefs = self.pref_store.load()
        except (OSError, ValueError) as exc:
            self.prefs = dict(self.pref_store.defaults)
            self.preferences_error = str(exc)
        self.relay.settings = relay_settings(self.prefs, self.relay.settings)
        self.handoff_store = HandoffStore(self.prefs["handoff_dir"])
        self.template_store = TemplateStore(self.data_root)
        self.config_store = ConfigStore(self.data_root / "config-backups")
        self.monitor = MonitorService(self.relay.settings["chat_db"], self.prefs["monitor"])
        self.pid, self.chat, self.catalog, self.snapshot = None, None, None, {}
        self.metric_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qingxu-reader")
        self.metric_job = self.metric_timer = None
        self.metric_generation, self.request_cache, self.rows = 0, {}, []
        self.closed = False
        self.status = tk.StringVar(root, value="选择项目和聊天，查看用量或准备接续。")
        self.context_notice = tk.StringVar(root, value="监测跟随轻续所选聊天；可打开悬浮窗查看最近活动。")
        self.selection_info = tk.StringVar(root)
        self.page_title = tk.StringVar(root)
        self.page_subtitle = tk.StringVar(root)
        self.configure_theme()
        root.title(f"{TITLE} {VERSION}")
        root.geometry("1240x840")
        root.minsize(980, 680)
        root.configure(bg=BG)
        icon = Path(__file__).with_name("qingxu.ico")
        if os.name == "nt" and icon.is_file():
            root.iconbitmap(default=str(icon))
        self.build_shell()
        self.floating = FloatingMonitor(self)
        self.root.report_callback_exception = self.callback_error
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Destroy>", self.stop_metrics, add="+")
        self.root.bind("<Control-1>", lambda _: self.show_page("usage"))
        self.root.bind("<Control-2>", lambda _: self.show_page("handoff"))
        self.root.bind("<Control-3>", lambda _: self.show_page("settings"))
        self.show_page("usage")
        try:
            self.reload_choices()
            self.recall_chat()
            if not self.chat and self.catalog and self.catalog.get("chats"):
                self.choose_record(self.catalog["chats"][0])
        except Exception as exc:
            self.set_status("尚未连接 Codex 数据。可在设置选择目录，或手动选择项目目录接续。" + str(exc))
        if self.preferences_error:
            self.set_status("偏好读取失败；已保留原文件。" + self.preferences_error)
        self.schedule_metrics()

    def configure_theme(self):
        families = set(font.families(self.root))
        self.font_family = next((name for name in ("Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", "Noto Sans CJK JP", "DejaVu Sans") if name in families), "TkDefaultFont")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            font.nametofont(name).configure(family=self.font_family, size=10)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=(self.font_family, 10), background=BG, foreground=INK)
        style.configure("App.TFrame", background=BG)
        style.configure("Side.TFrame", background="#EBEDF1")
        style.configure("Card.TFrame", background=SURFACE, relief="flat")
        for name, background, foreground, size, weight in (
            ("TLabel", BG, INK, 10, "normal"), ("Title.TLabel", BG, INK, 22, "bold"),
            ("Heading.TLabel", SURFACE, INK, 13, "bold"), ("Section.TLabel", BG, INK, 13, "bold"),
            ("CardTitle.TLabel", SURFACE, INK, 12, "bold"), ("Card.TLabel", SURFACE, INK, 10, "normal"),
            ("Muted.TLabel", BG, SECONDARY, 9, "normal"), ("CardMuted.TLabel", SURFACE, SECONDARY, 9, "normal"),
            ("Number.TLabel", SURFACE, INK, 25, "bold"), ("Side.TLabel", "#EBEDF1", SECONDARY, 9, "normal")):
            style.configure(name, background=background, foreground=foreground, font=(self.font_family, size, weight))
        style.configure("TButton", padding=(12, 7), background="#E8EBEF", relief="flat", borderwidth=0, focusthickness=2, focuscolor=BLUE)
        style.map("TButton", background=[("disabled", "#EFF0F2"), ("pressed", "#C9D0D9"), ("active", "#DDE5EF")], foreground=[("disabled", "#9A9CA1")])
        style.configure("Primary.TButton", background=BLUE, foreground="white", font=(self.font_family, 10, "bold"))
        style.map("Primary.TButton", background=[("disabled", "#BDD9F5"), ("pressed", "#005AC2"), ("active", "#1686FF")], foreground=[("disabled", "#F7FAFF"), ("!disabled", "white")])
        style.configure("Nav.TButton", anchor="w", padding=(16, 13), background="#EBEDF1", font=(self.font_family, 11))
        style.configure("Selected.Nav.TButton", anchor="w", background=SURFACE, foreground=BLUE, font=(self.font_family, 11, "bold"))
        style.map("Selected.Nav.TButton", background=[("active", "#FFFFFF"), ("!active", "#FFFFFF")])
        style.configure("TCombobox", padding=(7, 5), fieldbackground=SURFACE, arrowsize=13, bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE)
        style.map("TCombobox", fieldbackground=[("readonly", SURFACE)], foreground=[("readonly", INK)])
        style.configure("TEntry", padding=(7, 5), fieldbackground=SURFACE, bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE)
        style.configure("TNotebook", background=BG, borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG, tabmargins=(0, 5, 0, 10))
        style.configure("TNotebook.Tab", padding=(15, 7), borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG)
        style.map("TNotebook.Tab", background=[("selected", SURFACE), ("!selected", "#E8EAEE")], foreground=[("selected", BLUE), ("!selected", SECONDARY)])
        style.layout("Shell.TNotebook.Tab", [])
        style.configure("Shell.TNotebook", tabmargins=0, borderwidth=0, background=BG)
        style.configure("Horizontal.TProgressbar", troughcolor="#E7EBF1", background=BLUE, thickness=9, borderwidth=0)
        style.configure("Treeview", rowheight=30, fieldbackground=SURFACE, background=SURFACE, borderwidth=0)
        style.configure("Treeview.Heading", font=(self.font_family, 10, "bold"), background="#EDF0F4", relief="flat")
        style.configure("Metrics.Treeview", rowheight=25)
        style.configure("Notice.TLabel", background="#E8F2FF", foreground="#245078", padding=(12, 8))
        style.configure("Alert.TLabel", background="#FFF0D7", foreground="#8F5100", padding=(12, 8))
        style.configure("Toolbutton", padding=(9, 6), background="#E7EAEE")
        style.map("Toolbutton", background=[("selected", SURFACE)], foreground=[("selected", BLUE)])
        self.configure_checks(style)

    def build_shell(self):
        shell = ttk.Frame(self.root, style="App.TFrame")
        shell.pack(fill="both", expand=True)
        side = ttk.Frame(shell, style="Side.TFrame", width=178, padding=(16, 24))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = tk.Label(side, text="轻续", bg="#EBEDF1", fg=INK, font=(self.font_family, 24, "bold"), anchor="w")
        brand.pack(fill="x", pady=(0, 2))
        ttk.Label(side, text="把工作轻轻续上", style="Side.TLabel").pack(anchor="w", pady=(0, 26))
        self.nav = {}
        for key, label in (("usage", "用量概览"), ("handoff", "会话接续"), ("settings", "设置")):
            button = ttk.Button(side, text=label, style="Nav.TButton", command=lambda k=key: self.show_page(k))
            button.pack(fill="x", pady=4)
            self.nav[key] = button
        ttk.Label(side, text=f"{VERSION}\n本地数据 · 按需读取", style="Side.TLabel", justify="left").pack(side="bottom", anchor="w", pady=6)
        ttk.Button(side, text="打开悬浮监测", command=lambda: self.floating.show()).pack(side="bottom", fill="x", pady=14)
        main = ttk.Frame(shell, style="App.TFrame", padding=(22, 18, 22, 12))
        main.pack(side="left", fill="both", expand=True)
        titlebar = ttk.Frame(main)
        titlebar.pack(fill="x", pady=(0, 12))
        heading = ttk.Frame(titlebar)
        heading.pack(side="left", fill="x", expand=True)
        ttk.Label(heading, textvariable=self.page_title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(heading, textvariable=self.page_subtitle, style="Muted.TLabel").pack(anchor="w", pady=(3, 0))
        self.button(titlebar, "手动选择项目目录…", self.choose_manual_project, feedback=self.status).pack(side="right")
        self.picker = ttk.Frame(main, style="Card.TFrame", padding=12)
        self.picker.pack(fill="x", pady=(0, 10))
        self.source_kind = tk.StringVar(value="按项目选聊天")
        modes = ttk.Frame(self.picker, style="Card.TFrame")
        modes.grid(row=0, column=0, padx=(0, 12))
        for label, mode in (("按项目", "按项目选聊天"), ("最近聊天", "Codex 最近聊天")):
            ttk.Radiobutton(modes, text=label, value=mode, variable=self.source_kind, style="Toolbutton", command=lambda: self.guarded(self.reload_choices, self.status)).pack(side="left", padx=2)
        self.project_label = ttk.Label(self.picker, text="项目", style="Card.TLabel")
        self.project_label.grid(row=0, column=1)
        self.category_choice = ttk.Combobox(self.picker, state="readonly", width=18)
        self.category_choice.grid(row=0, column=2, sticky="ew", padx=(7, 12))
        self.category_choice.bind("<<ComboboxSelected>>", lambda _: self.guarded(self.filter_chats, self.status))
        chat_label = ttk.Label(self.picker, text="聊天", style="Card.TLabel")
        chat_label.grid(row=0, column=3)
        self.project_choice = ttk.Combobox(self.picker, state="disabled", width=30)
        self.project_choice.grid(row=0, column=4, sticky="ew", padx=(7, 10))
        self.project_choice.bind("<<ComboboxSelected>>", lambda _: self.guarded(self.select_project, self.status))
        refresh = self.button(self.picker, "刷新", self.refresh_catalog, feedback=self.status)
        refresh.grid(row=0, column=5)
        self.picker.columnconfigure(4, weight=1)
        detail = ttk.Label(self.picker, textvariable=self.selection_info, style="CardMuted.TLabel", wraplength=870)
        detail.grid(row=1, column=0, columnspan=6, sticky="w", pady=(8, 0))
        compact = False

        def fit_picker(event):
            nonlocal compact
            detail.configure(wraplength=max(250, event.width - 30))
            controls = (modes, self.project_label, self.category_choice,
                        chat_label, self.project_choice, refresh)
            required = sum(control.winfo_reqwidth() for control in controls) + 72
            narrow = event.width < required
            if narrow == compact:
                return
            compact = narrow
            if compact:
                chat_label.grid_configure(row=1, column=0, sticky="w", pady=(6, 0))
                self.project_choice.grid_configure(row=1, column=1, columnspan=4, pady=(6, 0))
                refresh.grid_configure(row=1, pady=(6, 0))
                detail.grid_configure(row=2)
            else:
                chat_label.grid_configure(row=0, column=3, sticky="", pady=0)
                self.project_choice.grid_configure(row=0, column=4, columnspan=1, pady=0)
                refresh.grid_configure(row=0, pady=0)
                detail.grid_configure(row=1)

        self.picker.bind("<Configure>", fit_picker)
        self.choice_records, self.category_map, self.loaded_category = {}, {}, ""
        self.loaded_source = self.source_kind.get()
        self.context_banner = ttk.Label(main, textvariable=self.context_notice, style="Notice.TLabel", wraplength=920)
        # Normal progress belongs in the overview card; global banner appears
        # only when there is an actionable warning or a read failure.
        self.context_banner.bind("<Configure>", lambda e: self.context_banner.configure(wraplength=max(300, e.width - 30)))
        footer = ttk.Label(main, textvariable=self.status, style="Muted.TLabel", wraplength=950)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        footer.bind("<Configure>", lambda e: footer.configure(wraplength=max(300, e.width - 10)))
        self.tabs = ttk.Notebook(main, style="Shell.TNotebook")
        self.tabs.pack(fill="both", expand=True)
        self.frames = [ttk.Frame(self.tabs) for _ in range(3)]
        for frame, name in zip(self.frames, ("会话接续", "用量概览", "设置")):
            self.tabs.add(frame, text=name)
        self.handoff_panel = HandoffPanel(self.frames[0], self)
        self.handoff_panel.pack(fill="both", expand=True)
        self.usage_panel = UsagePanel(self.frames[1], self)
        self.usage_panel.pack(fill="both", expand=True)
        self.chat_usage = self.usage_panel.chat_usage
        self.settings_tabs = ttk.Notebook(self.frames[2])
        self.settings_tabs.pack(fill="both", expand=True)
        self.preferences_page, self.config_page = ttk.Frame(self.settings_tabs), ttk.Frame(self.settings_tabs)
        self.settings_tabs.add(self.preferences_page, text="目录与提醒")
        self.settings_tabs.add(self.config_page, text="Codex 配置")
        self.preferences_panel = PreferencesPanel(self.preferences_page, self)
        self.preferences_panel.pack(fill="both", expand=True)
        self.build_config(self.config_page)
        self.settings_tabs.bind("<<NotebookTabChanged>>", self.config_tab_changed)

    def show_page(self, key):
        index = {"handoff": 0, "usage": 1, "settings": 2}[key]
        self.tabs.select(self.frames[index])
        self.page = key
        captions = {"usage": ("用量概览", "知道何时收尾，让下一次对话更轻。"), "handoff": ("会话接续", "保留项目目标和当前断点，在新聊天继续。"), "settings": ("设置", "选择自己的目录、提醒方式和模型配置。")}
        self.page_title.set(captions[key][0])
        self.page_subtitle.set(captions[key][1])
        for name, button in self.nav.items():
            button.configure(style="Selected.Nav.TButton" if name == key else "Nav.TButton")

    def set_status(self, text):
        self.status.set(text)

    def chat_catalog(self, result):
        self.catalog = result
        self.usage_panel.render_catalog(result)

    def clear_project(self):
        self.snapshot = {}
        self.handoff_panel.set_record(self.chat)
        self.usage_panel.set_record(self.chat)
        self.watch_chat()

    def unsaved(self):
        return self.handoff_panel.has_unsaved()

    def discard_ok(self):
        return not self.unsaved() or messagebox.askyesno(TITLE, "当前摘要有未保存的编辑。放弃后切换？", parent=self.root)

    def choose_record(self, record):
        if record and str(record.get("chat_id", "")).startswith("manual-"):
            if not self.same_chat(record) and not self.discard_ok():
                return
            self.apply_selection(record)
            return
        self.choose_usage_chat(record)

    def choose_manual_project(self):
        if not self.discard_ok():
            return
        selected = filedialog.askdirectory(parent=self.root, title="选择接续项目的工作目录")
        if not selected:
            return
        folder = Path(selected).absolute()
        label = simpledialog.askstring("手动接续", "给这次旧聊天起一个名称（方便再次找到它）：", initialvalue="手动接续", parent=self.root)
        if not label:
            return
        identity = "manual-" + hashlib.sha256((path_key(str(folder)) + "\0" + label).encode()).hexdigest()[:16]
        record = {"chat_id": identity, "path": str(folder), "label": label, "app_project_id": None,
                  "app_project_name": folder.name, "project_id": None, "updated_at": 0}
        self.apply_selection(record)
        self.category_choice.set(folder.name)
        self.project_choice.set(label)
        self.set_status("手动接续已选定；没有绑定 Codex 日志，用量显示未知。")
        self.show_page("handoff")

    def open_path(self, path):
        value = Path(path).expanduser().absolute()
        if not value.exists():
            raise ValueError("目录尚未创建。请先保存摘要，或在设置中保存目录。")
        if os.name == "nt":
            os.startfile(str(value))
        elif os.sys.platform == "darwin":
            subprocess.Popen(["open", str(value)])
        else:
            subprocess.Popen(["xdg-open", str(value)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def apply_preferences(self, updates, create_dirs=False):
        changing_source = updates.get("codex_home", self.prefs.get("codex_home")) != self.prefs.get("codex_home")
        if changing_source and not self.discard_ok():
            raise ValueError("已取消切换数据目录；原配置、聊天和未保存摘要均已保留。")
        candidate = self.pref_store.save(updates, expected=self.prefs, create_dirs=create_dirs)
        settings = relay_settings(candidate, self.relay.settings)
        source_changed = settings["chat_db"] != self.relay.settings["chat_db"]
        old_config = self.relay.settings["config"]
        self.prefs, self.relay.settings = candidate, settings
        self.handoff_store = HandoffStore(candidate["handoff_dir"])
        self.template_store = TemplateStore(self.data_root)
        self.metric_generation += 1
        self.monitor = MonitorService(settings["chat_db"], candidate["monitor"])
        self.rows = []
        self.floating.render([])
        self.usage_panel.render([])
        self.handoff_panel.refresh_preferences()
        if settings["config"] != old_config:
            self.config_path.set(settings["config"])
            self.config_auto_read = False
        if source_changed:
            self.request_cache = {}
            self.pid, self.chat = None, None
            self.catalog = {"app_projects": [], "chats": [], "total": 0}
            self.chat_catalog(self.catalog)
            self.clear_project()
            self.project_choice.set("")
            self.category_choice.set("")
            self.selection_info.set("数据目录已切换；请从新目录选择聊天。")
            self.guarded(self.reload_choices, self.status)
        self.refresh_usage()
        self.refresh_metrics()
        return candidate

    def close(self):
        if not self.discard_ok():
            return
        if self.preferences_panel.has_unsaved() and not messagebox.askyesno(TITLE, "目录或提醒设置尚未保存。放弃这些设置并退出？", parent=self.root):
            return
        self.closed = True
        self.root.destroy()

    def watch_chat(self):
        self.metric_generation += 1
        self.request_cache = {}
        self.rows = []
        self.context_notice.set("正在读取所选聊天…" if self.chat else "选择聊天后显示上下文参考；没有 Codex 数据也可手动接续。")
        self.context_banner.configure(style="Notice.TLabel")
        self.context_banner.pack_forget()
        if hasattr(self, "floating"):
            self.floating.render([])
        self.schedule_metrics()

    def refresh_metrics(self):
        self.metric_generation += 1
        self.schedule_metrics()

    def refresh_catalog(self):
        if self.reload_choices():
            self.refresh_metrics()
            self.set_status("列表已刷新。监测只跟随轻续选择，不读取 Codex 窗口焦点。")

    @staticmethod
    def collect_metrics(settings, service, records, identity, cache):
        # Avoid full history totals on every background tick.
        config = file_limit(settings["config"])
        catalog = None
        try:
            catalog = Relay(settings).chats(limit=None)
            records = catalog["chats"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        rows = service.poll(records, selected_id=identity, config_limit=config, official_limit=None, limit=5)
        detail = None
        if identity and not identity.startswith("manual-"):
            try:
                detail = chat_metrics(settings["chat_db"], identity, include_totals=True, request_cache=cache)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                detail = {"sample": None, "requests": None, "sources": None,
                          "requests_error": str(exc), "sources_error": str(exc)}
        return rows, detail, catalog

    def update_metrics(self):
        self.metric_timer = None
        if self.closed:
            return
        if self.metric_job is not None:
            generation, identity, job = self.metric_job
            if not job.done():
                self.schedule_metrics(150)
                return
            self.metric_job = None
            if generation == self.metric_generation:
                try:
                    rows, detail, catalog = job.result()
                    self.render_metrics(rows, detail, catalog)
                except Exception as exc:
                    self.context_notice.set("监测暂不可读 · " + str(exc))
                    self.context_banner.pack(fill="x", pady=(0, 8), before=self.tabs)
                    self.usage_panel.render([])
                    self.floating.render([])
                self.schedule_metrics(15000)
                return
        identity = self.chat.get("chat_id") if self.chat else None
        records = list((self.catalog or {}).get("chats", []))
        if self.chat and identity.startswith("manual-"):
            records.insert(0, self.chat)
        self.metric_job = self.metric_generation, identity, self.metric_worker.submit(
            self.collect_metrics, dict(self.relay.settings), self.monitor, records, identity, self.request_cache)
        self.schedule_metrics(150)

    def render_metrics(self, rows, detail, catalog):
        identity_warning = None
        current = next((r for r in (catalog or {}).get("chats", []) if self.chat and r["chat_id"] == self.chat["chat_id"]), None)
        if current and not self.same_chat(current):
            identity_warning = "此聊天的工作目录或项目归属已变化。请重新选择该聊天并核对；当前草稿已保留。"
            rows = [row for row in rows if row.get("chat_id") != current["chat_id"]]
            detail = {"sample": None, "requests": None, "sources": None, "requests_error": identity_warning}
        self.handoff_panel.binding_warning = identity_warning
        self.rows = rows
        if catalog:
            self.chat_catalog(catalog)
            # Refresh the choices without changing the current draft/selection.
            self.set_categories(catalog.get("app_projects", []))
            recent = self.source_kind.get() == "Codex 最近聊天"
            records = [r for r in catalog["chats"] if recent or r.get("app_project_id") == (self.chat or {}).get("app_project_id")]
            self.choice_records = self.chat_records(records)
            self.project_choice.configure(values=list(self.choice_records), state="readonly" if self.choice_records else "disabled")
            selected_label = next((name for name, r in self.choice_records.items() if self.chat and r["chat_id"] == self.chat["chat_id"]), None)
            if selected_label:
                self.project_choice.set(selected_label)
        self.usage_panel.render(rows)
        self.floating.render(rows)
        selected = next((row for row in rows if row.get("selected")), None)
        if selected:
            self.context_notice.set(selected["message"])
            self.context_banner.configure(style="Alert.TLabel" if selected["level"] in ("warning", "alert") else "Notice.TLabel")
            if selected["level"] in ("warning", "alert"):
                self.context_banner.pack(fill="x", pady=(0, 8), before=self.tabs)
            else:
                self.context_banner.pack_forget()
        elif not self.chat:
            self.context_notice.set("可选择一个聊天，或打开悬浮窗查看最近活动。")
        else:
            self.context_notice.set("当前聊天没有可用监测样本。可使用会话接续，不会把未知数值当作零。")
        if identity_warning:
            self.context_notice.set(identity_warning)
            self.context_banner.configure(style="Alert.TLabel")
            self.context_banner.pack(fill="x", pady=(0, 8), before=self.tabs)
            self.handoff_panel.feedback.set(identity_warning)
        if detail:
            self.chat_usage.show_sample(detail.get("sample"), (selected or {}).get("message"))
            self.chat_usage.show_requests(detail.get("requests"), detail.get("requests_error"))
            self.chat_usage.show_sources(detail.get("sources"), detail.get("sources_error"))
            self.chat_usage.show_context_diagnosis((selected or {}).get("level", "unknown"), (selected or {}).get("message", "暂无参考"))
        targets = [selected] if selected else rows[:1] if not self.chat else []
        for row in targets:
            if row and self.monitor.should_notify(row):
                flash_taskbar(self.root)
                # Bring up the small monitor as the visible, non-modal warning.
                self.floating.show()
                self.set_status("换聊提醒 · " + row.get("title", "聊天") + "：" + row["message"])

    def stop_metrics(self, event):
        if event.widget is self.root:
            self.closed = True
            if self.metric_timer is not None:
                try:
                    self.root.after_cancel(self.metric_timer)
                except tk.TclError:
                    pass
            self.metric_worker.shutdown(wait=False, cancel_futures=True)

    def refresh_usage(self):
        if not self.relay.settings.get("snapshot_enabled"):
            self.usage_panel.snapshot_disabled()
            return
        try:
            self.usage_panel.render_snapshot(self.relay.status())
        except Exception as exc:
            self.usage_panel.snapshot_disabled()
            self.set_status("ccusage 快照读取失败 · " + str(exc))

    def config_tab_changed(self, _event=None):
        if self.settings_tabs.select() == str(self.config_page) and not self.config_auto_read:
            self.config_auto_read = True
            self.guarded(self.load_config, self.config_status)

    def build_config(self, frame):
        self.original_config = self.original_config_path = self.config_preview = None
        self.setting_preset, self.use_file_catalog, self.config_auto_read = False, False, False
        self.file_model = self.file_agent_model = None
        self.config_status = tk.StringVar(value="读取配置，勾选需要修改的项。所有写入均先预览、备份和读回。")
        self.config_source, self.config_scope, self.agent_effort_note = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.config_path = tk.StringVar(value=self.relay.settings["config"])
        self.model_view, self.preset = tk.StringVar(value=FILE_MODEL), tk.StringVar(value=PRESETS[2])
        footer = ttk.Frame(frame, style="Card.TFrame", padding=12)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        actions = ttk.Frame(footer, style="Card.TFrame")
        actions.pack(fill="x")
        self.button(actions, "预览修改", self.preview_config, feedback=self.config_status).pack(side="left")
        self.config_apply = self.button(actions, "备份并应用", self.apply_config, style="Primary.TButton", feedback=self.config_status)
        self.config_apply.pack(side="left", padx=8)
        self.config_apply.configure(state="disabled")
        self.button(actions, "恢复配置备份…", self.restore_config, feedback=self.config_status).pack(side="right")
        ttk.Label(footer, textvariable=self.config_status, style="CardMuted.TLabel", wraplength=900).pack(anchor="w", pady=(6, 0))
        self.config_canvas, body = self.scroll_page(frame)
        source = ttk.Frame(body, style="Card.TFrame", padding=14)
        source.pack(fill="x", pady=(5, 9))
        ttk.Label(source, text="目标配置文件", style="Heading.TLabel").pack(anchor="w")
        row = ttk.Frame(source, style="Card.TFrame")
        row.pack(fill="x", pady=8)
        ttk.Entry(row, textvariable=self.config_path).pack(side="left", fill="x", expand=True)
        self.button(row, "选择…", self.choose_config, feedback=self.config_status).pack(side="left", padx=7)
        self.button(row, "读取", self.load_config, feedback=self.config_status).pack(side="left")
        ttk.Label(source, textvariable=self.config_scope, style="CardMuted.TLabel", wraplength=880).pack(anchor="w")
        self.config_items, self.config_entries, self.config_cells = {}, {}, {}

        def section(title, keys, hint):
            card = ttk.Frame(body, style="Card.TFrame", padding=14)
            card.pack(fill="x", pady=(0, 9))
            ttk.Label(card, text=title, style="Heading.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
            for col, text in enumerate(("勾选要修改的项", "本机目录参考", "文件当前值", "准备写入")):
                ttk.Label(card, text=text, style="CardMuted.TLabel").grid(row=1, column=col, sticky="w", padx=(0, 12), pady=(0, 5))
            for i, key in enumerate(keys, 2):
                enabled, value = tk.BooleanVar(value=False), tk.StringVar(value=INHERIT)
                self.config_items[key] = enabled, value
                ttk.Checkbutton(card, text=CONFIG_FIELDS[key][0], variable=enabled, style="Clean.TCheckbutton").grid(row=i, column=0, sticky="w", padx=(0, 10), pady=4)
                official, current = tk.StringVar(value="待读取"), tk.StringVar(value="待读取")
                self.config_cells[key] = official, current
                ttk.Label(card, textvariable=official, style="CardMuted.TLabel").grid(row=i, column=1, sticky="w", padx=(0, 12))
                ttk.Label(card, textvariable=current, style="Card.TLabel").grid(row=i, column=2, sticky="w", padx=(0, 12))
                if key in (core.MODEL, core.AGENT_MODEL):
                    entry = ttk.Combobox(card, textvariable=value, values=(INHERIT, *MODEL_CHOICES.values(), "gpt-6-luna"), width=24)
                elif key in ("model_reasoning_effort", core.AGENT_EFFORT):
                    entry = ttk.Combobox(card, textvariable=value, values=(INHERIT, *core.EFFORTS), state="readonly", width=24)
                else:
                    entry = ttk.Entry(card, textvariable=value, width=24)
                self.config_entries[key] = entry
                entry.grid(row=i, column=3, sticky="ew", pady=4)
                value.trace_add("write", self.config_value_edited)
                enabled.trace_add("write", self.config_selection_edited)
            card.columnconfigure(3, weight=1)
            ttk.Label(card, text=hint, style="CardMuted.TLabel", wraplength=850).grid(row=len(tuple(keys))+2, column=0, columnspan=4, sticky="w", pady=(8, 0))
            return card

        self.config_advanced = section("主 Agent 与子 Agent", (core.MODEL, "model_reasoning_effort", core.AGENT_MODEL, core.AGENT_EFFORT), "可输入本机支持的模型 ID；列表是快捷项。勾选才会写入，选择「继承」会移除当前文件覆盖。运行时采用仍需验证。")
        self.agent_panel = self.config_advanced
        ttk.Label(self.config_advanced, textvariable=self.agent_effort_note, style="CardMuted.TLabel", wraplength=850).grid(row=7, column=0, columnspan=4, sticky="w", pady=(4, 0))
        for key in (core.MODEL, core.AGENT_MODEL):
            for variable in self.config_items[key]:
                variable.trace_add("write", lambda *_: self.show_model_defaults() if not self.setting_preset else None)
        numeric = section("Codex 压缩与输出", ("model_context_window", "model_auto_compact_token_limit", "tool_output_token_limit", "compact_prompt"), "这些项修改 Codex 文件。轻续的提前提醒在「目录与提醒」中设置，二者分别生效。这里的默认值来自本机目录，未知保持未知。")
        row = ttk.Frame(numeric, style="Card.TFrame")
        row.grid(row=7, column=0, columnspan=4, sticky="w", pady=(9, 0))
        ttk.Label(row, text="数值方案", style="Card.TLabel").pack(side="left")
        selector = ttk.Combobox(row, textvariable=self.preset, values=PRESETS, state="readonly", width=25)
        selector.pack(side="left", padx=8)
        selector.bind("<<ComboboxSelected>>", lambda _: self.guarded(self.select_preset, self.config_status))
        self.save_custom_button = self.button(row, "保存自定义方案", self.save_custom, feedback=self.config_status)
        self.save_custom_button.pack(side="left")
        ttk.Label(body, textvariable=self.config_source, style="Muted.TLabel", wraplength=870).pack(anchor="w", pady=(0, 8))
        self.config_preview_area = ttk.Frame(body, style="Card.TFrame", padding=14)
        self.config_result_title = tk.StringVar(value="应用预览")
        ttk.Label(self.config_preview_area, textvariable=self.config_result_title, style="Heading.TLabel").pack(anchor="w")
        self.config_text = self.text(self.config_preview_area, 7)
        self.config_text.configure(state="disabled", font=(self.font_family, 10))
        self.config_path.trace_add("write", self.config_path_edited)
        self.show_config_scope()
        self.show_model_defaults()

    def prospective(self, key, old=None):
        if key not in self.config_items:
            return old
        enabled, value = self.config_items[key]
        return (None if value.get() == INHERIT else value.get()) if enabled.get() else old

    def show_model_defaults(self):
        if not self.config_items or core.AGENT_EFFORT not in self.config_entries:
            return
        main = self.prospective(core.MODEL, self.file_model)
        defaults = self.model_reference(main) if self.use_file_catalog else {}
        self.config_source.set((f"本机模型目录 · {main} · {defaults.get('version') or '版本未知'}" if defaults else "本机模型默认值尚未核实。") + "  ·  文件值与当前会话实际采用值分开核对。")
        for key, (official, _) in self.config_cells.items():
            val = defaults.get(key)
            official.set(self.config_display_value(key, val) if val is not None else "待核实")
        self.config_cells[core.MODEL][0].set("客户端默认")
        efforts = defaults.get("efforts")
        self.config_entries["model_reasoning_effort"].configure(values=(INHERIT, *(core.EFFORTS if efforts is None else efforts)))
        self.show_agent_efforts()

    def show_agent_efforts(self, *_):
        if self.setting_preset or core.AGENT_MODEL not in self.config_items:
            return
        main = self.prospective(core.MODEL, self.file_model)
        child_explicit = self.prospective(core.AGENT_MODEL, self.file_agent_model)
        child = child_explicit or main
        reference = self.model_reference(child) if self.use_file_catalog else {}
        efforts = reference.get("efforts")
        self.config_entries[core.AGENT_EFFORT].configure(values=(INHERIT, *(core.EFFORTS if efforts is None else efforts)))
        self.config_cells[core.AGENT_MODEL][0].set("继承主模型")
        self.config_cells[core.AGENT_EFFORT][0].set(reference.get("model_reasoning_effort") or "待核实")
        self.agent_effort_note.set(f"子 Agent 预计模型：{child or '客户端默认'}。" + ("推理档位按本机模型目录列出。" if reference.get("efforts") else "本机支持档位未核实；显示通用选项。"))

    def config_entry_states(self):
        for key, entry in self.config_entries.items():
            entry.configure(state="readonly" if key in ("model_reasoning_effort", core.AGENT_EFFORT) else "normal")
        self.save_custom_button.configure(state="normal" if self.preset.get() == PRESETS[2] else "disabled")

    def select_preset(self):
        mode = self.preset.get()
        values = dict.fromkeys(core.TOKEN_KEYS)
        if mode == PRESETS[2]:
            path = self.custom_path()
            values = json.loads(read_file(path, 65536).decode("utf-8")) if path.exists() else {}
            if values:
                core.validate(values)
        self.setting_preset = True
        try:
            for key in core.TOKEN_KEYS:
                enabled, value = self.config_items[key]
                enabled.set(key in values)
                if key in values:
                    value.set(INHERIT if values[key] is None else str(values[key]))
            # A numeric preset must never delete or alter either Agent model.
            self.config_entry_states()
        finally:
            self.setting_preset = False
        self.invalidate_config("已选择数值方案；主/子 Agent 选择保持原样。请核对勾选并预览。")

    def preview_config(self):
        self.invalidate_config("请核对文件和勾选项。")
        if self.original_config is None or Path(self.config_path.get()) != self.original_config_path:
            raise ValueError("请先读取所选配置。")
        selection = self.config_changes()
        if read_file(self.original_config_path) != self.original_config:
            raise ValueError("配置文件已被其他程序改动。请重新读取，再预览。")
        parsed = core.parse(self.original_config)
        changes = {key: value for key, value in selection.items() if core.config_value(parsed, key) != value}
        if not changes:
            self.config_status.set("文件已经是这些值，当前无需写入。")
            return
        if self.use_file_catalog:
            main, effort, child, child_effort = final_models(parsed, selection)
            self.check_model_effort(main, effort)
            self.check_model_effort(child, child_effort)
        candidate = core.propose(self.original_config, changes)
        self.config_preview = (self.original_config_path, self.original_config, selection, changes, sha(candidate))
        text = f"{self.original_config_path}\n\n" + "\n\n".join(CONFIG_FIELDS[key][0] + "\n" + self.config_display_value(key, core.config_value(parsed, key)) + "  →  " + self.config_display_value(key, value) for key, value in changes.items())
        self.show_config_result(text)
        self.config_apply.configure(state="normal")
        self.config_status.set("预览已就绪。应用会先备份，只修改勾选项，并读回核验；本会话采用情况仍未知。")

    def scroll_page(self, frame):
        canvas, form = super().scroll_page(frame)
        def wheel(event, direction):
            if event.widget is canvas or str(event.widget).startswith(str(form) + "."):
                if not isinstance(event.widget, (tk.Text, ttk.Treeview, ttk.Combobox)):
                    canvas.yview_scroll(direction, "units")
        self.root.bind("<Button-4>", lambda e: wheel(e, -2), add="+")
        self.root.bind("<Button-5>", lambda e: wheel(e, 2), add="+")
        return canvas, form
