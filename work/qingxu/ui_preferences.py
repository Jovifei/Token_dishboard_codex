"""Directory and reminder settings UI for Qingxu's three-workspace shell.

The panel edits application preferences, never Codex TOML or library contents.
"""
from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from monitor import DEFAULT_POLICY, validate_policy
from preferences import PATH_ACCESS, validate_directory

BG = "#F5F5F7"
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
        self._wrap_labels = []
        self.vars = {key: tk.StringVar(self) for key in PATH_LABELS}
        self.vars.update({
            "knowledge_mode": tk.StringVar(self),
            "knowledge_policy": tk.StringVar(self),
            "monitor_mode": tk.StringVar(self),
            "custom_kind": tk.StringVar(self),
            "custom_value": tk.StringVar(self),
            "enabled": tk.BooleanVar(self),
        })
        self.path_feedback = {}
        self.path_controls = {}
        self.status_var = tk.StringVar(self)
        self.state_var = tk.StringVar(self, value="saved")
        style = ttk.Style(self)
        style.configure("Preferences.TCheckbutton", background=WHITE, foreground=TEXT)
        style.configure("Preferences.TRadiobutton", background=WHITE, foreground=TEXT)
        style.map("Preferences.TCheckbutton", background=[("active", WHITE)])
        style.map("Preferences.TRadiobutton", background=[("active", WHITE)])

        footer = ttk.Frame(self, style="Card.TFrame", padding=(16, 12))
        footer.pack(side="bottom", fill="x")
        self.save_button = ttk.Button(
            footer, text="保存设置", style="Primary.TButton", command=self.save)
        self.save_button.pack(side="right")
        self.undo_button = ttk.Button(
            footer, text="撤销未保存", command=self.refresh)
        self.undo_button.pack(side="right", padx=(0, 10))
        self.status_label = ttk.Label(
            footer, textvariable=self.status_var, style="CardMuted.TLabel",
            wraplength=500, justify="left")
        self.status_label.pack(side="left", fill="x", expand=True, padx=(0, 18))

        scroll = ttk.Frame(self, style="App.TFrame")
        scroll.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(scroll, bg=BG, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(scroll, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.content = ttk.Frame(self.canvas, style="App.TFrame", padding=(0, 4, 12, 14))
        self._window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._content_resized)
        self.canvas.bind("<Configure>", self._canvas_resized)
        footer.bind("<Configure>", lambda event: self.status_label.configure(
            wraplength=max(150, event.width - self.save_button.winfo_reqwidth()
                           - self.undo_button.winfo_reqwidth() - 84)))

        self._build_monitor()
        locations = self._card(
            "数据与保存位置",
            "更换位置只切换读取和保存目标，不迁移原有文件。缺少的交接或导出目录可在保存时明确创建。")
        self._path_row(locations, "codex_home", "只读本机项目、会话和用量记录；可留空稍后连接。")
        self._path_row(locations, "handoff_dir", "保存简短交接文件；新会话按需读取。")
        self._path_row(locations, "export_dir", "用于交接导出和诊断材料。")

        knowledge = self._card("可选知识资料", "没有知识库也能完整交接。选择普通文件夹即可使用 Markdown 资料。")
        kinds = ttk.Frame(knowledge, style="Card.TFrame")
        kinds.pack(fill="x", pady=(0, 10))
        ttk.Label(kinds, text="知识库类型", style="Card.TLabel").pack(side="left", padx=(0, 12))
        self.knowledge_combo = ttk.Combobox(
            kinds, state="readonly", textvariable=self.vars["knowledge_mode"],
            values=tuple(KNOWLEDGE_LABELS), width=26)
        self.knowledge_combo.pack(side="left")
        self._path_row(knowledge, "knowledge_dir", "用于目录索引与按需正文；选择位置不会自动读取或写入整个知识库。")
        policy = ttk.Frame(knowledge, style="Card.TFrame")
        policy.pack(fill="x", pady=(4, 0))
        ttk.Label(policy, text="知识更新方式", style="Card.TLabel").pack(side="left", padx=(0, 12))
        self.policy_combo = ttk.Combobox(
            policy, state="readonly", textvariable=self.vars["knowledge_policy"],
            values=tuple(POLICY_LABELS), width=28)
        self.policy_combo.pack(side="left")
        self._description(
            knowledge, "这里控制交接提示词的知识更新要求。实际更新仍由旧会话按目标范围执行，轻续不会在保存设置时写知识库。")

        snapshots = self._card(
            "可选 ccusage 快照",
            "读取已经生成的统计快照，与当前会话进度分开呈现。选择目录不会启动、停止或安装采集器。")
        self._path_row(snapshots, "snapshot_dir", "未配置时仍可查看本机聊天的最近用量。")

        for key, variable in self.vars.items():
            variable.trace_add("write", lambda *_args, name=key: self._changed(name))
        self._bind_wheel(self)
        self.refresh()

    def _description(self, parent, text):
        label = ttk.Label(
            parent, text=text, style="CardMuted.TLabel", wraplength=720, justify="left")
        label.pack(anchor="w", fill="x", pady=(7, 0))
        self._wrap_labels.append(label)
        return label

    def _card(self, title, description):
        card = ttk.Frame(self.content, style="Card.TFrame", padding=(20, 17))
        card.pack(fill="x", pady=(0, 12))
        ttk.Label(card, text=title, style="Heading.TLabel",
                  background=WHITE).pack(anchor="w")
        intro = self._description(card, description)
        intro.pack_configure(pady=(5, 12))
        return card

    def _build_monitor(self):
        card = self._card(
            "提醒策略", "提醒用于提前安排收尾与续聊。Codex 的实际压缩参数在旁边的“Codex 配置”中单独修改。")
        ttk.Checkbutton(
            card, text="启用上下文提醒", variable=self.vars["enabled"],
            style="Preferences.TCheckbutton").pack(anchor="w", pady=(0, 9))
        choices = ttk.Frame(card, style="Card.TFrame")
        choices.pack(fill="x", pady=(0, 9))
        for label, value in (("官方参考", "official"), ("轻续推荐", "recommended"), ("自定义", "custom")):
            ttk.Radiobutton(
                choices, text=label, value=value, variable=self.vars["monitor_mode"],
                style="Preferences.TRadiobutton").pack(side="left", padx=(0, 26))
        custom = ttk.Frame(card, style="Card.TFrame")
        custom.pack(fill="x")
        ttk.Label(custom, text="自定义提醒线", style="Card.TLabel").pack(side="left", padx=(0, 12))
        self.kind_combo = ttk.Combobox(
            custom, textvariable=self.vars["custom_kind"], values=tuple(KIND_LABELS),
            state="readonly", width=19)
        self.kind_combo.pack(side="left", padx=(0, 8))
        self.value_entry = ttk.Entry(custom, textvariable=self.vars["custom_value"], width=12)
        self.value_entry.pack(side="left")
        self.unit_var = tk.StringVar(self)
        ttk.Label(custom, textvariable=self.unit_var, style="CardMuted.TLabel").pack(side="left", padx=8)
        self.policy_note_var = tk.StringVar(self)
        note = ttk.Label(card, textvariable=self.policy_note_var, style="CardMuted.TLabel",
                         wraplength=720, justify="left")
        note.pack(anchor="w", fill="x", pady=(10, 0))
        self._wrap_labels.append(note)

    def _path_row(self, parent, key, note):
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 13))
        ttk.Label(row, text=PATH_LABELS[key], style="Card.TLabel").pack(anchor="w")
        input_row = ttk.Frame(row, style="Card.TFrame")
        input_row.pack(fill="x", pady=(5, 0))
        entry = ttk.Entry(input_row, textvariable=self.vars[key])
        entry.pack(side="left", fill="x", expand=True)
        browse = ttk.Button(input_row, text="浏览…", command=lambda: self.browse_path(key))
        browse.pack(side="left", padx=(7, 0))
        validate = ttk.Button(input_row, text="检查", command=lambda: self.validate_path(key))
        validate.pack(side="left", padx=(7, 0))
        open_button = ttk.Button(input_row, text="打开", command=lambda: self.open_path(key))
        open_button.pack(side="left", padx=(7, 0))
        feedback = tk.StringVar(self, value=note)
        label = ttk.Label(row, textvariable=feedback, style="CardMuted.TLabel",
                          wraplength=720, justify="left")
        label.pack(anchor="w", fill="x", pady=(4, 0))
        self._wrap_labels.append(label)
        self.path_feedback[key] = feedback
        self.path_controls[key] = (entry, browse, validate, open_button)
        return row

    def _content_resized(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_resized(self, event):
        self.canvas.itemconfigure(self._window, width=max(1, event.width))
        for label in self._wrap_labels:
            label.configure(wraplength=max(210, event.width - 68))

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
        return {key: variable.get() for key, variable in self.vars.items()}

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
        self.unit_var.set("%" if KIND_LABELS.get(kind, kind) == "percent" else "tokens")
        self.policy_note_var.set(POLICY_NOTES.get(
            self.vars["monitor_mode"].get(), "请选择一种提醒策略。"))
        mode = KNOWLEDGE_LABELS.get(self.vars["knowledge_mode"].get(), self.vars["knowledge_mode"].get())
        enabled = mode != "none"
        for control in self.path_controls.get("knowledge_dir", ()):
            control.configure(state="normal" if enabled else "disabled")
        self.policy_combo.configure(state="readonly" if enabled else "disabled")

    def _changed(self, key):
        if self._loading:
            return
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
            monitor = DEFAULT_POLICY | (prefs.get("monitor") or {})
            self.vars["monitor_mode"].set(monitor["mode"])
            self.vars["custom_kind"].set(_label(monitor["custom_kind"], KIND_LABELS))
            self.vars["custom_value"].set(str(monitor["custom_value"]))
            self.vars["enabled"].set(monitor["enabled"])
            self._update_controls()
            self._baseline = self._raw_form()
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
        current = Path(self.vars[key].get()).expanduser() if self.vars[key].get().strip() else None
        initial = current if current and current.is_dir() else current.parent if current and current.parent.is_dir() else self.app.data_root
        value = filedialog.askdirectory(
            parent=self.app.root, title="选择" + PATH_LABELS[key],
            initialdir=str(initial),
            mustexist=PATH_ACCESS[key] == "read")
        if value:
            self.vars[key].set(value)
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
        text = check["message"]
        if check.get("can_create") and key in ("handoff_dir", "export_dir"):
            text = "目录尚不存在；点击“保存设置”时创建此目录，原有文件不会移动。"
        self.path_feedback[key].set(text)
        return check

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

