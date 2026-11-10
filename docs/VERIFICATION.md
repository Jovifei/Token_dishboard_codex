# 轻续 0.10.0-rc2 · 验证记录

当前维护版的测试与交付核对见 [0.10.0-rc4-ui2 本机复验](verification/0.10.0-rc4-ui2/README.md)。[rc3 本机复验](verification/0.10.0-rc3/README.md)

## Windows 回归

使用 Windows 11 x64（22631）、Python 3.14.2、Tk 8.6.15。以独立合成数据运行 `Test-Windows.cmd`。最终运行 249 项，246 项通过，3 项因无文件符号链接创建权限跳过，0 失败、0 异常。目录重解析用例使用真实 NTFS junction，剪贴板用例使用内存替身。

首轮 Windows 验证定位了两处仅处理 Tk idle 任务而未等待窗口映射的测试问题。后续补验发现长或多行的真实 Codex 标题阻断交接，以及 980×680 高 DPI 选择栏裁切。rc2 对交接显示名称作单行限长处理，身份仍按工作目录和会话 ID 校验。窄窗口把聊天选择移到下一行。两处缺陷均加入回归用例。

[Windows 结果](verification/0.10.0-rc2/windows-tests.json) · [脱敏测试日志](verification/0.10.0-rc2/windows-test-log.txt)

## 独立本机验证

正常入口读取真实 Codex 项目和所选聊天工作目录，合成窗口标为「合成演示」。真实旧聊和独立新聊验证了文件引用与携带摘要，跨项目验证了摘要、日志和文件哈希隔离。只读接续实验中的两份文件为合成样本。工具的权限只在这两次验收进程内配置。 [OpenAI Docs 的 MCP 配置](https://learn.chatgpt.com/docs/extend/mcp)

配置采用以新会话和子 Agent 的实际运行日志为证。压缩阈值由 app-server 返回的生效配置核对，实际达线触发另行验证。安装包验证包括 PE x64、ZIP CRC、应用模块与图标、中文和空格路径解压启动、许可及哈希。作者本机沿用原入口部署，记录的原始本机路径和聊天正文保存在私有验收资料内。

[本机验收清单](LOCAL-ACCEPTANCE.md) · [脱敏验收回执](verification/0.10.0-rc2/local-acceptance.json) · [安装包校验](../install/SHA256SUMS.txt)

## 历史交接

rc1 的 246 项 Linux Tk 回归和 10 张合成截图作为远端 UI 证据保存。0.9.3 冻结基线也保留用于对照。 [rc1 离线报告](ui-handoff-0.10.0-rc1/index.html) · [0.9.3 冻结报告](ui-handoff-0.9.3/index.html)
