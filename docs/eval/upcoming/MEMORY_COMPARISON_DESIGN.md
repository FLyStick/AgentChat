# Memory 两层 vs 三层真实对比设计与执行记录

> 状态：completed / 已执行（2026-08-18）。
> 原始 JSON：`docs/eval/live/live_memory_comparison_20260818_105736.json`
> 目标：用真实 `/api/v1/completion` 对话评测“两层记忆 vs 三层记忆”的最终回答差异。
> 对应计划：`VIBECODING_PLAN.md` P5.10。

## 1. 口径确认

面试口径固定为“两层 vs 三层”：

| 组别 | `enable_memory` | 上下文构成 |
| --- | --- | --- |
| 两层 | `False` | 短期对话历史 + 中期摘要 |
| 三层 | `True` | 短期对话历史 + 中期摘要 + 长期向量记忆 |

代码位置：`src/backend/agentchat/api/v1/completion.py:65-91`（构建上下文），`:126-135`（写记忆）。

说明：

- 短期历史通过 `HistoryService.get_short_term_messages` 获取；
- 中期摘要是 `DialogService.get_dialog_history_summary`；
- 长期记忆只在 `enable_memory=True` 时执行 `memory_client.search(...)`，且写入时带 `user_id / agent_id / run_id`；
- “两层 vs 三层”不是测试“第 2 层摘要本身”，而是测试“加不加长期向量记忆层”对最终回答的影响。

## 2. 现有证据的边界

已有 `docs/eval/live/live_memory_20260814_154252.json` 证明：

- 真实 memory_client + Chroma + Embedding 能写入事实；
- 同 run / 跨 run 都能检索回事实；
- `hit_rate=1.0`、`mean_mrr=1.0`。

它不能证明：

- 两层与三层在真实多轮对话中的回答差异；
- LLM 是否真的把长期记忆用进最终答案；
- 事实跨会话召回能否转化为 Fact Recall 命中。

所以 P5.10 必须做对话级评测，而不是继续做“写入后检索回”。

## 3. 测试形态：seed 对话埋点 + 全新 probe 会话

实际执行构造 30 条 Fact Recall 场景、62 个 Gold Facts，fixture：`src/backend/agentchat/benchmarks/fixtures/memory_live_ab/scenarios.json`。

```text
seed 阶段：2 轮自然对话中埋入 2-3 条 Gold Facts（fixture 的 fact_turns）
probe 阶段：完全新建的 probe 会话，提问必须依赖 seed 阶段的事实才能正确回答
```

Gold Facts 示例（酒店/办公场景，最终以 fixture 为准）：

- 用户鸡蛋过敏，餐食不能被包含鸡蛋；
- 用户偏好高层、南向房间；
- 报销统一走公司账户；
- 用户习惯早晨开会；
- 设备是 MacBook，需要对应转接头。

每条场景标注：

- `scenario_id`
- `turns`（用户/助手消息）
- `gold_facts`（3-5 条）
- `probe_question`
- `expected_probe_hint`（判别式评分参考）

设计初期考虑过 6-10 轮（含干扰信息、闲聊、主题偏移），正式 fixture 收敛为“每条 2 轮明确事实埋点 + 全新 probe 会话”：两层与三层跑完全相同的剧本，在 probe 会话中短期历史不会携带 seed 事实，长期向量记忆是跨会话唯一可携带信息的通道；两层 probe 场景级全对数为 0，反过来验证了 seed 短期历史没有泄漏到 probe。该形态更直接测量“长期记忆层”本身，同时控制真实 LLM 调用成本。

## 4. 执行方式

### 4.1 两组跑同一套剧本

- 两层组：创建 `enable_memory=False` 的测试 Agent，独立 `user_id`
- 三层组：创建 `enable_memory=True` 的测试 Agent，独立 `user_id`
- 同一套场景脚本，逐轮调真实 `/api/v1/completion`
- 切忌：先跑对照组，然后用同一个 user/agent 直接跑实验组，否则早期记忆会污染实验组

### 4.2 独立身份防污染（实际执行）

- 实际执行按“每个场景 × 每个 arm”分别创建独立 user/agent，共 30×2=60 组身份；
- 每次运行的身份前缀为 `ab2l_<stamp>_s<num>`（两层）与 `ab3l_<stamp>_s<num>`（三层），不同场景不共享身份，也不与历史运行复用；
- seed 与 probe 使用同一 user/agent 但不同 dialog，身份信息同时写入 `memory_ab_state.json` 供审计；
- 结果 JSON 记录 `user_id / agent_id / seed_dialog_id / probe_dialog_id / enable_memory`。

### 4.3 评测方式

实际采用判别式字符串 hint 匹配（`judge_method=discriminative_hint_match`），未引入 LLM-as-Judge：

- 每条 Gold Fact 配 `expected_hint`，可拆成多个 term；一条 fact 命中要求所有 term 都出现在最终回答中；
- 支持 `expected_variants` 同义变体（如“无烟 / 非吸烟 / 禁烟”），任一变体全中即算命中；
- 回答中出现“没有记录 / 未检索到 / 无法确认”等不确定标记时，即使关键字恰好命中也判为 `used_wrongly`；
- 每个 fact 输出 `used_correctly / missing / used_wrongly` 三态，JSON 保留逐条 term 命中明细，便于人工复核。

取舍：可复现、零额外模型成本、可逐条审计；代价是对同义改写敏感，因此 fixture 显式提供变体，并保留逐场景原文供面试追问。

## 5. 指标

```text
fact_recall = 被正确使用的 Gold Facts / 总 Gold Facts
case_pass_rate = 该场景 probe 完全答对的场景数 / 场景数
mean_token_input / mean_token_output / mean_total_latency_ms
```

输出建议：

```json
{
  "created_at": "...",
  "scenario_counts": 30,
  "enable_memory_false": {
    "fact_recall": 0.0,
    "case_pass_rate": 0.0,
    "latency_ms": {...}
  },
  "enable_memory_true": {
    "fact_recall": 0.0,
    "case_pass_rate": 0.0,
    "latency_ms": {...}
  }
}
```

正式结果（`live_memory_comparison_20260818_105736.json`）：

| 指标 | 两层（enable_memory=False） | 三层（enable_memory=True） | 差值 |
| --- | --- | --- | --- |
| Fact Recall | 0.0968（6/62） | 0.6774（42/62） | +0.5806 |
| Case Pass Rate | 0.0（0/30） | 0.4667（14/30） | +0.4667 |
| missing rate | 0.9032 | 0.3226 | -0.5806 |
| used_wrongly_rate | 0.0 | 0.0 | 0.0 |

延迟与 token（真实 `/api/v1/completion`）：

- 三层 probe 平均延迟 `33574ms`，两层 `16527ms`；
- 三层总延迟均值 `37853ms`、两层 `13308ms`；三层首 chunk 均值 `10695ms`、两层 `16406ms`，说明三层延迟增量主要来自记忆写入等待，而非首 token 更慢；
- 两层 `11164 input / 54823 output / 90 calls`；三层 `13475 input / 46487 output / 90 calls`。

原始 JSON 归档：

```text
docs/eval/live/live_memory_comparison_YYYYMMDD_HHMMSS.json
```

新增脚本建议：

```text
src/backend/agentchat/benchmarks/live_memory_ab.py
```

## 6. 成本与冒烟（实际执行）

- 实际 seed 每条 2 轮，全量 30 条 × 2 组：两层 90 次调用、三层 90 次调用（seed 60 次 + probe 60 次）；
- 先执行 `--limit 2` 冒烟，确认接口、身份创建、检索轮询与评分正常后再扩到全量；
- 两层本身场景级全对数为 0，报告中保留逐场景明细，面试时如实说明场景分布；
- 冒烟与正式命令（从 `src/backend` 执行）：

  ```powershell
  & 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_memory_ab --limit 2 --wait-memory-timeout 15 --output-dir ..\..\docs\eval\live

  & 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_memory_ab --limit 30 --wait-memory-timeout 15 --output-dir ..\..\docs\eval\live
  ```

## 7. 面试口径约束

- 正式 JSON 已落盘，可以写“30 条真实跨会话场景，Fact Recall 从 0.0968 提升到 0.6774（+58.06 个百分点）”，不要写成“提升 600%”；
- Case Pass Rate 从 0.0 到 0.4667（14/30），说明只有三层能完整复述全部事实的场景有 14 条；
- 引用口径必须带“真实 `/api/v1/completion` 链路、每场景独立 user/agent、判别式 hint 评分”，并说明不是 LLM-as-Judge；
- 两层仍有 6/62 的 fact-level 命中，但没有一个场景全对，说明少量命中来自通用表述中的关键词巧合，不代表记忆恢复；面试以 Case Pass Rate 与逐条 JSON 证据一起解释；
- `live_memory_20260814_154252.json` 只证明“写入后可检索回”，不能单独作为“最终回答提升”证据。

## 8. 结果与限制

结果：

- 真实链路全量 30 条场景 / 62 个 Gold Facts 完成，退出码 0；
- 三层 Fact Recall `0.6774`，两层 `0.0968`；三层 Case Pass Rate `0.4667`，两层 `0.0`；
- 每场景独立 user/agent（60 组身份）落盘在 `docs/eval/live/memory_ab_state.json`，结果与逐条 term/verdict 明细在 `live_memory_comparison_20260818_105736.json`；
- `live_memory_comparison_20260818_104236.json`、`105122.json` 为开发冒烟/过程归档，正式结果以 `105736.json` 为准。

限制：

- `live_memory_ab.py` 在 probe 前用真实 `LiveMemoryAdapter` 轮询 `memory_client.search`（`--wait-memory-timeout 15`）：30 个三层场景中仅 `mem_ab_01` 返回 `ready=true`，其余 29 个 `ready=false`（`result_count=0`），但其中 13 个场景 probe 仍然答对。说明该 evidence 轮询目前不能作为“每条命中都来自检索”的逐条证据，可能原因是写入已落库但 embedding/索引可见性滞后、轮询查询与 completion 实际检索路径不一致，或 top-k 排序问题；
- 最终指标仍有效：两层与三层跑完全相同的剧本，probe 是全新会话且两层场景级全对数为 0，三层显著更高，差异可归因于启用长期记忆层；
- 如需把每条命中做成“检索 -> 注入 -> 回答”闭环证据，应把 completion 链路实际注入的 memory 上下文落盘，或修复 evidence 轮询与 completion 检索入口一致后重跑；
- 评测是判别式字符串匹配，同义表达覆盖依赖 fixture 中的 `expected_variants`；人工抽查可直接看 JSON 中 `scenarios[*].three_layer.fact_results`。
