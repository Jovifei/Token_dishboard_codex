# 轻续 0.10.0-rc4-ui2 · 本轮源码与验收交接

## 本次基线和身份

用户目标：继续改善轻续的苹果风格布局、点击反馈、弹窗和悬浮窗图案。明确要求不使用凹陷、下压、缩放或位置跳动来表达点击。保留当前项目/聊天选择、用量口径、三步交接和配置安全逻辑。

源码来自 `Jovifei/Token_dishboard_codex` 的 UI 分支 `codex/apple-ui-soft-motion-20261110`。接收时对应 Draft PR #1，基线为 GitHub Actions 打包的 `5e4a6e5c41fdbec67f22c566ca646196eb2e4abc`。当前版本为 **0.10.0-rc4-ui2**。

来源运行 `42831370866`、源码产物 `13771607161`。原 ZIP SHA-256：`2a4de87b93e6571d9523910bb48c877752ea225c7cd79c5c2e88ddf5d9ebfe38`。`SOURCE-COMMIT.txt` 只说明取回的基线，不表示修改后的文件仍等于该提交；当前文件哈希见 `DELIVERY-MANIFEST.json`。

收到的源码验收 ZIP 含 219 个清单文件及清单自身。ZIP SHA-256 为 `03a8334ddfa952bd01a02725c0ee17b4c7bf2ace84b4da144d836f574453af5d`。本机以该提交为 Git 基线接入补丁，再验证并打包 Windows 候选版。

## 实际修复

| 范围 | 修改 | 证据/入口 |
|---|---|---|
| 点击反馈 | 原动效硬编码 `focusthickness=1`，与主界面的 2 不一致，悬停会缩小 2 px。改为继承完整源样式，保留字体、内边距、焦点尺寸与文字状态。 | `ui_motion.py`；几何与样式回归 |
| 动效状态 | 鼠标、空格键和焦点都覆盖；120 ms 只过渡颜色，不下移、不缩放、不加内阴影。业务回调保持 ttk 原生执行。 | 单次点击/空格触发、禁用、变体切换、插值终点测试 |
| 生命周期 | 窗口销毁/隐藏取消动画；弹窗重新打开复用样式；主窗口不再遗留未跟踪的 after_idle 动效安装回调。 | 基线日志曾有 Tcl invalid-command 回调；本包最终日志无此消息 |
| 全局选项 | 项目模式 Toolbutton 的 selected/pressed 状态均为 flat，移除默认凹陷边框。 | `workbench.py` |
| 精简悬浮窗 | 用图标代替重复“轻续”文案；加入小进度条、所选/最近及项目提示；按内容/字体计算尺寸。100%·旧及大字体不裁字。 | `ui_monitor.py`；精简态截图及边界测试 |
| 小窗菜单 | 右键可见完整聊天/项目/状态，提供“减少动态效果（本次运行）”；也支持原有环境变量。 | 不修改 Codex 或项目偏好的回归 |
| 软件图案 | 沿用原创双环标志，矢量/位图几何对齐、小尺寸边缘抗锯齿；同一 Tcl 解释器共享图像，子窗口不覆盖默认图标。 | `ui_brand.py`；PNG CRC / ICO 六尺寸验证 |
| 构建资产 | 更新源码 `qingxu.ico`，构建脚本继续生成同款多尺寸 ICO。 | 源码资产已生成，不代表 Windows EXE 已构建 |

监测算法、阈值语义、日志/累计口径、知识库授权、摘要身份/哈希校验、配置写入保护均未改动。悬浮窗仍只跟随轻续所选或最近记录，不跟随 Codex 焦点。小窗不是动画桌面宠物。

## 验证

包内 Linux/Xvfb 基线通过 268 项，修改后通过 295 项。随包回执记录 Python 3.13.5。新增 `test_ui_polish` 26 项，并把已有路径支持用例纳入标准入口。所有用例使用隔离资料或合成样本。

真实 Tk 截图 **11 张**，涵盖主界面、三步交接、设置、Codex 配置、模板弹窗、完整/精简悬浮窗，以及 980×680 窗口。截图元数据在 `docs/design/apple-ui-v2/screens-ui2/metadata.json`；精简态 100%·旧为明确注入的合成状态，不是用户真实用量。

本机补充了 Win32 主窗口图标测试。Windows 使用 `Test-Windows.cmd` 运行，结果和测试边界见本机复验。截图脚本 `tools/capture-ui-review.py` 使用 Pillow。[UI2 本机复验](verification/0.10.0-rc4-ui2/README.md)

## Figma 的实际情况

原交接记录的 Figma 文件 `bQhX9t0ZeniY3gh4MJNEuV` 受 Starter MCP 调用额度限制。在线画布、组件和连线待同步。

当前设计记录包含原创 SVG、多尺寸 PNG、实际 Tk 截图和动效状态。[UI2 设计交接](design/apple-ui-v2/README.md)

## 本机使用

Windows x64 便携 ZIP 位于 `install/`。解压后运行 `轻续.exe`。使用源码时，`Preview-Demo.cmd` 打开合成演示，`Start-Qingxu.cmd` 打开真实数据入口，需要带 Tk 的 Python 3.11 或更高版本。[安装包目录](../install/)

Windows x64 使用锁定的 PyInstaller 依赖运行 `Build-Windows.cmd <新目录>`。验证时使用隔离偏好。需要原位升级时先关闭轻续，并备份原程序及桌面快捷方式。

## 后续维护

界面任务以当前 `work/qingxu/` 和 UI2 截图对照。修改后先运行受影响的 Tk 测试和 Windows 全量回归。实际 Codex 配置采用、真实知识库写回、采集器启停分别记录。
