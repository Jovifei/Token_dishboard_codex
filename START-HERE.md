# 轻续 Qingxu · 新聊天接手

当前版本是 **0.12.0-ui-rc1**，源码与测试在 `work/qingxu/`，入口 `launch.pyw` 加载 Tk `Workbench`。先核对当前分支、源码版本、所选 Codex 项目和授权范围。

四页布局和时间筛选见 [交互规范](docs/UX-SPEC.md)、[使用指南](docs/BILLING-USER-GUIDE.md) 与 [本机验收](docs/verification/0.11.3-rc1/README.md)。计费口径见 [计费交接](docs/BILLING-HANDOFF.md)。

| 本次任务 | 入口 |
| --- | --- |
| 安装、演示和构建 | [README](README.md) |
| Windows 与真实 Codex 验收 | [UI2 本机复验](docs/verification/0.10.0-rc4-ui2/README.md) · [rc2 完整验收](docs/LOCAL-ACCEPTANCE.md) |
| 代码入口及下一阶段 | [工程交接](docs/ENGINEERING-HANDOFF.md) · [UI2 增量交接](docs/UI-REVISION-HANDOFF.md) |
| 当前 UI 结构与历史截图 | [交互规范](docs/UX-SPEC.md) · [UI2 设计交接](docs/design/apple-ui-v2/README.md) · [rc1 远端界面](docs/ui-handoff-0.10.0-rc1/index.html) |
| 用户目标与 0.9.3 来源 | [项目背景](docs/PROJECT-BACKGROUND.md) · [0.9.3 冻结报告](docs/ui-handoff-0.9.3/index.html) |

恢复任务时先看当前项目和短摘要，遇到不一致时核对现场。真实配置、长期知识和发布按本次授权处理。修改后按受影响的入口验证，写入有回执的内容需读回。历史截图和合成测试、真实新会话、运行时参数采用分别记录。

本轮 UI 变更与回归入口：[Quiet UI 设计与接手](docs/design/quiet/README.md)。当前基线为 main@f26dddb，旧数据逻辑保留。
