# 轻续 0.10.0-rc4-dev · Apple UI 测试边界

本分支基于 `main@2489d8a4`。这是新的 UI 候选，并非从旧 0.10.0-rc1 修改包覆盖。图标、布局和按钮动效与接续/监测/配置的业务逻辑分离。

## 已独立验证

- 在 Linux Xvfb 中运行新添加的 `test_ui_brand_motion.py`：4 项通过，0 失败（自定义图标 PNG/SVG、Windows ICO、图标 Tk 显示、按钮按下保持原坐标与尺寸）。
- 图标用 Python 标准库生成 16 / 32 / 48 / 64 / 128 / 256 六尺寸 PNG 帧的 ICO，不增加运行时第三方依赖。
- 代码设计保持普通 ttk.Button 的 `command`、`state`、`invoke()`、键盘焦点和布局，主业务数据处理模块不改。
- 为全部旧版 GUI 回归和新图标测试新增 GitHub Actions Linux Xvfb / Windows 生成检查。**CI 未完成前不得标记全套通过**。

## 仍需实际验收

- PR 触发的全量 0.10.0-rc3+ GUI 回归；特别是 980×680，侧栏按钮和悬浮 120×64 收起态的尺寸。
- Windows 100% / 150% / 200% DPI 真实截图；Windows 标题栏、任务栏、小窗图标与新构建 EXE 一致性。
- 真实 Codex 项目选择、旧聊接续、新聊执行和配置运行时采用，仍不能用合成测试代替。
- Figma Starter MCP 已返回额度超限。可编辑 Figma 页面、动画轨道尚未建立；本分支提供 SVG 母版和映射规范供后续同步。

## 在本机验证

源码入口 `work/qingxu/launch.pyw`，Windows `Test-Windows.cmd` 运行现有测试并包含新测试。可运行 `Build-Windows.cmd <空目录>` 打包，脚本在新目录生成 ICO 后构建独立候选 EXE。**不要覆盖原本 rc3 发布包。**

也可用 Linux 安装 `python3-tk xvfb` 后运行 `xvfb-run -a python3 tools/run-windows-tests.py`。若只检查新图标与按钮，可运行 `xvfb-run -a python3 -m unittest discover -s work/qingxu -p test_ui_brand_motion.py`。
