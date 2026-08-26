# 简历四要点全量技术解析

> 编写日期：2026-08-21。
> 对象：简历中 AgentChat 项目的四个技术要点。
> 口径：只引用当前仓库源码与 `docs/eval/` 下可复现归档；简历旧数字若仓库内无对应档案，会明确标注“旧口径，无复现档案”。
> 总览文档：[AGENTCHAT_FULL_TECHNICAL_DOC.md](D:/实习记录/开源项目/AgentChat/docs/technical/AGENTCHAT_FULL_TECHNICAL_DOC.md)

## 一、多 Agent 协同架构

### 简历要点原文

> 多 Agent 协同架构：GeneralAgent 总调度 + AgentConfig 驱动，声明式组装 MCP/Skill/知识库工具；子 Agent 封装为 @tool 保留独立 ReAct 推理，实现协同决策与能力复用。

### 实现原理

`GeneralAgent` 是总调度入口，`AgentConfig` 是声明式配置模型，只声明 `mcp_ids / tool_ids / agent_skill_ids / knowledge_ids / llm_id / system_prompt / enable_multi_agent` 等字段。初始化时按固定顺序把 MCP Server、普通工具、Skill、知识库工具装配成 LangChain 工具集，再创建统一 ReAct Agent。

`enable_multi_agent=True` 时，`GeneralAgent` 会构造演示编排器。主 Agent 在收到消息后先做固定关键词路由；命中时调用子 Agent，否则走普通单 Agent 对话。每个 `SubAgent` 拥有独立模型、系统提示词、工具集和 `ReactAgent`，因此是完整推理单元，不是单次函数调用。

### 代码链路

1. `AgentConfig(**db_config)`：从数据库 Agent 配置创建声明式对象。
2. `GeneralAgent.init_agent()`：依次 `setup_mcp_agent_as_tools()`、`setup_tools()`、`setup_agent_skill_as_tools()`、`setup_knowledge_tool()`、`setup_language_model()`、`setup_react_agent()`。
3. `GeneralAgent.astream()`：通过 `_is_multi_agent_input()` 判断固定场景命中，命中后走 `_stream_multi_agent()`。
4. `MultiAgentOrchestrator.route()`：按 `SubAgent.keywords` 做包含匹配，可同时命中多个子 Agent。
5. `MultiAgentOrchestrator.run()`：发出 `agent_start / agent_plan / sub_agent_start / sub_agent_end / agent_end`，并透传子 Agent 内部事件，统一在末尾拼接回答。
6. `SubAgent.invoke()`：运行自己的 `ReactAgent.astream`，汇总响应文本、工具调用次数与内部事件。

关键文件：

- [general_agent.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/core/agents/general_agent.py)
- [orchestrator.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/core/agents/orchestrator.py)
- [react_agent.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/core/agents/react_agent.py)

### 关键设计

- 声明式能力组装：数据库中的工具、Skill、MCP 和知识库不写死在代码里，Agent 通过 ID 引用组装。
- 分层事件：主 Agent 与子 Agent 各自有 `agent_run_id`，子事件带 `parent_agent_run_id`，前端可以还原编排树。
- 结果聚合：多个子 Agent 命中时按展示名分段聚合，单场景测试可稳定断言。
- 默认保守：`enable_multi_agent` 默认 `False`，避免未经充分验证的编排进入普通对话。

### 量化成果（真实链路）

证据文件：[live_multi_agent_20260814_155110.json](D:/实习记录/开源项目/AgentChat/docs/eval/live/live_multi_agent_20260814_155110.json)

| 指标 | 值 |
| --- | --- |
| 固定业务任务数 | 5 |
| `pass_rate` | 1.0（5/5） |
| `route_match_rate` | 1.0（5/5） |
| `sub_agent_pair_count` | 5/5，启动/结束成对 |
| `sub_agent_tool_calls_total` | 5 |
| `tool_error_case_count` | 0 |
| 平均总延迟 | 12294.756 ms |
| 平均首 chunk | 6614.585 ms |

可表述为：“真实 `/api/v1/completion` 链路上 5 个固定场景全部通过，路由命中、子 Agent 启停事件、工具调用均符合预期。”

### 面试口径与限制

- 可以讲：`AgentConfig` 声明式组装、子 Agent 独立 ReAct、分层事件、真实 demo 路由已跑通。
- 不建议讲“子 Agent 封装为 @tool”：代码中是 Skill/MCP 被封装为 `@tool`，子 Agent 是独立 ReAct 链。
- 不建议讲“自然语言智能编排”：当前实现是固定关键词包含匹配，不能处理任意自然语言到子 Agent 的自动规划。

## 二、Context Engineering

### 简历要点原文

> Context Engineering：短期滑动窗口（Token 阈值截断）+ 中期增量摘要（LLM 压缩）+ 长期向量记忆（ChromaDB 跨会话召回）。写入时 LLM 提取事实去重合并，实测关键信息召回率提升 35%，上下文稳定在 4000 tokens 以内。

### 实现原理

对话上下文按三层组织：

1. 短期层：从 DB 读取 `summary_last_time` 之后的新消息，作为多轮提示词的 `short_history`。
2. 中期层：`DialogService.update_dialog_summary` 对超出 token 预算的旧对话做 LLM 增量摘要，并通过 `summary_last_time` 形成水位线。
3. 长期层：`AsyncMemory.add()` 在对话结束后抽取事实写入 Chroma；下一次对话按 `user_id / agent_id` 作用域召回并注入 SystemMessage。

“写入时 LLM 提取事实去重合并”落在 `AsyncMemory._add_to_vector_store`：先让 LLM 抽 `facts`，再对每条事实做相似记忆检索，随后让 LLM 输出 `ADD/UPDATE/DELETE/NONE`，最后执行向量写与历史变更。

### 代码链路

- 上下文构建：[completion.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/api/v1/completion.py)
  - `HistoryService.get_short_term_messages`：短期消息。
  - `DialogService.get_dialog_history_summary`：中期摘要。
  - `memory_client.search`：长期记忆召回。
  - `build_completion_system_prompt`：注入摘要与记忆。
- 短期历史：[history.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/api/services/history.py)
- 增量摘要：[dialog.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/api/services/dialog.py)
- 共享 token 预算：[message_budget.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/utils/message_budget.py)
- 长期记忆：[memory/client.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/memory/client.py)
- 记忆向量存储：[memory/vector_stores](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/memory/vector_stores)

### 关键设计

- 增量摘要不重复总结：用 `summary_last_time` 只对新增消息做摘要，旧摘要作为“已有总结”输入。
- 生产与测试共用同一分片逻辑：`DialogService.split_messages_by_token` 与 offline benchmark 都调用 `split_messages_by_token`。
- 多对至少保留最新一对：防止 token 超限时模型拿到空上下文。
- 精确查重：向量库 payload 内保存 hash 与原文，`_find_existing_memory` 同时比较 hash 和 content，重复写入复用旧 id。
- 写历史失败不阻断向量写：`_write_history` 捕获异常并告警。

### 量化成果

#### 1. 两层 vs 三层真实 A/B（P5.10）

证据文件：[live_memory_comparison_20260818_105736.json](D:/实习记录/开源项目/AgentChat/docs/eval/live/live_memory_comparison_20260818_105736.json)

200 字说明：两层组 `enable_memory=False`（短期历史 + 中期摘要），三层组 `enable_memory=True`（再叠加长期向量记忆）。每场景 2 轮 seed 埋点 + 全新 probe 会话，两层与三层跑同一剧本，每场景每 arm 独立 user/agent，共 60 组身份；评分方式是判别式 hint 匹配，不是 LLM-as-Judge。

| 指标 | 两层 | 三层 | 差值 |
| --- | --- | --- | --- |
| 场景数 | 30 | 30 | - |
| Gold Facts | 62 | 62 | - |
| Fact Recall | 0.0968（6/62） | 0.6774（42/62） | +0.5806 |
| Case Pass Rate | 0.0（0/30） | 0.4667（14/30） | +0.4667 |
| missing rate | 0.9032 | 0.3226 | -0.5806 |

该对比证明了“是否启用长期记忆层”对全新 probe 会话的回答差异，是当前 Context Engineering 最有分量的答案级证据。

#### 2. 记忆去重（P3 离线）

证据文件：[memory_dedup_p3.json](D:/实习记录/开源项目/AgentChat/docs/eval/offline/memory_dedup_p3.json)

| 指标 | 值 |
| --- | --- |
| add attempts | 60 |
| inserted | 20 |
| duplicates skipped | 40 |
| skip rate | 0.6667 |
| 历史表写失败模拟 | 1 次，向量写存活 |

#### 3. Token 预算（P3 离线）

证据文件：[token_budget_p3.json](D:/实习记录/开源项目/AgentChat/docs/eval/offline/token_budget_p3.json)

| 指标 | 值 |
| --- | --- |
| 样本 | 40 对消息 / 8560 tokens |
| cutoff 1000 | 保持 4 对 / 840 tokens |
| cutoff 3000 | 保持 14 对 / 2970 tokens |
| cutoff 5000 | 保持 23 对 / 5000 tokens |
| 口径 | 与生产 `DialogService` 共用 `split_messages_by_token` |

### 面试口径与限制

- 可以讲“两层 vs 三层真实 A/B”与去重、摘要预算的离线结果，口径要带“真实 `/api/v1/completion`、独立身份、判别式匹配”。
- 简历旧口径“提升 35%”“稳定 4000 tokens”在仓库内没有对应复现档案，不能作为事实主动陈述。
- 限制：Memory A/B 的证据轮询只有 1/30 场景 `ready=true`，因此当前不能逐条证明“检索 -> 注入 -> 回答”因果；结论只能落在“同一剧本下启用长期记忆层后最终回答命中显著更高”。

## 三、RAG 检索

### 简历要点原文

> RAG 检索：Query 改写 → Milvus/ES 混合检索 → Rerank 精排 → 融合输出，RagHandler 统一封装暴露为 Agent 工具。在线评测 Recall@5 从 0.72 提升至 0.89，首条命中率从 0.45 提升至 0.67；支持 8 种文档格式。

### 实现原理

文档侧先由 `DocParser` 按 8 类格式解析为文本，按 `chunk_size / overlap_size` 分块并可选生成摘要，再写入向量库。查询侧由 `RagHandler` 统一执行：

```text
通过 Agent 工具 retrival_knowledge
  → RagHandler.retrieve_ranked_documents
  → Query Rewrite
  → 向量/ES 多路召回（content 或 summary 字段）
  → merge_documents_by_score 去重排序
  → Rerank 精排
  → min_score / rerank_threshold 过滤
  → top_k 拼接原文
```

### 代码链路

- 文档解析：[parser.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rag/parser.py)
- 查询处理：[handler.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rag/handler.py)
- 多路召回：[retrieval.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rag/retrieval.py)
- 结果合并：[result_merger.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rag/result_merger.py)
- Rerank：[rerank.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rag/rerank.py)
- Query Rewrite：[query_write.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rewrite/query_write.py)
- 向量/ES 客户端：[services/rag/vector_stores](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rag/vector_stores)、[es_client.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rag/es_client.py)

### 关键设计

- 生产 Agent 工具直接调用 `RagHandler`，评测脚本复用同一组件，不另写 mock 检索器。
- Query Rewrite 返回多个候选 query，原始 query 永远保留并去重。
- 支持 `content`、`summary`、`content+summary` 三种召回字段；summary 召回不足时可回退 content。
- Rerank 失败时回退原始检索分数，保证工具可继续回答。
- `min_score` 与 `rerank_threshold` 都可配置；`gte-rerank-v2` 分数口径低，正式 A/B 使用 `min_score=0.0`。

### 量化成果（真实 A/B，P5.9）

证据文件：[live_rag_comparison_20260814_182215.json](D:/实习记录/开源项目/AgentChat/docs/eval/live/live_rag_comparison_20260814_182215.json)

评测范围：21 个自建业务文档 / 102 chunk / 50 条 ground truth；同一 Chroma collection 与 ground truth；baseline 为原始 query + content 向量召回，优化组为 Query Rewrite + Rerank + 阈值修正。

全量 50 条：

| 指标 | Baseline | Optimized | 差异 |
| --- | --- | --- | --- |
| Recall@5 | 0.8467 | 0.9600 | +0.1133 |
| MRR@5 | 0.6967 | 0.7983 | +0.1016 |
| Hit@1 | 0.88 | 0.98 | +0.10 |
| 证据命中率 | 0.85 | 0.97 | +0.12 |

hard 17 条：

| 指标 | Baseline | Optimized | 差异 |
| --- | --- | --- | --- |
| Recall@5 | 0.7843 | 0.9412 | +0.1569 |
| MRR@5 | 0.7255 | 0.7843 | +0.0588 |
| Hit@1 | 0.8824 | 1.00 | +0.1176 |
| 证据命中率 | 0.7941 | 0.9706 | +0.1765 |

组件可用性：`query_rewrite` 50/50；`rerank` 48/50，2 条因网络瞬时错误按生产 fallback 降级。

离线回归（非真实链路）：[rag_p3_before_after.json](D:/实习记录/开源项目/AgentChat/docs/eval/offline/rag_p3_before_after.json)，9 条固定 query，`mean_mrr` 从 0.9259 到 1.0。

### 面试口径与限制

- 可以讲 P5.9 的真实 A/B 绝对值，但要带范围与组合口径：“50 条自建真实业务 query、同一知识库、差异来自 Query Rewrite + Rerank + 阈值修正组合，Rerank 48/50 可用。”
- 无法归因到“Rerank 单独提升”，因为未做单组件消融。
- “Recall@5 从 0.72 到 0.89”“首条命中 0.45 到 0.67”是简历旧口径，仓库内没有复现档案，不能主动讲。
- 优化组平均延迟约 11.4s，基线约 1.06s，因此本组数字不是性能优化证据。
- “Milvus/ES 混合检索”有代码路径，但正式 A/B 环境 `enable_elasticsearch=False`、向量库为 Chroma；叙述时以“支持配置的混合检索 + 当前实测 Chroma 链路”更严谨。

## 四、可观测与流控

### 简历要点原文

> 可观测与流控：中间件发射调用事件 + StreamWriter 透传链路，解决 LLM 工具调用“黑盒”；基于 Starlette 实现可中断响应，客户端断开时 500ms 内自动终止推理。

### 实现原理

`EmitEventAgentMiddleware` 包装 LangChain 模型调用与工具调用：

- 模型调用结束写 `MODEL_CALL`，标记当前增量是否属于最终回答。
- 工具调用开始写 `START`，结束写 `END`，失败写 `ERROR`，事件带友好名称、类型与耗时。
- 工具错误不直接抛给用户，而是返回 `ToolMessage` 字符串，让模型感知后继续处理。

流式侧，`GeneralAgent.astream` 用 `CancellableAsyncStream` 包住生产者，前端收到工具事件后再收到最终文本。`WatchedStreamingResponse` 在收到 `http.disconnect` 时调用 `stop_streaming_callback()`，立即取消 producer。

### 代码链路

- 中间件：[general_agent.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/core/agents/general_agent.py)
- SSE 响应与断流：[streaming.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/api/responses/streaming.py)
- 可取消流：[cancellable_stream.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/utils/cancellable_stream.py)
- 事件结构与 Trace ID：[events.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/utils/events.py)
- Trace 中间件：[trace_id_middleware.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/middleware/trace_id_middleware.py)

### 关键设计

- 一条 SSE 事件最少包含 `type / event_id / timestamp / trace_id / data`，日志与前端可关联同一次请求。
- 工具调用前的半成品文本先进入 pending 队列，模型真正开始最终回答后才透传，前端不会展示“思考中的碎片”。
- 断开后的收尾使用 `CancelScope(shield=True)`：取消推理、记录 `stream_cancel`、写历史、写摘要仍会执行。
- `CancellableAsyncStream.summary()` 暴露 `cancel_to_terminate_ms` 和 `total_duration_ms`，这是断流验收的直接指标。

### 量化成果（真实 SSE 断流，P5.5）

证据文件：[live_cancel_20260814_151430.json](D:/实习记录/开源项目/AgentChat/docs/eval/live/live_cancel_20260814_151430.json)

| 指标 | 值 |
| --- | --- |
| 测试轮数 | 5 |
| `pass_rate` | 1.0 |
| 阈值要求 | 所有 `cancel_to_terminate_ms <= 500ms` |
| mean | 0.485 ms |
| p90 | 0.933 ms |
| max | 1.356 ms |

工具事件链路也有真实链路证据：多 Agent 评测中每个固定任务都产生 `START/END` 工具事件与完整生命周期事件，`event_type_counts` 逐 case 落盘，见 [live_multi_agent_20260814_155110.json](D:/实习记录/开源项目/AgentChat/docs/eval/live/live_multi_agent_20260814_155110.json)。

### 面试口径与限制

- 可以讲“真实 SSE 链路上 5/5 在 500ms 内终止，指标是服务端从断开到推理停止的 `cancel_to_terminate_ms`”，但测试环境是本机服务 + Docker 依赖，不是公网生产网络。
- 可以讲工具事件不再是黑盒：模型调用、工具开始/结束/失败、耗时和 Trace ID 都进入统一事件结构。
- 不建议把“500ms 内”表述为全生产环境下稳定达成；更准确是“本机真实服务链路实测 5 轮全部达标”。
- 断流评测是功能级验收，不是压测级吞吐数据，不应引申为并发能力。

## 总结

四个要点中，最真实、可直接支撑简历的量化为：

1. 多 Agent：真实链路 5 个固定场景全部通过，路由与子 Agent 生命周期成对完成。
2. Context Engineering：两层/三层 A/B 中 Fact Recall `0.0968 -> 0.6774`，Case Pass Rate `0 -> 0.4667`；去重 skip rate `0.6667`。
3. RAG：50 条真实 query 中 Recall@5 `0.8467 -> 0.96`，Hit@1 `0.88 -> 0.98`，hard 子集提升更明显。
4. 可观测与流控：真实 SSE 断流 5/5 达标，`cancel_to_terminate_ms` max 1.356ms。

所有“提升”都需要带评测范围、组件组合与限制；旧简历中的 35%、4000 tokens、0.72→0.89 等口径在仓库内没有复现档案，应删除或更换为以上可复现数字。

---

# 附：专业简历项目介绍（可直接替换简历中的该项目）

> 用途：以下段落可直接替换简历“第二个项目”的介绍，数字均有 `docs/eval/live/` 原始 JSON 支撑；面试被追问口径时回到本文件全文。

## AgentChat：多智能体企业知识对话平台

**职责**：后端核心开发，负责 Agent 装配、对话上下文、RAG 检索、可观测与流控，并搭建离线 + 真实链路评测体系。

**技术栈**：FastAPI / Starlette / SSE、LangChain 1.x ReAct、SQLModel / MySQL、Redis、ChromaDB（Pymilvus / Elasticsearch 可配置）、Query Rewrite、Rerank、Vue 3 / Element Plus、Docker Compose

### 项目简介

面向企业内部场景的多智能体对话平台：基于 FastAPI 与 LangChain 提供真实对话接口，通过 GeneralAgent 声明式组装 MCP、Skill、知识库能力，支持多轮上下文压缩、跨会话向量记忆、多路 RAG 检索与 SSE 流式可观测；同步建立可复现的评测体系，产出可面试的真实链路证据。

### 核心工作与量化成果

1. 多 Agent 协同：实现 `GeneralAgent + AgentConfig` 声明式能力装配，子 Agent 保留独立 ReAct 推理，编排层输出分层事件；真实 `/api/v1/completion` 链路 5 个固定业务场景全部通过，`pass_rate=1.0`、`route_match_rate=1.0`、工具错误 0。
2. Context Engineering：构建“短期滑动窗口 + 中期增量摘要 + 长期向量记忆”三层上下文，由 LLM 抽取事实、去重合并后写入 Chroma；30 场景 / 62 Gold Facts 的跨会话真实 A/B 中，Fact Recall 从 `0.0968` 提升到 `0.6774`（+0.5806），Case Pass Rate 从 `0` 提升到 `0.4667`；记忆离线幂等写 60 次中 40 次自动去重（skip rate `0.6667`）。
3. RAG 检索：实现 Query Rewrite → 多路召回 → 去重融合 → Rerank 精排 → 阈值过滤，统一封装为 Agent 工具；50 条真实业务 query / 102 chunk 的 A/B 中，全量 Recall@5 从 `0.8467` 到 `0.96`、MRR@5 从 `0.6967` 到 `0.7983`、Hit@1 从 `0.88` 到 `0.98`；hard 17 条 Recall@5 从 `0.7843` 到 `0.9412`、Hit@1 从 `0.8824` 到 `1.0`；Rerank 可用 48/50。
4. 可观测与流控：中间件把模型调用与工具生命周期统一写成事件流并携带 Trace ID；SSE 断流真实链路 5/5 轮在 500ms 内终止推理，`cancel_to_terminate_ms` 均值 `0.485ms`、p90 `0.933ms`、max `1.356ms`；Completion 端到端 15 条 `case_ok_rate=1.0`、无工具错误。

### 简历短版

基于 FastAPI/LangChain 的企业多智能体对话平台。多 Agent 编排真实链路 5/5 通过；跨会话记忆 A/B 中 Fact Recall 提升至 `0.6774`；RAG 真实 A/B 全量 Recall@5 提升至 `0.96`、hard Hit@1 提升至 `1.0`；SSE 断流 5/5 轮在 500ms 内终止推理。
