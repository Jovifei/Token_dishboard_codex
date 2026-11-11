# 计费功能验证 · 0.11.0-billing-rc2

本记录对应计费分支的 Windows 修复、版本提升和便携包构建。

## 回归

在 Windows 11、Python 3.13.0、Tk 8.6 环境运行 `Test-Windows.cmd --result windows-tests.json`，隔离合成资料执行完整套件：

- 382 项总计
- 378 项通过
- 0 失败、0 异常
- 4 项跳过，均为符号链接权限不可用

测试入口固定 `PYTHONUTF8=1`。增量日志测试以 UTF-8 无换行转换模式写入，CSV 验证显式按 UTF-8 读回。

## 构建与启动

`tools/build-windows.py` 使用 PyInstaller 6.22.3 从 `work/qingxu/launch.pyw` 构建 `Qingxu-0.11.0-billing-rc2-Windows-x64.zip`。构建包解压后包含 `轻续.exe`、使用说明和许可证；启动检查保持进程运行超过 5 秒，随后正常退出。

便携包 SHA-256：`1be5a3134246d0da56f160124c18be00719bf7008616c9c285a51a4d8235dca9`。

费用页面仍使用隔离合成数据截图；真实 Codex 日志覆盖、真实供应商账单对账和更多 DPI 档位需要用户本机继续验收。
