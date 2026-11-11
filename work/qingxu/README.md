# 轻续 Qingxu · 当前维护源码

版本 0.11.0-billing-rc4。Windows 的正式入口是 `launch.pyw`，它经 `app.main()` 启动 `Workbench`。保留的 `App` 继续提供配置逻辑和旧测试覆盖。

从仓库根目录可双击 `Start-Qingxu.cmd`，或用带 Tk 的 Python 3.11+ 运行 `py -3 -B .\work\qingxu\launch.pyw`。优先用 `Preview-Demo.cmd` 和 `Test-Windows.cmd` 在合成环境验证。

Python 3.11–3.13 请先安装 `tools/requirements-runtime.txt` 中的回移包；Python 3.14 自带解码器。未安装时普通日志仍可读，并提示压缩日志未纳入。

[仓库首页](../../README.md) · [计费指南](../../docs/BILLING-USER-GUIDE.md) · [工程交接](../../docs/ENGINEERING-HANDOFF.md) · [本机验收](../../docs/verification/0.11.0-billing-rc4/README.md)
