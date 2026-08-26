# AgentChat 全量技术文档

> 面向对象：以本仓库实现为基准的新成员、面试准备、后续功能扩展。
> 编写日期：2026-08-21。
> 文档原则：所有架构和量化描述均以源码与 `docs/eval/` 下已落盘的评测 JSON 为准；README 中的宣传性数字不直接作为事实。

## 1. 项目定位

AgentChat 是一个前后端分离的 LLM 对话与 Agent 平台。后端以 FastAPI + LangChain 1.x 为核心，提供用户、Agent、工具、MCP、Skill、知识库、会话、记忆、用量统计等真实 API；前端使用 Vue 3 + Element Plus 提供对话工作台和管理界面。

本仓库进一步为该项目补齐了四个工程方向：

1. 多 Agent 协同架构：`GeneralAgent` 总调度，`AgentConfig` 声明式驱动能力组装。
2. Context Engineering：短期滑动窗口、中期增量摘要、长期向量记忆三层上下文。
3. RAG 检索：Query 改写、多路召回、Rerank 精排、阈值过滤与统一工具封装。
4. 可观测与流控：中间件事件流、Trace ID、客户端断流取消。

对应的简历深度解析见 [RESUME_FOUR_POINTS_DEEP_DIVE.md](D:/实习记录/开源项目/AgentChat/docs/technical/RESUME_FOUR_POINTS_DEEP_DIVE.md)。

## 2. 技术栈与运行环境

后端（`src/backend/pyproject.toml`）：

- 语言与框架：Python 3.12+、FastAPI 0.121、SQLModel、Pydantic v1 兼容配置。
- Agent 生态：LangChain 1.2.15、LangGraph Runtime、LangChain Agent/Tool 中间件。
- 数据与检索：MySQL、Redis、ChromaDB 1.3.4、Pymilvus、Elasticsearch 8 客户端。
- 文档解析：pypandoc、pymupdf4llm、pdf2docx、requests-html、Selenium 等。
- 外部模型：DashScope Compatible Mode、DeepSeek、MaaS rerank 路由；Embedding 与 Rerank 独立配置。

前端：Vue 3.4+、Element Plus、Pinia、Vite 5、TypeScript。

本机真实链路运行口径：conda `agentchat` 环境执行后端，Docker Compose 只启动 MySQL、Redis、MinIO；P5 系列评测均记录在后端 `127.0.0.1:7860` 上产生的结果，见 [benchmarks/README.md](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/benchmarks/README.md)。

## 3. 仓库结构与模块边界

```text
AgentChat/
├─ docker/                      # MySQL、Redis、MinIO compose 与镜像
├─ scripts/                     # 启动辅助脚本
├─ src/backend/agentchat/
│  ├─ api/                      # FastAPI 路由层
│  │  ├─ v1/                    # 业务接口：completion/dialog/agent/tool/knowledge/mcp...
│  │  ├─ responses/             # SSE 响应与断流监听
│  │  └─ services/              # 业务服务（历史、摘要、Agent、知识库）
│  ├─ core/agents/              # GeneralAgent、ReactAgent、SubAgent/Orchestrator
│  ├─ services/                 # RAG、Memory、Redis、Sandbox、Storage、Workspace
│  ├─ database/                 # SQLModel 表模型与 DAO
│  ├─ benchmarks/               # 离线与真实链路评测 CLI
│  ├─ config.yaml.example       # 完整配置模板
│  └─ main.py                   # 应用工厂与中间件
├─ src/frontend/                # Vue 3 前端
└─ docs/                        # 部署、API、评测、交付材料
```

模块边界可以概括为：

- 路由层只负责 HTTP 协议、JWT 用户身份、参数校验和 SSE 返回。
- `api/services` 负责会话归属校验、历史落库、摘要生成等事务性业务。
- `core/agents` 负责 Agent 生命周期：初始化、工具装配、ReAct 执行、多 Agent 路由。
- `services/rag` 与 `services/memory` 分别封装检索与记忆，不直接依赖 HTTP 路由。
- `database` 统一管理 SQLAlchemy/SQLModel 表模型；评测脚本只通过公开模块复用生产逻辑。

## 4. 总体调用链路

一次普通对话请求的核心路径：

```text
POST /api/v1/completion (SSE)
  → get_login_user 身份校验
  → DialogService.get_agent_by_dialog_id 取出 Agent 配置
  → AgentConfig(**db_config) 构建声明式配置
  → GeneralAgent.init_agent()
      ├─ MCP 服务封装为工具
      ├─ DB 工具加载（内置/OpenAPI 自定义）
      ├─ Skill Agent 封装为工具
      ├─ 知识库 retrival_knowledge 工具
      └─ ReAct Agent 创建（含事件中间件）
  → 读取短期历史 + 中期摘要
  → enable_memory=True 时基于 Chroma 长期记忆检索
  → 拼接 SystemMessage + short_history + HumanMessage
  → WatchedStreamingResponse 流式返回
  → finally: 结束流、写记忆、写历史、增量总结
```

关键代码：[completion.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/api/v1/completion.py)、[general_agent.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/core/agents/general_agent.py)、[streaming.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/api/responses/streaming.py)。

## 5. Agent 系统

### 5.1 AgentConfig

`AgentConfig` 是 Agent 的唯一声明式入口，字段包括：

- 身份边界：`user_id`、`agent_id`、`name`。
- 模型：`llm_id`。
- 能力引用：`mcp_ids`、`knowledge_ids`、`tool_ids`、`agent_skill_ids`。
- 行为：`system_prompt`、`enable_memory`、`enable_multi_agent`。

代码位置：[general_agent.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/core/agents/general_agent.py)。

### 5.2 能力装配

`GeneralAgent.init_agent()` 按固定顺序装配：

1. `setup_mcp_agent_as_tools()`：把 MCP Server 包装成单工具 `call_mcp_agent(query)`，主 Agent 通过调用该工具触发完整 MCP Agent 链。
2. `setup_tools()`：数据库工具按 `is_user_defined` 分流；内置工具走 `AgentToolsWithName` 注册表，用户自定义工具通过 `OpenAPIToolAdapter` 转换为 `StructuredTool`。
3. `setup_agent_skill_as_tools()`：每个 Skill 创建 `@tool` 包装，调用 `SkillAgent` 执行。
4. `setup_knowledge_tool()`：绑定知识库时注册 `retrival_knowledge` 工具，内部调用 `RagHandler.retrieve_ranked_documents`。
5. `setup_language_model()`：按 `llm_id` 或默认对话模型初始化。
6. `setup_react_agent()`：使用 LangChain `create_agent` 创建 ReAct Agent，统一注入工具与中间件。

所有工具写入 `tool_metadata_map`，中间件可以把它解析为“工具 / MCP / Skill”的展示名与友好名称。

### 5.3 ReAct 执行与事件

`astream()` 使用 `react_agent.astream(..., stream_mode=["messages", "custom"])`：

- `custom` 事件来自 `EmitEventAgentMiddleware`：`MODEL_CALL`、工具 `START/END/ERROR`。
- `messages` 流只负责最终回答文本；模型调用工具前的中间文本被暂存，确认进入最终回答后才透传，避免前端展示半成品。
- 事件统一用 `build_stream_event()` 包装，带 `event_id`、`timestamp`、`trace_id`。

### 5.4 多 Agent 编排

`enable_multi_agent=True` 时构造 `build_demo_orchestrator`，由 `MultiAgentOrchestrator` 实行固定关键词路由：

- 制度 Agent：请假、报销、加班等。
- 酒店 Agent：入住、退房、Wi-Fi、早餐等。
- 项目 Agent：启动命令、RAG 链路、部署等。

每个 `SubAgent` 保存独立模型、系统提示词、工具集，并在 `__post_init__` 中构造自己的 `ReactAgent`。主 Agent 发出 `agent_start / agent_plan / sub_agent_start / sub_agent_end / agent_end`，子 Agent 内部事件以 `parent_agent_run_id / agent_run_id` 透传。

重要边界：该编排器是固定关键词 demo 路由，不是通用自然语言图编排；子 Agent 是独立 ReAct 链，不是 `@tool` 封装的单次函数调用。代码位置：[orchestrator.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/core/agents/orchestrator.py)。

## 6. 对话主链路

### 6.1 请求处理

`completion()` 的核心顺序：

1. 通过 `dialog_id` 查询 Agent 配置并转为 `AgentConfig`。
2. 写入 `user_id` / `agent_name` 上下文，供 usage 统计与 Trace 使用。
3. `build_completion_user_input` 合并文件 URL 与文本输入。
4. 需要知识库时在系统提示词中追加工具约束，要求先调用 `retrival_knowledge` 且只能依据原文回答。
5. 读取短期历史 `get_short_term_messages` 与中期摘要 `get_dialog_history_summary`。
6. `enable_memory=True` 时调用 `memory_client.search`，把长期记忆文本注入系统提示词。
7. 组装 `[SystemMessage, *short_history, HumanMessage]` 交给 `GeneralAgent.astream`。

### 6.2 落库与收尾

`finally` 块使用 `anyio.CancelScope(shield=True)` 保证客户端断开后仍能执行：

- 若流被取消，记录 `stream_cancel` 事件。
- `enable_memory=True` 时写入用户与助手最新一轮消息。
- 保存助手回复、事件列表、token usage。
- `DialogService.update_dialog_summary` 做增量摘要并推进 `summary_last_time`。

用户消息在流开始前先落库，防止断流导致用户输入丢失。

## 7. Context Engineering

### 7.1 三层结构

| 层 | 实现 | 代码 |
| --- | --- | --- |
| 短期 | 按 `summary_last_time` 读取最近消息，进入多轮提示词 | [history.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/api/services/history.py) |
| 中期 | LLM 增量摘要，按 token 预算切分新消息 | [dialog.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/api/services/dialog.py) |
| 长期 | Chroma 向量库按 user/agent 作用域召回事实 | [memory/client.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/memory/client.py) |

### 7.2 摘要预算

`utils/message_budget.py` 是生产摘要与离线 benchmark 共用的 token 切分逻辑：

- 按 user/assistant 两两成对，从新到旧累计。
- 超过 cutoff 的旧对话交给摘要；多对时至少保留最新一对，避免空上下文。
- 默认 cutoff 由 `default_config.dialog_summary_cutoff_tokens` 决定，缺省 3000。

代码位置：[message_budget.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/utils/message_budget.py)。

### 7.3 长期记忆写入

`AsyncMemory.add()` 的写入链：

1. 解析消息，调用 LLM 抽取事实（JSON `facts`）。
2. 对每条事实做 Embedding，并在同作用域下检索相似旧记忆。
3. 让 LLM 输出 `ADD / UPDATE / DELETE / NONE` 动作。
4. `ADD` 前用精确 hash + content 查重，重复则复用旧 id 并跳过插入。
5. 向量库写成功后再写记忆变更历史；历史写失败只告警，不阻断向量写。

检索端 `search()` 按 `user_id / agent_id / run_id` 过滤，默认从 Chroma 召回语义相似事实。

## 8. RAG 检索系统

### 8.1 文档解析与索引

`DocParser` 按后缀分流，覆盖 8 类解析：

| 类型 | 后缀/分支 |
| --- | --- |
| Markdown | `md` |
| 文本 | `txt` |
| Word | `docx` |
| PDF | `pdf` |
| PPT | `pptx` |
| 图片 | `jpg/jpeg/png/bmp/webp/tiff`，先 OCR 转文本 |
| Excel | `xls/xlsx`，转文本 |
| 类文本 | `json/html/htm/csv`，通用转文本 |

默认切块参数 `chunk_size=500`、`overlap_size=100`；`enable_summary=True` 时再对每个 chunk 生成约 100 字摘要，chunk 与摘要条目一起写入向量库。代码位置：[parser.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rag/parser.py)。

### 8.2 生产查询链路

`RagHandler.retrieve_ranked_documents()`：

```text
raw query
  → Query Rewrite（保留原始 query 在前、去重）
  → Milvus/Chroma 向量召回，可选 ES 关键词召回
  → content / summary / content+summary 字段控制
  → merge_documents_by_score（按 chunk_id 去重、降序取 top 10）
  → Reranker.rerank_documents 精排
  → min_score / rerank_threshold 过滤
  → top_k 截断并拼接原文
  → 无结果时返回 "No relevant documents found."
```

关键设计：

- Query Rewrite 使用独立 LLM 生成候选 query，原始 query 永远保留。
- Rerank 失败时回退到原始检索分数，不返回空结果。
- ES 未启用时只走向量库；启用时 ES 与向量库分别排序后拼接，再统一去重。
- 向量库模式支持 Chroma / Milvus standalone / Milvus Lite。

代码位置：[handler.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rag/handler.py)、[retrieval.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rag/retrieval.py)、[rerank.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rag/rerank.py)、[query_write.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/services/rewrite/query_write.py)。

### 8.3 当前实测口径

P5.9 生产组件 A/B 使用同一 `knowledge_id`（Chroma collection）、同一 query 集与 ground truth，只切换检索策略。正式环境配置：

- 实际向量库：Chroma，knowledge_id `t_2aadac46967e4487`。
- `enable_elasticsearch=False`，因此“ES + 向量混合”路径存在代码但不在本轮数据内。
- Rerank 使用 `gte-rerank-v2`，正式结果 `min_score=0.0`，避免旧阈值误过滤。

## 9. 可观测与流控

### 9.1 事件流

`EmitEventAgentMiddleware` 包装模型调用与工具调用：

- `MODEL_CALL`：标记模型是否准备进入最终回答，供生产者决定延迟文本是否展示。
- `START/END/ERROR`：工具开始、成功、失败，附带 `tool_type`、展示名与耗时。
- 事件统一由 `get_stream_writer()` 写入，`GeneralAgent` 再包装成 SSE event。

### 9.2 Trace ID

`TraceIDMiddleware` 在中间件层为请求注入 `trace_id`；`build_stream_event` 给每条 SSE 事件带 `trace_id`，日志、工具事件、取消统计通过同一 id 关联。

### 9.3 断流取消

- `WatchedStreamingResponse` 在 ASGI task group 中同时监听 `http.disconnect` 与 body 生产。
- 收到断开后调用 `GeneralAgent.stop_streaming_callback()`，`CancellableAsyncStream.request_cancel()` 立即终止 producer。
- `CancellableAsyncStream.summary()` 统计 `total_duration_ms` 与 `cancel_to_terminate_ms`。
- 断开后的收尾通过 shield scope 保护，历史仍能落库。

代码位置：[streaming.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/api/responses/streaming.py)、[cancellable_stream.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/utils/cancellable_stream.py)、[events.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/utils/events.py)。

## 10. API、数据模型与周边能力

### 10.1 主要 API 分组

`/api/v1` 下按模块拆分：

- 对话：`completion`、`dialog`、`history`、`message`。
- 资产：`agent`、`agent_skill`、`tool`、`llm`。
- 知识库：`knowledge`、`knowledge_file`、`upload`。
- MCP：`mcp_server`、`mcp_user_config`、`register_mcp`、`register_mcp_completion`、`register_task`。
- 平台：`user`、`usage_stats`、`workspace`、`wechat`、`lingseek`、`mars`。

路由注册见 [v1/router.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/api/v1/router.py)，另有 MCP proxy 路由挂在 [api/router.py](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/api/router.py)。

### 10.2 数据模型

`database/models` 包含：user、role、user_role、agent、agent_skill、llm、tool、dialog、history、message、knowledge、knowledge_file、mcp_server、mcp_user_config、mcp_agent、register_mcp、register_mcp_tool、register_task、workspace_session、usage_stats、memory_history。

这些表支撑 Agent 配置、会话归属、权限、知识库文件、MCP 注册、用量统计与记忆变更审计。

### 10.3 认证、权限与存储

- JWT 认证：`auth/auth_jwt.py`，登录后携带 access token。
- 权限：`utils/permissions.py` 的 owner/admin 校验，对话与历史接口先验证归属再返回。
- 对象存储：`services/storage` 支持 MinIO 与 OSS，本地部署默认 MinIO。
- Redis：`services/redis.py` 与 MCP Session Manager 使用，缓存 MCP 会话。

## 11. 前端

前端位于 `src/frontend`，Vue 3 + Vite + TypeScript：

- `components/drawer`、`commonCard` 等通用组件承载对话界面。
- `pages/workspace` 提供工作区与应用中心切换。
- `store` 管理用户、历史会话、聊天消息。
- `vite.config.ts` 将 `/api` 代理到后端 `7860`。

本文重点为后端与评测体系，前端仅记录边界，不展开逐页实现。

## 12. 部署与配置

### 12.1 依赖服务

```powershell
cd docker
Copy-Item ..\.env.example .env
# 填写 MYSQL_* 与 MINIO_*
docker compose up -d
```

端口：MySQL `3307`、Redis `6380`、MinIO API `9002`、MinIO Console `9003`。当前 compose 不包含 backend/frontend 服务。

### 12.2 后端启动

```powershell
Copy-Item src\backend\agentchat\config.yaml.example src\backend\agentchat\config.yaml
cd src\backend
uvicorn agentchat.main:app --port 7860
```

本机推荐 conda 环境：

```powershell
C:\Users\20235\.conda\envs\agentchat\python.exe -m pip install -r src\backend\requirements.txt
```

配置模板中的默认值（`top_k=5`、`enable_summary=False`、`enable_elasticsearch=False`、`vector_db.mode=chroma`、`dialog_summary_cutoff_tokens=3000`）是文档引用时的“代码默认”，不要误写成强制生产默认。

## 13. 测试与评测体系

### 13.1 单元/回归测试

测试位于 `src/backend/tests/`，覆盖 token budget、RAG merger/optimizer/handler、query array、权限、多 Agent、记忆去重/过滤、事件、摘要预算等。P4 交付材料记录 P3.5 全量结果为 `68 passed`（原始记录见 [P4_INTERVIEW_MATERIAL.md](D:/实习记录/开源项目/AgentChat/docs/delivery/P4_INTERVIEW_MATERIAL.md)）。

### 13.2 离线评测

所有命令在 `src/backend` 下执行，原始 JSON 在 `docs/eval/offline/`：

| 模块 | 结果 | 归档 |
| --- | --- | --- |
| Token Budget | 40 对 / 8560 tokens，口径与生产共用 `split_messages_by_token` | `token_budget_p3.json` |
| RAG Optimizer | 9 条固定 query，`mean_mrr 0.9259 -> 1.0` | `rag_p3_before_after.json` |
| Memory Dedup | 60 次写入：20 插入、40 去重跳过，skip rate 0.6667 | `memory_dedup_p3.json` |

离线数字是固定 fixture 回归，不等同于真实链路。

### 13.3 真实链路评测

已落盘 P5 结果：

| 评测 | 结果 | 原始 JSON |
| --- | --- | --- |
| Completion 端到端 | 15 条业务问题 `case_ok_rate=1.0`，无工具错误 | `live_completion_20260814_134552.json` |
| SSE 断流 | 5/5，`cancel_to_terminate_ms` mean 0.485 / max 1.356 | `live_cancel_20260814_151430.json` |
| Memory 写入后检索 | 5/5，`hit_rate=1.0`、`mean_mrr=1.0` | `live_memory_20260814_154252.json` |
| 多 Agent 链路 | 5 个固定任务 `pass_rate=1.0`、路由全命中 | `live_multi_agent_20260814_155110.json` |
| RAG A/B | 50 query / 102 chunk，`Recall@5 0.8467->0.96`、`Hit@1 0.88->0.98` | `live_rag_comparison_20260814_182215.json` |
| Memory A/B | 30 场景 / 62 facts，Fact Recall `0.0968->0.6774` | `live_memory_comparison_20260818_105736.json` |

评测设计与限制见：  
[RAG_COMPARISON_DESIGN.md](D:/实习记录/开源项目/AgentChat/docs/eval/upcoming/RAG_COMPARISON_DESIGN.md)  
[MEMORY_COMPARISON_DESIGN.md](D:/实习记录/开源项目/AgentChat/docs/eval/upcoming/MEMORY_COMPARISON_DESIGN.md)  
[benchmarks/README.md](D:/实习记录/开源项目/AgentChat/src/backend/agentchat/benchmarks/README.md)

## 14. 当前已知限制

1. 多 Agent 为固定关键词 demo 路由，不是通用 NL 图编排；子 Agent 不是 `@tool`。
2. RAG A/B 差异来自 Query Rewrite + Rerank + `min_score` 修正的组合，未做单组件消融；Rerank 50 条中可用 48 条，2 条走生产 fallback。
3. RAG A/B 的向量库是 Chroma，配置中的 ES 混合路径未参与本轮指标。
4. Memory A/B 使用判别式 hint 匹配，不是 LLM-as-Judge；评测前 evidence 轮询仅 1/30 场景 `ready=true`，尚不能逐条证明“检索 -> 注入 -> 回答”的因果。
5. 简历旧表述“35% 召回率提升”“上下文稳定 4000 tokens”“Recall@5 0.72 -> 0.89”在仓库内没有对应复现档案，不应作为当前事实引用。
6. 断流与真实链路指标来自本机服务 + Docker 依赖环境，不是公网生产环境指标。

## 15. 相关文档

- 简历四要点深度解析：[RESUME_FOUR_POINTS_DEEP_DIVE.md](D:/实习记录/开源项目/AgentChat/docs/technical/RESUME_FOUR_POINTS_DEEP_DIVE.md)
- P0-P5 规划与执行记录：[VIBECODING_PLAN.md](D:/实习记录/开源项目/AgentChat/docs/VIBECODING_PLAN.md)
- 面试口径与证据表：[P4_INTERVIEW_MATERIAL.md](D:/实习记录/开源项目/AgentChat/docs/delivery/P4_INTERVIEW_MATERIAL.md)
- 部署说明：[DEPLOYMENT.md](D:/实习记录/开源项目/AgentChat/docs/delivery/DEPLOYMENT.md)
- 评测目录语义：[eval/README.md](D:/实习记录/开源项目/AgentChat/docs/eval/README.md)
