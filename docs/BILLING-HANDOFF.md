# 轻续 0.11.0-billing-rc2 · 计费功能工程交接

0.11.0-billing-rc4 支持压缩归档流式读取和自动分批同步；项目范围等修复来自 rc3。[本机验收](verification/0.11.0-billing-rc4/README.md)。下方 rc2 验证和接手清单保留为历史记录，当前结果以 rc4 验收为准。

## 基线与本轮目标

用户要求依据截图增加用量计费：日期/供应商/模型筛选，费用、请求、Token、缓存命中四指标，按日图和请求/供应商/模型明细；延续无凹陷的苹果风格和已有项目/聊天工作流。

本轮取回 `Jovifei/Token_dishboard_codex` 主线 `2f53b196de01ccfb3d3d1170da3f511bab216389`（0.10.0-rc4-ui2，经用户 Windows 验收后发布）。来源为 GitHub Actions 运行 `42852267722` 的源码产物 `13780528035`，SHA-256 `8ba8dd29e7a40e5398abb378411a6d864cb850f3425d6c9992c13e03a6f22de8`。不是从 rc1 或此前未提交包覆盖。`SOURCE-COMMIT.txt` 标识取回基线，不意味着本包改动已提交到该 SHA。

本轮源码版本 0.11.0-billing-rc2。基于计费分支进行 Windows 修复、版本提升和便携 EXE 构建；不改动用户真实配置或 Codex 数据。

## 入口及实现

| 文件 | 作用 |
| --- | --- |
| `work/qingxu/billing.py` | 请求契约、Decimal 精确计算、价格版本/锁/备份、日期筛选、聚合、CSV。 |
| `work/qingxu/billing_index.py` | 只读增量索引原生日志到 SQLite；白名单缓存、不保存提示词；去重、冲突排除、项目角色映射、标准导入。 |
| `work/qingxu/ui_billing.py` | Native Tk 四指标、图表、筛选、五类明细、单价编辑、请求明细、异步读取与生命周期。 |
| `work/qingxu/ui_monitor.py` | 用量概览下增加计费统计页签；旧概览/详细记录/ccusage 保留。 |
| `work/qingxu/workbench.py` | 切换 Codex 数据来源时清空计费旧状态并作废旧异步结果。 |
| `work/qingxu/test_billing*.py`、`test_ui_billing.py` | 86 项新测试，计算 43、索引 25、UI 18。 |
| `tools/demo-billing.py`、`tools/fixtures/billing_demo.py`、`Preview-Billing.cmd` | 明确标注的独立合成数据演示。 |
| `tools/run-windows-tests.py` | 将新测试加入完整回归。 |
| `docs/billing/` | 价格参考、标准导入示例和字段契约。 |

一级导航仍是“用量概览 / 会话接续 / 设置”。新增入口为“用量概览 → 计费统计”。已有 UI 动效、悬浮窗、知识处理、配置安全逻辑不被替换。

## 计算和口径

基础请求费用 USD：

`(非缓存输入×输入单价 + 缓存读取×读取单价 + 30m 写入×30m 单价 + 12h 写入×12h 单价 + 输出×输出单价) / 1,000,000`

原始输入含缓存时先拆分，不再把缓存读取/写入叠加到总输入收费。兼容本项目原 `normalize()` 能核对的“raw input 含写入”与“不含写入”两种格式。推理 Token 属于 output 子集，仅作明细，绝不再次相加。

Responses 的 `input_tokens_details.cache_write_tokens_details` 如提供 `ttl_30m_tokens`、`ttl_12h_tokens`，必须是两个不重叠、相加等于写入总量的整数，按两个单价分别计费；不能把 aggregate `cache_write_tokens` 再计一次。没有分项才按明确保留期，缺失保留期按可关闭的 30m 假设估算。

每个请求先按其输入总量判断长上下文档，再对全请求分项计算相应倍率；不是只对超出门槛的部分收费。处理档位、长输入门槛、倍率均可配置。内置四个模型支持已检索的标准/fast/flex/batch，适用型号支持 ultrafast；明确 OpenAI regional/fedramp 会加 10%。未知档位、地域、模型、单价不置零。

示例：Sol 10000 输入，其中缓存读 8000、写 1000，输出 1000（含推理 700）；非缓存输入为 1000，费用 `(1000×2 + 8000×0.1 + 1000×2.5 + 1000×10)/1e6 = 0.0153 USD`。没有再加 700 推理 Token。

内置所有费率是 **2026-11-11 当前价表快照**，对历史也按这张表重算；未实现逐日期历史价表匹配。折扣、实际供应商账单、工具/存储、税费及订阅费不在 Token 估算中。`reported_cost_usd` 单独口径、显式 USD、未经对账，不以估算补缺。

缓存命中率为已知缓存拆分请求中 `Σ cacheRead / Σ(ordinaryInput+cacheRead+cacheWrite)`。未知缓存拆分不进入分母，同时显示样本数；成功率为已知 success/error/cancelled 的 success 比例；unknown 不当成功。速度和首字时间是显式记录的算术平均，绝不由任务总时长推断。

## 索引、完整性及安全

- 扫描配置的 Codex home 下 `sessions`、`archived_sessions` 的普通 `.jsonl` 和压缩 `.jsonl.zst`，只信任 `token_usage_record`；`token_count` 可能是上下文/累计/压缩后合成快照，不计为请求。
- 请求的显式模型优先，其次匹配 turn_id 的 turn_context；不拿 SQLite 的最新模型或当前配置推断过去的模型。缺失保留 unknown。
- 活跃/归档副本以 provider + response_id 去重；影响金额的字段冲突则排除，并报告覆盖不全。fork 中来源线程不属于当前 session 的历史记录不计入子会话；来源原件存在时由原件统计。子 Agent 独立真实请求可统计，但不叠加父线程的累计值。
- 记录文件 inode/size/mtime、字节偏移、边界哈希；完整未变文件不重读正文。改写会重索引；未写完行等待下一次；超过 1 MiB 的行跨读取预算排空而不缓存原文。
- 每批处理正文预算约 128 MiB 展开数据，界面在线程中自动连续批次并显示进度。压缩文件恢复时从帧头重放至已记录的解压偏移，不保存原始正文；文件变更会重新索引。解码异常、缺解码器、文件数上限约 30000 或其他读取限制均提示不完整。当前表格分页，数据聚合仍在内存完成。
- 初次打开计费页/手动刷新使用独立单 worker；不在 15 秒上下文监测循环里全量扫描。改筛选不重新读日志、不调用模型。源切换和窗口销毁停止或丢弃旧 worker 结果。
- 缓存在轻续私有 data_root 的 `billing/source-<来源目录哈希>/requests.sqlite3`。只留数值与项目/请求身份字段，不读 auth.json、密钥、Cookie，也不保存聊天正文；路径和会话标题仍属本机隐私，不应将整个缓存提交仓库。
- 单价 `billing/prices.json` 有项目既有锁、并发指纹检查、旧版本备份、原子写入和读回。自动备份只在已有价格文件时发生；首次保存创建新文件。
- 标准 JSON/JSONL 整体验证后事务导入，同一来源同 ID 相同内容幂等；冲突拒绝而非覆盖。CSV 防公式注入，金额保留完整精度，导出全部筛选结果而非当前 100 条页面。
- 不与既有 ccusage 快照混加。其它供应商可标准导入并配置价表，但尚未实现 Claude/DeepSeek/各类网关的原生采集和 API 账单连接。

## rc2 验证（历史）

Windows 11 + Tk + Python 3.13.0，使用隔离合成数据。完整回归 **382 项：378 通过，0 失败，0 异常，4 跳过**。跳过项均为当前用户未启用符号链接权限；UTF-8 测试入口已固定，CSV 和 CRLF 增量字节计数已在 Windows 复验。最终证据在 `docs/verification/0.11.0-billing-rc2/`；计费界面截图沿用并核对 rc1 的 9 张原生 Tk 截图。

Windows 缩放的更多 DPI 档位、真实账户账单对账、用户本机历史日志覆盖率未验证。费用示例、截图数值和测试均非用户真实消费。

## rc2 接手清单（历史）

1. 新目录解压，查看 `docs/billing/SCREENSHOTS.md`；运行 `Preview-Billing.cmd` 先看合成页，不覆盖现有数据。
2. 对已有 Git 仓库先 `git status`，检查本机用户改动；基线为上面的 2f53b196。合并本包 `CODE-CHANGES.patch` 前执行 `git apply --check`，基线有变化则三方比较，不做强制覆盖。
3. 运行 `Test-Windows.cmd`。核对所有表、图、卡片、CSV 的同范围合计；切换项目/供应商/模型/时区后复验。
4. 从真实日志随机选 10 个 response_id，核输入/cache/write/output、对应当时模型、去重，再与真正供应商相同期间的 Token 账单核对；缺失字段不得补成 success 或 0。
5. 人为修改一项单价，核对待保存状态、备份和重算；用两窗口模拟冲突。验证全部未知、部分已知和真实零分别显示。
6. 准备好 `tools/requirements-build.txt` 中的构建依赖后，在 Windows x64 运行 `Build-Windows.cmd <新目录>`；rc2 已完成本机构建并记录在验证目录。
7. 验收后的修改和证据再提交功能分支/PR。未验收不替换当前发布版。

## 参考来源

访问核对日：2026-11-11。官方页面可能调整，代码不在线抓价。详见 `docs/billing/PRICING-SOURCES.md`。以下是费率/字段依据，非用户账单来源。
