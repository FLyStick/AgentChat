# P4 面试材料：AgentChat 简历口径与证据

> 本文用于把简历中的 AgentChat 项目描述、代码路径、评测结果和面试回答口径对齐。
> 原则：面试官看不到源码，但任何被追问的数字和技术表述都必须有代码或评测文件支撑。

## 1. 总结论

当前项目可以支撑的面试主线：

- 面向企业级多 Agent 平台的基础工程能力：用户、Agent、工具、LLM、MCP、知识库、会话和 Skill 管理都有真实 API 与代码路径；
- 对话主链路、三层记忆、可观测事件、断流取消：有真实实现路径；
- 多 Agent 协作、RAG 优化、记忆去重、token 预算：有可复现的离线评测数据。

当前不建议在面试中主动陈述的表述：

- “子 Agent 封装为 `@tool`”：代码中主 Agent 将 Skill、MCP 等能力包装为工具，子 Agent 是独立 ReAct 链；
- “自然语言任务编排”：当前多 Agent 是固定关键词 demo 路由，不是通用 NL 图编排；
- “召回率提升 35%”“Recall@5 0.72→0.89”“首条命中 0.45→0.67”：仓库内没有对应复现档案，不能作为当前事实陈述；
- “500ms 内终止推理”：离线压测 5/5 通过，真实 LLM 链路尚未补测，只能说“实现了可中断响应，离线压测通过”。

## 2. 简历点与代码/评测位置对照

| 简历表述 | 代码/评测位置 | 面试口径 |
| --- | --- | --- |
| 多 Agent 协同架构：GeneralAgent 总调度 + AgentConfig 驱动 | `src/backend/agentchat/core/agents/general_agent.py`、`orchestrator.py` | GeneralAgent 按 AgentConfig 声明式组装普通工具、Skill 工具、MCP 工具和知识库工具；`enable_multi_agent=True` 时才构造 demo orchestrator |
| 子 Agent 封装为 @tool 保留独立 ReAct 推理 | `orchestrator.py` | 子 Agent 不是 `@tool`，而是各自持有独立 ReAct 链；主 Agent 按固定关键词路由并发出 `sub_agent_start/sub_agent_end` 分层事件 |
| Context Engineering：短期窗口 + 中期摘要 + 长期向量记忆 | `api/v1/completion.py`、`api/services/history.py`、`api/services/dialog.py`、`services/memory/client.py` | 对话链路按短期历史、历史摘要、长记忆拼接上下文；`agent_config.enable_memory=True` 时启用跨会话长记忆 |
| 写入时 LLM 提取事实去重合并 | `services/memory/client.py`、`services/memory/prompts.py` | 记忆写入包含事实提取、精确 hash 查重和更新跳过；离线评测 `docs/eval/memory_dedup_p3.json`：60 attempts / 20 inserted / 40 skipped |
| RAG：Query 改写 → 混合检索 → Rerank → 融合输出 | `services/rag/handler.py`、`services/rag/retrieval.py`、`services/rag/rerank.py`、`services/rewrite/query_write.py` | RagHandler 统一封装暴露为 Agent 工具；支持 `content`、`summary`、`content+summary` 字段和 `rerank_threshold` |
| RAG 支持 8 种文档格式 | `services/rag/parser.py` | 覆盖 md、txt、docx、pdf、pptx、image、excel 等 8 类解析分支 |
| 可观测：中间件发射调用事件 + StreamWriter 透传链路 | `core/agents/general_agent.py`、`api/responses/` | 工具调用、耗时、失败原因写入统一流式事件；Trace ID 贯穿请求 |
| 断流：客户端断开 500ms 内终止推理 | `api/responses/streaming.py`、`src/backend/cancel_result.json` | `WatchedStreamingResponse` 监听断开并触发取消；离线压测 5/5 通过，`cancel_to_terminate` 均值约 0.146ms |
| 私有文档/API/Skill 注册为工具 | `core/agents/general_agent.py`、`api/v1/agent_skill.py`、`register_mcp*.py` | Skill 和 MCP 服务被包装为 `BaseTool`；OpenAPI 信息通过注册 MCP 流程生成服务 |

## 3. 当前评测数字

面试中可以讲的数字只使用以下来源：

- RAG 固定基准：`docs/eval/rag_p3_before_after.json`，9 条固定 query，`mean_mrr` 从 `0.9259` 提升到 `1.0`；
- 记忆去重：`docs/eval/memory_dedup_p3.json`，60 次写入中 20 次插入、40 次跳过重复；
- 长对话 token 预算：`docs/eval/token_budget_p3.json`，40 对消息 / 8560 tokens；
- 断流压测：`src/backend/cancel_result.json`，5/5 通过，阈值 500ms；
- P3.5 全量测试：`68 passed`。

口径说明：以上 RAG 和断流数据都是固定 fixture、mock 或离线模拟，不是线上真实模型链路指标的替代品。

## 4. 面试追问口径

### Q1：这个多 Agent 架构是真实触发，还是纸上谈兵？

真实触发，但有边界。`GeneralAgent.astream()` 先判断输入是否命中固定 demo 关键词，命中后走 `orchestrator` 分支；每个子 Agent 使用独立 ReAct 链和独立工具集，并输出带 `agent_run_id`、`parent_agent_run_id` 的分层事件。默认 `enable_multi_agent=False`，避免未验证场景进入生产对话。

### Q2：子 Agent 和普通工具的区别是什么？

普通工具是单次函数调用，子 Agent 是完整推理单元：有自己的 SystemMessage、ReAct 循环、工具集和结果聚合逻辑。实现上子 Agent 不是 `@tool`，这一点在简历里应改为“子 Agent 独立 ReAct 链”。

### Q3：RAG 检索为什么这样设计？

Query 改写解决提问与知识库措辞不一致；混合检索同时覆盖向量语义和关键词；Rerank 精排后统一去重合并；`content+summary` 双字段检索解决分块摘要与原文命中不一致的问题。每层都有可回退路径：ES 未开启时只走向量库，summary 召回不足时回退 content。

### Q4：Context Engineering 具体做了什么？

三层记忆：短期历史按 token 窗口截断，中期历史用 LLM 增量摘要，长期记忆用向量库按用户/agent 边界检索。写入时 LLM 从对话抽取事实，精确 hash 查重，避免重复写入和无效更新。

### Q5：断流取消的实现和局限？

流式响应由 `WatchedStreamingResponse` 监听客户端断开，触发 `request_cancel()`，停止继续产出并记录终止时长。仓库内压测是离线模拟；真实模型调用中的断流表现需要 P4 之后的服务实测，不能直接包装成生产指标。

### Q6：整体系统如何证明“可运维”？

有独立用户、Agent、工具、LLM、MCP、知识库、会话管理模块；有 JWT 认证、对话归属校验、API 统一响应；RAG 和记忆有 benchmark CLI；CI 有 pytest 入口。部署文档见 `docs/delivery/DEPLOYMENT.md`。

## 5. 简历修订建议

- “子 Agent 封装为 @tool 保留独立 ReAct 推理”改为“子 Agent 各自持有独立 ReAct 链，主 Agent 分层编排并透传执行事件”；
- “自然语言任务编排”改为“AgentConfig 驱动的声明式工具组装 + 固定场景多 Agent 编排”；
- “实测关键信息召回率提升 35%”改为“记忆写入去重：60 次写入中 20 次插入、40 次跳过”，或补真实链路对比后再写；
- RAG 数字改用仓库可复现口径：“9 条固定 query 上 mean_mrr 0.9259 → 1.0”；
- “500ms 内自动终止推理”改为“实现客户端断开可中断响应，离线压测 5/5 通过”。

