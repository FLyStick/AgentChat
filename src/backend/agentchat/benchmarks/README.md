# AgentChat Benchmarks

可复现的评测入口，服务于 P2/P3「评测与演示证据」与 P5「真实链路评测」。前六个离线子命令只依赖 Python 标准库，不需要启动 MySQL、Redis、Milvus、ES 或外部模型，用于固定基线回归；`live_*` 子命令需要后端、Docker 依赖和真实 Embedding/LLM，结果代表本机服务链路实测。

## 运行

在 `src/backend` 目录执行：

```powershell
python -m agentchat.benchmarks rag --top-k 5 --output rag_result.json
python -m agentchat.benchmarks memory --mode short_term --mode summary --mode long_term --top-k 3 --output memory_result.json
python -m agentchat.benchmarks cancel --runs 5 --initial-delay-ms 150 --chunk-interval-ms 30 --chunks 30 --cancel-after-ms 250 --threshold-ms 500 --output cancel_result.json
python -m agentchat.benchmarks token --pairs 40 --cutoffs 1000 2000 3000 4000 5000 --output token_budget.json
python -m agentchat.benchmarks rag-optimizer --top-k 5 --threshold 0.08 --output rag_before_after.json
python -m agentchat.benchmarks memory-duplicate --output memory_dedup.json
```

不带 `--output` 时，结果以 UTF-8 JSON 打印到 stdout；带 `--output` 时写出可归档的原始 JSON。

## 评测内容

### RAG

- 固定 8 个知识块、9 条 query，ground truth 是 `chunk_id`
- 离线检索器使用词法相似度（字母数字连续串 + 中文单字 token），再加 tag 命中加分，仅用于可复现回归，不代表生产语义检索
- 输出 Recall@K、MRR@K、Hit Rate@K、延迟分位

### Memory

- 12 个 case：短期窗口、摘要、长期事实各 4 条
- 每个模式从对应上下文检索 top K，命中定义为检索结果包含 expected 片段
- 核心展示指标是每个模式的 Hit Rate，辅助提供 Recall/MRR

### Cancel

- 模拟 SSE 产流中断，验证 `CancellableAsyncStream` 在 `request_cancel()` 后终止正在运行的生产者
- 输出 `cancel_to_terminate_ms` 以及是否满足阈值（默认 500ms）
- 当前是本地 asyncio 模拟压测，不是真实 LLM 链路；写简历前必须在真实服务链路补测

### Token Budget

- 按 user/assistant 两两分组，复刻生产 `DialogService.split_messages_by_token`
- 从最新一对向前累计，超过 cutoff 后把旧对话交给摘要，输出摘要触发点、保留轮数、保留 token 占比
- 40 对样本 / 8560 tokens；默认 1000-5000 阈值都会触发摘要，5000 阈值保留 23 对 / 5000 tokens

### RAG Optimizer

- 对同一固定数据集分别跑 baseline 与优化检索器，输出前后对比
- 优化项：确定性查询改写、content/summary/tags 混合字段、低置信结果 rerank 阈值过滤
- 当前 9 条 query：`mean_mrr` 从 `0.9259` 提升到 `1.0`，加班硬查询从第 3 名提升到第 1 名

### Memory Dedup

- 精确 hash+content 查重，重复写入复用原 id；冗余 update 跳过，未知 id 跳过
- 模拟历史表写失败，验证向量写不被中断
- 当前 60 次 add 尝试中 20 次插入、40 次去重跳过，skip rate `0.6667`

## 当前离线基线（2026-08-13）

| 评测 | 结果 |
| --- | --- |
| RAG | 9 queries, `mean_recall_at_k=1.0`, `mean_mrr=0.9259`, `hit_rate_at_k=1.0` |
| Memory | 12 cases, 三模式 `hit_rate` 均为 `1.0` |
| Cancel | 5 runs, `pass_rate=1.0`, `cancel_to_terminate` mean `0.102ms`, p50 `0.085ms`, p90 `0.169ms`, max `0.212ms` |
| Token | 40 pairs / 8560 tokens, cutoff 1000-5000 均触发摘要；5000 时保留 23 pairs / 5000 tokens |
| RAG Optimizer | hard query rank 3 -> 1, `mean_mrr` 0.9259 -> 1.0 |
| Memory Dedup | 60 add attempts, 20 inserted, 40 skipped, skip rate `0.6667`, history failure fallback ok |

## 目录结构

```text
benchmarks/
├── __main__.py          # CLI 入口
├── metrics.py           # 指标计算
├── rag.py               # 离线/线上 RAG 适配器
├── memory.py            # 离线/线上记忆适配器
├── cancel.py            # 断流取消压测
├── token_budget.py      # token 预算校准
├── rag_optimizer.py     # RAG 优化前后对比
├── memory_duplicate.py  # 记忆去重与失败兜底
├── live_utils.py        # 真实链路 API/状态/SSE 客户端
├── live_seed.py         # 知识库与用户/Agent 灌入
├── live_rag.py          # 真实 RAG 召回
├── live_rag_ab.py       # 真实 RAG A/B 对比
├── live_completion.py   # 真实 Completion SSE 评测
├── live_cancel.py       # 真实 SSE 断流评测
├── live_memory.py       # 真实记忆链路评测
├── live_multi_agent.py  # 真实多 Agent 评测
├── live_memory_ab.py    # 两层 vs 三层记忆真实 A/B
└── fixtures/
    ├── rag/dataset.json
    ├── rag_live/         # P5 真实 RAG sources（3 份）
    ├── rag_live_ab/      # P5.9 A/B queries 与 21 份 sources
    ├── memory/cases.json
    └── memory_live_ab/   # P5.10 30 条场景 / 62 Gold Facts
```

P3 原始 JSON 归档在 `docs/eval/offline/token_budget_p3.json`、`docs/eval/offline/rag_p3_before_after.json`、`docs/eval/offline/memory_dedup_p3.json`。

## 真实链路评测（P5）

真实链路脚本默认读取 `%TEMP%\agentchat_live_bench_state.json`（由 `live_seed.py` 生成），需要后端已启动、Docker 依赖 healthy，且状态文件里的 token 未过期。全部命令在 `src/backend` 目录执行：

```powershell
# 0. 灌入知识库/用户/Agent（首次或状态文件过期时）
& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_seed --output-dir ..\..\docs\eval\live

# 1. RAG 真实召回（30 条 ground truth）
& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_rag --output-dir ..\..\docs\eval\live

# 2. Completion 端到端（默认 15 条）
& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_completion --output-dir ..\..\docs\eval\live

# 3. SSE 断流（正式 5 轮）
& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_cancel --rounds 5 --output-dir ..\..\docs\eval\live

# 4. 记忆真实链路
& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_memory --output-dir ..\..\docs\eval\live

# 5. 多 Agent 真实链路（5 个固定业务任务）
& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_multi_agent --output-dir ..\..\docs\eval\live

# 6. RAG 真实 A/B（P5.9，首次或重建时先灌入 P5.9 知识库）
& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_seed `
  --queries-file agentchat/benchmarks/fixtures/rag_live_ab/queries.json `
  --user-name live_ab_0814 `
  --email live_ab_0814@bench.local `
  --output-dir ..\..\docs\eval\live

& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_rag_ab `
  --top-k 5 `
  --output-dir ..\..\docs\eval\live

# 7. Memory 两层 vs 三层（P5.10，冒烟 2 条）
& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_memory_ab --limit 2 --wait-memory-timeout 15 --output-dir ..\..\docs\eval\live

# 8. Memory 两层 vs 三层（P5.10，正式全量 30 条）
& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_memory_ab --limit 30 --wait-memory-timeout 15 --output-dir ..\..\docs\eval\live
```

已落盘的真实链路结果：

| 评测 | 结果 | 原始 JSON |
| --- | --- | --- |
| RAG | 30 queries，`mean_recall_at_k=1.0`、`mean_mrr=0.9167`、`hit_rate_at_k=1.0`，平均延迟 804.8ms | `docs/eval/live/live_rag_20260814_123820.json` |
| Completion | 15 queries，`case_ok_rate=1.0`、`knowledge_ok_rate=1.0`、`tool_error_case_count=0`；平均 total 24841.758ms | `docs/eval/live/live_completion_20260814_134552.json` |
| Cancel | 5 rounds，`pass_rate=1.0`，`cancel_to_terminate_ms` 均值 0.485ms、max 1.356ms | `docs/eval/live/live_cancel_20260814_151430.json` |
| Memory | 5 facts，same run 5/5、cross run 5/5，`hit_rate=1.0`、`mean_mrr=1.0` | `docs/eval/live/live_memory_20260814_154252.json` |
| Multi-Agent | 5 cases，`pass_rate=1.0`、`route_match_rate=1.0`、`sub_agent_pair_count=5/5`、`tool_calls=5` | `docs/eval/live/live_multi_agent_20260814_155110.json` |
| RAG A/B | 50 queries / 102 chunks，hard 子集 `Recall@5` 0.7843→0.9412、`MRR@5` 0.7255→0.7843、`Hit@1` 0.8824→1.0；Rerank 48/50 | `docs/eval/live/live_rag_comparison_20260814_182215.json` |
| Memory A/B | 30 scenarios / 62 facts，`fact_recall` 0.0968→0.6774、`case_pass_rate` 0→0.4667；判别式 hint 评分 | `docs/eval/live/live_memory_comparison_20260818_105736.json` |

要点：`live_seed.py` 会新建/复用评测用户；`live_completion.py`、`live_cancel.py`、`live_memory.py`、`live_multi_agent.py` 每次运行都会创建新的测试 Agent/Dialog，`live_memory_ab.py` 为每场景每 arm 创建独立身份，结果写入新的时间戳 JSON，不会覆盖历史归档。真实链路数字只用于 P5 面试口径，离线数字继续作为可复现 baseline 保留。

## 路线 B：RAG 真实 A/B（P5.9，已完成）

设计与执行记录：`docs/eval/live/RAG_COMPARISON_DESIGN.md`。

- 同一真实知识库、同一向量库、同一 ground truth；
- baseline：原始 query 单路 `content` 向量检索 + merge + topK，不改写、不 Rerank；
- 实验组：生产完整链路 Query Rewrite + content/summary 混合/回退 + Rerank + 阈值过滤（正式结果 `min_score=0`）；
- 主指标 `Recall@5 / Hit@1 / MRR@5`，辅助指标 `Recall@10 / Hit@10` 与延迟分位；
- 当前归档 `docs/eval/live/live_rag_comparison_20260814_182215.json`，同一知识库 `t_2aadac46967e4487` 共 21 个文档 / 102 个 chunk / 50 条 ground truth；第一轮 Rerank 403 结果 `live_rag_comparison_20260814_173430.json` 保留为历史对照；
- 结果：全量 `Recall@5` 0.8467→0.96、`MRR@5` 0.6967→0.7983、`Hit@1` 0.88→0.98；hard 17 条 `Recall@5` 0.7843→0.9412、`MRR@5` 0.7255→0.7843、`Hit@1` 0.8824→1.0；
- 限制：`query_rewrite` 可用 50/50，`rerank` 可用 48/50（2 条 DNS 瞬时错误后按生产 fallback 降级，JSON 记录 `availability.rerank=false`）；`min_score` 由 `0.2` 调整为 `0.0` 以匹配 `gte-rerank-v2` 分数口径；差异来自组合策略，未做单组件消融，不能表述为 Rerank 单独提升。

## Memory 两层 vs 三层（P5.10，已完成）

设计与执行记录：`docs/eval/upcoming/MEMORY_COMPARISON_DESIGN.md`。

- 两层：`enable_memory=False`，短期历史 + 中期摘要；三层：`enable_memory=True`，短期历史 + 中期摘要 + 长期向量记忆；
- 实际形态：每条场景 2 轮 seed 事实埋点 + 全新 probe 会话，30 条场景 / 62 Gold Facts；
- 每场景、每 arm 独立 user/agent，共 60 组身份；身份清单 `docs/eval/live/memory_ab_state.json`；
- 判别式 hint 匹配（支持 `expected_variants` 同义变体），非 LLM-as-Judge；
- 结果 `docs/eval/live/live_memory_comparison_20260818_105736.json`：Fact Recall `0.0968 → 0.6774`（+0.5806）、Case Pass Rate `0 → 0.4667`（14/30）；
- 限制：评测前的记忆证据轮询仅 1/30 场景 `ready=true`，其中 13 个 `ready=false` 场景最终仍答对；该轮询暂不能逐条证明“检索 -> 注入 -> 回答”因果，详情见设计文档第 8 节。
