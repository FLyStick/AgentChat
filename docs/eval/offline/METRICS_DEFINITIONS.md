# 简历指标口径说明

> 目的：简历上每个数字都能回答“数据集是什么、怎么算、能不能复现”。
> 口径原则：先跑固定 fixture，再保留原始 JSON，最后写简历。

## 统一原则

- 指标结果全部来自 `src/backend/agentchat/benchmarks`，代码路径可定位
- 固定数据集放在 `fixtures/`，任何环境重跑结果应一致（离线部分）
- 结论必须带数据集规模，例如“9 条固定 query 上 hit_rate=1.0”，不写无上下文的“准确率 100%”

## 指标定义

### Recall@K

单个 query 的召回率：

`recall@K = |ground_truth ∩ retrieved[:K]| / |ground_truth|`

对应实现：`metrics.recall_at_k`。RAG 报告里的 `mean_recall_at_k` 是所有 query 的平均值。

### Hit Rate@K

单个 query 是否在 top K 内命中任意一个 ground truth：

`hit@K = 1` 当且仅当 `MRR@K > 0`

对应实现：`metrics.hit_at_k`。RAG 报告里的 `hit_rate_at_k` 是平均命中率。

### MRR@K

第一个命中结果排名的倒数，未命中为 0：

`MRR@K = 1 / first_hit_rank`，无命中则为 0

对应实现：`metrics.mrr_at_k`。`mean_mrr` 是所有 query 的平均值。

### 延迟统计

每个 case 记录单次检索耗时，汇总输出 `mean_ms / p50_ms / p90_ms / p95_ms / max_ms`。

对应实现：`metrics.latency_stats`。本机离线结果只用于回归，不代表生产 SLA。

### 记忆 Hit Rate

按记忆模式分别计算：

- `short_term`：从最近 3 条对话历史检索
- `summary`：从摘要列表检索
- `long_term`：从长期事实列表检索

每个 case 检索 top K 上下文，只要任一结果包含 expected 片段即命中：

`hit = 1` 当且仅当存在 retrieved item，使 `expected.lower() in item.lower()`

`hit_rate = hits / case_count`。当前固定样本为每模式 4 条。

### Cancel-to-Terminate

流取消后到生产者真正终止的时长：

`cancel_to_terminate_ms = (finished_at - cancel_requested_at) * 1000`

对应实现：`utils.cancellable_stream.CancellableAsyncStream.summary()`。

单次压测 `pass` 条件为 `cancel_to_terminate_ms <= threshold_ms`，默认阈值 500ms。

`pass_rate = passed_runs / runs_with_latency`。

### Token 预算校准

把对话按 `user/assistant` 两两分组，从最新一对向前累计 token；累计值超过 cutoff 时，剩余旧对话交给摘要，`kept_pairs` 是不触发摘要的最新媒体窗口。

`summary_triggered = old_pairs > 0`

`summary_trigger_kept_ratio = kept_token_count / (old_token_count + kept_token_count)`

对应实现：`token_budget.split_messages_by_token`，与生产 `DialogService.split_messages_by_token` 保持同一分对和截断语义。当前固定样本 40 对 / 8560 tokens，默认 cutoff 1000-5000 均触发摘要，5000 时保留 23 对 / 5000 tokens。

### RAG 优化对比

同一个固定 fixture 分别跑 baseline 与 `OptimizedRetriever`，固定三项改动：查询改写、content/summary/tags 混合字段、rerank 阈值过滤。

对比口径：

- `mean_recall_after >= mean_recall_before`
- `mean_mrr_after >= mean_mrr_before`
- hard query 的 ground truth 排名是否前移

当前 9 条 query：`mean_mrr` 从 `0.9259` 提升到 `1.0`，加班硬查询从第 3 名提升到第 1 名；原始归档在 `docs/eval/offline/rag_p3_before_after.json`。

### 记忆去重与失败兜底

用离线 `DedupMemoryStore` 验证与生产 `AsyncMemory._create_memory` 相同的幂等契约：

- 精确 `hash + content` 查重，重复 add 复用原 id，不产生新向量
- 内容相同的 update 跳过，未知 id 的 update/delete 安全跳过
- 历史表写失败只丢失历史日志，不影响已经完成的向量写

当前 60 次 add 尝试中 20 次插入、40 次跳过，`duplicate_skip_rate=0.6667`，`exact_hash_ids_stable=true`；原始归档在 `docs/eval/offline/memory_dedup_p3.json`。

## 数据集口径

| 评测 | case 数 | 数据来源 | 标签/ground truth |
| --- | --- | --- | --- |
| RAG | 9 queries / 8 docs | `fixtures/rag/dataset.json` | `chunk_id` |
| Memory | 12 cases | `fixtures/memory/cases.json` | 每模式 4 条 expected 片段 |
| Cancel | 可配置 runs | 本地模拟生产者 | 阈值 500ms |
| Token | 40 pairs | `token_budget.build_long_conversation` | 摘要触发点 / 保留占比 |
| RAG Optimizer | 9 queries / 8 docs | `fixtures/rag/dataset.json` | baseline vs optimized 前后对比 |
| Memory Dedup | 60 add attempts | `memory_duplicate.DedupMemoryStore` | 幂等 id、skip rate、失败兜底 |

## 当前限制

- 离线 RAG 使用词法相似度：字母数字连续串 + 中文单字 token，再加 tag 命中加分；它验证的是“固定样本可复现”，不表达生产语义检索能力
- 离线记忆只验证“上下文里能否找回答案片段”，不验证 LLM 抽取、去重、合并质量
- 断流评测包含本地 asyncio 模拟与真实 SSE 链路（P5.5）两种口径；离线数字只做回归，真实链路数字以 `docs/eval/live/live_cancel_20260814_151430.json` 为准
- 多 Agent 当前是固定关键词 demo 路由，不支持通用 NL 图编排；真实链路路由与子 Agent ReAct 已在 P5.7 的 5 个固定场景中验证
- Token 校准、RAG 优化、记忆去重仍保留离线/模拟口径，数字用于回归和解释；真实链路提升以 P5.9（RAG A/B）、P5.10（Memory 两层 vs 三层）为准
- 只有归档 `docs/eval/live/` 原始 JSON 后，数字才能作为真实链路口径进入简历；P5 已完成后，面试数字以 P5.9/P5.10 等正式 JSON 为准，P2/P3 离线数字继续只做回归解释

## 面试表达建议

- “RAG：9 条离线 query 上 hit_rate=1.0、MRR 0.9259 只用来解释回归；真实链路口径用 P5.9 的 50 条 query A/B，Recall@5 0.8467 -> 0.96”
- “记忆：离线三档 hit_rate=1.0 证明检索链路可用；两层 vs 三层真实 A/B（P5.10）是最终回答级证据，Fact Recall 0.0968 -> 0.6774”
- “断流：模拟链路先校准 500ms 阈值；真实 SSE 链路（P5.5）5/5 轮通过，cancel_to_terminate 均值 0.485ms、max 1.356ms”
- “多 Agent：固定 demo 路由 + 子 Agent 独立 ReAct 链 + 分层事件；P5.7 真实链路 5 个场景 pass_rate=1.0、route_match_rate=1.0”
- “Context Engineering：离线 token 摘要阈值校准 + 记忆去重 20/60 插入、40 次跳过；真实记忆提升以 P5.10 两层 vs 三层为准”

P5.5 真实 SSE 断流、P5.7 多 Agent、P5.9 RAG A/B、P5.10 Memory 两层 vs 三层均已落盘；设计与执行记录见 `docs/eval/upcoming/RAG_COMPARISON_DESIGN.md`、`docs/eval/upcoming/MEMORY_COMPARISON_DESIGN.md`，原始 JSON 在 `docs/eval/live/`，面试数字只有以这些 JSON 为证据才可引用。
