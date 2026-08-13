# AgentChat Benchmarks

可复现的评测入口，服务于 P2「评测与演示证据」。当前三个子命令都只依赖 Python 标准库，不需要启动 MySQL、Redis、Milvus、ES 或外部模型，用于固定基线回归。

## 运行

在 `src/backend` 目录执行：

```powershell
python -m agentchat.benchmarks rag --top-k 5 --output rag_result.json
python -m agentchat.benchmarks memory --mode short_term --mode summary --mode long_term --top-k 3 --output memory_result.json
python -m agentchat.benchmarks cancel --runs 5 --initial-delay-ms 150 --chunk-interval-ms 30 --chunks 30 --cancel-after-ms 250 --threshold-ms 500 --output cancel_result.json
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

## 当前离线基线（2026-08-13）

| 评测 | 结果 |
| --- | --- |
| RAG | 9 queries, `mean_recall_at_k=1.0`, `mean_mrr=0.9259`, `hit_rate_at_k=1.0` |
| Memory | 12 cases, 三模式 `hit_rate` 均为 `1.0` |
| Cancel | 5 runs, `pass_rate=1.0`, `cancel_to_terminate` mean `0.102ms`, p50 `0.085ms`, p90 `0.169ms`, max `0.212ms` |

## 目录结构

```text
benchmarks/
├── __main__.py          # CLI 入口
├── metrics.py           # 指标计算
├── rag.py               # 离线/线上 RAG 适配器
├── memory.py            # 离线/线上记忆适配器
├── cancel.py            # 断流取消压测
└── fixtures/
    ├── rag/dataset.json
    └── memory/cases.json
```

## 真实链路接入

`rag.LiveRetriever` 和 `memory.LiveMemoryAdapter` 已预留生产适配器入口，分别对接 `RagHandler.mix_retrival_documents` 和 `memory_client.search`。CLI 暂未暴露 `--live`，等 P4 服务链路可用后接入，并补齐真实 RAG、记忆与 LLM 断流数据。
