# 标准请求导入契约 v1

JSON 外层：`{"schema":"qingxu.requests.v1","requests":[...]}`。JSONL 每行直接放请求对象。不接受只有累计 totals 的对象。最大文件 16 MiB / 100000 条；先整体验证，再事务导入。示例文件为合成，不代表消费。

| 字段 | 类型 / 行为 |
| --- | --- |
| response_id | 必填、非空字符串；供应商内请求唯一 ID。重试是新的真实请求，应使用新 ID；响应重复落盘使用同 ID。 |
| time / timestamp | 必填带时区 ISO8601，如 `2026-11-11T01:00:00Z`，拒绝无时区日期。 |
| provider | 真实供应商 ID，如 `openai`、`my-proxy`；缺失 unknown，不根据模型名推断。 |
| model | 完整模型 ID，不能以当前配置替代历史实际模型；缺失 unknown。 |
| thread_id / project / cwd / title / role | 会话、项目和角色元数据；`cwd` 为真实工作目录，当前项目筛选按规范化路径匹配；role 可 primary / child / fork / unknown。 |
| usage | 可缺失（如仅失败日志），但不显示成零费用。支持 input_tokens/output_tokens/total_tokens + cached_input_tokens/cache_write_input_tokens/reasoning_output_tokens；或 Responses input_tokens_details 和 output_tokens_details。 |
| service_tier | standard / fast / flex / batch / ultrafast，依据价表；default 映射 standard、priority 映射 fast。未知枚举不会隐式降级。 |
| retention | 无写入分项时使用 30m 或 12h。缺失在允许估算时用 30m 并标记。 |
| region | global / regional / fedramp。后两项 OpenAI 加 10%；其它供应商需独立价表。缺失按显式允许的全球基础价假设。 |
| status | success / error / cancelled / unknown；completed/ok 映射 success，failed/incomplete 映射 error；没记录就 unknown。 |
| duration_ms / ttft_ms / tokens_per_second | 可选、有限非负数；ttft 不能大于已知 duration。不从其它字段猜测。 |
| reported_cost_usd | 可选非负十进制金额字符串；currency 如提供必须 USD。只有选择来源报告金额时才累加。 |

`input_tokens_details.cache_write_tokens_details` 可包含两个必须齐备的整数 `ttl_30m_tokens`、`ttl_12h_tokens`，两者与 cache_write_tokens 总量一致。原始缓存写入总量只有一处，不能因同时报告分项而加两次。output_tokens_details.reasoning_tokens 只作输出子集。

未识别的 prompt/messages/headers/credentials 字段不会保存在索引中；但导入文件自身由使用者负责安全保管。应用不修改源导入文件。CSV 是审计导出格式，不是默认反向导入格式。
