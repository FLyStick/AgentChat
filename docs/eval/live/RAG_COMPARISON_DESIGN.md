# RAG 真实 A/B 设计与执行记录（P5.9，路线 B）

> 状态：已完成（2026-08-14）。本文同时记录设计口径与实际执行结果，原始证据以 `docs/eval/live/live_rag_comparison_*.json` 落盘文件为准。
> 对应计划：`VIBECODING_PLAN.md` P5.9。

## 1. 为什么选路线 B

简历要支撑的是“我在真实 RAG 链路上做了检索优化并测出差异”。离线词法检索器和 6 个 chunk 的真实知识库都无法支撑这个口径：

| 现有证据 | 问题 |
| --- | --- |
| `docs/eval/offline/rag_p3_before_after.json` | 离线词法检索器，不是真实 Embedding、真实向量库、真实生产链路，只能证明固定样本回归 |
| `docs/eval/live/live_rag_20260814_123820.json` | 真实向量库检索，但只有 6 个 chunk，`mean_recall_at_k=1.0`，检索饱和，无法体现策略差异 |

路线 B 的定位：

1. 不引入与业务无关的公开检索数据集；
2. 不把 Indexing 侧换文档/换切块和 Retrieval 侧改策略混在一起；
3. 用项目自己的真实业务语料灌入真实向量库，人工标注 ground truth；
4. 同一批 query 只切换检索策略，其余条件完全一致；
5. 数字只能说“在 xxx 条真实 query 上，生产检索链路 vs 原始检索的 Recall/MRR 差异”，而不是“离线 mock 提升”。

## 2. 代码事实

生产 Agent 的 RAG 链路：

```text
GeneralAgent.setup_knowledge_tool
  -> retrival_knowledge(query)
  -> RagHandler.retrieve_ranked_documents(query, knowledge_ids)
  -> Query Rewrite
  -> MixRetrival(content / content+summary)
  -> merge_documents_by_score
  -> Reranker.rerank_documents
  -> _filter_reranked_documents(min_score / rerank_threshold)
```

代码位置：`src/backend/agentchat/services/rag/handler.py`、`src/backend/agentchat/services/rag/rerank.py`、`src/backend/agentchat/services/rewrite/query_write.py`。

P5.9 的评测脚本直接复用上述生产组件，保证与 Agent 工具行为一致，而不是另写一套检索器。

## 3. 评测数据

### 语料

- 21 个 txt 业务文档位于 `src/backend/agentchat/benchmarks/fixtures/rag_live_ab/sources/`，覆盖酒店服务、企业制度、项目部署、项目 FAQ、运营 SOP 五类业务；
- 由生产切块算法（`chunk_size=500 / overlap_size=100`）切出 102 个 chunk；
- 真实灌入后端知识库 `RagAb0814`，`knowledge_id=t_2aadac46967e4487`，向量库为 Chroma；
- 灌入结果归档 `docs/eval/live/live_seed_ab_result.json`，状态文件 `docs/eval/live/live_rag_ab_state.json`。

### Query 与 ground truth

- 50 条 query，按难度分布：easy 11、normal 22、hard 17；
- 共 68 条可逐字校验的事实片段，全部 facts 已用生产切块结果校验命中，`misses=0`；
- ground truth 使用 `chunk_id` 作为期望召回，由 `live_seed.py` 从语料切块与 query facts 生成，加载文件为 `src/backend/agentchat/benchmarks/fixtures/rag_live_ab/queries.json`；
- 归档 `docs/eval/live/live_rag_ab_ground_truth.json`。

hard 子集覆盖的难点：口语提问 vs 文档措辞、跨 chunk 线索、低词面重合、多条件联合限制。这部分是 A/B 最能体现差异的样本。

## 4. A/B 定义

### Baseline（原始检索）

```text
raw_query
  -> RagHandler.mix_retrival_documents(query, ids, "content")
  -> merge_documents_by_score
  -> top_k=5
```

不做 Query Rewrite、不做 summary 检索、不做 Rerank、不做阈值过滤。

### Optimized（生产完整链路）

```text
raw_query
  -> Query Rewrite（deepseek-v4-flash，保留原始 query 与改写 query 去重）
  -> content 向量检索（enable_summary=False，summary 可用性单独记录）
  -> merge_documents_by_score
  -> Reranker.rerank_documents（gte-rerank-v2）
  -> min_score / rerank_threshold 过滤（正式结果 min_score=0）
  -> top_k=5
```

两组共用同一个 `knowledge_id`、同一个 Chroma collection、同一份 ground truth。每次运行记录改写、检索、rerank 的耗时与可用性，失败不伪造数字。

## 5. 实际执行结果

原始 JSON：`docs/eval/live/live_rag_comparison_20260814_182215.json`

历史对照：第一轮正式 A/B（Rerank `403` 全部 fallback）归档为 `live_rag_comparison_20260814_173430.json`，保留作为历史证据，不作为当前结论来源。

运行配置：

- `knowledge_id`：`t_2aadac46967e4487`
- `top_k`：5
- Embedding：`qwen3.7-text-embedding`（DashScope 真实调用）
- Rewrite：`deepseek-v4-flash`（可用 50/50）
- Rerank：`gte-rerank-v2`，通过阿里云 MaaS 原生 rerank 路由调用（可用 48/50，2 条因网络瞬时错误降级，详见限制）
- `min_score`：`0.0`；`gte-rerank-v2` 的分数口径与向量余弦不同，沿用旧阈值 `0.2` 会误过滤相关文档并导致部分结果为空
- Summary：未启用（`enable_summary=False`）

### 总体（50 条 query）

| 指标 | Baseline | Optimized | 差异 |
| --- | --- | --- | --- |
| Recall@5 | 0.8467 | 0.9600 | +0.1133 |
| MRR@5 | 0.6967 | 0.7983 | +0.1016 |
| Hit@1 | 0.88 | 0.98 | +0.1000 |
| 平均延迟 | 1055.564ms | 11402.720ms | +10347.156ms |

### hard 子集（17 条 query）

| 指标 | Baseline | Optimized | 差异 |
| --- | --- | --- | --- |
| Recall@5 | 0.7843 | 0.9412 | +0.1569 |
| MRR@5 | 0.7255 | 0.7843 | +0.0588 |
| Hit@1 | 0.8824 | 1.0000 | +0.1176 |
| 平均延迟 | 439.378ms | 12708.014ms | +12268.636ms |

### easy + normal（33 条 query）

| 指标 | Baseline | Optimized | 差异 |
| --- | --- | --- | --- |
| Recall@5 | 0.8788 | 0.9697 | +0.0909 |
| MRR@5 | 0.6818 | 0.8056 | +0.1238 |
| Hit@1 | 0.8788 | 0.9697 | +0.0909 |
| 平均延迟 | 1372.993ms | 10730.295ms | +9357.302ms |

### 事实证据命中率

- Baseline：全量 `evidence_hit_rate_joined=0.85`，hard `0.7941`
- Optimized：全量 `evidence_hit_rate_joined=0.97`，hard `0.9706`

### 组件可用性

- `query_rewrite`：50/50
- `rerank`：48/50，剩余 2 条自动降级为原始检索分数
- `summary`：0/50（本组未启用，符合设计）

## 6. 结果解读

能真实表述的结论：

- 在 50 条真实 query 上，`Query Rewrite + Rerank + min_score 修正`组合使三项主指标同步提升：`Recall@5` 从 `0.8467` 到 `0.96`、`MRR@5` 从 `0.6967` 到 `0.7983`、`Hit@1` 从 `0.88` 到 `0.98`；
- 差异最集中在 hard 子集：`Recall@5` 从 `0.7843` 提升到 `0.9412`、`Hit@1` 从 `0.8824` 提升到 `1.0`，证据命中率从 `0.7941` 提升到 `0.9706`，说明重组策略对低词面重合、口语化提问更有效；
- `Rerank` 已真实接入并参与 48/50 条排序，不再是以往的“不可用”；剩余 2 条网络抖动按生产 fallback 降级，结果不伪造。

不能表述的结论：

- “Rerank 单独带来提升”：本轮实验组同时包含 Query Rewrite、Rerank 和 `min_score` 修正，没有分别做消融，不能把差值全部归因到 Rerank 一个组件；
- “端到端回答准确率提升”：本轮指标只覆盖检索召回（Recall/MRR/Hit/证据片段命中），不评估最终生成回答的 Factuality；
- “吞吐提升”：优化组平均延迟约 `11.4s`、baseline 约 `1.06s`，主要增加来自 DeepSeek Query Rewrite 调用，延迟不能用于性能宣称。

## 7. 限制与后续

### Rerank 可用性与 min_score 口径

服务接入方式：MaaS 网关原生路由 `https://ws-ze1y8crceadogt4l.cn-beijing.maas.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank`；DashScope Compatible Mode 的 `/v1/rerank` 路由未暴露，不能作为本网关的调用路径。

正式 50 条中 `rerank` 可用 48/50，2 条（`live_ab_p_mcp_stdio`、`live_ab_p_knowledge_bind`）因网络瞬时错误在调用层失败，脚本按生产 handler 逻辑降级为原始检索分数，并逐 case 记录 `availability.rerank=false`。JSON 只记录可用性，不记录错误串，运行错误类型为 DNS 解析 `getaddrinfo failed`，属于网络抖动而非接口或模型配置问题。

`min_score` 调整说明：`gte-rerank-v2` 返回的 relevance score 数值明显低于向量余弦相似度；沿用旧的 `min_score=0.2` 会导致 12 条优化组结果被过滤为空，并出现 Recall/MRR 倒挂。正式结果使用 `min_score=0.0`，只按重排顺序截断 `top_k=5`，`rerank_threshold=None`。

后续可选消融：分别跑 `rewrite only`、`rerank only`、`rewrite + rerank`，才能把组合提升拆到单个组件；若需 `rerank` 可用率达到 50/50，在稳定网络下重跑会生成新的时间戳 JSON，不覆盖历史结果。

### 其他边界

- 评测环境为本机 conda `agentchat` 后端 + Docker 依赖服务，不能写成通用生产环境指标；
- 语料是自建业务语料，不替代公开检索基准；
- 50 条 query 的 ground truth 已固定，研究后续改动时禁止在脚本中动态生成期望结果。

## 8. 面试口径

- 说绝对值：`hard 子集 Recall@5 0.7843 -> 0.9412，Hit@1 0.8824 -> 1.0`；全量 `Recall@5 0.8467 -> 0.96、MRR@5 0.6967 -> 0.7983、Hit@1 0.88 -> 0.98`；
- 说真实范围：`50 条自建真实业务 query，同一 Chroma collection、同一 ground truth，仅切换检索策略`；
- 说责任边界：`差异来自 Query Rewrite + Rerank + min_score 修正的组合，Rerank 48/50 可用，另有 2 条网络抖动走生产 fallback`；
- 不说“Rerank 单独提升”、不说端到端回答准确率提升、不把 `11.4s` 延迟说成性能优化。

## 9. 复现命令

从 `src/backend` 执行：

```powershell
# 1. 灌入/复用知识库与 ground truth
& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_seed `
  --queries-file agentchat/benchmarks/fixtures/rag_live_ab/queries.json `
  --user-name live_ab_0814 `
  --email live_ab_0814@bench.local `
  --output-dir ..\..\docs\eval\live

# 2. 全量 A/B
& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_rag_ab `
  --top-k 5 --output-dir ..\..\docs\eval\live

# 3. 冒烟（前 5 条）
& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_rag_ab `
  --top-k 5 --limit 5 --output-dir ..\..\docs\eval\live
```

本档关键归档：

- `docs/eval/live/live_seed_ab_result.json`
- `docs/eval/live/live_rag_ab_ground_truth.json`
- `docs/eval/live/live_rag_comparison_20260814_182215.json`（最新，Rerank 48/50）
- `docs/eval/live/live_rag_comparison_20260814_173430.json`（Rerank 403 历史对照）
- `docs/eval/live/live_rag_ab_state.json`
