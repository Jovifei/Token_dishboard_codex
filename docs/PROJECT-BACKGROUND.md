# 轻续 Qingxu · 项目背景、技术路线与需求

## 由来与决策

1. 最初想解释同一台机器上某些 Codex 聊天的 token 消耗为何很高。排查对象包括长历史、工具和图片返回、技能/MCP、反复调用与父子任务。需求是从可核实的记录判断瓶颈，再考虑缩短上下文、接力或调整推理档。累计、缓存和额度有不同口径，不能仅凭总量给出节省结论。
2. 接着设计本地接力。旧聊天收尾时保存短任务状态。新聊天先看当前项目和短断点，有缺口才定向读知识。真实代码与摘要冲突时核对现场。采集元数据和语义摘要分开。写入、读回、在真正新聊接续是三个验收点。
3. 早期 Codex Pocket 有配置和手工模板。轻续沿用轻量 Python/Tk 路线，把它扩展为按项目选聊天、用量诊断、配置恢复及提示词工作台。0.9.3 增加同聊实时诊断与可调提醒。分享版封成 Windows x64 EXE。2026-11-09 已准备给外部 ChatGPT/Figma 的可读、可运行、可编辑交接包。
4. 本次把产品从原长聊天的工作目录搬到独立项目。后续以 `work/qingxu/` 为当前源，冻结报告用于对照。作者原先的多项目用量审计与 P2 账本是独立任务。

## Jovi 的产品要求

- 项目分类跟 Codex 侧栏，两级为项目和聊天。进入项目默认最新聊天，也能直接用最近聊天，记住所选范围。打开其他聊天应立即清除旧图和诊断。
- 现有一级页面优先保持三页，即「交接与知识库」「聊天用量」「配置与恢复」。用量在同页区分最近请求、累计、内容来源与下方诊断。Token、百分比、调用次数和日志字节各自标单位和范围。优先使用清晰、简洁、接近 Apple 的布局，按钮反馈和错误恢复要实际点验。
- 需要能定位上下文压力、重复工具或 MCP 调用、最大输出及可做的处理。顶栏提醒默认 80%，可调整、可暂停，到线或近期压缩提示并在 Windows 闪动一次。由用户决定是否保存交接、何时换聊。
- 配置有官方继承、轻续推荐、自定义三种。窗口、自动压缩阈值和单次工具保留量独立。读本机模型目录，Max/Ultra 随模型支持显示。清楚标明作用域、文件值、目录参考、候选值、写入回执和运行时采用，允许备份和恢复。继承数值作为对照，按任务使用更高推理或子 agent。
- 旧聊生成短断点，新聊从同项目继续。普通 Markdown 即可，Obsidian 可选。长期知识按目标/需求、计划、进度、决策/阻塞、经验、调研、Bug/版本分类。当前勾选生成给 Codex 的指令，由用户发送并按授权执行。项目目标或计划实质变更时可询问是否更新；未回答仍待确认。
- 下一阶段目标包含可选择目录、无知识库可用、按需知识索引和更清晰的点击/错误状态。上述是产品目标。当前 GUI 只可选择配置文件，其他目录选择器、摘要文件导入/导出和自动知识库写回仍是缺口。

## 技术路线

| 层 | 现有实现与边界 |
| --- | --- |
| 客户端 | Windows Python 标准库、Tk/ttk、SQLite 和文本文件。入口 `launch.pyw`，主窗口和回调 `app.py`，图表与页面 `dashboard.py`。PyInstaller 封装运行环境，分享 EXE 默认读取使用者自己的 `.codex`。 |
| 聊天身份 | `chats.py` 只读 Codex SQLite 与桌面侧栏 JSON。优先明确项目身份和主机，其后使用确切根或工作树提示。歧义归未归类。私有接力登记以独立项目 ID 和准确工作路径校验。 |
| 用量 | `context_usage.py` 从选中聊天 JSONL 读最近请求、压缩和逐请求记录。累计按当前 thread_id/response_id 去重。`usage.py`、可选 `observer.py` 与 `attribution.py` 消费 ccusage 日期增量快照。日志字节和工具事件只用于诊断。只轮询选中的聊天，约 15 秒。 |
| 配置 | `core.py` 按白名单改选中的 TOML 字段。`config_store.py` 预览、备份、并发保护、写后读回与恢复。`privacy.py`、`sync.py` 提供本地绝对路径、私有目录和受检文件替换。 |
| 交接与知识 | `relay.py` 与 `app.py` 生成旧聊、新聊和知识指令。登记模式调用独立 `checkpoint.py`，项目外保留 UTF-8 短摘要与 revision，实际写入需校验。手动模式粘贴短摘要，无自动发消息或创建聊天。映射只解析目标路径，正文按需读。 |
| 发布与验证 | `releases/` 有 0.9.3 的原便携包及 UI 交接包。`ui-handoff-0.9.3/` 包含可选引擎源码、可重建的合成样本、运行命令、截图与控件对照。 |

当前真实计量公式、默认值、目录、按钮反馈、原生截图和已知问题，以 [离线交接报告](ui-handoff-0.9.3/index.html) 及 [文件清单](ui-handoff-0.9.3/manifest.json) 为准。报告里的作者配置是带采样时刻的历史文件读数，后续任务以现场重查为准。

## 借鉴和实际使用的工程

- **Codex Pocket 0.1.0 preview** 是初始附件。`work/qingxu/provenance.json` 记录原 ZIP 哈希和沿用的 `core.py`、`prompts.py`、`test_core.py`、MIT 许可。当前交接提示词主要由 `app.py`、`relay.py` 生成。`prompts.py` 是保留的早期模板参考。
- **ccusage** 是日期快照生产端的真实可选依赖。作者观察器固定使用 20.0.48；轻续本体读取守恒校验后的增量 JSON，并用 Codex 侧栏重新归类。它的计量与会员额度或账单分开。 [ccusage 上游](https://github.com/ccusage/ccusage)
- **Codex 本机接口与配置参考** 提供项目、日志和模型配置语义。代码根据本机可获得字段确定显示范围，运行时采用仍须独立核对。 [OpenAI 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference) · [OpenAI 子 agent 说明](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- **RTK** 为过滤工具输出的先前评估对象。**QMD** 为按项目检索 Markdown 的可选思路。**planning-with-files** 与 **Beads** 供文件恢复、任务状态和依赖关系设计参考。当前 app 和分享包均没有这些运行依赖。 [RTK](https://github.com/rtk-ai/rtk) · [QMD](https://github.com/tobi/qmd) · [planning-with-files](https://github.com/OthmanAdi/planning-with-files) · [Beads](https://github.com/gastownhall/beads)
- 长上下文检索、压缩和推理预算研究启发了保留关键证据、减少重复返回以及按难度选推理。论文样本不同于本机任务，没有给出轻续的统一最佳阈值。 [Lost in the Middle](https://arxiv.org/abs/2307.03172) · [ACON](https://arxiv.org/abs/2510.00615) · [测试时算力分配](https://arxiv.org/abs/2408.03314)

## 后续分级

P0 优先验证项目绑定、口径和真实新聊接续。P1 再补目录选择、可移植普通 Markdown、提示反馈和失败恢复。P2 做视觉与图表。若要改 Figma，以截图编号和控件回调表为交接，保持三页先做可用闭环。本机观察器、全局配置和真实知识库分别按实际授权操作。
