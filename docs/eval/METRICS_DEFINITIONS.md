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

## 数据集口径

| 评测 | case 数 | 数据来源 | 标签/ground truth |
| --- | --- | --- | --- |
| RAG | 9 queries / 8 docs | `fixtures/rag/dataset.json` | `chunk_id` |
| Memory | 12 cases | `fixtures/memory/cases.json` | 每模式 4 条 expected 片段 |
| Cancel | 可配置 runs | 本地模拟生产者 | 阈值 500ms |

## 当前限制

- 离线 RAG 使用词法相似度：字母数字连续串 + 中文单字 token，再加 tag 命中加分；它验证的是“固定样本可复现”，不表达生产语义检索能力
- 离线记忆只验证“上下文里能否找回答案片段”，不验证 LLM 抽取、去重、合并质量
- 断流压测是本地 asyncio 模拟生产者，不是真实 LLM 推理链路；真实服务的 `cancel_to_terminate_ms` 必须在部署链路中重跑
- 只有完成真实链路复跑并归档原始 JSON 之后，P2 数字才可以进入简历

## 面试表达建议

- “RAG 部分我建了固定知识库和 query 集，9 条 query 上 hit_rate=1.0，MRR 0.9259；离线基线用于回归，线上数字等部署后补测”
- “记忆我按短期窗口、摘要、长期事实三档做 benchmark，每档 4 条固定样本，先验证链路能找回答案”
- “断流我做了任务级取消，压测目标 500ms，当前模拟链路已通过；真实模型链路的数字我会在整体部署后补”
