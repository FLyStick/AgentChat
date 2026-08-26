# 评测证据目录

按“证据性质”拆成三个子目录：`offline/` 存放离线固定基线，`live/` 存放真实链路原始 JSON，`upcoming/` 存放评测设计与执行记录文档。

## 最新状态（2026-08-21）

- P5.1-P5.10 全部执行完成，真实链路原始 JSON 已全部归档在 `live/`。
- P5.9 RAG 真实 A/B 已完成：50 条 query / 102 chunk，正式结果 [live_rag_comparison_20260814_182215.json](D:/实习记录/开源项目/AgentChat/docs/eval/live/live_rag_comparison_20260814_182215.json)，全量 `Recall@5 0.8467 -> 0.96`、`MRR@5 0.6967 -> 0.7983`、`Hit@1 0.88 -> 0.98`；hard 17 条 `Recall@5 0.7843 -> 0.9412`、`Hit@1 0.8824 -> 1.0`。
- P5.10 Memory 两层 vs 三层已完成：30 场景 / 62 Gold Facts，正式结果 [live_memory_comparison_20260818_105736.json](D:/实习记录/开源项目/AgentChat/docs/eval/live/live_memory_comparison_20260818_105736.json)，Fact Recall `0.0968 -> 0.6774`、Case Pass Rate `0 -> 0.4667`。
- 设计与执行记录文档统一放在 `upcoming/`；面试/交付引用数字时以 `live/` 原始 JSON 为准，离线数字只作为回归基线。

## `offline/`

- 内容：P2/P3 的可复现离线基准与指标口径。
- 示例：`rag_p3_before_after.json`、`memory_dedup_p3.json`、`token_budget_p3.json`、`METRICS_DEFINITIONS.md`、`REPORT_TEMPLATE.md`。
- 特点：只依赖 Python 标准库，任何环境重跑结果应一致，用于回归与解释，不冒充真实链路。

## `live/`

- 内容：P5 已完成的真实服务链路原始 JSON，以及后续执行生成的真实链路结果。
- 示例：`live_rag_*.json`、`live_rag_ab_*`、`live_rag_comparison_*.json`、`live_completion_*.json`、`live_cancel_*.json`、`live_memory_*.json`、`live_multi_agent_*.json`、`live_memory_comparison_*.json`。
- 特点：需要 conda `agentchat` 后端、Docker 依赖和真实 Embedding/LLM；面试材料只能引用这个目录里的原始 JSON 来支撑“服务链路实测”。
- 归档规则：live benchmark 脚本默认输出到本目录，每次运行生成带时间戳的新文件，不覆盖旧结果。
- 正式 A/B 结果：`live_rag_comparison_20260814_182215.json`（Rerank 48/50）为 P5.9 最新正式结果，`live_rag_comparison_20260814_173430.json` 保留为 Rerank 403 历史对照；`live_memory_comparison_20260818_105736.json` 为 P5.10 正式结果。

## `upcoming/`

- 内容：评测设计/执行记录文档；已执行的 P5.9、P5.10 文档也在本目录保留，后续新设计继续放这里。
- 示例：`RAG_COMPARISON_DESIGN.md`（P5.9，已完成）、`MEMORY_COMPARISON_DESIGN.md`（P5.10，已完成）。
- 特点：文档包含设计口径、执行方式、原始命令、限制与面试口径；执行结果以 `live/` 原始 JSON 为准，未执行的数字不能进入简历。

## 已落盘结果速查（P5）

| 评测 | 原始 JSON | 一句话结果 |
| --- | --- | --- |
| Completion 端到端 | `live_completion_20260814_134552.json` | 15 条 `case_ok_rate=1.0`、无工具错误 |
| SSE 断流 | `live_cancel_20260814_151430.json` | 5/5 达标，`cancel_to_terminate_ms` max 1.356ms |
| Memory 写入后检索 | `live_memory_20260814_154252.json` | 5/5、`hit_rate=1.0`、`mean_mrr=1.0` |
| 多 Agent | `live_multi_agent_20260814_155110.json` | 5 个固定场景全部通过，路由全命中 |
| RAG 单链路召回 | `live_rag_20260814_123820.json` | 30 条 `mean_recall_at_k=1.0` |
| RAG A/B（P5.9） | `live_rag_comparison_20260814_182215.json` | 全量 `Recall@5 0.8467 -> 0.96`，hard `0.7843 -> 0.9412` |
| Memory A/B（P5.10） | `live_memory_comparison_20260818_105736.json` | Fact Recall `0.0968 -> 0.6774` |

## 目录外文件

`docs/eval/` 根目录暂时可能保留运行中后端占用的 debug 日志（如 `backend_p55_dbg.log`）。后端停止后应归并到 `live/` 或删除，根目录不作为评测产物归档位置。
