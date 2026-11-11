# 计费功能本机验收 · 0.11.0-billing-rc3

本次从计费分支 `9394f3a` 接收，在 Windows 11 本机验证并修复项目归属和缩放问题；既有 UI2、会话接续和设置功能保留。rc2 的构建及合成数据记录见 [rc2 验收](../0.11.0-billing-rc2/README.md)。

## 范围与结果

- Windows 11、Python 3.14.2 和 Tk：隔离合成数据的完整回归共 **391 项，387 通过、0 失败、0 异常、4 跳过**。跳过项均因本机用户没有创建符号链接权限。测试回执见 [windows-tests.json](windows-tests.json)。
- 项目统计按 Codex 已确认的项目标识汇总不同工作树，同名但身份不同的项目分开；未确认归属的记录不混入当前项目。独立子 Agent 只在可唯一确认父项目时继承项目归属。旧索引和导入格式的兼容、重复导入也有回归覆盖。
- 使用本机真实 Codex 日志的只读来源和隔离索引，实际加载计费页并检查项目、聊天范围与不完整提示；记录只保留聚合验证结果，未上传私有日志、项目名称或请求明细。扫描为有预算的分批读取，普通日志可继续同步；压缩 `.jsonl.zst` 暂不支持。不能把当前结果当作全部历史用量。
- 980×680 窗口下使用 Tk 进程内 96、144、192 DPI 缩放，筛选控件可到达；高缩放时筛选区可滚动。回执见 [billing-layout.json](billing-layout.json)。这不是修改 Windows 系统缩放后的物理显示器验收。
- 31 个原位安装程序文件逐项校验，新版本由原桌面快捷方式启动并响应；已有数据覆盖前后校验一致，旧程序和原入口已备份。安装后的真实项目/聊天列表和隔离索引也已复核。脱敏结果见 [desktop-verified.json](desktop-verified.json)。

## Windows 便携包

从同一源码构建 Windows x64 ZIP，检查 ZIP CRC、EXE 架构、内置多尺寸图标与主要模块；在含中文及空格的解压路径、无 Python PATH 和隔离数据目录下启动并正常关闭。回执见 [package-static.json](package-static.json) 和 [package-startup.json](package-startup.json)。

[下载便携 ZIP](../../../install/Qingxu-0.11.0-billing-rc3-Windows-x64.zip) · SHA-256：`4cbc58ff44cf5227fae923aa29810e17efeebf67eaee08ad2454f1a899481d96`

费用为本地价表对应的 Token 估算，非订阅费用或已对账的供应商账单。费率是快照，可查 [OpenAI API 价格](https://developers.openai.com/api/docs/pricing) 并在软件中调整；缺失单价或用量仍显示未知。真实账单对账、全部历史日志覆盖和符号链接权限测试尚未完成。
