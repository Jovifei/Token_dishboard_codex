# 价格与字段参考（2026-11-11）

**不是账户账单，不是实时抓价；历史查询按当前本地价表重算。** 标准 USD / 1M Tokens：

| 模型 | 普通输入 | 缓存读取 | 写入 30m | 写入 12h | 输出（含推理） |
| --- | ---: | ---: | ---: | ---: | ---: |
| gpt-6-astra | 10 | 1 | 12.5 | 25 | 50 |
| gpt-6.1-sol | 2 | 0.1 | 2.5 | 5 | 10 |
| gpt-6.1-terra | 1 | 0.05 | 1.25 | 2.5 | 5 |
| gpt-6-luna | 0.1 | 0.01 | 0.125 | 0.25 | 0.5 |

来源：官方 Pricing 页面检索快照，标准/长上下文/档位为不同视图，不应把 Standard 与 Flex 表混读。核对条目使用模型页及缓存指南。

- https://developers.openai.com/api/docs/pricing?latest-pricing-expanded=true
- https://developers.openai.com/api/docs/models/gpt-6.1-sol?trk=public_post_comment-text
- https://developers.openai.com/api/docs/models/gpt-6.1-terra?trk=public_post_comment-text
- https://developers.openai.com/api/docs/models/gpt-6-luna?trk=public_post_comment-text
- https://developers.openai.com/api/docs/models/gpt-6-astra
- https://developers.openai.com/api/docs/guides/prompt-caching
- https://help.openai.com/en/articles/4936856-understanding-and-counting-tokens
- https://help.openai.com/en/articles/9039756-managing-billing-for-chatgpt-and-the-api-platform
- https://github.com/openai/codex/blob/main/codex-rs/core/src/session/mod.rs
- https://github.com/openai/codex/blob/main/codex-rs/core/src/client.rs

费用按互斥普通输入、读缓存、两种写缓存、输出分项计算。GPT-6 系列输入超过 272000 时，本地参考表使用全请求输入/缓存 2 倍、输出 1.5 倍。Fast=2、Flex/Batch=0.5；Astra/Sol Ultrafast=6，地域加价按记录明确条件。缺少条件会展示假设或在严格模式拒绝估算，不据此声称历史已实付。

长期维护应由人工核对费率或实现具有来源/生效日期/审核的新价表导入流程；不要静默爬价并重写历史账本。未知新模型保持未计价，允许用户明确配置。
