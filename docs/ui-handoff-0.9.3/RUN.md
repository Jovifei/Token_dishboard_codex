# 轻续 0.9.3 交接包运行说明

先完整解压。双击 `index.html` 打开离线中文报告。`source/work/qingxu/` 保留现有模块的相对布局，`tests/work/qingxu/` 保留原测试。图示运行的是 Tk 桌面程序。

## 用合成数据启动

本轮使用 Windows 11 x64、Python 3.14.2 和 Tk 8.6.15。开发入口需要带 Tk 的 Python 3.11 或更高版本。以下命令在解压目录执行，演示根目录须是新目录。

```powershell
$demoRoot = Join-Path $env:TEMP ('Qingxu-演示-' + [guid]::NewGuid().ToString('N'))
py -3 -B .\fixtures\demo.py --root $demoRoot --launch
```

切到「交接与知识库」，在项目下拉选择「示例蓝图」或「示例行程」。「聊天用量」的左树也可选择对应聊天。加入 `--blank` 可查看空白状态。`fixtures/demo.py` 生成小型 SQLite、日志、快照、配置副本、登记表和普通 Markdown 目录。示例数据都在指定的新目录内。运行界面中的配置写入和恢复会落在该示例目录。

如需让源码读取本机 Codex 数据，执行 `py -3 -B .\source\work\qingxu\launch.pyw`。启动默认使用当前用户的 `.codex` 或现有 `CODEX_HOME`，可用轻续自己的 `QINGXU_CODEX_HOME` 指向另一套本地数据目录。开发启动会在源码目录旁使用 `data` 保存轻续偏好和备份。原 `start-windows.cmd` 随源码保留供对照，它引用作者机器的 Python 路径。

## 验证

```powershell
py -3 -B .\tests\run-relevant.py
```

这一组运行项目归属、日志计量、提醒、配置和接力边界的现有用例。旧同步写入辅助库测试仍在包里，默认选集不调用它，也不修改剪贴板。完整截图回放需要 Pillow，使用已安装的 Python 环境，在交接包副本内运行。它会替换该副本的截图、示例提示词和截图测试证据。

```powershell
$captureRoot = Join-Path $env:TEMP ('Qingxu-截图-' + [guid]::NewGuid().ToString('N'))
py -3 -B .\tests\capture-ui.py --sandbox $captureRoot
```

截图脚本用真实 Tk 和 Win32 确认框。剪贴板与任务栏闪动调用被拦截核对，恢复回执路径由测试注入。它会通过合成路径上的 `checkpoint.py collect`、`semantic-save` 和 `semantic-read` 验证版本保存与读回。它不会替你发送聊天。

## 重建分享版

在 Windows x64 使用 64 位 Python 3.14.2 可复现本次构建环境。输出目录须不存在。分享版在启动时使用 `%LOCALAPPDATA%\Qingxu` 存放自己的偏好与配置备份，可用 `QINGXU_DATA_DIR` 指向隔离目录。

```powershell
py -3.14 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r .\source\requirements-build.txt
.\.venv-build\Scripts\python.exe -B .\source\build-windows.py --output .\release-build
```

输出为 `release-build/Qingxu-0.9.3-Windows-x64.zip`，内容是 GUI 单文件 `轻续.exe`、分享说明和许可。构建脚本使用 PyInstaller 6.22.3、内嵌 Tk 运行时和现有图标。版本或依赖变化时重新核对架构、许可、解压启动和内嵌档案。构建文件的哈希可能随构建环境变化。

## 可选集成

`source/local-integrations/project-relay-p0/` 是原登记接力引擎和日期快照生产端。演示会把引擎复制到合成目录并为 `Relay(settings)` 注入精确路径。真实项目需提供自己的绝对路径、授权登记和私有目录。Git 工作区指纹需要 Git。`observer.py` 还需要单独配置的 Node、ccusage、统计目录和工作树别名。此包提供生产端源码、合成快照和消费界面，默认启动不会运行观察器。分享 EXE 默认走手动交接。
