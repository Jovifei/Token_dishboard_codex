# 轻续 Qingxu 工程交接

## 基线与工程入口

当前维护版本为 **0.10.0-rc2**。`work/qingxu/` 是唯一维护的源码与测试目录，`launch.pyw` 通过 `app.main()` 启动新版 Tk `Workbench`。开始工作前核对当前分支、工作区变更和用户授权。

0.9.3 冻结包保留在 `docs/ui-handoff-0.9.3/`。外部 0.10.0-rc1 升级 ZIP 的 97 个清单文件已核对，当前代码以 rc1 接入的 `work/qingxu/` 为基础继续修改。`docs/ui-handoff-0.10.0-rc1/` 中的截图、设计和测试证据属于历史交接，应以当前源码与实际运行结果判断现状。

| 位置 | 职责 |
| --- | --- |
| `work/qingxu/workbench.py`、`app.py` | 三页面工作台、项目与聊天选择、异步数据读取及沿用的配置逻辑。旧 `App` 保留兼容和复用实现及相关测试。 |
| `handoff.py`、`ui_handoff.py` | 摘要保存和读回、文件型与内嵌型提示词、模板及三步交接。 |
| `monitor.py`、`ui_monitor.py` | 当前用量、提醒、概览、详细记录、ccusage 快照及悬浮窗。 |
| `preferences.py`、`ui_preferences.py` | 数据目录、知识库模式、提醒策略及可恢复的偏好修改。 |
| `core.py`、`config_store.py` | 白名单配置预览、备份、写入和冲突保护的恢复。 |
| `chats.py`、`context_usage.py` | Codex 项目和聊天发现、会话日志与用量读取。 |
| `tools/demo-current.py`、`tools/fixtures/` | 当前源码的合成数据演示。 |
| `tools/run-windows-tests.py`、`tools/build-windows.py` | Windows 回归和基于当前源码的构建入口。 |

## 关键不变量

1. 会话身份同时核对 ID、工作目录和项目归属。来源或选择变化后丢弃旧 worker 结果，不能把旧摘要绑定到新库中的同名记录。
2. 摘要保存、提示词复制、旧聊天执行和真实新聊天恢复分别确认。程序由用户操作消息发送和换聊。
3. 文件型恢复指令复制前再次检查文件、项目身份和 SHA-256。文件变化、丢失或跨项目时拒绝沿用旧凭据。
4. 知识库可选。模板编辑保留身份与校验约束，知识更新遵循选定范围和既有授权。
5. 未知、真实零、过期、压缩后待测和读取失败分别显示。最近请求、聊天累计、日期快照和日志字节使用各自口径。`official_limit` 仅在有可核验来源时提供，当前 Workbench 传入 `None`。
6. 配置预览核对变更后的主模型和继承的子模型与推理档。TOML 写入经过备份、并发检查和读回，文件写入与运行时采用分别验收。
7. 目录切换保留原有数据。来源关闭与重新启用需要维持正确的文件绑定，损坏的偏好不得被静默覆盖。

## 验收记录

- Windows 11、Python 3.14.2 与 Tk 8.6.15 环境的合成回归运行 249 项，246 项通过、3 项因符号链接权限跳过。两处 Tk 测试已调整为处理窗口事件，Windows 目录别名测试使用真实 junction。两个新增用例覆盖真实长标题和高 DPI 选择栏。机器回执见 [最终测试](verification/0.10.0-rc2/windows-tests.json)。
- 文件引用型和内嵌摘要型各完成一次真实 Codex 新聊天接续，读取范围限定为两份合成文件。默认 shell 的读取尝试被策略阻拦，后续通过允许的只读工具完成验证。该证据覆盖限定的合成接续场景。
- 隔离项目中，主 agent 的 `gpt-6.1-sol/low` 在 thread/start 和实际主 turn_context 中得到核对，默认子 agent 的 `gpt-6-luna/medium` 在关联的子 turn_context 中得到核对。app-server `config/read` 返回生效的 `model_auto_compact_token_limit=48000`，尚未验证压缩触发。隔离项目配置已恢复，全局 `config.toml` 哈希核对一致。
- 合成回归的 `not_verified` 字段记录运行测试时的边界，真实 Codex 接续和配置实验属于之后独立进行的验收。真实知识库写回、采集器启停和可比任务的 Token 节省仍待验。

## 交付与后续验收

Windows 便携 ZIP 放在 [install/](../install/)，完整验收包放在 `releases/`。发布时核对 rc2 版本、文件哈希、包内许可、解压启动与安装位置。0.9.3 的冻结产物和哈希作为独立基线保留。

普通入口已在实际安装位置验证两级目录、真实聊天标题及所选工作目录。合成演示有单独的窗口标识。作者本机使用原入口原位升级，原应用和数据保留核对。其他实际验收和边界见 [本机验收](LOCAL-ACCEPTANCE.md)。

## 常用命令

以下命令从项目根目录执行，需要 Windows 图形会话和带 Tk 的 Python 3.11 及以上版本。演示根目录须是不存在的新目录。

```powershell
$demoRoot = Join-Path $env:TEMP ('Qingxu-demo-' + [guid]::NewGuid().ToString('N'))
py -3 -B .\tools\demo-current.py --root $demoRoot --check

py -3 -B .\tools\run-windows-tests.py
```

回归使用合成数据和临时目录，其中旧界面剪贴板用例使用内存替身。构建需要 64 位 Python、`tools/requirements-build.txt` 中的 PyInstaller 和一个不存在的输出目录。

```powershell
$buildRoot = Join-Path $env:TEMP ('Qingxu-build-' + [guid]::NewGuid().ToString('N'))
py -3 -B .\tools\build-windows.py --output $buildRoot
```

详细功能与操作见 [用户指南](USER-GUIDE.md)、[交互规范](UX-SPEC.md) 和 [本机验收清单](LOCAL-ACCEPTANCE.md)。
