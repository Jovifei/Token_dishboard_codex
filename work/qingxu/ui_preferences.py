"""Directory and reminder settings UI for Qingxu's three-workspace shell.

The panel edits application preferences, never Codex TOML or library contents.
"""
from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk

from app import KB_CATEGORIES
from handoff import project_key
from knowledge import create_templates, project_folder, validate_categories
from monitor import DEFAULT_POLICY, validate_policy
from preferences import PATH_ACCESS, validate_directory
from ui_controls import ChoiceButton, install_controls

BG = "#FFFFFF"
WHITE = "#FFFFFF"
TEXT = "#1D1D1F"
MUTED = "#6E6E73"
RED = "#B42318"
GREEN = "#267348"
AMBER = "#8A5A16"
KNOWLEDGE_LABELS = {
    "不使用知识库": "none",
    "普通 Markdown 文件夹": "folder",
    "Obsidian Vault": "obsidian",
}
POLICY_LABELS = {
    "不更新，只保存交接": "none",
    "由旧会话询问后更新": "ask",
    "按已授权范围更新": "authorized",
}
KIND_LABELS = {"参考容量百分比": "percent", "固定 Token 数": "tokens"}
PATH_LABELS = {
    "codex_home": "Codex 数据目录",
    "handoff_dir": "交接保存目录",
    "export_dir": "默认导出目录",
    "knowledge_dir": "知识资料目录",
    "snapshot_dir": "ccusage 快照目录",
}
POLICY_NOTES = {
    "official": "使用可核验的会话运行时压缩线或模型官方默认值。官方参考未知时会如实显示未知，不猜阈值。提醒仍由轻续发出。",
    "recommended": "在已知参考压缩线的 80% 提前提醒；只有窗口容量时使用窗口的 70%。这是轻续的保守预警规则，不代表最佳成本，也不修改 Codex。",
    "custom": "自行设置当前参考容量的百分比，或固定 Token 数。只控制轻续何时提醒；实际数据缺失时不会显示虚假的进度。",
}


def _code(value, labels, field):
    if value in labels:
        return labels[value]
    if value in labels.values():
        return value
    raise ValueError("请选择有效的" + field + "。")


def _label(value, labels):
    return next((label for label, code in labels.items() if code == value), str(value))


def parse_form(form):
    """Translate a UI form to a small preference patch; no filesystem access."""
    values = {}
    for key in PATH_LABELS:
        value = form.get(key, "")
        if not isinstance(value, str):
            raise ValueError(PATH_LABELS[key] + "必须是文本路径。")
        values[key] = value.strip()
    values["knowledge_mode"] = _code(form.get("knowledge_mode"), KNOWLEDGE_LABELS, "知识库类型")
    values["knowledge_policy"] = _code(form.get("knowledge_policy"), POLICY_LABELS, "知识更新方式")
    values["knowledge_categories"] = validate_categories(form.get("knowledge_categories", []))
    values["knowledge_project_key"] = form.get("knowledge_project_key", "")
    kind = _code(form.get("custom_kind"), KIND_LABELS, "提醒单位")
    value = form.get("custom_value")
    try:
        if isinstance(value, bool):
            raise ValueError("boolean is not a threshold")
        number = int(value)
        if isinstance(value, float) and value != number:
            raise ValueError("fractional threshold")
        if not 1 <= number <= (100 if kind == "percent" else 1_000_000_000):
            raise ValueError("threshold outside range")
    except (TypeError, ValueError) as exc:
        if form.get("monitor_mode") == "custom":
            raise ValueError("百分比须为 1–100 的整数。" if kind == "percent" else
                             "Token 提醒线须为 1–1,000,000,000 的整数。") from exc
        # Switching away from an invalid custom draft must remain possible.
        # The inactive custom editor returns to its documented default.
        kind, number = "percent", DEFAULT_POLICY["custom_value"]
    values["monitor"] = validate_policy({
        "mode": form.get("monitor_mode"),
        "custom_kind": kind,
        "custom_value": number,
        "enabled": form.get("enabled"),
    })
    return values


class PreferencesPanel(ttk.Frame):
    """Scrollable settings cards, with save/cancel state kept in a fixed footer."""
    def __init__(self, parent, app):
        super().__init__(parent, style="App.TFrame")
        self.app = app
        self._loading = False
        self._baseline = {}
        self._original = {}
        install_controls(self)
        self.vars = {key: tk.StringVar(self) for key in PATH_LABELS}
        self.vars.update({
            "knowledge_mode": tk.StringVar(self),
            "knowledge_policy": tk.StringVar(self),
            "knowledge_project_key": tk.StringVar(self),
            "monitor_mode": tk.StringVar(self),
            "custom_kind": tk.StringVar(self),
            "custom_value": tk.StringVar(self),
            "enabled": tk.BooleanVar(self),
        })
        self.knowledge_checks = {kind: tk.BooleanVar(self) for kind in KB_CATEGORIES}
        self.path_feedback = {}
        self.path_controls = {}
        self.status_var = tk.StringVar(self)
        self.state_var = tk.StringVar(self, value="saved")
        style = ttk.Style(self)
        style.configure("Preferences.TCheckbutton", background=WHITE, foreground=TEXT)
        style.map("Preferences.TCheckbutton", background=[("active", WHITE)])

        footer = ttk.Frame(self, style="Panel.TFrame", padding=(16, 12))
        footer.pack(side="bottom", fill="x")
        self.save_button = ttk.Button(
            footer, text="保存设置", style="Primary.TButton", command=self.save)
        self.save_button.pack(side="right")
        self.undo_button = ttk.Button(
            footer, text="撤销未保存", style="Ghost.TButton", command=self.refresh)
        self.undo_button.pack(side="right", padx=(0, 10))
        self.status_label = ttk.Label(
            footer, textvariable=self.status_var, style="CardMuted.TLabel",
            wraplength=500, justify="left")
        self.status_label.pack(side="left", fill="x", expand=True, padx=(0, 18))

        scroll = ttk.Frame(self, style="App.TFrame")
        scroll.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(scroll, bg=BG, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(scroll, orient="vertical", command=self.canvas.yview,
                                  style="Slim.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.content = ttk.Frame(self.canvas, style="App.TFrame", padding=(0, 6, 8, 8))
        self._window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._content_resized)
        self.canvas.bind("<Configure>", self._canvas_resized)
        footer.bind("<Configure>", lambda event: self.status_label.configure(
            wraplength=max(150, event.width - self.save_button.winfo_reqwidth()
                           - self.undo_button.winfo_reqwidth() - 44)))

        self.top = ttk.Frame(self.content, style="Card.TFrame")
        self.top.pack(fill="x")
        self.locations_column = ttk.Frame(self.top, style="Card.TFrame")
        self.monitor_column = ttk.Frame(self.top, style="Card.TFrame")
        self.locations_column.grid(row=0, column=0, sticky="nsew")
        self.monitor_column.grid(row=1, column=0, sticky="nsew")
        self.top.columnconfigure(0, weight=1)
        self._columns = 1
        locations = self._card(
            "数据与保存位置",
            "更换位置会切换读取和保存目标。", parent=self.locations_column)
        self._path_row(locations, "codex_home", "只读本机项目、会话和用量记录；可留空稍后连接。")
        self._path_row(locations, "handoff_dir", "保存简短交接文件；新会话按需读取。")
        self._path_row(locations, "export_dir", "用于交接导出和诊断材料。")
        self._build_monitor(self.monitor_column)

        knowledge = self._card("项目知识库（可选）", "可选择普通 Markdown 文件夹或 Obsidian Vault。")
        self.knowledge_card = knowledge
        kinds = ttk.Frame(knowledge, style="Card.TFrame")
        kinds.pack(fill="x", pady=(0, 7))
        ttk.Label(kinds, text="知识库类型", style="Card.TLabel").pack(side="left", padx=(0, 12))
        self.knowledge_combo = ChoiceButton(
            kinds, state="readonly", textvariable=self.vars["knowledge_mode"],
            values=tuple(KNOWLEDGE_LABELS), width=22)
        self.knowledge_combo.pack(side="left")
        self._path_row(knowledge, "knowledge_dir", "选择当前项目的知识文件夹；确认后保存项目绑定。")
        policy = ttk.Frame(knowledge, style="Card.TFrame")
        policy.pack(fill="x", pady=(4, 0))
        ttk.Label(policy, text="知识更新方式", style="Card.TLabel").pack(side="left", padx=(0, 12))
        self.policy_combo = ChoiceButton(
            policy, state="readonly", textvariable=self.vars["knowledge_policy"],
            values=tuple(POLICY_LABELS), width=22)
        self.policy_combo.pack(side="left")
        self._description(knowledge, "勾选用于模板和旧会话更新的分类。保存设置只写入项目绑定。")
        categories = ttk.Frame(knowledge, style="Card.TFrame")
        categories.pack(fill="x", pady=(5, 0))
        self.knowledge_category_controls = []
        for index, (kind, (label, _description)) in enumerate(KB_CATEGORIES.items()):
            control = ttk.Checkbutton(categories, text=label, variable=self.knowledge_checks[kind],
                                      style="Preferences.TCheckbutton")
            control.grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 18), pady=3)
            self.knowledge_category_controls.append(control)
        self.knowledge_init_button = ttk.Button(knowledge, text="创建缺失的分类模板", style="Outline.TButton",
                                                command=self.initialize_knowledge)
        self.knowledge_init_button.pack(anchor="w", pady=(10, 0))

        snapshots = self._card(
            "可选 ccusage 快照",
            "读取已经生成的统计快照，与当前会话进度分开呈现。选择目录不会启动、停止或安装采集器。")
        self._path_row(snapshots, "snapshot_dir", "未配置时仍可查看本机聊天的最近用量。")

        for key, variable in self.vars.items():
            variable.trace_add("write", lambda *_args, name=key: self._changed(name))
        for variable in self.knowledge_checks.values():
            variable.trace_add("write", lambda *_args: self._changed("knowledge_categories"))
        self._bind_wheel(self)
        self.refresh()

    def _description(self, parent, text):
        label = ttk.Label(
            parent, text=text, style="CardMuted.TLabel", wraplength=720, justify="left")
        label.pack(anchor="w", fill="x", pady=(4, 0))
        self._wrap_to_width(label)
        return label

    @staticmethod
    def _wrap_to_width(label):
        def resize(event):
            width = max(120, event.width)
            if int(float(label.cget("wraplength"))) != width:
                label.configure(wraplength=width)
        label.bind("<Configure>", resize, add="+")

    def _card(self, title, description, *, parent=None):
        card = ttk.Frame(parent if parent is not None else self.content,
                         style="Panel.TFrame", padding=(18, 16))
        card.pack(fill="x", pady=(0, 14))
        ttk.Label(card, text=title, style="Heading.TLabel",
                  background=WHITE).pack(anchor="w")
        intro = self._description(card, description)
        intro.pack_configure(pady=(3, 8))
        return card

    def _build_monitor(self, parent):
        card = self._card(
            "上下文提醒", "Codex 压缩参数在「Codex 配置」中修改。", parent=parent)
        ttk.Checkbutton(
            card, text="启用上下文提醒", variable=self.vars["enabled"],
            style="Preferences.TCheckbutton").pack(anchor="w", pady=(0, 5))
        choices = ttk.Frame(card, style="Card.TFrame")
        choices.pack(fill="x", pady=(0, 7))
        for label, value in (("官方", "official"), ("推荐", "recommended"), ("自定义", "custom")):
            ttk.Radiobutton(
                choices, text=label, value=value, variable=self.vars["monitor_mode"],
                style="Toolbutton").pack(side="left", padx=(0, 3))
        self.unit_var = tk.StringVar(self)
        ttk.Label(card, textvariable=self.unit_var, style="CardMuted.TLabel").pack(anchor="w", pady=(0, 3))
        custom = ttk.Frame(card, style="Card.TFrame")
        custom.pack(fill="x")
        self.kind_combo = ChoiceButton(
            custom, textvariable=self.vars["custom_kind"], values=tuple(KIND_LABELS),
            state="readonly")
        self.kind_combo.pack(side="left", padx=(0, 6))
        self.value_entry = ttk.Entry(custom, textvariable=self.vars["custom_value"], width=6)
        self.value_entry.pack(side="left")
        self.policy_note_var = tk.StringVar(self)
        note = ttk.Label(card, textvariable=self.policy_note_var, style="CardMuted.TLabel",
                         wraplength=720, justify="left")
        note.pack(anchor="w", fill="x", pady=(7, 0))
        self._wrap_to_width(note)

    def _path_row(self, parent, key, note):
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 7))
        ttk.Label(row, text=PATH_LABELS[key], style="Card.TLabel").pack(anchor="w")
        input_row = ttk.Frame(row, style="Card.TFrame")
        input_row.pack(fill="x", pady=(3, 0))
        entry = ttk.Entry(input_row, textvariable=self.vars[key], width=15)
        entry.pack(side="left", fill="x", expand=True)
        browse = ttk.Button(input_row, text="浏览…", style="Outline.TButton", command=lambda: self.browse_path(key))
        browse.pack(side="left", padx=(4, 0))
        validate = ttk.Button(input_row, text="检查", style="Ghost.TButton", command=lambda: self.validate_path(key))
        validate.pack(side="left", padx=(2, 0))
        open_button = ttk.Button(input_row, text="打开", style="Ghost.TButton", command=lambda: self.open_path(key))
        open_button.pack(side="left", padx=(2, 0))
        feedback = tk.StringVar(self, value=note)
        label = ttk.Label(row, textvariable=feedback, style="CardMuted.TLabel",
                          wraplength=720, justify="left")
        label.pack(anchor="w", fill="x", pady=(4, 0))
        self._wrap_to_width(label)
        self.path_feedback[key] = feedback
        self.path_controls[key] = (entry, browse, validate, open_button)
        return row

    def _content_resized(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_resized(self, event):
        self.canvas.itemconfigure(self._window, width=max(1, event.width))
        face = font.nametofont("TkDefaultFont", root=self)
        columns = 2 if event.width >= face.measure("中" * 36) else 1
        if columns != self._columns:
            self._columns = columns
            self.top.columnconfigure(0, weight=3 if columns == 2 else 1, uniform="top" if columns == 2 else "")
            self.top.columnconfigure(1, weight=2 if columns == 2 else 0, uniform="top" if columns == 2 else "")
            self.monitor_column.grid_configure(row=0 if columns == 2 else 1, column=columns - 1)

    def _bind_wheel(self, widget):
        # Local bindings work while focus is on an entry too, without taking over
        # unrelated tabs, Figma previews, or a native directory chooser.
        widget.bind("<MouseWheel>", self._wheel, add="+")
        widget.bind("<Button-4>", self._wheel, add="+")
        widget.bind("<Button-5>", self._wheel, add="+")
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _wheel(self, event):
        if not self.winfo_ismapped():
            return None
        if self.canvas.yview() == (0.0, 1.0):
            return "break"
        delta = getattr(event, "delta", 0)
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        elif delta:
            units = -int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
        else:
            return None
        self.canvas.yview_scroll(units, "units")
        return "break"

    def _raw_form(self):
        values = {key: variable.get() for key, variable in self.vars.items()}
        values["knowledge_categories"] = [kind for kind, variable in self.knowledge_checks.items() if variable.get()]
        return values

    def has_unsaved(self):
        return self._raw_form() != self._baseline

    def _set_status(self, text, state="pending"):
        self.state_var.set(state)
        self.status_var.set(text)
        self.status_label.configure(foreground={"error": RED, "saved": GREEN}.get(state, MUTED))

    def _update_controls(self):
        custom = self.vars["monitor_mode"].get() == "custom"
        self.kind_combo.configure(state="readonly" if custom else "disabled")
        self.value_entry.configure(state="normal" if custom else "disabled")
        kind = self.vars["custom_kind"].get()
        unit = "%" if KIND_LABELS.get(kind, kind) == "percent" else "Tokens"
        self.unit_var.set("自定义提醒线 · " + unit)
        self.policy_note_var.set(POLICY_NOTES.get(
            self.vars["monitor_mode"].get(), "请选择一种提醒策略。"))
        mode = KNOWLEDGE_LABELS.get(self.vars["knowledge_mode"].get(), self.vars["knowledge_mode"].get())
        enabled = mode != "none"
        for control in self.path_controls.get("knowledge_dir", ()):
            control.configure(state="normal" if enabled else "disabled")
        self.policy_combo.configure(state="readonly" if enabled else "disabled")
        for control in self.knowledge_category_controls:
            control.configure(state="normal" if enabled else "disabled")
        try:
            current_project = project_key(self.app.chat) if self.app.chat else ""
        except (AttributeError, ValueError, TypeError):
            current_project = ""
        saved = self.app.prefs
        bound = self.vars["knowledge_project_key"].get()
        can_create = (enabled and current_project and current_project == bound
                      and bound == saved.get("knowledge_project_key", "")
                      and self.vars["knowledge_dir"].get() == saved.get("knowledge_dir", "")
                      and bool(saved.get("knowledge_categories")) and not self.has_unsaved())
        self.knowledge_init_button.configure(state="normal" if can_create else "disabled")

    def refresh_project(self):
        """Refresh project-bound actions without replacing unsaved form changes."""
        self._update_controls()

    def show_knowledge(self):
        self.canvas.yview_moveto(self.knowledge_card.winfo_y() / max(1, self.content.winfo_height()))

    def _changed(self, key):
        if self._loading:
            return
        if key == "knowledge_dir" and self.vars["knowledge_project_key"].get():
            self.vars["knowledge_project_key"].set("")
        self._update_controls()
        if key in self.path_feedback:
            self.path_feedback[key].set("位置已更改，尚未验证。原有文件不会迁移。")
        dirty = self.has_unsaved()
        self.undo_button.configure(state="normal" if dirty else "disabled")
        self.save_button.configure(state="normal" if dirty or not self.app.pref_store.path.exists() else "disabled")
        self._set_status("有未保存的修改。点击保存后生效。" if dirty else "已回到已加载的设置。",
                         "pending" if dirty else "saved")

    def refresh(self):
        self._loading = True
        try:
            prefs = deepcopy(self.app.prefs)
            self._original = prefs
            for key in PATH_LABELS:
                self.vars[key].set(prefs.get(key, ""))
            self.vars["knowledge_mode"].set(_label(prefs.get("knowledge_mode", "none"), KNOWLEDGE_LABELS))
            self.vars["knowledge_policy"].set(_label(prefs.get("knowledge_policy", "ask"), POLICY_LABELS))
            self.vars["knowledge_project_key"].set(prefs.get("knowledge_project_key", ""))
            selected = prefs.get("knowledge_categories", [])
            for kind, variable in self.knowledge_checks.items():
                variable.set(kind in selected)
            monitor = DEFAULT_POLICY | (prefs.get("monitor") or {})
            self.vars["monitor_mode"].set(monitor["mode"])
            self.vars["custom_kind"].set(_label(monitor["custom_kind"], KIND_LABELS))
            self.vars["custom_value"].set(str(monitor["custom_value"]))
            self.vars["enabled"].set(monitor["enabled"])
            self._update_controls()
            self._baseline = self._raw_form()
            self._update_controls()
        finally:
            self._loading = False
        for key, text in self.path_feedback.items():
            path = self.vars[key].get()
            if key == "knowledge_dir" and prefs.get("knowledge_mode") == "none":
                text.set("知识库未启用；交接仍可正常使用。")
            elif not path:
                text.set("未配置，可稍后选择。")
            else:
                text.set("当前保存的位置；点击“检查”查看可用性。")
        self.undo_button.configure(state="disabled")
        exists = self.app.pref_store.path.exists()
        self.save_button.configure(state="disabled" if exists else "normal")
        self._set_status("设置已加载。" if exists else "当前使用默认设置。可修改位置后保存。",
                         "saved" if exists else "pending")

    def browse_path(self, key):
        record = getattr(self.app, "chat", None)
        if key == "knowledge_dir" and not record:
            self._set_status("请先选择需要绑定知识目录的项目聊天。", "error")
            return
        current = Path(self.vars[key].get()).expanduser() if self.vars[key].get().strip() else None
        initial = current if current and current.is_dir() else current.parent if current and current.parent.is_dir() else self.app.data_root
        value = filedialog.askdirectory(
            parent=self.app.root, title="选择" + PATH_LABELS[key],
            initialdir=str(initial),
            mustexist=PATH_ACCESS[key] == "read")
        if value:
            if key == "knowledge_dir":
                try:
                    selected_project = project_key(record)
                    folder = project_folder(value)
                    confirmed = messagebox.askyesno(
                        "确认项目知识文件夹",
                        "当前项目：" + str(record.get("path") or record.get("cwd")) +
                        "\n知识文件夹：" + str(folder) +
                        "\n\n确认这是该项目的文件夹？保存设置后绑定。",
                        parent=self.app.root)
                    if not confirmed:
                        return
                    if project_key(self.app.chat) != selected_project:
                        raise ValueError("项目已切换，请重新选择知识文件夹。")
                except (OSError, ValueError, TypeError) as exc:
                    self._set_status(str(exc), "error")
                    return
            self.vars[key].set(value)
            if key == "knowledge_dir":
                self.vars["knowledge_project_key"].set(selected_project)
            self.validate_path(key)

    def validate_path(self, key):
        mode = KNOWLEDGE_LABELS.get(self.vars["knowledge_mode"].get(), self.vars["knowledge_mode"].get())
        if key == "knowledge_dir" and mode == "none":
            check = {"ok": True, "code": "DISABLED", "path": self.vars[key].get(),
                     "message": "知识库未启用；交接仍可正常使用。", "exists": False, "can_create": False}
        else:
            check = validate_directory(
                self.vars[key].get(), access=PATH_ACCESS[key],
                optional=key in ("codex_home", "snapshot_dir"))
            if key == "knowledge_dir" and check["ok"]:
                try:
                    project_folder(check["path"])
                except (OSError, ValueError) as exc:
                    check.update(ok=False, code="INVALID", message=str(exc))
        text = check["message"]
        if check.get("can_create") and key in ("handoff_dir", "export_dir"):
            text = "目录尚不存在；点击“保存设置”时创建此目录，原有文件不会移动。"
        self.path_feedback[key].set(text)
        return check

    def initialize_knowledge(self):
        try:
            if self.has_unsaved():
                raise ValueError("请先保存项目知识目录和分类，再创建模板。")
            prefs = self.app.prefs
            record = self.app.chat
            selected_project = project_key(record)
            if (prefs.get("knowledge_mode") == "none" or not prefs.get("knowledge_categories")
                    or prefs.get("knowledge_project_key") != selected_project):
                raise ValueError("请选择当前项目并确认绑定知识文件夹和分类。")
            if self.app.pref_store.load() != prefs:
                raise ValueError("设置已在其他位置更改，请重新读取后再创建模板。")
            folder = project_folder(prefs["knowledge_dir"])
            names = [KB_CATEGORIES[kind][0] for kind in prefs["knowledge_categories"]]
            confirmed = messagebox.askyesno(
                "创建项目知识模板",
                "当前项目：" + str(record.get("path") or record.get("cwd")) +
                "\n目标文件夹：" + str(folder) + "\n分类：" + "、".join(names) +
                "\n\n只创建缺失文件，已有文件保留。继续？",
                parent=self.app.root)
            if not confirmed:
                return
            if project_key(self.app.chat) != selected_project or self.app.pref_store.load() != prefs:
                raise ValueError("项目或设置已更改，请重新检查后再创建模板。")
            result = create_templates(folder, prefs["knowledge_categories"])
            self._set_status("已创建 " + str(len(result["created"])) + " 个模板，保留 " +
                             str(len(result["existing"])) + " 个已有文件。", "saved")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self._set_status(str(exc), "error")

    def open_path(self, key):
        check = self.validate_path(key)
        if check["ok"] and check.get("exists"):
            try:
                self.app.open_path(check["path"])
            except (OSError, ValueError) as exc:
                self._set_status("无法打开目录：" + str(exc), "error")
        else:
            self._set_status("当前目录尚不可打开；请检查或重新选择。", "error")

    def save(self):
        try:
            values = parse_form(self._raw_form())
            # A partial patch lets a user disable a moved library or update a
            # reminder while another, unchanged optional source is offline.
            updates = {key: value for key, value in values.items()
                       if value != self._original.get(key)}
            if not self.app.pref_store.path.exists():
                updates = values
            errors = []
            for key in PATH_LABELS:
                check = self.validate_path(key)
                required_now = key in updates
                if key == "knowledge_dir":
                    required_now = (values["knowledge_mode"] != "none" and
                                    (required_now or "knowledge_mode" in updates))
                can_make = (key in ("handoff_dir", "export_dir") and
                            check.get("can_create") and check["code"] == "MISSING")
                if required_now and not check["ok"] and not can_make:
                    # Missing unedited read defaults do not prevent first-use
                    # manual handoff. Selecting a new invalid source is blocked.
                    same = values[key] == self._original.get(key)
                    if key in ("codex_home", "snapshot_dir") and same:
                        continue
                    errors.append(PATH_LABELS[key] + "：" + check["message"])
            if errors:
                raise ValueError(errors[0])
            if not updates:
                self._set_status("设置未改变，无需重复保存。", "saved")
                return True
            self.save_button.configure(state="disabled")
            self._set_status("正在保存并核验…", "pending")
            result = self.app.apply_preferences(updates, create_dirs=True)
            if isinstance(result, dict):
                self.app.prefs = result
            self.refresh()
            self._set_status("已保存并读回。新设置已应用，原有文件未迁移。", "saved")
            self.app.set_status("目录与提醒设置已保存；Codex 配置文件未更改。")
            return True
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.save_button.configure(state="normal")
            self._set_status(str(exc), "error")
            return False

