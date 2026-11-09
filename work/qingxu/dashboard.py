"""Local snapshot dashboard; never starts collectors or calls a model."""
import math
import tkinter as tk
from tkinter import ttk

from chats import usage_metadata
from context_usage import FREQUENT_CALLS, LARGE_RESULT_BYTES, SOURCE_LABELS, TOOL_WINDOW_MINUTES
from usage import COMPONENTS, group_sessions, summarize, token_text

ROLE_NAMES = {"primary": "主 agent", "child": "子 agent", "unknown": "未知"}
TABLE_METRICS = ("totalTokens", "inputTokens", "cacheReadTokens", "cacheCreationTokens", "outputTokens")

# Apple 风语义色板（浅色；暗色未实现）。app.py 的样式从这里取色，保持集中定义。
BG = "#F5F5F7"          # 页面背景
SURFACE = "#FFFFFF"     # 卡片 / 次级背景
LABEL = "#142842"       # 主文字
SECONDARY = "#4D6179"   # 次级文字
TERTIARY = "#AEAEB2"    # 三级文字
SEPARATOR = "#D8E4EE"   # 分隔线
ACCENT = "#1465BC"      # 主色
DANGER = "#FF3B30"      # 危险色
SERIES = ("#007AFF", "#34C759", "#FF9500", "#FF3B30")  # 图表系列色
PROJECT_COLORS = ("#386DE8", "#008F83", "#9B62CE", "#CA7521",
                  "#D64B74", "#1F91BE", "#5C8735", "#7269AE")
ATTRIBUTION = {"PROJECTLESS": "Codex 标记为无项目", "AMBIGUOUS_ROOT": "工作目录属于多个项目",
               "UNKNOWN_PROJECT": "项目 ID 未在侧栏找到", "OTHER_HOST": "其他主机",
               "UNASSIGNED": "缺少项目归属", "NO_DB_MATCH": "未匹配本机聊天", "NO_ID": "快照缺少聊天 ID"}


class Composition(ttk.Frame):
    """One denominator for both the ring and its accessible numeric table."""
    def __init__(self, parent, rows):
        super().__init__(parent, padding=(0, 3), style="Card.TFrame")
        self.ring = tk.Canvas(self, width=108, height=108, bg=SURFACE, highlightthickness=0)
        self.ring.pack(side="left", padx=(0, 12))
        self.table = ttk.Treeview(self, columns=("tokens", "share"), show="tree headings",
                                  height=rows, selectmode="none", style="Metrics.Treeview")
        for column, text, width in (("#0", "构成", 120), ("tokens", "tokens", 105), ("share", "占比", 68)):
            self.table.heading(column, text=text)
            self.table.column(column, width=width, minwidth=58, anchor="w" if column == "#0" else "e")
        self.table.pack(side="left", fill="both", expand=True)
        self.swatches = {}
        self.render([], "待读取")

    def render(self, parts, center):
        self.table.delete(*self.table.get_children())
        self.ring.delete("all")
        bounds = (3, 3, 105, 105)
        self.ring.create_oval(*bounds, fill=SEPARATOR, outline="")
        total = sum(amount for _, amount, _ in parts)
        angle = 90.0
        for label, amount, color in parts:
            share = amount / total if total else 0
            if color not in self.swatches:
                swatch = tk.PhotoImage(master=self, width=9, height=9)
                swatch.put(color, to=(0, 0, 9, 9))
                self.swatches[color] = swatch
            self.table.insert("", "end", text=label, image=self.swatches[color],
                              values=(token_text(amount), f"{share:.1%}"))
            if share == 1:
                self.ring.create_oval(*bounds, fill=color, outline="")
            elif share * 360 >= 1:  # On Windows a sub-degree Tk arc can fill the entire circle.
                self.ring.create_arc(*bounds, start=angle, extent=share * 360, fill=color,
                                     outline="", style=tk.PIESLICE)
            angle += share * 360
        self.ring.create_oval(18, 18, 90, 90, fill=SURFACE, outline="")
        self.ring.create_text(54, 54, text=center, fill=LABEL, justify="center",
                              font=("Microsoft YaHei UI", 10, "bold"))


class ChatDashboard(ttk.Frame):
    def __init__(self, parent, on_select, scroll_page):
        super().__init__(parent)
        self.on_select = on_select
        self.chats, self.projects = {}, {}
        self.active = None
        self.catalog = tk.StringVar(value="读取 Codex 侧栏中…")
        split = ttk.Panedwindow(self, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ttk.Frame(split, padding=(0, 0, 8, 0))
        right = ttk.Frame(split, padding=(8, 0, 0, 0))
        split.add(left, weight=1)
        split.add(right, weight=3)
        ttk.Label(left, text="项目与聊天", style="Section.TLabel").pack(anchor="w")
        ttk.Label(left, textvariable=self.catalog, foreground=SECONDARY, wraplength=234
                  ).pack(anchor="w", pady=(0, 5))
        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse", height=9)
        self.tree.column("#0", width=230, minwidth=165, stretch=True)
        scrollbar = ttk.Scrollbar(tree_frame, command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.pick)
        self.tree.bind("<Return>", self.pick)
        self.heading = tk.StringVar(value="请选择一次聊天")
        ttk.Label(right, textvariable=self.heading, style="Section.TLabel",
                  wraplength=510).pack(anchor="w", pady=(0, 6))
        self.detail_canvas, content = scroll_page(right)
        context, usage, sources = (ttk.Frame(content, style="Card.TFrame", padding=(10, 7))
                                   for _ in range(3))
        for card, title in ((context, "最近请求上下文"), (usage, "聊天累计用量"), (sources, "内容来源")):
            card.pack(fill="x", pady=(0, 9))
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
        self.context_detail = tk.StringVar(value="选择左侧的一条聊天。")
        ttk.Label(context, textvariable=self.context_detail, style="Card.TLabel", wraplength=490
                  ).pack(anchor="w", pady=(0, 5))
        self.context = Composition(context, 3)
        self.context.pack(fill="x")
        self.usage = Composition(usage, 4)
        self.usage.pack(fill="x")
        self.usage_detail = tk.StringVar(value="按该聊天本机日志里的独立请求计数。")
        ttk.Label(usage, textvariable=self.usage_detail, style="Card.TLabel", wraplength=495
                  ).pack(anchor="w", pady=(3, 0))
        self.source_detail = tk.StringVar(value="请选择一次聊天。")
        ttk.Label(sources, textvariable=self.source_detail, style="Card.TLabel",
                  wraplength=490).pack(anchor="w", pady=(3, 5))
        self.source_table = ttk.Treeview(sources, columns=("records", "bytes", "share"), show="tree headings",
                                         height=len(SOURCE_LABELS), selectmode="none", style="Metrics.Treeview")
        for column, label, width in (("#0", "来源", 155), ("records", "记录数", 60),
                                     ("bytes", "日志字节", 92), ("share", "字节占比", 82)):
            self.source_table.heading(column, text=label)
            self.source_table.column(column, width=width, minwidth=55, anchor="w" if column == "#0" else "e")
        self.source_table.pack(fill="x")
        ttk.Label(sources, text="占比以表中记录的总字节为分母，包含 JSON 封装与图片编码。"
                  "系统工具定义、MCP 工具定义与服务指令、技能、压缩预留和延迟加载项没有独立计量。",
                  style="Card.TLabel", wraplength=490).pack(anchor="w", pady=(7, 0))
        self.diagnosis = ttk.Frame(content, style="Card.TFrame", padding=(10, 7))
        self.diagnosis.pack(fill="x", pady=(0, 9))
        ttk.Label(self.diagnosis, text="本次诊断", style="CardTitle.TLabel").pack(anchor="w")
        self.diagnosis_scope = ttk.Label(self.diagnosis, text="选择一次聊天后查看。", style="Card.TLabel")
        self.diagnosis_scope.pack(anchor="w", pady=(3, 2))
        self.diagnosis_notes = {}
        for key, title in (("context", "上下文压力 · tokens"), ("tools", "普通工具调用 · 次"),
                           ("mcp", "MCP 调用 · 次"), ("output", "最大单次结果 · 日志字节")):
            ttk.Label(self.diagnosis, text=title, style="Card.TLabel", foreground=LABEL,
                      font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(6, 0))
            note = ttk.Label(self.diagnosis, text="待测量", style="Card.TLabel", justify="left")
            note.pack(anchor="w")
            self.diagnosis_notes[key] = note
        self.diagnosis.bind("<Configure>", self.wrap_diagnosis)

    def wrap_diagnosis(self, event):
        for label in (self.diagnosis_scope, *self.diagnosis_notes.values()):
            label.configure(wraplength=max(180, event.width - 22))

    def render_catalog(self, result):
        opened = {identity for identity, item in self.projects.items() if self.tree.item(item, "open")}
        self.tree.delete(*self.tree.get_children())
        self.chats, self.projects = {}, {}
        chats_by_project = {}
        for record in result["chats"]:
            chats_by_project.setdefault(record["app_project_id"], []).append(record)
        projects = [(p["app_project_id"], p["label"]) for p in result["app_projects"]]
        projects.append((None, "未归类"))
        for identity, label in projects:
            chats = chats_by_project.get(identity, ())
            parent = self.tree.insert("", "end", text=f"{label}  ·  {len(chats)}",
                                      open=identity in opened, tags=("project",))
            self.projects[identity] = parent
            for record in chats:
                key = record["chat_id"]
                self.chats[key] = record
                self.tree.insert(parent, "end", iid=key, text=record["label"])
        self.tree.tag_configure("project", foreground=ACCENT)
        self.catalog.set(f'{len(result["app_projects"])} 个项目 · {len(self.chats)} 条活跃聊天')
        self.select_chat(self.active)

    def pick(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        item = selected[0]
        if item in self.projects.values():
            self.tree.item(item, open=True)
            children = self.tree.get_children(item)
            if not children:
                self.on_select(None)
                return
            item = children[0]
        if item in self.chats and item != self.active:
            self.on_select(self.chats[item])
        elif item == self.active:
            self.select_chat(item)

    def select_chat(self, identity):
        self.active = identity
        if identity in self.chats:
            parent = self.tree.parent(identity)
            self.tree.see(identity)
            if parent and not self.tree.bbox(parent):
                self.tree.see(parent)
                if not self.tree.bbox(identity):
                    self.tree.see(identity)
            if self.tree.selection() != (identity,):
                self.tree.selection_set(identity)
        elif self.tree.selection():
            self.tree.selection_remove(*self.tree.selection())

    def set_chat(self, record):
        self.select_chat(record["chat_id"] if record else None)
        self.detail_canvas.yview_moveto(0)
        if record:
            self.heading.set(f'{record.get("app_project_name") or "未归类"}  ›  {record["label"]}')
        else:
            self.heading.set("请选择一次聊天")
        self.context.render([], "待读取")
        self.usage.render([], "待读取")
        self.source_table.delete(*self.source_table.get_children())
        self.source_detail.set("正在读取所选聊天的日志…" if record else "请选择一次聊天。")
        self.context_detail.set("正在读取选中聊天的最近请求…" if record else "选择左侧的一条聊天。")
        self.usage_detail.set("正在汇总本机可读的独立请求…" if record else "选择左侧的一条聊天。")
        self.show_context_diagnosis("unknown", self.context_detail.get())
        self.show_tool_diagnosis(None, "正在读取所选聊天的日志…" if record else "选择一次聊天后查看。")

    def show_sample(self, sample, message=None):
        if not sample:
            self.context.render([], "无样本")
            self.context_detail.set(message or "近期日志没有可用的请求测量。")
            return
        usage, window = sample["usage"], sample["window"]
        used = usage["totalTokens"]
        parts = [("最近请求输入", used - usage["outputTokens"], ACCENT),
                 ("最近请求输出", usage["outputTokens"], "#8C66D9")]
        center = "最近请求"
        if window and used <= window:
            parts.append(("窗口余量（估）", window - used, "#E3EBF4"))
            center = f"{used / window:.1%}\n窗口占用（估）"
        self.context.render(parts, center)
        time = sample["time"].astimezone().strftime("%m-%d %H:%M:%S")
        self.context_detail.set(f'{token_text(used)} / {token_text(window) if window else "未知"} tokens'
                                f' · 本机时间 {time}')

    def show_requests(self, result, error=None):
        data = result.get("totals") if result else None
        if not data:
            self.usage.render([], "无明细")
            self.usage_detail.set(error or "本机日志未提供逐请求计数，可另查日期快照。")
            return
        parts = [(label, data[key], color) for (label, key), color in zip(COMPONENTS, SERIES)]
        self.usage.render(parts, "合计\n" + token_text(data["totalTokens"]))
        detail = (f'{result["requests"]} 个独立请求 · {result["files"]} 段本机日志'
                  f' · 推理 {token_text(data["reasoningOutputTokens"])} tokens 已计入输出。')
        if result["unavailable"] or result["duplicates"]:
            detail += f' 不可计量 {result["unavailable"]} 条或段，重复跳过 {result["duplicates"]} 条。'
        time = result["measured_at"].astimezone().strftime("%H:%M:%S")
        self.usage_detail.set(detail + f" 更新于 {time}。")

    def show_sources(self, result, error=None):
        self.source_table.delete(*self.source_table.get_children())
        if result is None:
            self.source_detail.set(error or "暂无可读的消息和工具记录。")
            self.show_tool_diagnosis(None, error)
            return
        rows = result["rows"]
        total = sum(size for _, size in rows.values())
        for key, label in SOURCE_LABELS.items():
            records, size = rows[key]
            self.source_table.insert("", "end", iid=key, text=label,
                                     values=(token_text(records), token_text(size),
                                             f"{size / total:.1%}" if total else "—"))
        window = "最后 8 MiB" if result["truncated"] else "最近的完整日志段"
        scanned = token_text(result["scanned_bytes"])
        self.source_detail.set(f"{window} · 读取 {scanned} 字节。这里展示日志大小和字节占比。")
        self.show_tool_diagnosis(result.get("diagnosis"))

    def diagnosis_note(self, key, text, warning=False):
        self.diagnosis_notes[key].configure(text=text, foreground="#69400C" if warning else SECONDARY)

    def show_context_diagnosis(self, level, text):
        self.diagnosis_note("context", ("需关注 · " if level == "alert" else "") + text, level == "alert")

    def show_tool_diagnosis(self, diagnosis, unavailable=None):
        if diagnosis is None:
            self.diagnosis_scope.configure(text=unavailable or "暂无工具诊断样本。")
            for key in ("tools", "mcp", "output"):
                self.diagnosis_note(key, "待测量")
            return
        end = diagnosis["window_end"]
        stamp = end.astimezone().strftime("%m-%d %H:%M:%S") if end else "时间未知"
        self.diagnosis_scope.configure(
            text=f"调用频率 · 截至 {stamp} 的最近 {TOOL_WINDOW_MINUTES} 分钟可读记录，"
                 f"同一工具达到 {FREQUENT_CALLS} 次提示。最大结果取上方日志样本，"
                 f"单条达到 {token_text(LARGE_RESULT_BYTES)} 字节提示。")
        for key, advice in (("tools", "核对重复读取或轮询，合并可以一起执行的只读查询。"),
                            ("mcp", "合并同源查询，先列出命中项，再读取必要字段。")):
            counts = diagnosis["calls"][key]
            if not counts:
                self.diagnosis_note(key, "样本内未识别到带时间的调用。")
                continue
            ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            flagged = [(tool, count) for tool, count in ranked if count >= FREQUENT_CALLS]
            listed = flagged or ranked
            names = "、".join(f"{tool} {count} 次" for tool, count in listed[:3])
            if len(listed) > 3:
                names += f"，另 {len(listed) - 3} 项"
            text = f"共 {sum(counts.values())} 次 · {names}。"
            self.diagnosis_note(key, "调用较频繁 · " + text + advice if flagged else text, bool(flagged))

        largest = diagnosis["largest_result"]
        if largest is None:
            self.diagnosis_note("output", "样本内未识别到工具结果。")
            return
        when = largest["time"]
        stamp = when.astimezone().strftime("%m-%d %H:%M:%S") if when else "时间未知"
        source = largest["tool"] or "工具未关联"
        size = largest["bytes"]
        warning = size >= LARGE_RESULT_BYTES
        text = f"{source} · {stamp} · {token_text(size)} 字节"
        if largest["call_suffix"]:
            text += f" · 调用尾号 {largest['call_suffix']}"
        if warning:
            text = "结果偏大 · " + text + "。可让工具只返回关键行或必要字段。"
        self.diagnosis_note("output", text, warning)


class Dashboard(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.totals = None
        self.rows = {}
        self.segments = []
        self.info = tk.StringVar(value="尚未读取。仅展示既有 ccusage 增量快照，不扫描聊天正文。")
        ttk.Label(self, textvariable=self.info, wraplength=950).pack(anchor="w", pady=4)
        scope = ttk.Frame(self)
        scope.pack(fill="x", pady=(0, 4))
        self.scope = tk.StringVar(value="全局快照")
        ttk.Label(scope, textvariable=self.scope, style="Section.TLabel",
                  wraplength=680).pack(side="left")
        ttk.Button(scope, text="回到总览", command=self.overview).pack(side="right")
        cards = ttk.Frame(self)
        cards.pack(fill="x")
        self.cards = {}
        for index, (metric, label) in enumerate((
                ("totalTokens", "总 token"), ("inputTokens", "非缓存输入"),
                ("cacheReadTokens", "缓存读取"), ("cacheCreationTokens", "缓存创建"),
                ("outputTokens", "输出（含推理）"))):
            card = ttk.Frame(cards, style="Card.TFrame", padding=10)
            card.grid(row=0, column=index, sticky="nsew", padx=(0, 8))
            cards.columnconfigure(index, weight=1)
            ttk.Label(card, text=label, style="Card.TLabel").pack(anchor="w")
            value = tk.StringVar(value="—")
            self.cards[metric] = value
            ttk.Label(card, textvariable=value, style="Number.TLabel").pack(anchor="w")
        self.components = []
        composition = ttk.Frame(self)
        composition.pack(fill="x", pady=4)
        self.chart = tk.Canvas(composition, height=22, bg=BG, highlightthickness=0)
        self.chart.pack(fill="x", pady=(7, 0))
        self.chart.bind("<Configure>", lambda _: self.draw())
        legend = ttk.Frame(composition)
        legend.pack(fill="x", pady=(2, 5))
        self.legend = []
        for color in SERIES:
            text = tk.StringVar()
            self.legend.append(text)
            item = ttk.Frame(legend)
            item.pack(side="left", expand=True)
            ttk.Label(item, text="■", foreground=color).pack(side="left", padx=(0, 4))
            ttk.Label(item, textvariable=text).pack(side="left")
        self.diagnostic = tk.StringVar(value="")
        ttk.Label(composition, textvariable=self.diagnostic, wraplength=630).pack(anchor="w")
        self.draw()
        browse = ttk.Frame(self)
        browse.pack(fill="both", expand=True)
        projects = ttk.Frame(browse, padding=(0, 8, 10, 0))
        projects.pack(side="left", anchor="n")
        self.ring = tk.Canvas(projects, width=148, height=148, bg=BG, highlightthickness=0)
        self.ring.pack(anchor="w")
        self.ring.bind("<Motion>", self.hover)
        self.ring.bind("<Leave>", lambda _: self.hover(None))
        self.ring.bind("<Button-1>", self.open_sector)
        self.ring_info = tk.StringVar(value="项目圆环 · 悬停看占比，点击展开聊天。")
        ttk.Label(projects, textvariable=self.ring_info, wraplength=148, width=18).pack(anchor="w")
        self.notebook = ttk.Notebook(browse)
        self.notebook.pack(side="left", fill="both", expand=True)
        self.trees = {}
        for key, label, name in (("projects", "项目与聊天", "项目 › 聊天标题"),
                                 ("roles", "主 / 子 agent", "角色")):
            panel = ttk.Frame(self.notebook)
            self.notebook.add(panel, text=label)
            columns = (*TABLE_METRICS, "records")
            headings = ("总 token", "非缓存输入", "缓存读取", "缓存创建", "输出", "记录数")
            tree = ttk.Treeview(panel, columns=columns, show="tree headings", height=5,
                                selectmode="browse")
            tree.bind("<<TreeviewSelect>>", lambda _, t=tree: self.select_row(t))
            tree.heading("#0", text=name, command=lambda t=tree: self.sort(t, "#0"))
            tree.column("#0", width=260, minwidth=155, anchor="w")
            for column, heading in zip(columns, headings):
                tree.heading(column, text=heading,
                             command=lambda t=tree, c=column: self.sort(t, c))
                tree.column(column, width=100 if column != "records" else 70, minwidth=70, anchor="e")
            yscroll = ttk.Scrollbar(panel, orient="vertical", command=tree.yview)
            yscroll.pack(side="right", fill="y")
            xscroll = ttk.Scrollbar(panel, orient="horizontal", command=tree.xview)
            xscroll.pack(side="bottom", fill="x")
            tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
            tree.pack(fill="both", expand=True)
            self.trees[key] = tree
        self.health = tk.StringVar(value="采集器状态未读取。")
        ttk.Label(self, textvariable=self.health, wraplength=760,
                  foreground=SECONDARY).pack(anchor="w", pady=(5, 3))
        self.detail = tk.StringVar(value="来源为本机已有的 ccusage 增量快照。")
        ttk.Label(self, textvariable=self.detail, wraplength=760).pack(anchor="w")

    def clear(self):
        self.totals = None
        self.rows.clear()
        self.segments.clear()
        for value in self.cards.values():
            value.set("—")
        for tree in self.trees.values():
            tree.delete(*tree.get_children())
            tree._sort = None
        self.components = []
        self.scope.set("全局快照")
        self.diagnostic.set("")
        self.draw()
        self.draw_ring()
        self.info.set("无有效快照；正在读取，失败不会沿用上次数值。")
        self.health.set("采集器状态未知。")
        self.detail.set("尚无有效来源。")

    @staticmethod
    def sort(tree, column):
        reverse = getattr(tree, "_sort", None) != (column, True)

        def key(item):
            if column == "#0":
                return tree.item(item, "text").casefold()
            return int(tree.set(item, column).replace(",", ""))

        for parent in ("", *tree.get_children()):
            for position, item in enumerate(sorted(tree.get_children(parent), key=key, reverse=reverse)):
                tree.move(item, parent, position)
        tree._sort = (column, reverse)

    def draw(self):
        self.chart.delete("all")
        width = max(1, self.chart.winfo_width() - 4)
        total = sum(value for _, value in self.components)
        x = 2
        labels = ("非缓存输入", "缓存读取", "缓存创建", "输出（含推理）")
        for index, (label, color) in enumerate(zip(labels, SERIES)):
            value = self.components[index][1] if self.components else 0
            right = x + width * value / total if total else x
            if right > x:
                self.chart.create_rectangle(x, 2, right, 20, fill=color, outline="")
            share = f"  {value / total:.1%}" if total else ""
            self.legend[index].set(label + share)
            x = right

    def draw_ring(self):
        self.ring.delete("all")
        bounds = (4, 4, 144, 144)
        self.ring.create_oval(*bounds, fill=SEPARATOR, outline="")
        for start, end, _, color in self.segments:
            extent = end - start
            if extent >= 359.999:
                self.ring.create_oval(*bounds, fill=color, outline="")
            elif extent >= 1:
                self.ring.create_arc(*bounds, start=start, extent=extent, fill=color,
                                     style=tk.PIESLICE, outline="")
        self.ring.create_oval(31, 31, 117, 117, fill=BG, outline="")
        self.ring.create_text(74, 74, text="项目\n占比", justify="center", fill=LABEL)
        self.hover(None)

    def sector(self, event):
        x, y = event.x - 74, 74 - event.y
        if 43 <= math.hypot(x, y) <= 70:
            angle = math.degrees(math.atan2(y, x)) % 360
            return next((item for start, end, item, _ in self.segments if start <= angle < end), None)
        return None

    def hover(self, event):
        item = self.sector(event) if event is not None else None
        self.ring.configure(cursor="hand2" if item else "")
        if item:
            title, row = self.rows["projects", item]
            count = row["totalTokens"]
            self.ring_info.set(f'{title} · {token_text(count)} tokens · {count / self.totals["totalTokens"]:.1%}')
        else:
            self.ring_info.set("项目圆环（全局） · 悬停看占比，点击展开聊天。")

    def open_sector(self, event):
        item = self.sector(event)
        if item:
            tree = self.trees["projects"]
            self.notebook.select(0)
            tree.item(item, open=True)
            tree.see(item)
            tree.focus(item)
            tree.selection_set(item)
            self.select_row(tree)

    def select_row(self, tree):
        selection = tree.selection()
        if selection:
            key = "projects" if tree is self.trees["projects"] else "roles"
            entry = self.rows.get((key, selection[0]))
            if entry:
                self.show_scope(*entry)

    def overview(self):
        for tree in self.trees.values():
            if tree.selection():
                tree.selection_remove(*tree.selection())
        if self.totals is not None:
            self.show_scope("全局快照", self.totals)

    def show_scope(self, title, row):
        self.scope.set(title)
        for key, value in self.cards.items():
            value.set(token_text(row[key]))
        self.components = [(label, row[key]) for label, key in COMPONENTS]
        self.draw()
        label, largest = max(self.components, key=lambda pair: pair[1])
        total = row["totalTokens"]
        hint = (f'最大项 {label} {largest / total:.1%} · 推理 {token_text(row["reasoningOutputTokens"])} tokens'
                if total else "此范围没有 token 记录")
        reasons = row.get("unclassified", {})
        if reasons:
            top = sorted(reasons, key=reasons.get, reverse=True)[:3]
            hint += "\n未归类原因 · " + " · ".join(
                f"{ATTRIBUTION.get(reason, '归属未知')} {token_text(reasons[reason])}" for reason in top)
        self.diagnostic.set(hint)

    def render(self, result, settings):
        self.clear()
        data = summarize(result["usage_snapshot"])
        try:
            metadata = usage_metadata(settings["chat_db"], data["sessions"])
            metadata_note = "展开项目查看聊天；无明确归属的用量在「未归类」。"
        except (ValueError, OSError) as exc:
            metadata = {}
            metadata_note = "聊天元数据暂不可读，本次用量归在「未归类」。" + str(exc)
        projects = group_sessions(data["sessions"], metadata)
        unassigned = projects.get(None, {})
        self.totals = data["totals"] | {"unclassified": unassigned.get("unclassified", {})}
        total = self.totals["totalTokens"]
        age = data["age_seconds"]
        freshness = ("时间未知" if age is None else "时间异常" if age < 0 else
                     f"{age / 60:.0f} 分钟前" + (" · 快照可能过期" if age > 5400 else ""))
        unclassified = unassigned.get("totalTokens", 0)
        share = f"（{unclassified / total:.1%}）" if total else ""
        self.info.set(f'快照日期 {data["date"]} · {data["timezone"]} · 采集 {data["captured"]}（{freshness}）\n'
                      f'{data["records"]} 条记录 · 项目未归类 {token_text(unclassified)} tokens{share}。'
                      + metadata_note)

        def insert(tree, parent, title, row):
            values = [token_text(row[key]) for key in TABLE_METRICS]
            return tree.insert(parent, "end", text=title, values=values + [row["records"]])

        tree = self.trees["projects"]
        angle = 0
        ordered = sorted(projects.items(), key=lambda pair: pair[1]["totalTokens"], reverse=True)
        for index, (pid, project) in enumerate(ordered):
            item = insert(tree, "", project["title"], project)
            self.rows["projects", item] = ("项目 · " + project["title"], project)
            if total and project["totalTokens"]:
                end = min(360, angle + 360 * project["totalTokens"] / total)
                color = PROJECT_COLORS[index % len(PROJECT_COLORS)] if pid is not None else TERTIARY
                self.segments.append((angle, end, item, color))
                angle = end
                tree.tag_configure(item, foreground=color if pid is not None else SECONDARY)
                tree.item(item, tags=(item,))
            for chat in sorted(project["chats"].values(), key=lambda row: row["totalTokens"], reverse=True):
                title = chat["title"]
                if title == "标题未读到" and chat["id"]:
                    title += " · " + chat["id"][:8]
                if chat["child_records"]:
                    title += f'  ·  含 {chat["child_records"]} 条子 agent 记录'
                child = insert(tree, item, title, chat)
                self.rows["projects", child] = (project["title"] + " › " + chat["title"], chat)
        for role, row in sorted(data["roles"].items(), key=lambda item: item[1]["totalTokens"], reverse=True):
            name = ROLE_NAMES.get(role, "未知")
            item = insert(self.trees["roles"], "", name, row)
            self.rows["roles", item] = (name, row)
        self.draw_ring()
        self.overview()
        observer = result["observer"]
        self.health.set(f'采集器心跳 {observer["heartbeat"]} · PID {observer.get("pid")} · '
                        f'进程身份 {observer["process_identity"]} · STOP {result["STOP"]}。')
        self.detail.set("增量快照 · 推理计入输出 · 无轮次、工具或技能的独立 token 计量。\n"
                        + str(settings["usage"]))
