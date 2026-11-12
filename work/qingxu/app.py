"""Qingxu: an offline operator UI for the existing private project relay."""
import difflib
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import core
from chats import path_key
from config_store import ConfigStore, model_defaults
from context_usage import DEFAULT_REMINDER_PERCENT, chat_metrics, file_limit, reminder
from privacy import ensure_private
from relay import Relay, build_prompt
from sync import locks, read_file, replace_checked, safe_path, sha
from dashboard import ChatDashboard, Dashboard, ACCENT, BG, LABEL, SECONDARY, SEPARATOR, SURFACE

TITLE = "轻续 · Token 工作台"
VERSION = "0.11.2-rc1"
INHERIT = "继承"
PRESETS = ("官方默认（继承）", "轻续推荐（继承数值）", "用户自定义")
FILE_MODEL = "所选文件的模型"
MODEL_CHOICES = {
    "Astra · gpt-6-astra": "gpt-6-astra",
    "Sol · gpt-6.1-sol": "gpt-6.1-sol",
    "Terra · gpt-6.1-terra": "gpt-6.1-terra",
}
CONFIG_RESTART = ("应用或恢复后先在旧聊天保存交接。桌面端完全退出并重新打开 Codex，再开同项目新聊天验证。"
                  "CLI 退出并重新启动 Codex。无需重启 Windows。")
CONFIG_FIELDS = {
    "model": ("主 Agent 模型", "继承客户端默认"),
    "model_context_window": ("上下文窗口 (tokens)", "继承模型默认"),
    "model_auto_compact_token_limit": ("自动压缩阈值 (tokens)", "继承模型默认"),
    "tool_output_token_limit": ("工具输出上限 (tokens)", "继承模型默认"),
    "model_reasoning_effort": ("推理档位", "保留文件值"),
    "compact_prompt": ("压缩保留规则", "保留文件值"),
    core.AGENT_MODEL: ("默认子 agent 模型", "随客户端配置"),
    core.AGENT_EFFORT: ("默认子 agent 推理", "随客户端配置"),
}
KB_CATEGORIES = {
    "REQUIREMENTS": ("目标与需求", "记录需求变更、影响范围和验收条件。"),
    "PLAN": ("计划与步骤", "记录阶段拆分、依赖和下一步。"),
    "NOW": ("当前进度", "写入已验收的阶段、证据和余项。"),
    "DECISIONS": ("决策与阻塞", "记录重要决定、取舍或长期阻塞。"),
    "LESSONS": ("经验与流程", "记录可复用的步骤、踩坑和验证方法。"),
    "RESEARCH": ("调研与资料", "记录结论、资料入口和适用范围。"),
    "BUGS": ("Bug 与版本", "记录现象、根因、修复和验证版本。"),
}


def flash_taskbar(window):
    if os.name != "nt":
        return

    class FlashInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.UINT), ("window", wintypes.HWND), ("flags", wintypes.DWORD),
                    ("count", wintypes.UINT), ("timeout", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    ancestor = user32.GetAncestor
    ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
    ancestor.restype = wintypes.HWND
    handle = ancestor(window.winfo_id(), 2)  # GA_ROOT gives the taskbar window, not TkChild.
    if not handle:
        return
    flash = FlashInfo(ctypes.sizeof(FlashInfo), handle, 2, 1, 0)
    api = user32.FlashWindowEx
    api.argtypes = [ctypes.POINTER(FlashInfo)]
    api.restype = wintypes.BOOL
    api(ctypes.byref(flash))


class ReminderDialog(simpledialog.Dialog):
    def __init__(self, parent, enabled, percent):
        self.enabled = tk.BooleanVar(parent, value=enabled)
        self.percent = tk.StringVar(parent, value=str(percent))
        super().__init__(parent, "换聊提醒设置")

    def body(self, parent):
        ttk.Checkbutton(parent, text="开启换聊提醒", variable=self.enabled,
                        style="Clean.TCheckbutton").pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(parent)
        row.pack(fill="x")
        ttk.Label(row, text="提醒比例 (%)").pack(side="left", padx=(0, 8))
        entry = ttk.Spinbox(row, from_=1, to=100, textvariable=self.percent, width=7)
        entry.pack(side="left")
        ttk.Label(parent, text="达到设定比例或记录到压缩时，顶部变黄，任务栏闪一次。"
                  "你可以在当前工作段结束后决定是否换聊。", wraplength=320
                  ).pack(anchor="w", pady=(8, 0))
        return entry

    def buttonbox(self):
        buttons = ttk.Frame(self, padding=(8, 4))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="保存设置", style="Primary.TButton", command=self.ok).pack(side="right")
        ttk.Button(buttons, text="取消", command=self.cancel).pack(side="right", padx=(0, 5))
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

    def validate(self):
        try:
            if 1 <= int(self.percent.get()) <= 100:
                return True
        except ValueError:
            pass
        messagebox.showerror("提醒比例", "请输入 1 到 100 的整数。", parent=self)
        return False

    def apply(self):
        self.result = self.enabled.get(), int(self.percent.get())


class App:
    def __init__(self, root, relay=None, data_root=None):
        self.root = root
        self.relay = relay or Relay()
        self.data_root = ensure_private(Path(data_root or Path(__file__).parent / "data").absolute())
        self.config_store = ConfigStore(self.data_root / "config-backups")
        self.reminder_file = self.data_root / "chat-reminder.json"
        self.reminder_enabled, self.reminder_percent = self.load_reminder()
        self.pid = None
        self.chat = None
        self.snapshot = {}
        self.config_preview = None
        self.catalog = None
        self.metric_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qingxu-metrics")
        self.metric_timer, self.metric_job = None, None
        self.refresh_pending = False
        self.request_cache = {}
        self.alerted = set()
        self.status = tk.StringVar(value="选择项目后会打开最新聊天。")
        self.root.title(f"{TITLE} {VERSION}")
        icon = Path(__file__).with_name("qingxu.ico")
        if os.name == "nt" and icon.is_file():
            self.root.iconbitmap(default=str(icon))
        self.root.geometry("1150x875")
        self.root.minsize(850, 640)
        self.root.configure(bg=BG)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), background=BG)
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(2, 4, 2, 0))
        style.configure("TNotebook.Tab", padding=(13, 5), borderwidth=0, font=("Microsoft YaHei UI", 10))
        style.map("TNotebook.Tab", background=[("selected", SURFACE), ("!selected", "#ECEEF2")],
                  foreground=[("selected", LABEL), ("!selected", SECONDARY)])
        style.configure("TCombobox", padding=(6, 3), fieldbackground=SURFACE, background="#EDF0F4",
                        bordercolor=SEPARATOR, lightcolor=SURFACE, darkcolor=SEPARATOR, arrowsize=12)
        style.map("TCombobox", fieldbackground=[("readonly", SURFACE)], foreground=[("readonly", LABEL)])
        style.configure("TEntry", padding=(5, 3), bordercolor=SEPARATOR, lightcolor=SURFACE,
                        darkcolor=SEPARATOR)
        style.configure("TButton", padding=(10, 6), relief="flat", borderwidth=0)
        style.configure("Toolbutton", padding=(8, 5), relief="flat", borderwidth=0, background="#E9E9EE")
        style.map("Toolbutton", background=[("selected", ACCENT), ("active", "#DBDFE5")],
                  foreground=[("selected", SURFACE), ("!selected", LABEL)])
        style.configure("Primary.TButton", background=ACCENT, foreground="#FFFFFF",
                        font=("Microsoft YaHei UI", 10, "bold"), padding=(10, 7))
        style.map("Primary.TButton", background=[("disabled", "#B0D7FF"),
                                                  ("pressed", "#0062CC")])
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 22, "bold"),
                        foreground=LABEL, background=BG)
        style.configure("Card.TFrame", background=SURFACE, relief="flat")
        style.configure("Card.TLabel", background=SURFACE, foreground=SECONDARY)
        style.configure("CardTitle.TLabel", background=SURFACE, foreground=LABEL,
                        font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Notice.TLabel", background="#E2EDF8", foreground=LABEL, padding=(10, 5))
        style.configure("Alert.TLabel", background="#FFF0CB", foreground="#69400C", padding=(10, 5))
        style.configure("Number.TLabel", background=SURFACE, foreground=LABEL,
                        font=("Bahnschrift", 22))
        style.configure("Section.TLabel", foreground=LABEL, background=BG,
                        font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Treeview", rowheight=28, fieldbackground=SURFACE, background=SURFACE, borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"),
                        background="#ECEEF2", foreground=LABEL, relief="flat")
        style.configure("Metrics.Treeview", rowheight=22, fieldbackground=SURFACE, background=SURFACE)
        self.configure_checks(style)
        header = ttk.Frame(root)
        header.pack(fill="x", padx=20, pady=(10, 5))
        ttk.Label(header, text="轻续", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"Token 工作台  ·  {VERSION}", foreground=SECONDARY).pack(side="left", padx=12)
        notice = ttk.Frame(root)
        notice.pack(fill="x", padx=20, pady=(0, 6))
        notice.columnconfigure(0, weight=1)
        self.context_notice = tk.StringVar()
        self.context_banner = ttk.Label(notice, textvariable=self.context_notice, style="Notice.TLabel", wraplength=790)
        self.context_banner.grid(row=0, column=0, sticky="ew")
        self.set_context_notice("选一次聊天开始监测。")
        notice_actions = ttk.Frame(notice)
        notice_actions.grid(row=0, column=1, sticky="e")
        self.reminder_caption = tk.StringVar(value=self.reminder_caption_text())
        ttk.Button(notice_actions, textvariable=self.reminder_caption,
                   command=lambda: self.guarded(self.edit_reminder)).pack(side="left", padx=(8, 0))
        ttk.Button(notice_actions, text="去保存交接", command=lambda: self.tabs.select(self.frames[0])
                   ).pack(side="right", padx=(8, 0))
        notice.bind("<Configure>", lambda event: self.context_banner.configure(
            wraplength=max(180, event.width - notice_actions.winfo_reqwidth() - 22)))
        self.picker = ttk.Frame(root)
        self.picker.pack(fill="x", padx=18)
        modes = ttk.Frame(self.picker)
        modes.grid(row=0, column=0, padx=(0, 12), sticky="w")
        self.source_kind = tk.StringVar(value="按项目选聊天")
        for label, value in (("按项目", "按项目选聊天"), ("最近聊天", "Codex 最近聊天")):
            ttk.Radiobutton(modes, text=label, value=value, variable=self.source_kind, style="Toolbutton",
                            command=lambda: self.guarded(self.reload_choices, self.status)).pack(side="left", padx=(0, 6))
        self.project_label = ttk.Label(self.picker, text="项目")
        self.project_label.grid(row=0, column=1, sticky="w")
        self.category_choice = ttk.Combobox(self.picker, state="readonly", width=20)
        self.category_choice.grid(row=0, column=2, padx=(6, 12), sticky="ew")
        self.category_choice.bind("<<ComboboxSelected>>", lambda _: self.guarded(self.filter_chats, self.status))
        ttk.Label(self.picker, text="聊天").grid(row=0, column=3, sticky="w")
        self.project_choice = ttk.Combobox(self.picker, state="disabled", width=25)
        self.project_choice.grid(row=0, column=4, padx=6, sticky="ew")
        self.picker.columnconfigure(4, weight=1)
        self.project_choice.bind("<<ComboboxSelected>>", lambda _: self.guarded(self.select_project, self.status))
        self.button(self.picker, "刷新列表", self.reload_choices, feedback=self.status).grid(row=0, column=5)
        self.choice_records = {}
        self.loaded_category = ""
        self.loaded_source = self.source_kind.get()
        self.selection_info = tk.StringVar(value="选择项目后会打开最新聊天。")
        ttk.Label(self.picker, textvariable=self.selection_info, foreground=SECONDARY,
                  wraplength=790).grid(row=1, column=0, columnspan=6, sticky="w", pady=(4, 0))
        ttk.Label(root, textvariable=self.status, wraplength=1050, foreground=LABEL,
                  font=("Microsoft YaHei UI", 10)).pack(side="bottom", fill="x", padx=20, pady=(3, 8))
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=20, pady=(5, 5))
        self.frames = []
        for name in ("交接与知识库", "聊天用量", "配置与恢复"):
            frame = ttk.Frame(self.tabs, padding=12)
            self.tabs.add(frame, text=name)
            self.frames.append(frame)
        self.build_relay(self.frames[0])
        self.build_config(self.frames[2])
        self.build_status(self.frames[1])
        self.tabs.bind("<<NotebookTabChanged>>", self.workspace_changed)
        self.root.report_callback_exception = self.callback_error
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Destroy>", self.stop_metrics, add="+")
        try:
            self.reload_choices()
            self.recall_chat()
        except Exception as exc:
            self.status.set("聊天列表暂不可读。可稍后点「刷新列表」。" + str(exc))

    def configure_checks(self, style):
        off = tk.PhotoImage(master=self.root, width=15, height=15)
        off.put("#B9C0CA", to=(1, 1, 14, 14))
        off.put(SURFACE, to=(2, 2, 13, 13))
        on = tk.PhotoImage(master=self.root, width=15, height=15)
        on.put(ACCENT, to=(1, 1, 14, 14))
        for x, y in ((4, 7), (5, 8), (6, 9), (7, 8), (8, 7), (9, 6), (10, 5)):
            on.put(SURFACE, to=(x, y, x + 2, y + 2))
        self.check_images = off, on
        if "Qingxu.indicator" not in style.element_names():
            style.element_create("Qingxu.indicator", "image", off, ("selected", on), sticky="w")
        style.layout("Clean.TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Qingxu.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "w"})]})]})])
        style.configure("Clean.TCheckbutton", background=SURFACE, padding=(3, 4))

    def reload_choices(self):
        try:
            result = self.relay.chats(limit=None)
        except Exception:
            self.source_kind.set(self.loaded_source)
            raise
        identity = self.chat["chat_id"] if self.chat else None
        current = next((chat for chat in result["chats"] if chat["chat_id"] == identity), None)
        same = current and self.same_chat(current)
        if self.chat and not same and not self.discard_ok():
            self.source_kind.set(self.loaded_source)
            return False
        self.chat_catalog(result)
        self.set_categories(result["app_projects"])
        label = self.loaded_category if self.loaded_category in self.category_map else ""
        if current:
            label = self.category_for(current)
        self.show_choices(label, current["chat_id"] if current else None)
        return True

    def set_categories(self, projects):
        self.category_map = {}
        for project in projects:
            label = project["label"]
            if sum(p["label"] == label for p in projects) > 1:
                label += " · " + project["app_project_id"]
            self.category_map[label] = project["app_project_id"]
        self.category_map["未归类"] = None
        self.category_choice["values"] = list(self.category_map)

    def category_for(self, record):
        return next(label for label, key in self.category_map.items() if key == record["app_project_id"])

    def show_choices(self, category, identity=None):
        self.loaded_source = self.source_kind.get()
        recent = self.loaded_source == "Codex 最近聊天"
        self.loaded_category = category
        self.category_choice.set(category)
        if recent:
            self.project_label.grid_remove()
            self.category_choice.grid_remove()
        else:
            self.project_label.grid()
            self.category_choice.grid()
        records = (chat for chat in self.catalog["chats"]
                   if recent or (category and chat["app_project_id"] == self.category_map[category]))
        self.choice_records = self.chat_records(records)
        self.project_choice.configure(values=list(self.choice_records),
                                      state="readonly" if self.choice_records else "disabled")
        if self.choice_records:
            label = next((label for label, chat in self.choice_records.items() if chat["chat_id"] == identity),
                         next(iter(self.choice_records)))
            self.project_choice.set(label)
            self.apply_selection(self.choice_records[label])
        else:
            self.project_choice.set("暂无聊天" if category or recent else "")
            self.pid, self.chat = None, None
            self.clear_project()
            self.selection_info.set("此项目暂无活跃聊天。" if category else "选择项目后会打开最新聊天。")

    def filter_chats(self):
        if not self.discard_ok():
            self.category_choice.set(self.loaded_category)
            return
        self.show_choices(self.category_choice.get())

    @staticmethod
    def chat_records(chats):
        records = {}
        for chat in chats:
            try:
                updated = datetime.fromtimestamp(chat["updated_at"]).strftime("%m-%d %H:%M")
            except (ValueError, TypeError, OverflowError, OSError):
                updated = "时间未知"
            label = f'{chat["label"]}  ·  {updated}'
            if label in records:
                label += "  ·  " + chat["chat_id"]
            records[label] = chat
        return records

    def clear_project(self):
        self.snapshot = {}
        self.kb_mode.set("单独生成")
        self.copied_summary = ""
        self.put(self.summary_text, "")
        self.set_prompt("", None)
        for choice in self.kb_checks.values():
            choice.set(False)
        self.update_relay_state()
        self.relay_feedback.set("先生成旧聊天的交接指令，或勾选要更新的知识库分类。" if self.chat
                                else "请先选择聊天。")
        self.watch_chat()

    def unsaved(self):
        prompt = self.prompt_text.get("1.0", "end-1c")
        summary = self.summary_text.get("1.0", "end-1c")
        return bool(prompt and prompt not in (self.generated_prompt, self.copied_prompt)) or (
            bool(summary and summary not in (self.copied_summary, self.snapshot.get("summary"))))

    def discard_ok(self):
        return not self.unsaved() or messagebox.askyesno(
            TITLE, "有尚未复制的编辑。是否放弃这些编辑？", parent=self.root)

    def close(self):
        if self.discard_ok():
            self.root.destroy()

    def callback_error(self, kind, value, trace):
        self.status.set("操作未完成；不要视作已保存或已同步。")
        messagebox.showerror(TITLE, str(value), parent=self.root)

    def guarded(self, action, feedback=None):
        try:
            return action()
        except (ValueError, OSError) as exc:
            if feedback is not None:
                feedback.set(str(exc))
                return
            self.status.set("已停止该操作。" + str(exc))
            messagebox.showerror(TITLE, str(exc), parent=self.root)
        except Exception as exc:
            if feedback is not None:
                feedback.set("操作未完成 · " + str(exc))
            else:
                self.status.set("已停止该操作 · " + str(exc))
                messagebox.showerror(TITLE, str(exc), parent=self.root)

    def button(self, parent, text, action, style=None, feedback=None):
        return ttk.Button(parent, text=text, style=style,
                          command=lambda: self.guarded(action, feedback))

    def text(self, parent, height):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=5)
        box = tk.Text(frame, height=height, wrap="word", undo=True, bg=SURFACE,
                      fg=LABEL, insertbackground=ACCENT, relief="flat",
                      highlightthickness=1, highlightbackground=SEPARATOR,
                      highlightcolor=ACCENT,
                      font=("Microsoft YaHei UI", 11), padx=12, pady=10)
        scroll = ttk.Scrollbar(frame, command=box.yview)
        box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        box.pack(side="left", fill="both", expand=True)
        return box

    @staticmethod
    def put(box, value):
        state = box.cget("state")
        if state == "disabled":
            box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", value)
        if state == "disabled":
            box.configure(state="disabled")

    def selected(self):
        if not self.pid:
            if self.chat:
                raise ValueError("此聊天可粘贴旧聊天返回的摘要继续。")
            raise ValueError("请先选择聊天。")
        project = self.relay.project(self.pid)
        if self.chat and path_key(project["path"]) != path_key(self.chat["path"]):
            raise ValueError("接力登记的路径已变，请刷新列表并重新选择聊天。")
        return project

    def select_project(self, event=None):
        if not self.discard_ok():
            previous = next((label for label, record in self.choice_records.items()
                             if (record.get("chat_id") == self.chat["chat_id"] if self.chat
                                 else record.get("project_id") == self.pid)), "")
            self.project_choice.set(previous)
            return
        record = self.choice_records[self.project_choice.get()]
        self.apply_selection(record)

    def same_chat(self, record):
        return self.chat is not None and (
            record["chat_id"] == self.chat["chat_id"]
            and record.get("project_id") == self.pid
            and path_key(record["path"]) == path_key(self.chat["path"]))

    def apply_selection(self, record):
        same = self.same_chat(record)
        self.pid, self.chat = record.get("project_id"), record
        if not same:
            self.clear_project()
        group = record.get("app_project_name") or "未归类"
        path = record["path"].removeprefix("\\\\?\\")
        self.selection_info.set(f"{group}  ›  {record['label']}   ·   {path}")
        self.status.set("当前聊天已选定。")
        try:
            path = self.data_root / "last-chat.json"
            content = json.dumps({"chat_id": record["chat_id"]}) + "\n"
            if path.exists():
                original = read_file(path, 512)
                if original != content.encode("utf-8"):
                    replace_checked(path, sha(original), content.encode("utf-8"))
            else:
                core.export_new(path, content)
        except (ValueError, OSError):
            self.status.set("已选择。下次打开时可能需要重新选聊天。")

    def build_relay(self, frame):
        split = ttk.Panedwindow(frame, orient="horizontal")
        split.pack(fill="both", expand=True)
        sidebar = ttk.Frame(split, padding=(0, 0, 7, 0))
        editor = ttk.Frame(split, padding=(7, 0, 0, 0))
        split.add(sidebar, weight=0)
        split.add(editor, weight=1)
        self.relay_canvas, sidebar_content = self.scroll_page(sidebar)
        self.relay_canvas.configure(width=310)
        actions = ttk.Frame(sidebar_content, style="Card.TFrame", padding=(11, 8))
        actions.pack(fill="x", pady=(0, 8))
        self.relay_feedback = tk.StringVar()
        self.relay_state = tk.StringVar(value="选择聊天后可生成交接指令。")
        ttk.Label(actions, text="任务交接", style="CardTitle.TLabel").pack(anchor="w")
        row = ttk.Frame(actions, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 3))
        for text, action, style in (
            ("复制保存指令", lambda: self.copy_generated("save"), "Primary.TButton"),
            ("核对交接摘要", self.review_summary, None),
            ("复制恢复指令", lambda: self.copy_generated("restore"), None),
        ):
            self.button(row, text, action, style, self.relay_feedback).pack(fill="x", pady=(0, 4))
        ttk.Label(actions, textvariable=self.relay_state, style="Card.TLabel",
                  wraplength=245).pack(anchor="w")

        knowledge = ttk.Frame(sidebar_content, style="Card.TFrame", padding=(11, 8))
        knowledge.pack(fill="x", pady=(0, 8))
        ttk.Label(knowledge, text="项目知识库", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(knowledge, text="勾选分类，生成给 Codex 的指令。", style="Card.TLabel", wraplength=245
                  ).pack(anchor="w", pady=(2, 1))
        checks = ttk.Frame(knowledge, style="Card.TFrame")
        checks.pack(fill="x")
        self.kb_checks = {}
        for index, (kind, (label, _)) in enumerate(KB_CATEGORIES.items()):
            value = tk.BooleanVar(value=False)
            self.kb_checks[kind] = value
            ttk.Checkbutton(checks, text=label, variable=value, style="Clean.TCheckbutton",
                            command=self.knowledge_selection_edited
                            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 7))
        tools = ttk.Frame(knowledge, style="Card.TFrame")
        tools.pack(fill="x", pady=(3, 0))
        self.kb_mode = tk.StringVar(value="单独生成")
        mode = ttk.Combobox(tools, textvariable=self.kb_mode, state="readonly", width=16,
                            values=("单独生成", "附到旧聊交接"))
        mode.pack(fill="x", pady=(0, 5))
        mode.bind("<<ComboboxSelected>>", lambda _: self.knowledge_selection_edited())
        self.button(tools, "生成知识库指令", self.generate_knowledge_prompt,
                    "Primary.TButton", self.relay_feedback).pack(fill="x")

        panes = ttk.Notebook(editor)
        footer = ttk.Frame(editor)
        footer.pack(side="bottom", fill="x", pady=(4, 0))
        self.button(footer, "复制当前指令", self.copy_prompt, feedback=self.relay_feedback).pack(side="right")
        ttk.Label(footer, textvariable=self.relay_feedback, wraplength=275).pack(side="left", fill="x", expand=True)
        panes.pack(fill="both", expand=True)
        preview = ttk.Frame(panes, padding=6)
        summary = ttk.Frame(panes, padding=6)
        panes.add(preview, text="指令详情")
        panes.add(summary, text="交接摘要")
        self.relay_tabs, self.prompt_pane, self.summary_pane = panes, preview, summary
        ttk.Label(preview, text="可直接编辑。复制后回 Codex 粘贴发送。", foreground=SECONDARY
                  ).pack(anchor="w")
        self.prompt_text = self.text(preview, 6)
        self.prompt_kind = None
        self.generated_prompt = self.copied_prompt = self.copied_summary = ""
        self.knowledge_signature = None
        ttk.Label(summary, text="这里可核对读回的本机任务摘要，也可粘贴旧聊天返回的摘要用于本次恢复。",
                  foreground=SECONDARY, wraplength=385).pack(anchor="w")
        self.summary_text = self.text(summary, 6)
        self.summary_text.bind("<<Modified>>", self.update_relay_state)

    def set_prompt(self, text, kind):
        self.put(self.prompt_text, text)
        self.generated_prompt, self.copied_prompt = text, ""
        self.prompt_kind = kind
        self.knowledge_signature = None
        self.relay_tabs.select(self.prompt_pane)

    def update_relay_state(self, event=None):
        if self.summary_text.edit_modified():
            self.summary_text.edit_modified(False)
        saved = self.snapshot.get("summary") or ""
        text = self.summary_text.get("1.0", "end-1c")
        if text and text != saved:
            state = "已粘贴摘要，可生成本次恢复指令。"
        elif saved:
            state = f'已读回任务摘要 · 版本 {self.snapshot["revision"]}'
            if self.snapshot.get("summary_stale") in (True, "STALE"):
                state += " · 现场已有变化，恢复前核对。"
        elif self.chat:
            state = "待保存摘要 · 把保存指令发给旧聊天。"
        else:
            state = "选择聊天后可生成交接指令。"
        self.relay_state.set(state)

    def review_summary(self):
        self.relay_tabs.select(self.summary_pane)
        if self.pid:
            self.load_snapshot()
        elif self.chat:
            self.relay_feedback.set("把旧聊天返回的任务摘要粘贴在这里，再复制恢复指令。")
            self.summary_text.focus_set()
        else:
            raise ValueError("请先选择聊天。")

    def load_snapshot(self, discard=False):
        self.selected()
        if not discard:
            edited = self.summary_text.get("1.0", "end-1c")
            if edited and edited != (self.snapshot.get("summary") or "") and not self.discard_ok():
                return False
        self.snapshot = self.relay.snapshot(self.pid)
        summary = self.snapshot.get("summary") or ""
        self.put(self.summary_text, summary)
        self.update_relay_state()
        self.relay_feedback.set("已读回。请核对任务和下一步。" if summary else
                                "还没有任务摘要。发保存指令后再回来，或粘贴旧聊天返回的摘要。")
        return bool(summary)

    def make_prompt(self, kind):
        if not (self.chat or self.pid):
            raise ValueError("请先选择聊天。")
        project = self.selected() if self.pid else None
        path = project["path"] if project else self.chat["path"]
        summary = self.summary_text.get("1.0", "end-1c").strip()
        manual = summary and summary != (self.snapshot.get("summary") or "").strip()
        if kind == "restore" and (manual or not project):
            if not summary or len(summary.encode("utf-8")) > 8192:
                self.relay_tabs.select(self.summary_pane)
                raise ValueError("请先把旧聊天生成的不含凭据的短摘要粘贴到「交接摘要」，最多 8192 UTF-8 字节。")
            prompt = (f"请继续此前未完成的任务。原聊天工作目录：{path}\n"
                      "以下是我手动提供的短摘要。请先核实项目目录和现场代码；如与摘要冲突，以现场证据为准。"
                      "默认不读取全部旧聊天、整个 Obsidian 或全局记忆。\n\n【短摘要】\n" + summary)
        elif kind == "restore":
            if not self.load_snapshot(discard=True):
                self.relay_tabs.select(self.summary_pane)
                raise ValueError("还没有已保存的摘要。先把保存指令发给旧聊天，或粘贴它返回的摘要。")
            prompt = build_prompt(kind, project, self.snapshot,
                                  self.relay.checkpoint_path(self.pid), self.relay.settings)
        else:
            snapshot = self.relay.snapshot(self.pid) if project else {}
            if snapshot.get("revision") is None:
                prompt = (f"当前聊天的工作目录：{path}\n"
                          "请在回复中给出可粘贴的短摘要（最多 8192 UTF-8 字节），包含目标、进度、未完成、"
                          "关键证据、验收条件和下一步。排除凭据。由我把摘要用于同项目新聊天。")
            else:
                prompt = build_prompt(kind, project, snapshot,
                                      self.relay.checkpoint_path(self.pid), self.relay.settings)
            if self.chat:
                prompt = f'来源聊天：{self.chat.get("label", "当前聊天")}。请核对工作目录。\n' + prompt
        self.set_prompt(prompt, kind)
        return True

    def copy_generated(self, kind):
        text = self.prompt_text.get("1.0", "end-1c")
        if text and text not in (self.generated_prompt, self.copied_prompt) and not self.discard_ok():
            return
        if self.make_prompt(kind):
            self.copy_prompt()

    def copy_prompt(self):
        text = self.prompt_text.get("1.0", "end-1c")
        if not text.strip():
            raise ValueError("请先生成指令。")
        if self.knowledge_signature and self.knowledge_signature != self.knowledge_selection():
            raise ValueError("知识库勾选或方式已变动。请重新生成知识库指令。")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        self.copied_prompt = text
        self.relay_tabs.select(self.prompt_pane)
        if self.prompt_kind == "restore":
            self.copied_summary = self.summary_text.get("1.0", "end-1c")
            self.relay_feedback.set("已复制。到同项目新聊天粘贴发送。")
        elif self.prompt_kind == "knowledge":
            self.relay_feedback.set("已复制。发给掌握本项目依据的 Codex 聊天。")
        else:
            name = self.chat.get("label") if self.chat else None
            self.relay_feedback.set(f"已复制。到「{name or '旧聊天'}」粘贴发送。")

    def knowledge_selection(self):
        return (tuple(kind for kind, value in self.kb_checks.items() if value.get()), self.kb_mode.get())

    def knowledge_selection_edited(self):
        if self.knowledge_signature and self.knowledge_signature != self.knowledge_selection():
            self.relay_feedback.set("勾选或生成方式已修改，请重新生成知识库指令。")

    def generate_knowledge_prompt(self):
        if not (self.chat or self.pid):
            raise ValueError("请先选择项目聊天。")
        kinds, mode = self.knowledge_selection()
        if not kinds:
            raise ValueError("请勾选至少一个要更新的分类。")
        path = self.selected()["path"] if self.pid else self.chat["path"]
        pages = {}
        if self.pid:
            try:
                pages, _ = self.relay.knowledge_mapping(self.pid)
            except (OSError, ValueError, KeyError):
                pass
        targets = "\n".join(f"- {KB_CATEGORIES[kind][0]} · {pages[kind] if kind in pages else '按项目映射核实目标页'}"
                            f"。{KB_CATEGORIES[kind][1]}" for kind in kinds)
        block = (f"请按本次勾选范围更新当前项目知识库。工作目录：{path}\n"
                 f"{targets}\n"
                 "先核实当前项目的映射与最新已确认的改动。只读并更新上述类别的目标页，保留其他内容。"
                 "若路径、缺页新建授权、证据或版本存在疑问，先问我。当前进度须有阶段验收，决策页只记录重要决定或长期阻塞。"
                 "写后读回核对并说明更新了哪些页。")
        if mode == "附到旧聊交接":
            if self.prompt_kind != "save":
                raise ValueError("先点「复制保存指令」，再把知识库指令附到旧聊交接。")
            separator = "\n\n【项目知识库更新】\n"
            text = self.prompt_text.get("1.0", "end-1c").partition(separator)[0].rstrip()
            self.set_prompt(text + separator + block, "save")
        else:
            if not self.discard_ok():
                return
            self.set_prompt(block, "knowledge")
        self.knowledge_signature = kinds, mode
        self.relay_feedback.set("已生成。核对范围和内容后，点「复制当前指令」。")

    def scroll_page(self, frame):
        canvas = tk.Canvas(frame, background=BG, highlightthickness=0, yscrollincrement=20)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        frame = ttk.Frame(canvas, padding=(0, 0, 8, 0))
        form = canvas.create_window(0, 0, anchor="nw", window=frame)
        frame.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox(form)))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(form, width=event.width))

        def scroll_form(event):
            if (event.widget is canvas or str(event.widget).startswith(str(frame) + ".")) and not isinstance(
                    event.widget, (tk.Text, ttk.Treeview, ttk.Combobox)):
                if event.delta:
                    canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        def show_focused_field(event):
            if not str(event.widget).startswith(str(frame) + "."):
                return
            offset = event.widget.winfo_rooty() - canvas.winfo_rooty()
            bottom = offset + event.widget.winfo_height()
            if offset < 0 or bottom > canvas.winfo_height():
                top = canvas.canvasy(0) + (offset if offset < 0 else bottom - canvas.winfo_height())
                canvas.yview_moveto(top / max(1, frame.winfo_height()))

        self.root.bind("<MouseWheel>", scroll_form, add="+")
        self.root.bind("<FocusIn>", show_focused_field, add="+")
        return canvas, frame

    def build_config(self, frame):
        self.original_config = self.original_config_path = None
        self.setting_preset = False
        self.file_model = self.file_agent_model = None
        self.use_file_catalog = False
        self.config_status = tk.StringVar(value="读取配置后，勾选要调整的项。")
        self.config_auto_read = False
        self.config_source = tk.StringVar()
        self.config_scope = tk.StringVar()
        footer = ttk.Frame(frame, style="Card.TFrame", padding=(10, 7))
        footer.pack(side="bottom", fill="x", pady=(7, 0))
        actions = ttk.Frame(footer, style="Card.TFrame")
        actions.pack(fill="x")
        self.button(actions, "预览勾选的修改", self.preview_config, feedback=self.config_status
                    ).pack(side="left")
        self.config_apply = self.button(actions, "备份并应用", self.apply_config, style="Primary.TButton",
                                        feedback=self.config_status)
        self.config_apply.pack(side="left", padx=7)
        self.config_apply.configure(state="disabled")
        self.button(actions, "从配置备份恢复…", self.restore_config, feedback=self.config_status
                    ).pack(side="right")
        ttk.Label(footer, textvariable=self.config_status, style="Card.TLabel", wraplength=775
                  ).pack(anchor="w", pady=(4, 0))
        self.config_canvas, frame = self.scroll_page(frame)

        source = ttk.Frame(frame, style="Card.TFrame", padding=(12, 8))
        source.pack(fill="x", pady=(0, 7))
        ttk.Label(source, text="配置文件", style="CardTitle.TLabel").pack(anchor="w")
        row = ttk.Frame(source, style="Card.TFrame")
        row.pack(fill="x", pady=(5, 0))
        self.config_path = tk.StringVar(value=self.relay.settings["config"])
        ttk.Entry(row, textvariable=self.config_path).pack(side="left", fill="x", expand=True)
        self.button(row, "选择文件…", self.choose_config, feedback=self.config_status).pack(side="left", padx=6)
        self.button(row, "读取配置", self.load_config, feedback=self.config_status).pack(side="left")
        self.show_config_scope()
        ttk.Label(source, textvariable=self.config_scope, style="Card.TLabel",
                  wraplength=900).pack(anchor="w", pady=(2, 0))
        section = ttk.Frame(frame, style="Card.TFrame", padding=(12, 8))
        section.pack(fill="x", pady=(0, 7))
        ttk.Label(section, text="方案与参数", style="CardTitle.TLabel").pack(anchor="w")
        row = ttk.Frame(section, style="Card.TFrame")
        row.pack(fill="x", pady=(5, 0))
        ttk.Label(row, text="型号", style="Card.TLabel").pack(side="left")
        self.model_view = tk.StringVar(value=FILE_MODEL)
        models = ttk.Combobox(row, textvariable=self.model_view, values=(FILE_MODEL, *MODEL_CHOICES),
                              state="readonly", width=23)
        models.pack(side="left", padx=(6, 12))
        self.model_view.trace_add("write", self.config_model_edited)
        ttk.Label(row, text="方案", style="Card.TLabel").pack(side="left")
        self.preset = tk.StringVar(value=PRESETS[2])
        selector = ttk.Combobox(row, textvariable=self.preset, values=PRESETS, state="readonly", width=19)
        selector.pack(side="left", padx=6)
        selector.bind("<<ComboboxSelected>>", lambda _: self.guarded(self.select_preset, self.config_status))
        self.save_custom_button = self.button(row, "保存自定义方案", self.save_custom, feedback=self.config_status)
        self.save_custom_button.pack(side="left")
        ttk.Label(section, textvariable=self.config_source, style="Card.TLabel",
                  wraplength=790).pack(anchor="w", pady=(5, 3))
        self.config_items = {}
        self.config_entries, self.config_cells = {}, {}

        def fields(parent, keys):
            table = ttk.Frame(parent, style="Card.TFrame")
            table.pack(fill="x")
            for column, weight in enumerate((2, 1, 1, 2)):
                table.columnconfigure(column, weight=weight)
            for col, heading in enumerate(("修改项", "本机模型参考", "文件当前值", "准备应用")):
                ttk.Label(table, text=heading, style="Card.TLabel"
                          ).grid(row=0, column=col, sticky="w", padx=(0, 16), pady=3)
            for i, key in enumerate(keys, 1):
                label = CONFIG_FIELDS[key][0]
                selected = tk.BooleanVar(value=False)
                value = tk.StringVar(value=INHERIT)
                ttk.Checkbutton(table, text=label, variable=selected, style="Clean.TCheckbutton"
                                ).grid(row=i, column=0, sticky="w", pady=2)
                official, current = tk.StringVar(value="待读取"), tk.StringVar(value="待读取")
                self.config_cells[key] = (official, current)
                for col, variable in enumerate((official, current), 1):
                    ttk.Label(table, textvariable=variable, style="Card.TLabel"
                              ).grid(row=i, column=col, sticky="w", padx=(0, 16))
                if key in ("model_reasoning_effort", core.AGENT_EFFORT):
                    entry = ttk.Combobox(table, textvariable=value, values=(INHERIT, *core.EFFORTS),
                                         state="readonly", width=16)
                elif key == core.AGENT_MODEL:
                    entry = ttk.Combobox(table, textvariable=value, width=21,
                                         values=(INHERIT, "gpt-6-luna", *MODEL_CHOICES.values()))
                else:
                    entry = ttk.Entry(table, textvariable=value, width=19)
                entry.grid(row=i, column=3, sticky="ew", pady=2)
                self.config_entries[key] = entry
                self.config_items[key] = (selected, value)
                value.trace_add("write", self.config_value_edited)
                selected.trace_add("write", self.config_selection_edited)

        fields(section, (key for key in CONFIG_FIELDS if key in core.TOKEN_KEYS))
        ttk.Label(section, text="窗口是容量 · 压缩阈值是整理历史的触发点 · 工具上限是单次输出保留量。"
                  "轻续推荐先继承这三项，作为长任务的对照基线。",
                  style="Card.TLabel", wraplength=790).pack(anchor="w", pady=(6, 3))
        def disclosure(title, keys, hint=None):
            panel = ttk.Frame(section, style="Card.TFrame")

            def toggle():
                if panel.winfo_manager():
                    panel.pack_forget()
                    button.configure(text=f"{title} ▸")
                else:
                    panel.pack(after=button, fill="x", pady=(3, 5))
                    button.configure(text=f"{title} ▾")

            button = ttk.Button(section, text=f"{title} ▸", command=toggle)
            button.pack(anchor="w", pady=(3, 0))
            fields(panel, keys)
            if hint:
                ttk.Label(panel, text=hint, style="Card.TLabel", wraplength=710).pack(anchor="w", pady=(3, 0))
            return panel, button

        self.config_advanced, self.config_advanced_button = disclosure(
            "主模型高级参数", ("model_reasoning_effort", "compact_prompt"),
            "日常建议从 Medium 开始。Max 加深单项推理，Ultra 可主动分派可拆分的工作。")
        self.agent_panel, self.agent_button = disclosure(
            "子 agent 默认设置", (core.AGENT_MODEL, core.AGENT_EFFORT),
            "按勾选后的子模型列档。xhigh 为 Extra High。小任务可从 Low 或 Medium 开始。")
        self.agent_effort_note = tk.StringVar()
        ttk.Label(self.agent_panel, textvariable=self.agent_effort_note, style="Card.TLabel",
                  wraplength=710).pack(anchor="w", pady=(3, 0))
        for variable in self.config_items[core.AGENT_MODEL]:
            variable.trace_add("write", self.show_agent_efforts)
        self.config_preview_area = ttk.Frame(frame, style="Card.TFrame", padding=(12, 8))
        self.config_result_title = tk.StringVar(value="应用预览")
        ttk.Label(self.config_preview_area, textvariable=self.config_result_title,
                  style="CardTitle.TLabel").pack(anchor="w")
        self.config_text = self.text(self.config_preview_area, 6)
        self.config_text.configure(state="disabled")
        self.config_path.trace_add("write", self.config_path_edited)
        self.show_model_defaults()

    def show_config_result(self, text, title="应用预览"):
        self.config_result_title.set(title)
        self.config_text.configure(state="normal")
        self.put(self.config_text, text)
        self.config_text.configure(state="disabled")
        if text:
            self.config_preview_area.pack(fill="x", pady=(0, 7))
            self.config_canvas.update_idletasks()
            self.config_canvas.yview_moveto(1)
        else:
            self.config_preview_area.pack_forget()

    def invalidate_config(self, message):
        self.config_preview = None
        self.config_apply.configure(state="disabled")
        self.show_config_result("")
        self.config_status.set(message)

    def config_path_edited(self, *_):
        self.original_config = self.original_config_path = None
        self.file_model = self.file_agent_model = None
        self.use_file_catalog = False
        for _, current in self.config_cells.values():
            current.set("待读取")
        self.show_config_scope()
        self.show_model_defaults()
        self.invalidate_config("文件路径已更改。请读取新路径后预览。")

    def show_config_scope(self):
        home = Path(self.relay.settings["config"]).parent
        path = Path(self.config_path.get())
        if path_key(str(path)) == path_key(str(home / "config.toml")):
            scope = "用户级配置 · 影响使用此 Codex 主目录的项目"
        elif path.name == "config.toml" and path.parent.name == ".codex":
            scope = "所选 .codex/config.toml · 需核对实际加载范围"
        else:
            scope = "所选文件或副本 · 加载范围待核对"
        self.config_scope.set(scope + " · 当前会话采用情况待验证")

    def config_value_edited(self, *_):
        if not self.setting_preset:
            self.preset.set(PRESETS[2])
            self.config_entry_states()
            self.invalidate_config("已编辑数值。请预览勾选的修改。")

    def config_selection_edited(self, *_):
        if not self.setting_preset:
            self.invalidate_config("已更改勾选。请预览勾选的修改。")

    def config_model_edited(self, *_):
        if not self.setting_preset:
            self.show_model_defaults()
            self.guarded(self.select_preset, self.config_status)

    def custom_path(self):
        model = MODEL_CHOICES.get(self.model_view.get(), self.file_model if self.use_file_catalog else None)
        filename = f"custom-preset-{model}.json" if model in MODEL_CHOICES.values() else "custom-preset.json"
        return safe_path(self.data_root / filename)

    def select_preset(self):
        mode = self.preset.get()
        self.invalidate_config("方案已选。请核对勾选项，再预览。")
        values = dict.fromkeys((key for key in self.config_items if key not in core.AGENT_KEYS)
                               if mode == PRESETS[0] else core.TOKEN_KEYS)
        saved_status = ""
        viewed = MODEL_CHOICES.get(self.model_view.get())
        matches_file = viewed is None or (viewed == self.file_model and self.use_file_catalog)
        if mode == PRESETS[2]:
            path = self.custom_path()
            legacy = safe_path(self.data_root / "custom-preset.json")
            if matches_file and not path.exists() and legacy.exists():
                path = legacy
                saved_status = "已载入旧通用方案。复核后可保存为此型号的方案。"
            values = json.loads(read_file(path, 65536).decode("utf-8")) if path.exists() else {}
            if values:
                core.validate(values)
            else:
                saved_status = "此型号暂无自定义方案。勾选并填写数值后可保存。"
        self.setting_preset = True
        try:
            original = core.parse(self.original_config) if matches_file and self.original_config is not None else {}
            for key, (enabled, value) in self.config_items.items():
                enabled.set(key in values)
                target = values.get(key, core.config_value(original, key))
                value.set(INHERIT if target is None else str(target))
            self.config_entry_states()
        finally:
            self.setting_preset = False
        self.show_agent_efforts()
        if {"model_reasoning_effort", "compact_prompt"} & values.keys() and not self.config_advanced.winfo_manager():
            self.config_advanced_button.invoke()
        if core.AGENT_KEYS & values.keys() and not self.agent_panel.winfo_manager():
            self.agent_button.invoke()
        if mode == PRESETS[0]:
            self.config_status.set("勾选项会移除所选文件的顶层覆盖。项目、配置档和启动参数仍可能覆盖。")
        elif mode == PRESETS[1]:
            self.config_status.set("三项数值继承所选模型和客户端默认，便于对照用量和返工。主模型推理、压缩规则及子 agent 设置沿用文件值。")
        elif saved_status:
            self.config_status.set(saved_status)

    def config_entry_states(self):
        editable = self.preset.get() == PRESETS[2]
        for key, entry in self.config_entries.items():
            entry.configure(state="disabled" if not (editable or key in core.AGENT_KEYS) else
                            "readonly" if key in ("model_reasoning_effort", core.AGENT_EFFORT) else "normal")
        self.save_custom_button.configure(state="normal" if editable else "disabled")

    def save_custom(self, keys=None):
        if self.preset.get() != PRESETS[2]:
            raise ValueError("请切换到自定义方案后再保存数值。")
        values = self.config_changes(keys)
        path = self.custom_path()
        text = json.dumps(values, ensure_ascii=False)
        with locks([path]):
            if path.exists():
                replace_checked(path, sha(read_file(path)), text.encode("utf-8"))
            else:
                core.export_new(path, text)
        if json.loads(read_file(path).decode("utf-8")) != values:
            raise ValueError("自定义方案写后读回不一致")
        self.invalidate_config(f"已保存 {path.name}。应用到 Codex 前请先预览。")

    def choose_config(self):
        path = filedialog.askopenfilename(parent=self.root, title="首次建议选择 config.toml 的副本")
        if path:
            self.config_path.set(path)

    def load_config(self):
        self.config_path_edited()
        path = safe_path(self.config_path.get())
        original = core.read_target(path)
        if read_file(path) != original:
            raise ValueError("读取时文件发生变化。请再读一次。")
        self.show_config(path, original)

    def show_config(self, path, original):
        config = core.parse(original)
        self.original_config, self.original_config_path = original, path
        model = config.get("model")
        self.file_model = model if isinstance(model, str) else None
        self.file_agent_model = core.config_value(config, core.AGENT_MODEL)
        self.use_file_catalog = (config.get("model_provider", "openai") == "openai"
                                 and not config.get("model_catalog_json"))
        self.setting_preset = True
        try:
            self.model_view.set(FILE_MODEL)
            self.preset.set(PRESETS[2])
            for key, (enabled, value) in self.config_items.items():
                enabled.set(False)
                actual = core.config_value(config, key)
                value.set(self.config_edit_value(key, actual))
            self.config_entry_states()
        finally:
            self.setting_preset = False
        self.show_model_defaults()
        for key, (_, current) in self.config_cells.items():
            value = core.config_value(config, key)
            current.set(self.config_display_value(key, None) if value is None else "已自定义" if key == "compact_prompt" else
                        f"{value:,}" if type(value) is int else str(value)[:25])
        self.invalidate_config("读取成功。表中显示所选文件，当前聊天的实际设置需另行核对。")

    def model_reference(self, model):
        if isinstance(model, str) and model:
            catalog = Path(self.relay.settings["config"]).with_name("models_cache.json")
            try:
                return model_defaults(catalog, model)
            except (ValueError, OSError, TypeError, KeyError, AttributeError):
                pass
        return {}

    def show_agent_efforts(self, *_):
        if self.setting_preset:
            return
        checked, entered = self.config_items[core.AGENT_MODEL]
        child_model = entered.get() if checked.get() else self.file_agent_model
        if child_model == INHERIT:
            child_model = None
        model = child_model or self.file_model
        reference = self.model_reference(model) if self.use_file_catalog else {}
        efforts = reference.get("efforts")
        self.config_entries[core.AGENT_EFFORT].configure(
            values=(INHERIT, *(core.EFFORTS if efforts is None else efforts)))
        self.config_cells[core.AGENT_MODEL][0].set("继承父级模型")
        self.config_cells[core.AGENT_EFFORT][0].set(
            (reference.get("model_reasoning_effort") or "待核实") if child_model else "随父级推理")
        self.agent_effort_note.set(f"本机目录 · {model}" if efforts is not None else
                                   "型号支持待核实。下拉框显示通用配置档位。")

    def show_model_defaults(self):
        viewed = MODEL_CHOICES.get(self.model_view.get())
        model = viewed or self.file_model
        defaults = self.model_reference(model) if viewed or self.use_file_catalog else {}
        source = "文件模型 " + (self.file_model or "未明确")
        if viewed:
            source += "  ·  正在查看 " + viewed
            if viewed != self.file_model or not self.use_file_catalog:
                source += "（可保存方案，应用需选择明确使用该型号的配置文件）"
        if defaults:
            source += f"  ·  本机 Codex 模型目录 {defaults['version'] or '版本未知'}"
            maximum = defaults["maximum"]
            if maximum:
                source += f"  ·  目录窗口上限 {maximum:,} tokens"
            source += "\n压缩默认值按默认窗口计算。保留窗口覆盖时，继承的压缩值可能变化。"
        else:
            source += "  ·  未核实此模型的默认数值。"
        self.config_source.set(source)
        for key, (official, _) in self.config_cells.items():
            value = defaults.get(key)
            official.set(self.config_display_value(key, value) if value is not None else
                         "待核实" if key in core.TOKEN_KEYS else "随模型或客户端")
        efforts = defaults.get("efforts")
        self.config_entries["model_reasoning_effort"].configure(
            values=(INHERIT, *(core.EFFORTS if efforts is None else efforts)))
        self.show_agent_efforts()

    def check_model_effort(self, model, effort):
        supported = self.model_reference(model).get("efforts")
        if effort is not None and supported is not None and effort not in supported:
            raise ValueError(f"本机目录未给 {model} 列出 {effort}。请从该模型的下拉框选档，或使用默认档位。")

    @staticmethod
    def config_edit_value(key, value):
        return INHERIT if value is None else str(value)

    def config_input_value(self, key, entered):
        return None if entered.strip() == INHERIT else entered

    def config_changes(self, keys=None):
        changes = {}
        for key, (enabled, value) in self.config_items.items():
            if enabled.get() and (keys is None or key in keys):
                entered = self.config_input_value(key, value.get())
                if entered is None:
                    changes[key] = None
                elif key in core.TOKEN_KEYS:
                    try:
                        changes[key] = int(entered)
                    except ValueError as exc:
                        raise ValueError(CONFIG_FIELDS[key][0] + "请填写整数，例如 200000。") from exc
                else:
                    changes[key] = entered
        if not changes:
            raise ValueError("请勾选要调整的参数。")
        core.validate(changes)
        return changes

    def preview_config(self):
        self.invalidate_config("请核对文件和勾选项。")
        if self.original_config is None or Path(self.config_path.get()) != self.original_config_path:
            raise ValueError("请先读取所选配置。")
        selection = self.config_changes()
        viewed = MODEL_CHOICES.get(self.model_view.get())
        if selection.keys() - core.AGENT_KEYS and viewed and (viewed != self.file_model or not self.use_file_catalog):
            raise ValueError("查看的型号与所选文件不匹配。请选择对应文件，或切回「所选文件的模型」。")
        if read_file(self.original_config_path) != self.original_config:
            raise ValueError("配置文件已被其他程序改动。请重新读取，再预览。")
        parsed = core.parse(self.original_config)
        changes = {key: value for key, value in selection.items() if core.config_value(parsed, key) != value}
        if not changes:
            self.config_status.set("文件已经是这些值，当前无需写入。")
            return
        if self.use_file_catalog:
            if "model_reasoning_effort" in changes:
                self.check_model_effort(self.file_model, changes["model_reasoning_effort"])
            if core.AGENT_KEYS & changes.keys():
                child_model = selection.get(core.AGENT_MODEL, self.file_agent_model) or self.file_model
                child_effort = selection.get(core.AGENT_EFFORT, core.config_value(parsed, core.AGENT_EFFORT))
                self.check_model_effort(child_model, child_effort)
        candidate = core.propose(self.original_config, changes)
        self.config_preview = (self.original_config_path, self.original_config, selection, changes, sha(candidate))
        text = f"{self.original_config_path}\n\n" + "\n\n".join(
            CONFIG_FIELDS[key][0] + "\n"
            + self.config_display_value(key, core.config_value(parsed, key)) + "  →  "
            + self.config_display_value(key, value) for key, value in changes.items())
        self.show_config_result(text)
        self.config_apply.configure(state="normal")
        self.config_canvas.yview_moveto(1.0)
        self.config_status.set("预览已就绪。核对下方修改后，点「备份并应用」。")

    def apply_config(self):
        preview = self.config_preview
        if preview is None:
            raise ValueError("请先预览勾选的修改。")
        path, original, selection, changes, expected = preview
        if read_file(path) != original:
            self.invalidate_config("配置文件已变化。请重新读取，再预览。")
            return
        if not messagebox.askyesno(TITLE, f"备份并应用这份预览？\n{path}\n\n{CONFIG_RESTART}", parent=self.root):
            self.config_status.set("已取消应用。你仍可核对预览。")
            return
        if (self.config_preview is not preview or Path(self.config_path.get()) != path
                or self.config_changes() != selection):
            self.invalidate_config("确认期间选择已变化。请重新预览。")
            return
        candidate = core.propose(original, changes)
        if sha(candidate) != expected:
            self.invalidate_config("预览已变化。请重新预览。")
            return
        self.invalidate_config("正在核对文件并备份。")
        receipt = self.config_store.apply(path, original, changes)
        actual = read_file(path)
        if sha(actual) != expected:
            self.show_config(path, actual)
            self.config_status.set("写入后检测到外部更改。请核对最新文件。")
            return
        self.show_config(path, actual)
        applied = core.parse(actual)
        lines = "\n".join(f"{CONFIG_FIELDS[key][0]}  {self.config_display_value(key, core.config_value(applied, key))}"
                          for key in changes)
        self.show_config_result(f"{path}\n{lines}\n\n恢复回执\n{receipt}", "写后读回一致")
        self.config_status.set("文件已写入并读回。运行时采用待验证，应用实际配置后请重开 Codex 核对。")

    @staticmethod
    def config_display_value(key, value):
        if value is None:
            return "继承"
        if key == "compact_prompt":
            return "已设置压缩规则"
        return f"{value:,}" if type(value) is int else str(value)

    def restore_config(self):
        path = filedialog.askopenfilename(parent=self.root, initialdir=self.config_store.root,
                                          title="选择配置备份 receipt.json")
        if path:
            _, original, target = self.config_store.load(path)
            if messagebox.askyesno(TITLE, "恢复这个目标？\n" + str(target)
                                  + "\n若存在本次应用后的其他改动，将拒绝恢复。", parent=self.root):
                result = self.config_store.restore(path)
                if Path(self.config_path.get()) == target:
                    self.show_config(target, original)
                self.show_config_result(f"{target}\n恢复来源\n{path}", "已读回恢复结果")
                self.config_status.set("目标文件已恢复并读回。恢复实际配置后，重开 Codex 并核对采用值。"
                                       if result == "RESTORED" else "文件已与备份一致。")

    def build_status(self, frame):
        pages = ttk.Notebook(frame)
        self.usage_tabs = pages
        pages.pack(fill="both", expand=True)
        chat_page, snapshot_page = ttk.Frame(pages, padding=8), ttk.Frame(pages, padding=8)
        pages.add(chat_page, text="当前聊天")
        pages.add(snapshot_page, text="日期快照")
        tools = ttk.Frame(chat_page)
        tools.pack(fill="x", pady=(0, 5))
        self.button(tools, "刷新项目和聊天用量", self.refresh_catalog, style="Primary.TButton",
                    feedback=self.status).pack(side="left")
        ttk.Label(tools, text="所选聊天的用量与诊断自动刷新", foreground=SECONDARY
                  ).pack(side="left", padx=8)
        self.chat_usage = ChatDashboard(chat_page, self.choose_usage_chat, self.scroll_page)
        self.chat_usage.pack(fill="both", expand=True)
        row = ttk.Frame(snapshot_page)
        row.pack(fill="x", pady=(0, 5))
        self.usage_canvas, body = self.scroll_page(snapshot_page)
        self.dashboard = Dashboard(body)
        self.button(row, "刷新用量快照", self.refresh_usage, style="Primary.TButton").pack(side="left")
        ttk.Label(row, text="本机增量快照 · 不含订阅额度和账单", foreground=SECONDARY).pack(side="left", padx=10)
        self.dashboard.pack(fill="both", expand=True)

    def workspace_changed(self, _event=None):
        if self.tabs.select() == str(self.frames[0]):
            self.picker.pack(fill="x", padx=18, before=self.tabs)
        else:
            self.picker.pack_forget()
        if self.tabs.select() == str(self.frames[2]) and not self.config_auto_read:
            self.config_auto_read = True
            if self.original_config is None:
                self.guarded(self.load_config, self.config_status)
        if self.tabs.select() == str(self.frames[1]) and self.catalog is None:
            self.guarded(self.refresh_catalog, self.chat_usage.catalog)

    def chat_catalog(self, result):
        self.catalog = result
        self.chat_usage.render_catalog(result)

    def refresh_catalog(self):
        if self.reload_choices() and self.chat:
            self.request_cache = {}
            self.refresh_pending = True
            self.schedule_metrics()

    def recall_chat(self):
        try:
            stored = json.loads(read_file(self.data_root / "last-chat.json", 512).decode("utf-8"))
            identity = stored.get("chat_id")
        except (AttributeError, OSError, ValueError):
            return
        if isinstance(identity, str):
            record = self.chat_usage.chats.get(identity)
            if record:
                self.choose_usage_chat(record)

    def choose_usage_chat(self, record, keep_source=False):
        if not (record and self.same_chat(record)) and not self.discard_ok():
            self.chat_usage.select_chat(self.chat["chat_id"] if self.chat else None)
            return
        if record is None:
            self.pid, self.chat = None, None
            self.project_choice.set("")
            self.project_choice.configure(values=(), state="disabled")
            self.category_choice.set("")
            self.loaded_category = ""
            self.clear_project()
            self.selection_info.set("此项目暂无活跃聊天。")
            self.chat_usage.heading.set("此项目暂无活跃聊天")
            return
        self.set_categories(self.catalog["app_projects"])
        if not keep_source:
            self.source_kind.set("按项目选聊天")
        self.show_choices(self.category_for(record), record["chat_id"])

    def load_reminder(self):
        try:
            data = json.loads(read_file(self.reminder_file, 512).decode("utf-8"))
            enabled, percent = data["enabled"], data["percent"]
            if type(enabled) is bool and type(percent) is int and 1 <= percent <= 100:
                return enabled, percent
        except (ValueError, OSError, TypeError, KeyError):
            pass
        return True, DEFAULT_REMINDER_PERCENT

    def reminder_caption_text(self):
        return f"提醒设置 · {self.reminder_percent}%" + ("（暂停）" if not self.reminder_enabled else "")

    def edit_reminder(self):
        dialog = ReminderDialog(self.root, self.reminder_enabled, self.reminder_percent)
        if dialog.result is not None:
            self.save_reminder(*dialog.result)

    def save_reminder(self, enabled, percent):
        if type(enabled) is not bool or type(percent) is not int or not 1 <= percent <= 100:
            raise ValueError("提醒比例必须是 1 到 100 的整数。")
        path = self.reminder_file
        text = json.dumps({"enabled": enabled, "percent": percent}) + "\n"
        with locks([path]):
            if path.exists():
                original = read_file(path, 512)
                updated = text.encode("utf-8")
                if original != updated:
                    replace_checked(path, sha(original), updated)
            else:
                core.export_new(path, text)
        self.reminder_enabled, self.reminder_percent = enabled, percent
        self.reminder_caption.set(self.reminder_caption_text())
        self.set_context_notice("设置已保存。" + ("正在检查当前聊天…" if self.chat else "请选择聊天。"))
        self.refresh_pending = bool(self.chat)
        self.schedule_metrics()

    def set_context_notice(self, text, alert=False):
        prefix = "换聊提醒" if self.reminder_enabled else "换聊提醒已暂停"
        self.context_notice.set(prefix + " · " + text)
        self.context_banner.configure(style="Alert.TLabel" if alert and self.reminder_enabled else "Notice.TLabel")

    def watch_chat(self):
        self.chat_usage.set_chat(self.chat)
        self.set_context_notice("正在读取当前聊天…" if self.chat else "选一次聊天开始监测。")
        self.refresh_pending = bool(self.chat)
        self.request_cache = {}
        self.schedule_metrics()

    def schedule_metrics(self, delay=0):
        if self.metric_timer is not None:
            self.root.after_cancel(self.metric_timer)
        self.metric_timer = self.root.after(delay, self.update_metrics)

    def update_metrics(self):
        self.metric_timer = None
        identity = self.chat_usage.active
        if self.metric_job:
            prior, cache, job = self.metric_job
            if not job.done():
                self.schedule_metrics(160)
                return
            self.metric_job = None
            if prior == identity and cache is self.request_cache:
                try:
                    result = job.result()
                except (OSError, ValueError) as exc:
                    self.chat_usage.show_sample(None, str(exc))
                    self.chat_usage.show_requests(None, str(exc))
                    self.chat_usage.show_sources(None, str(exc))
                    self.chat_usage.show_context_diagnosis("unknown", "暂无可用的上下文数据。")
                    self.set_context_notice("暂无可用数据。")
                else:
                    self.show_chat_metrics(identity, result, True)
                if not self.refresh_pending:
                    self.schedule_metrics(15000)
                    return
        if identity:
            self.refresh_pending = False
            job = self.metric_worker.submit(chat_metrics, self.relay.settings["chat_db"], identity,
                                            include_totals=True, request_cache=self.request_cache)
            self.metric_job = identity, self.request_cache, job
            self.schedule_metrics(160)

    def show_chat_metrics(self, identity, result, full):
        sample = result["sample"]
        level, text = reminder(sample, result["compacted"], file_limit(self.relay.settings["config"]),
                               percent=self.reminder_percent)
        self.chat_usage.show_sample(sample, text)
        self.chat_usage.show_context_diagnosis(level, text)
        if full:
            self.chat_usage.show_requests(result.get("requests"), result.get("requests_error"))
            self.chat_usage.show_sources(result.get("sources"))
        self.set_context_notice(text, alert=level == "alert")
        if self.reminder_enabled and level == "alert" and identity not in self.alerted:
            self.alerted.add(identity)
            flash_taskbar(self.root)
        elif level == "ready":
            self.alerted.discard(identity)

    def stop_metrics(self, event):
        if event.widget is self.root:
            if self.metric_timer is not None:
                self.root.after_cancel(self.metric_timer)
            self.metric_worker.shutdown(wait=False, cancel_futures=True)

    def refresh_usage(self):
        try:
            self.load_status()
        except (ValueError, OSError) as exc:
            self.dashboard.info.set("读取失败 · 没有有效快照 · " + str(exc))

    def load_status(self):
        try:
            result = self.relay.status()
            self.dashboard.render(result, self.relay.settings)
        except Exception:
            self.dashboard.clear()
            self.dashboard.info.set("读取失败；没有有效快照。请核采集文件与状态，未沿用旧数值。")
            raise

def main(data_root=None):
    from workbench import Workbench
    root = tk.Tk()
    Workbench(root, data_root=data_root)
    root.mainloop()


if __name__ == "__main__":
    main()
