# 评测报告模板

> 用途：P3/P4 每次优化前先记录 baseline，再做改动，最后对比提升幅度。
> 原则：没有原始 JSON 的数字不算数；写简历前必须保留 `--output` 产物。

## 1. 报告信息

| 字段 | 内容 |
| --- | --- |
| 日期 | yyyy-mm-dd |
| 运行环境 | OS / Python / 服务版本 |
| 数据集版本 | 固定 fixture 路径 |
| 复现命令 | 完整 CLI 命令 |
| 结果产物 | `rag_result.json` / `memory_result.json` / `cancel_result.json` |

## 2. 结论摘要

| 评测 | 指标 | baseline | current | 提升 |
| --- | --- | --- | --- | --- |
| RAG | mean_recall_at_k | - | - | - |
| RAG | mean_mrr | - | - | - |
| RAG | hit_rate_at_k | - | - | - |
| Memory | short_term hit_rate | - | - | - |
| Memory | summary hit_rate | - | - | - |
| Memory | long_term hit_rate | - | - | - |
| Cancel | pass_rate @ threshold | - | - | - |
| Cancel | cancel_to_terminate p50 | - | - | - |

## 3. RAG 评测

### 3.1 数据集

- 固定知识库：8 个 chunk，覆盖酒店 FAQ、项目手册、内部制度
- 固定 query：9 条，难度 easy / normal / hard
- ground truth：`chunk_id`

### 3.2 复现命令

```powershell
cd src/backend
python -m agentchat.benchmarks rag --top-k 5 --output rag_result.json
```

### 3.3 结果表

从 `rag_result.json` 的 `cases` 粘贴以下字段：

| query_id | difficulty | recall_at_k | mrr_at_k | hit_at_k | latency_ms |
| --- | --- | --- | --- | --- | --- |
| - | - | - | - | - | - |

### 3.4 参考基线（2026-08-13，离线词法检索）

| 指标 | 数值 |
| --- | --- |
| case_count | 9 |
| mean_recall_at_k | 1.0 |
| mean_mrr | 0.9259 |
| hit_rate_at_k | 1.0 |

## 4. 记忆评测

### 4.1 数据集

- 12 个 case：短期窗口、摘要、长期事实各 4 条
- 每个 case 有独立 context、query、expected 片段

### 4.2 复现命令

```powershell
cd src/backend
python -m agentchat.benchmarks memory --top-k 3 --output memory_result.json
```

### 4.3 结果表

| mode | case_count | hit_rate | mean_recall_at_k | mean_mrr |
| --- | --- | --- | --- | --- |
| short_term | - | - | - | - |
| summary | - | - | - | - |
| long_term | - | - | - | - |

### 4.4 参考基线（2026-08-13，离线）

三种模式各 4 条，`hit_rate` 均为 `1.0`。

## 5. 断流压力评测

### 5.1 复现命令

```powershell
cd src/backend
python -m agentchat.benchmarks cancel `
  --runs 5 --initial-delay-ms 150 --chunk-interval-ms 30 `
  --chunks 30 --cancel-after-ms 250 --threshold-ms 500 `
  --output cancel_result.json
```

### 5.2 结果表

| run | received_chunks | cancelled | cancel_to_terminate_ms | total_duration_ms |
| --- | --- | --- | --- | --- |
| 1 | - | true | - | - |

### 5.3 参考基线（2026-08-13，模拟生产者）

| 指标 | 数值 |
| --- | --- |
| runs | 5 |
| pass_rate | 1.0 |
| cancel_to_terminate mean | 0.102ms |
| cancel_to_terminate p50 | 0.085ms |
| cancel_to_terminate p90 | 0.169ms |
| cancel_to_terminate max | 0.212ms |

> 注意：这是本地 asyncio 模拟压测，不是真实模型推理链路；模拟值会随本地调度小幅波动，以本次命令导出的 JSON 为准。

## 6. 结论与简历表述

- 写出每项变化对应的代码改动
- 写出仍需补齐的验证（真实服务链路、不同模型、多轮压力）
- 只有真实链路复跑并归档 JSON 后，才可以把数字写进简历
