# Memory 两层 vs 三层真实对比设计

> 状态：upcoming，未开始执行。
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

## 3. 测试形态：对话级 Fact Recall

构造 30 条多轮业务场景，每条 6-10 轮：

```text
早期轮次：自然对话中埋入 3-5 条 Gold Facts
中间轮次：干扰信息、闲聊、主题偏移
末期问题：必须依赖早期轮次的事实才能正确回答
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

## 4. 执行方式

### 4.1 两组跑同一套剧本

- 两层组：创建 `enable_memory=False` 的测试 Agent，独立 `user_id`
- 三层组：创建 `enable_memory=True` 的测试 Agent，独立 `user_id`
- 同一套场景脚本，逐轮调真实 `/api/v1/completion`
- 切忌：先跑对照组，然后用同一个 user/agent 直接跑实验组，否则早期记忆会污染实验组

### 4.2 独立身份防污染

- 建议按 `user_id + agent_id` 完全隔离；
- 每组开始前确认长期记忆集合为空（三层组），或使用独立 user；
- 结果 JSON 记录 `user_id / agent_id / dialog_id / enable_memory`。

### 4.3 评测方式

推荐两种，任选其一或交叉验证：

1. 判别式提取：把最终回答与 `query + gold_fact` 交给评测 prompt，判断每一条 Gold Fact 是否被回答正确使用；
2. LLM-as-Judge：用结构化输出对最终回答打分，只接受 `used_correctly / missing / used_wrongly`。

不建议只看检索命中，因为最终面试口径是“答案差异”。

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

原始 JSON 归档：

```text
docs/eval/live/live_memory_comparison_YYYYMMDD_HHMMSS.json
```

新增脚本建议：

```text
src/backend/agentchat/benchmarks/live_memory_ab.py
```

## 6. 成本与冒烟

- 30 条 × 2 组 × 6-10 轮真实 LLM 调用，延迟和 token 成本都较高；
- 先跑 10-15 条冒烟，确认两组差异稳定后再扩到 30 条；
- 如果某些场景两层已经能答对，保留它们在报告中，面试只说“哪些场景只有三层能答对”。

## 7. 面试口径约束

- 只有 `live_memory_comparison_*.json` 落盘后，才能写“三层记忆提升 Fact Recall”；
- 建议说法：“30 条真实多轮场景，Fact Recall 从 0.35 到 0.73”，提升用百分点表述，不写“提升 108%”；
- 如果差异不显著，就说明场景分布和两层已能覆盖的部分；
- `live_memory_20260814_154252.json` 只能证明“写入后可检索回”，不能引用为“最终回答提升”。
