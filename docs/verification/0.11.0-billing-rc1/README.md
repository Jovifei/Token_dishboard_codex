# 计费功能验证 · 0.11.0-billing-rc1

全套回归 382 项：381 通过，0 失败，0 异常，1 跳过。唯一跳过项为 `test_workbench_registers_native_window_icon`，需要 Win32。

环境 `Linux-6.18.57-x86_64-with-glibc2.41`，Python 3.13.5，Tk 8.6，Xvfb；全部为隔离合成资料。命令：`xvfb-run -a python -B tools/run-windows-tests.py --result linux-tests.json`。

新增 86 项覆盖 Decimal 计费、互斥缓存分项/混合 TTL、推理不重计、档位、区域和长输入、缺失数据、真实零、价格文件并发/备份、增量游标、归档与 fork 去重、元数据归因、时区边界、CSV 全量导出、原生 Tk 交互与小窗口布局。原功能回归同跑。

9 张截图在 `docs/billing/screens`，由原生程序实际渲染；截图中 84 条请求都是合成，不能作为用户账单。`tools/capture-billing-review.py` 可用 Pillow 和桌面/Xvfb 重采（Pillow 非运行依赖）。

Windows 实机/DPI/EXE、真实 Codex 日志覆盖与正式账单对账仍需独立验收。Figma 无本轮新设计稿；没有将 HTML 预览充当原生程序实现。没有推送分支或更新线上安装包。
