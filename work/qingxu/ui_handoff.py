"""Three-step local handoff panel. No message sending or model calls."""
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from handoff import (HandoffError, POLICY_LABELS, build_restore_prompt,
                     build_save_prompt, default_templates)

BG = "#F5F5F7"
BLUE = "#007AFF"
INK = "#1D1D1F"
MUTED = "#6E6E73"
GREEN = "#238246"
BORDER = "#D9D9DE"


class HandoffPanel(ttk.Frame):
    """app: root, prefs(dict), chat, data_root, handoff_store, template_store;
    methods: set_status(text), show_page(name), open_path(path).
    The shell asks has_unsaved() before set_record() changes the selection.
    """
    def __init__(self, parent, app):
        super().__init__(parent, style="App.TFrame")
        self.app = app
        self.record = None
        self.saved = None
        self._draft_path = None
        self._loading = False
        self._generated_old = self._generated_new = ""
        self._copied_old = self._copied_new = self._copied_goal = ""
        self._old_signature = self._new_signature = None
        self._template_dialog = None
        self._template_boxes = None
        self._template_original = None
        self._templates = default_templates()
        self._responsive_rows = []
        self._narrow_mode = None
        self.step = tk.IntVar(self, value=1)
        self.mode = tk.StringVar(self, value="file")
        self.policy = tk.StringVar(self, value="none")
        self.policy_label = tk.StringVar(self, value=POLICY_LABELS["none"])
        self.goal = tk.StringVar(self)
        self.record_caption = tk.StringVar(self, value="先在顶部选择项目和聊天")
        self.destination_caption = tk.StringVar(self)
        self.knowledge_caption = tk.StringVar(self)
        self.feedback = tk.StringVar(self, value="选择聊天后，复制旧聊指令开始。")
        self.summary_state = tk.StringVar(self, value="尚未收到摘要")
        self.saved_caption = tk.StringVar(self, value="保存后会显示准确位置与读回结果。")
        self.restore_state = tk.StringVar(self)
        self.step_captions = [tk.StringVar(self, value=t) for t in
                              ("1  整理旧会话", "2  保存交接", "3  开始新会话")]
        self._build()
        self.mode.trace_add("write", lambda *_: self._update())
        self.goal.trace_add("write", lambda *_: self._update())
        self.refresh_preferences()
        self.set_record(None)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, background=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.body = ttk.Frame(self.canvas, style="App.TFrame", padding=(8, 4, 12, 12))
        self.window_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize)
        steps = ttk.Frame(self.body, style="App.TFrame")
        steps.pack(fill="x", pady=(0, 10))
        self.step_buttons = []
        for i in range(3):
            steps.columnconfigure(i, weight=1, uniform="step")
            button = ttk.Button(steps, textvariable=self.step_captions[i],
                                command=lambda n=i + 1: self.show_step(n))
            button.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 5, 0))
            self.step_buttons.append(button)

        self.pages = []
        for _ in range(3):
            card = ttk.Frame(self.body, style="Card.TFrame", padding=16)
            self.pages.append(card)
        # The shell already shows project identity and the page title. Keep
        # primary actions outside the scrollable body, visible at any height.
        self.footer = ttk.Frame(self, style="Card.TFrame", padding=(18, 10, 18, 8))
        self.footer.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.footer_pages = [ttk.Frame(self.footer, style="Card.TFrame") for _ in range(3)]
        self._build_old(self.pages[0])
        self._build_summary(self.pages[1])
        self._build_new(self.pages[2])
        self.footer_feedback = ttk.Label(self.footer, textvariable=self.feedback,
                                         style="CardMuted.TLabel", wraplength=850)
        self.footer_feedback.pack(anchor="w", fill="x", pady=(6, 0))
        self._bind_scroll(self.body)

    def _resize(self, event):
        self.canvas.itemconfigure(self.window_id, width=max(1, event.width))
        width = max(240, event.width - 80)
        def wrap(parent):
            for child in parent.winfo_children():
                if isinstance(child, ttk.Label) and int(float(str(child.cget("wraplength")) or 0)):
                    child.configure(wraplength=width)
                wrap(child)
        wrap(self.body)
        self.footer_feedback.configure(wraplength=max(240, event.width - 40))
        narrow = event.width < 820
        if narrow != self._narrow_mode:
            self._narrow_mode = narrow
            for frame, buttons in self._responsive_rows:
                for button in buttons:
                    button.pack_forget()
                    button.grid_forget()
                if narrow:
                    frame.columnconfigure(0, weight=1, uniform="action")
                    frame.columnconfigure(1, weight=1, uniform="action")
                    for i, button in enumerate(buttons):
                        last_single = i == len(buttons) - 1 and i % 2 == 0
                        button.grid(row=i // 2, column=i % 2,
                                    columnspan=2 if last_single else 1, sticky="ew",
                                    padx=(0 if i % 2 == 0 else 6, 0), pady=(0, 6))
                else:
                    for i, button in enumerate(buttons):
                        button.pack(side="left", padx=(0 if i == 0 else 8, 0))

    def _bind_scroll(self, parent):
        for child in parent.winfo_children():
            if not isinstance(child, tk.Text):
                child.bind("<MouseWheel>", self._wheel, add="+")
                child.bind("<Button-4>", lambda _: self.canvas.yview_scroll(-2, "units"), add="+")
                child.bind("<Button-5>", lambda _: self.canvas.yview_scroll(2, "units"), add="+")
            self._bind_scroll(child)

    def _wheel(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    @staticmethod
    def _text(parent, height=8):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill="both", expand=True, pady=(8, 10))
        box = tk.Text(frame, height=height, wrap="word", undo=True, borderwidth=0,
                      relief="flat", highlightthickness=1, highlightbackground=BORDER,
                      highlightcolor=BLUE, background="white", foreground=INK,
                      insertbackground=INK, padx=12, pady=10, font="TkTextFont",
                      selectbackground="#CCE5FF", selectforeground=INK)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=scroll.set)
        box.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        # Tk's default text bindings retain keyboard selection, undo and paste.
        return box

    def _build_old(self, card):
        ttk.Label(card, text="1 · 让旧会话整理项目与进度",
                  style="Heading.TLabel").pack(anchor="w")
        ttk.Label(card, text="把指令发给掌握当前工作的旧会话。它会返回短摘要，或保存交接文件。",
                  style="CardMuted.TLabel", wraplength=830).pack(anchor="w", pady=(5, 12))
        controls = ttk.Frame(card, style="Card.TFrame")
        controls.pack(fill="x")
        ttk.Label(controls, text="本次长期知识处理", style="Card.TLabel").pack(side="left")
        self.policy_box = ttk.Combobox(controls, textvariable=self.policy_label,
                                      values=tuple(POLICY_LABELS.values()),
                                      state="readonly", width=22)
        self.policy_box.pack(side="left", padx=10)
        self.policy_box.bind("<<ComboboxSelected>>", self._policy_changed)
        ttk.Button(controls, text="知识目录设置",
                   command=lambda: self.app.show_page("settings")).pack(side="right")
        ttk.Label(card, textvariable=self.knowledge_caption, style="CardMuted.TLabel",
                  wraplength=810).pack(anchor="w", pady=(8, 0))
        ttk.Label(card, textvariable=self.destination_caption, style="CardMuted.TLabel",
                  wraplength=810).pack(anchor="w", pady=(4, 0))
        self.old_text = self._text(card, 8)
        actions = self.footer_pages[0]
        self.copy_old_button = ttk.Button(actions, text="复制给旧会话",
                                          style="Primary.TButton",
                                          command=lambda: self._guard(self.copy_old))
        self.copy_old_button.pack(side="left")
        self.generate_old_button = ttk.Button(actions, text="重新生成",
                                               command=lambda: self._guard(
                                                   lambda: self.generate_old(force=True)))
        self.generate_old_button.pack(side="left", padx=8)
        ttk.Button(actions, text="编辑模板", command=lambda: self._guard(
            self.edit_templates)).pack(side="left")
        ttk.Button(actions, text="下一步：保存摘要 →",
                   command=lambda: self.show_step(2)).pack(side="right")
        self._responsive_rows.append((actions, list(actions.winfo_children())))

    def _build_summary(self, card):
        ttk.Label(card, text="2 · 把摘要保存成可恢复的交接",
                  style="Heading.TLabel").pack(anchor="w")
        ttk.Label(card, text="粘贴旧会话返回的 Markdown，或导入它保存的文件。"
                  "轻续将另存新版本，并读回核对。",
                  style="CardMuted.TLabel", wraplength=830).pack(anchor="w", pady=(5, 10))
        bar = ttk.Frame(card, style="Card.TFrame")
        bar.pack(fill="x")
        self.paste_button = ttk.Button(bar, text="粘贴摘要",
                                      command=lambda: self._guard(self.paste_summary))
        self.paste_button.pack(side="left")
        self.import_button = ttk.Button(bar, text="导入文件…",
                                       command=lambda: self._guard(self.import_summary))
        self.import_button.pack(side="left", padx=8)
        self.read_old_button = ttk.Button(bar, text="读取旧聊保存的文件",
                                         command=lambda: self._guard(self.read_old_file))
        self.read_old_button.pack(side="left", padx=(0, 8))
        self.recent_button = ttk.Button(bar, text="读取最近交接",
                                       command=lambda: self._guard(self.restore_latest))
        self.recent_button.pack(side="left")
        self._responsive_rows.append((bar, list(bar.winfo_children())))
        self.summary_text = self._text(card, 8)
        self.summary_text.bind("<<Modified>>", self._summary_edited)
        ttk.Label(card, textvariable=self.summary_state, style="Card.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=self.saved_caption, style="CardMuted.TLabel",
                  wraplength=830).pack(anchor="w", pady=(5, 12))
        actions = self.footer_pages[1]
        self.save_button = ttk.Button(actions, text="保存并核对", style="Primary.TButton",
                                      command=lambda: self._guard(self.save_summary))
        self.save_button.pack(side="left")
        ttk.Button(actions, text="打开保存目录", command=lambda: self._guard(
            self.open_directory)).pack(side="left", padx=8)
        ttk.Button(actions, text="去新会话续接 →",
                   command=lambda: self.show_step(3)).pack(side="right")
        self._responsive_rows.append((actions, list(actions.winfo_children())))

    def _build_new(self, card):
        ttk.Label(card, text="3 · 在同项目的新会话继续",
                  style="Heading.TLabel").pack(anchor="w")
        ttk.Label(card, text="先真正新建会话，再粘贴下方指令。新会话先了解项目目标，再从中断处继续。",
                  style="CardMuted.TLabel", wraplength=830).pack(anchor="w", pady=(5, 12))
        modes = ttk.Frame(card, style="Card.TFrame")
        modes.pack(fill="x")
        ttk.Radiobutton(modes, text="读取交接文件 · 指令更短", variable=self.mode,
                        value="file").pack(side="left")
        ttk.Radiobutton(modes, text="携带摘要 · 可直接粘贴", variable=self.mode,
                        value="inline").pack(side="left", padx=(18, 0))
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 6))
        ttk.Label(row, text="下一阶段目标", style="Card.TLabel").pack(side="left", padx=(0, 10))
        ttk.Entry(row, textvariable=self.goal).pack(side="left", fill="x", expand=True)
        ttk.Label(card, text="可留空：由新会话根据未完成事项选择下一步。",
                  style="CardMuted.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=self.restore_state, style="CardMuted.TLabel",
                  wraplength=830).pack(anchor="w", pady=(8, 0))
        self.new_text = self._text(card, 8)
        actions = self.footer_pages[2]
        self.copy_new_button = ttk.Button(actions, text="复制给新会话",
                                          style="Primary.TButton",
                                          command=lambda: self._guard(self.copy_new))
        self.copy_new_button.pack(side="left")
        self.generate_new_button = ttk.Button(actions, text="预览指令",
                                               command=lambda: self._guard(
                                                   lambda: self.generate_new(force=True)))
        self.generate_new_button.pack(side="left", padx=8)
        ttk.Button(actions, text="返回保存摘要", command=lambda: self.show_step(2)).pack(side="right")
        self._responsive_rows.append((actions, list(actions.winfo_children())))

    def _guard(self, action):
        try:
            return action()
        except (OSError, ValueError, tk.TclError) as exc:
            self._status(str(exc))
            return None

    def _status(self, text):
        self.feedback.set(text)
        self.app.set_status(text)

    def _require_record(self):
        if getattr(self, "binding_warning", None):
            raise HandoffError(self.binding_warning)
        if not self.record:
            raise HandoffError("请先在顶部选择项目和聊天。")
        return self.record

    @staticmethod
    def _get(box):
        return box.get("1.0", "end-1c")

    def _put(self, box, text):
        self._loading = True
        try:
            box.delete("1.0", "end")
            box.insert("1.0", text)
            box.edit_modified(False)
            box.edit_reset()
        finally:
            self._loading = False

    def show_step(self, value):
        self.step.set(value)
        for i, page in enumerate(self.pages, 1):
            page.pack_forget()
            self.footer_pages[i - 1].pack_forget()
            self.step_buttons[i - 1].configure(
                style="Primary.TButton" if i == value else "TButton")
        self.pages[value - 1].pack(fill="both", expand=True)
        self.footer_pages[value - 1].pack(fill="x", before=self.footer_feedback)
        self.canvas.yview_moveto(0)
        self._update()

    def refresh_preferences(self):
        prefs = self.app.prefs
        policy = prefs.get("knowledge_policy", "ask")
        knowledge_enabled = prefs.get("knowledge_mode", "none") != "none"
        if not knowledge_enabled:
            policy = "none"
        if policy not in POLICY_LABELS:
            policy = "none"
        self.policy.set(policy)
        self.policy_label.set(POLICY_LABELS[policy])
        self.policy_box.configure(values=tuple(POLICY_LABELS.values()) if knowledge_enabled
                                  else (POLICY_LABELS["none"],))
        directory = prefs.get("knowledge_dir", "") if knowledge_enabled else ""
        self.knowledge_caption.set(
            f"资料目录：{directory} · 只按需读索引与相关正文"
            if directory else "知识库未启用。普通 Markdown 交接仍然完整可用。")
        self.destination_caption.set("交接保存位置：" + str(self.app.handoff_store.root))
        self._draft_path = None
        self._old_signature = None
        try:
            self._templates = self.app.template_store.load()
        except (OSError, ValueError) as exc:
            self._templates = default_templates()
            self._status("自定义模板暂不可读，当前显示默认模板：" + str(exc))
        self._update()

    def set_record(self, record):
        self.binding_warning = None
        self.record = dict(record) if record else None
        self.saved = None
        self._draft_path = None
        self._generated_old = self._generated_new = ""
        self._copied_old = self._copied_new = self._copied_goal = ""
        self._old_signature = self._new_signature = None
        self.goal.set("")
        for box in (self.old_text, self.summary_text, self.new_text):
            self._put(box, "")
        if self.record:
            self.record_caption.set(
                f"{self.record.get('app_project_name') or '未归类项目'}  ›  "
                f"{self.record.get('label') or self.record.get('chat_id')}\n"
                f"{self.record.get('path') or '未提供工作目录'}")
            try:
                recent = self.app.handoff_store.latest(self.record)
                if recent:
                    self._accept_saved(recent)
                    self._status("已载入这条聊天最近保存的交接。请核对时间和当前进度。")
                else:
                    self.saved_caption.set("这条聊天还没有已保存的交接。")
                    self._status("先复制旧聊指令；收到摘要后再回这里保存。")
            except (OSError, ValueError) as exc:
                self.saved_caption.set("未读取到可验证交接。")
                self._status("读取交接失败：" + str(exc))
        else:
            self.record_caption.set("先在顶部选择项目和聊天")
            self.saved_caption.set("保存后会显示准确位置与读回结果。")
            self.feedback.set("选择聊天后，复制旧聊指令开始。")
        self.show_step(1)
        if self.record:
            # Build a useful first preview only; no clipboard or file write.
            self._guard(self.generate_old)
            if self.saved:
                self._status("旧聊指令已准备；也已载入这条聊天的最近交接，可到第 3 步续接。")

    def _policy_changed(self, _event=None):
        self.policy.set(next((key for key, value in POLICY_LABELS.items()
                              if value == self.policy_label.get()), "none"))
        self._old_signature = None
        if self._get(self.old_text).strip():
            self._status("本次知识处理方式已变；下次生成或复制将使用新设置。")

    def _summary_edited(self, _event=None):
        if not self.summary_text.edit_modified():
            return
        self.summary_text.edit_modified(False)
        if not self._loading:
            self._update()

    def _summary_value(self):
        return self._get(self.summary_text).replace("\r\n", "\n").strip()

    def _saved_current(self):
        return bool(self.saved and self._summary_value() == self.saved.get("summary", "").strip())

    def _update(self):
        if not hasattr(self, "copy_new_button"):
            return
        selected = bool(self.record)
        summary = self._summary_value()
        saved = self._saved_current()
        for button in (self.copy_old_button, self.generate_old_button, self.paste_button,
                       self.import_button, self.read_old_button, self.recent_button):
            button.configure(state="normal" if selected else "disabled")
        self.save_button.configure(state="normal" if selected and summary else "disabled")
        usable = selected and bool(summary) and (self.mode.get() == "inline" or saved)
        for button in (self.copy_new_button, self.generate_new_button):
            button.configure(state="normal" if usable else "disabled")
        self.step_captions[0].set("1  已复制旧聊指令" if self._copied_old else "1  整理旧会话")
        self.step_captions[1].set("2  已保存并核对" if saved else "2  保存交接")
        self.step_captions[2].set("3  待新聊执行" if self._copied_new else "3  开始新会话")
        self.summary_state.set("已保存并读回" if saved else
                               ("摘要已编辑，尚未保存" if summary else "尚未收到摘要"))
        if self.mode.get() == "file":
            self.restore_state.set("新会话将读取已保存文件。复制前会再次检查文件与项目。"
                                   if saved else "先在第 2 步保存并核对摘要，文件引用型才能使用。")
        else:
            self.restore_state.set(
                "摘要会直接放入新聊指令。"
                + ("当前版本已保存。" if saved else "当前摘要尚未保存；复制后仍需在新会话核对。"))

    def has_unsaved(self):
        summary = self._summary_value()
        if summary and not self._saved_current():
            return True
        for box, generated, copied in (
            (self.old_text, self._generated_old, self._copied_old),
            (self.new_text, self._generated_new, self._copied_new),
        ):
            value = self._get(box)
            if value and value not in (generated, copied):
                return True
        if self.goal.get().strip() and self.goal.get().strip() != self._copied_goal:
            return True
        if self._template_dialog and self._template_dialog.winfo_exists():
            return any(self._get(box) != self._template_original[kind]
                       for kind, box in self._template_boxes.items())
        return False

    def _replace_ok(self):
        if self._summary_value() and not self._saved_current():
            return messagebox.askyesno("替换当前摘要？",
                "当前摘要尚未保存。继续将替换编辑框内容。", parent=self)
        return True

    def generate_old(self, force=False):
        record = self._require_record()
        if self._draft_path is None:
            self._draft_path = self.app.handoff_store.new_path(record)
        knowledge_dir = self.app.prefs.get("knowledge_dir", "") if (
            self.app.prefs.get("knowledge_mode", "none") != "none") else ""
        signature = (record.get("chat_id"), record.get("path"), self.policy.get(),
                     knowledge_dir, str(self._draft_path), self._templates["save"])
        current = self._get(self.old_text)
        if not force and signature == self._old_signature and current.strip():
            return current
        if current and current not in (self._generated_old, self._copied_old):
            if not messagebox.askyesno("重新生成指令？",
                    "这会替换你编辑的旧聊指令。", parent=self):
                return None
        value = build_save_prompt(record, self._draft_path,
                                  knowledge_policy=self.policy.get(),
                                  knowledge_dir=knowledge_dir,
                                  template=self._templates["save"])
        self._put(self.old_text, value)
        self._generated_old = value
        self._old_signature = signature
        self._copied_old = ""
        self._status("旧聊指令已生成。可以编辑后复制；尚未执行或保存摘要。")
        self._update()
        return value

    def _copy(self, value):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(value)
        self.app.root.update_idletasks()

    def copy_old(self):
        value = self.generate_old()
        if value is None:
            return
        self._copy(value)
        self._copied_old = value
        self._status("已复制。请发给所选旧会话；它返回摘要后，进入第 2 步。")
        self._update()

    def paste_summary(self):
        self._require_record()
        value = self.app.root.clipboard_get()
        if not value.strip():
            raise HandoffError("剪贴板里没有摘要文字。")
        if not self._replace_ok():
            return
        self._put(self.summary_text, value)
        self._status("已粘贴，尚未保存。核对项目和内容后点击“保存并核对”。")
        self._update()
        self.summary_text.focus_set()

    def import_summary(self, path=None):
        self._require_record()
        if not self._replace_ok():
            return
        if path is None:
            options = {"title": "选择旧会话生成的摘要", "parent": self,
                       "filetypes": [("Markdown 与文本", "*.md *.txt"), ("所有文件", "*.*")]}
            if self.app.handoff_store.root.is_dir():
                options["initialdir"] = str(self.app.handoff_store.root)
            path = filedialog.askopenfilename(**options)
        if not path:
            return
        try:
            saved = self.app.handoff_store.import_file(
                self.record, path, knowledge_policy=self.policy.get())
        except HandoffError as exc:
            if "普通摘要没有项目标记" not in str(exc):
                raise
            if not messagebox.askyesno("确认摘要所属项目",
                    f"此文件没有轻续项目标记。\n\n请确认它属于："
                    f"{self.record.get('app_project_name') or self.record.get('path')}\n"
                    f"聊天：{self.record.get('label')}\n\n确认后另存新版本，原文件保持不变。",
                    parent=self):
                return
            saved = self.app.handoff_store.import_file(
                self.record, path, allow_unbound=True, knowledge_policy=self.policy.get())
        self._accept_saved(saved)
        self._status("已导入、另存并读回核对。原文件保持不变。")
        self._update()

    def _accept_saved(self, saved):
        self.saved = saved
        self._draft_path = None
        self._old_signature = None
        self._put(self.summary_text, saved["summary"])
        warnings = "；".join(saved.get("warnings", []))
        self.saved_caption.set(
            f"保存时间：{saved['metadata']['generated_at']}\n文件：{saved['path']}"
            + ("\n" + warnings if warnings else ""))
        self._new_signature = None
        self._generated_new = self._copied_new = ""
        self._put(self.new_text, "")
        self._update()

    def save_summary(self):
        record = self._require_record()
        summary = self._summary_value()
        if not summary:
            raise HandoffError("请先粘贴或导入摘要。")
        if self._saved_current():
            try:
                verified = self.app.handoff_store.load(self.saved["path"], record)
                if verified["sha256"] == self.saved["sha256"]:
                    self._status("当前摘要已经保存并核对，无需重复写入。")
                    return self.saved
            except (OSError, ValueError):
                pass  # Preserve editor content by saving a separate new version.
        saved = self.app.handoff_store.save(record, summary, knowledge_policy=self.policy.get())
        self._accept_saved(saved)
        self._status("交接已保存并读回核对。现在可以生成新会话指令。")
        return saved

    def read_old_file(self):
        self._require_record()
        if self._draft_path is None:
            raise HandoffError("先生成旧聊指令；已有其他交接文件可点击“导入文件”。")
        if not self._draft_path.is_file():
            raise HandoffError("旧会话尚未在指定位置保存文件。可粘贴它返回的摘要，或等待后再检查。")
        self.import_summary(path=self._draft_path)

    def restore_latest(self):
        self._require_record()
        if not self._replace_ok():
            return
        saved = self.app.handoff_store.latest(self.record)
        if not saved:
            raise HandoffError("这条聊天没有可验证的已保存交接。可粘贴摘要或导入文件。")
        self._accept_saved(saved)
        self._status("已读取这条聊天最近的交接。请核对时间和当前状态。")

    def open_directory(self):
        directory = Path(self.saved["path"]).parent if self.saved else self.app.handoff_store.root
        if not directory.is_dir():
            self._status("交接目录尚未创建。第一次保存时会创建，或到设置中选择现有目录。")
            return
        self.app.open_path(directory)

    def generate_new(self, force=False):
        record = self._require_record()
        summary = self._summary_value()
        if not summary:
            raise HandoffError("先在第 2 步粘贴或导入摘要。")
        saved = self.saved if self._saved_current() else None
        signature = (record.get("chat_id"), record.get("path"), summary,
                     saved.get("sha256") if saved else "", self.mode.get(),
                     self.goal.get().strip(), self._templates["restore"])
        current = self._get(self.new_text)
        if not force and signature == self._new_signature and current.strip():
            # Re-validate a path-based handoff even when the text was edited.
            if self.mode.get() == "file":
                build_restore_prompt(record, saved=saved, mode="file")
            return current
        if current and current not in (self._generated_new, self._copied_new):
            if not messagebox.askyesno("重新生成续接指令？",
                    "这会替换你编辑的新聊指令。", parent=self):
                return None
        value = build_restore_prompt(
            record, summary=summary, saved=saved, mode=self.mode.get(),
            next_goal=self.goal.get().strip(), template=self._templates["restore"])
        self._put(self.new_text, value)
        self._generated_new = value
        self._new_signature = signature
        self._copied_new = ""
        self._status("新聊指令已就绪。请在同项目真正的新会话粘贴发送。")
        self._update()
        return value

    def copy_new(self):
        value = self.generate_new()
        if value is None:
            return
        self._copy(value)
        self._copied_new = value
        self._copied_goal = self.goal.get().strip()
        self._status("已复制，待新会话执行。轻续没有自动创建会话，也未确认任务已恢复。")
        self._update()

    def edit_templates(self):
        if self._template_dialog and self._template_dialog.winfo_exists():
            self._template_dialog.lift()
            return
        window = tk.Toplevel(self)
        self._template_dialog = window
        window.title("编辑交接模板")
        window.geometry("800x580")
        window.minsize(620, 420)
        window.transient(self.app.root)
        window.configure(background=BG)
        outer = ttk.Frame(window, padding=18, style="App.TFrame")
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="你的续聊方式", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(outer, text="编辑指导正文。项目身份、文件格式与校验要求仍由轻续自动添加。",
                  style="Muted.TLabel", wraplength=730).pack(anchor="w", pady=(6, 12))
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        self._template_boxes = {}
        self._template_original = dict(self._templates)
        for kind, name in (("save", "旧会话收尾"), ("restore", "新会话接续")):
            pane = ttk.Frame(tabs, padding=10, style="Card.TFrame")
            tabs.add(pane, text=name)
            box = self._text(pane, 13)
            self._put(box, self._templates[kind])
            self._template_boxes[kind] = box
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.pack(fill="x", pady=(12, 0))
        def close():
            changed = any(self._get(box) != self._template_original[k]
                          for k, box in self._template_boxes.items())
            if changed and not messagebox.askyesno("放弃模板修改？",
                    "模板还未保存。关闭将放弃本次编辑。", parent=window):
                return
            window.grab_release()
            window.destroy()
            self._template_dialog = self._template_boxes = self._template_original = None
        def defaults():
            for kind, value in default_templates().items():
                self._put(self._template_boxes[kind], value)
        def save():
            values = {kind: self._get(box) for kind, box in self._template_boxes.items()}
            try:
                result = self.app.template_store.save(values)
                self._templates = result["templates"]
                self._template_original = dict(self._templates)
                self._old_signature = self._new_signature = None
                self._status("模板已保存。下一次生成指令时使用；旧模板版本保留。")
                close()
            except (OSError, ValueError) as exc:
                messagebox.showerror("模板未保存", str(exc), parent=window)
        ttk.Button(buttons, text="恢复默认正文", command=defaults).pack(side="left")
        ttk.Button(buttons, text="保存模板", command=save, style="Primary.TButton").pack(side="right")
        ttk.Button(buttons, text="取消", command=close).pack(side="right", padx=8)
        window.protocol("WM_DELETE_WINDOW", close)
        window.bind("<Escape>", lambda _: close())
        window.bind("<Control-Return>", lambda _: save())
        window.grab_set()
        self._template_boxes["save"].focus_set()

