# 轻续 · Apple 风格 UI v2 设计交接

源头：`main@2489d8a4` 的 0.10.0-rc3。本目录提供 **Figma 可导入矢量设计素材与实现映射**，不是已经编辑完成的 Figma 云端文件。当前 Figma Starter MCP 调用额度已用尽，云端设计同步待权限恢复。

## 原创图标

`icon.svg` 为 256×256 矢量母版。蓝色圆角底上的连续白色双环对应旧聊与新聊的接续；上方淡蓝节点表示下一阶段。16–256 px 可独立输出。源代码 `work/qingxu/ui_brand.py` 使用同一造型绘制主窗口、侧栏、悬浮窗，并通过 `write_ico()` 在 Windows 打包时生成多尺寸 ICO。

## 颜色 / 层级

| Token | 设计值 | 用途 |
|---|---|---|
| canvas | `#F5F5F7` | 主画布 |
| surface | `#FFFFFF` | 工作卡片 |
| sidebar | `#EEF3F9` | 三级导航的外层区域 |
| ink | `#1D1D1F` | 主要文字 |
| secondary | `#6E6E73` | 辅助和来源 |
| brand | `#087CF7` / `#007AFF` | 图标和主动作 |

页面仍只有三大入口“用量概览 / 会话接续 / 设置”；顶部的项目与聊天选择始终可见。主要动作优先置于固定底部操作区。桌面浮窗完整态含图标、标题和模式选择；120×64 收起态含同一图标与百分比、展开和隐藏。目录、上下文、保存与真实 Codex 生效语义不变。

## 动作规范

**不要在按下时产生凹陷。** 所有 ttk Button 不应出现暗色下压、纵向位移或缩小。普通/悬停/按下只切换很浅的颜色；`ui_motion.py` 的私有 ttk 样式用 120ms ease-in-out 色彩过渡，保留键盘焦点、Tab 顺序、原生调用和禁用状态。它不拖延回调；`QINGXU_REDUCE_MOTION=1` 可改为即时视觉反馈。

禁止使用永久循环的“呼吸/跳跃”来表达进度。状态仍要以文字解释：未知、过期、压缩待测不渲染伪造的零值；复制不等于已执行；配置落盘不等于 Codex 采用。

## 后续 Figma 图层结构

1. Brand / Mark, Windows Icon, Compact Mark
2. Desktop / Shell: Sidebar, Global Selection, Header
3. Desktop / Usage: Current Session, Recent Activities, Notice
4. Desktop / Handoff: Step 1 / Step 2 / Step 3, Sticky Actions
5. Desktop / Settings: Directories and Reminder, Codex Configuration
6. Desktop / Floating: Expanded / Compact
7. Modal / Template Editor
8. Component / Button: Rest, Hover, Press, Focus, Disabled
9. Component / Progress: Known, Unknown, Stale, Compacted

使用 Auto Layout 建立可编辑层级，再与 `docs/UX-SPEC.md` 和 `work/qingxu/` 实际 UI 对照。此目录的 SVG 是图案素材而非扁平化整体界面截图。

## 本机核验点

在 Windows 100%、150%、200% DPI 下确认 Logo 不糊、悬浮窗完整/精简态控件不裁切、模板弹窗焦点正常、按钮点击过程没有位移或凹陷、真实写入/摘要状态保持不变。此前 rc3 的本机测试不自动覆盖此 UI 分支。见本分支的测试与 PR 说明。
