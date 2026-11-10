# 轻续 0.10.0-rc4-ui2 · Windows 本机复验

2026-11-10 至 11-11（UTC+8）在 Windows 11 x64（22631）、Python 3.14.2 上验收。补丁在 `5e4a6e5` 应用，输入 ZIP 和文件哈希已核对。[源码与交接](../../UI-REVISION-HANDOFF.md)

| 验收项 | 实测结果 |
| --- | --- |
| Windows 基线与最终回归 | UI 基线运行 268 项，265 项通过，3 项因文件符号链接权限跳过。基线复现 Tcl 残留回调警告。最终 296 项，293 项通过，同样 3 项权限跳过，0 失败、0 异常，日志无残留回调警告。 |
| 动效、布局与悬浮窗 | 测试鼠标和空格键的单次激活、禁用、窗口隐藏/销毁和几何稳定。以进程内 Tk 96/144/192 DPI 检查 980×680 窗口和精简态。`100%·旧`、进度和按钮均在边界内，收起尺寸分别为 120×94、137×115、169×139。 |
| 原生选择器 | 启动 Windows 的工作目录和配置文件选择器，取消后仍保持选中聊天、配置路径和偏好状态。 |
| 真实 Codex | 只读 CLI 用隔离合成任务复验。旧聊按格式返回摘要，轻续保存并读回。独立新聊使用内嵌摘要恢复首个待办和编号。文件引用指令做本地校验。 |
| 新 EXE | ZIP CRC、PE x64、内嵌源码及六尺寸图标通过校验。窗口图标注册和任务栏窗口属性通过 Win32 核验。在 PATH 无 Python、含中文和空格的解压路径正常启动并退出。 |
| 桌面更新 | 备份 rc3 程序及原快捷方式，原位更新程序并重新保存同名桌面快捷方式。启动 UI2 后，核对真实两级项目/聊天列表和 10 个原有数据文件哈希。 |

首轮 EXE 验证发现 Tk 仅设置了默认图标，主窗口未显式注册。Windows 入口改为给主窗口加载 ICO，并补充 Win32 回归。EXE 生成的 ICO 与源码 ICO 的六帧像素一致，跨平台压缩字节允许有差异。[Win32 图标接口](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-geticon)

缩放验证在进程内进行。真实项目列表使用本机数据，配置写入和知识模板使用副本或临时目录。Codex 运行时采用、真实知识写回、采集器启停及 Figma 在线同步未列入本轮通过项。

[Windows 回归](windows-tests.json) · [Windows 基线](windows-baseline.json) · [缩放与选择器](windows-dpi-native.json) · [真实接续](codex-continuation.json) · [EXE 核验](package-receipt.json) · [桌面核验](desktop-verified.json) · [安装包目录](../../../install/)

随包 Linux/Xvfb 的 295 项测试和 11 张截图使用合成数据。[Linux 最终回执](regression-result.json) · [Linux 基线回执](baseline-result.json) · [运行截图](../../design/apple-ui-v2/screens-ui2/)
