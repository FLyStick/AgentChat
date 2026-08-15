# AgentChat 完善计划 v1

> 目标：不是为了堆功能，而是让简历里的每一条能力都变成“可演示、可解释、可量化”。
> 协作方式：按阶段推进，每阶段完成后更新本文件中的状态，再进入下一阶段。

## 阶段总览

| 阶段 | 主题 | 核心目标 | 预估 | 状态 |
| --- | --- | --- | --- | --- |
| P0 | 基线修复 | 修复会导致功能失效的已知问题，收敛权限和配置安全 | 3-5 天 | 已完成（静态验证） |
| P1 | 测试与可观测性 | 建立测试骨架，打通可观测链路，先保证工程可信 | 2-3 天 | 已完成（纯逻辑测试） |
| P2 | 评测与演示证据 | 让 RAG、记忆、断流三项指标可复现，准备面试 demo | 3-5 天 | 已完成（离线/模拟可复现，真实链路待补） |
| P3 | 核心能力增强 | 真多 Agent 协作、记忆质量、检索质量的增量优化 | 4-6 天 | 已完成（离线/模拟可复现） |
| P3.5 | 生产链路收敛 | 把 P3 的模拟增强接回生产配置，统一 token 预算并全量回归 | 1-2 天 | 已完成（生产配置化，全量测试通过） |
| P4 | 交付与简历对齐 | 文档收敛、部署验证、简历措辞与实测结果对齐 | 2-3 天 | 已完成（文档与部署对齐，真实链路补测项已单独列出） |
| P5 | 真实链路评测 | 把 P2/P3 的离线/模拟证据升级为真实服务链路数字，形成可面试口径 | 3-5 天 | 进行中（P5.9 已完成，P5.10 待执行） |

## P0：基线修复

目标：把当前明显不可用的功能修到“本地能跑、链路能通、权限安全”。

- [x] 修复 ES 检索结果遍历错误，命中结果不再丢失
- [x] 修复 Query Rewrite 输出格式不一致问题，统一为下游可解析的格式
- [x] 实现断流后停止继续产出（当前为循环级停止 yield；真正的任务级取消见 P2）
- [x] 删除未启用的 tool selector 中间件与相关死代码
- [x] 删除“工具过多时启用 search 工具”的未生效兜底链路
- [x] 修复 RAG 回退检索时 collection/index 命名错误
- [x] `GET /api/v1/history` 增加对话归属校验
- [x] 密钥迁移到环境变量，移除仓库内明文 API Key / 密码
- [x] 整理明显空实现：删除或补齐 workspace session、memory select history 等占位代码

验收标准：

- P0 任务全部勾选完成
- 后端启动无配置错误，密钥来自环境变量
- 普通对话、知识库检索、断流三条主链路手动验证通过
- 越权访问他人对话被拒绝

完成说明（静态验证）：

- ES 客户端改为延迟初始化，`search_documents` 正确遍历命中结果，`close()` 改为同步单例关闭
- Query Rewrite 强制输出 JSON 数组，解析失败时兜底为 `[user_input]`
- workspace session 创建/删除补齐，历史权限校验覆盖 `get_dialog_history` 与 `get_workspace_session_from_id`
- `general_agent` 删除 tool selector、search tool 等未生效链路；断流在事件循环层停止后续产出
- `config.yaml.example`、`docker-compose.yml` 使用 `${ENV_VAR}` 占位，新增 `.env.example`
- 运行验证依赖 P1 测试环境：当前仓库环境缺少 langchain、starlette、elasticsearch 等运行时依赖，已通过 `py_compile` 与静态引用检查

## P1：测试与可观测性

目标：让后续改动有测试保护，让工具调用链路能从日志和事件中还原。

- [x] 搭建 pytest 基础设施，补充测试依赖与 CI 脚本
- [x] 为 RAG Handler、Query Rewrite、Memory Client 补单元测试
- [x] 为 history 权限、知识库上传补服务层权限测试
- [x] 统一流式事件结构，补齐 tool 调用、耗时、失败原因的埋点
- [x] 增加请求级 Trace ID 与关键节点日志

验收标准：

- `pytest` 全量通过，核心链路覆盖率可量
- 一次对话可以从日志还原完整链路：输入、检索、工具调用、LLM 输出
- 工具调用耗时和失败原因可观测

完成说明（P1）：
- 新增纯逻辑单元测试，覆盖 Query Rewrite 解析、Memory filters、Memory Utils、RAG 结果合并、权限判断、流式事件结构
- 当前环境验证结果：`24 passed, 1 skipped`，跳过项为权限服务测试（依赖 loguru/fastapi/sqlmodel 等运行时依赖）
- 流式事件统一为 `type/event_id/timestamp/trace_id/data`，tool 事件补充 `tool_name`、`tool_type`、`duration_ms`、`error`
- 断流日志补充 Trace ID；清理 `test_React.py` 中的明文 API Key
- CI 工作流 `.github/workflows/ci.yml` 已加入，安装 pytest 系依赖后运行 `python -m pytest`

## P2：评测与演示证据

目标：让简历里的数字有自己的数据口径，并准备一套可现场演示的剧本。

- [x] 建立 RAG benchmark：固定知识库、query 集、ground truth、评测脚本
- [x] 建立记忆 benchmark：短期窗口 / 摘要 / 长期记忆的对比样本
- [x] 实现任务级断流取消：中断正在执行的模型调用，并记录真实终止时长
- [x] 建立断流压测脚本：记录断开到推理终止的真实时长
- [x] 输出评测报告模板：baseline、当前结果、提升幅度、复现方法
- [x] 编写端到端 demo 剧本：知识上传、RAG 问答、Skill、MCP、多轮记忆
- [x] 产出一份“简历指标口径说明”，每个数字写明测试集和计算方法

验收标准：

- RAG 和记忆脚本可一键复现
- 断流时间有可复现压测记录，达到“500ms”要求后再写回简历；当前为本地模拟，真实服务链路在 P4 补测
- demo 剧本每一步都有可见的输入与输出

完成说明（P2）：

- 新增 `agentchat.benchmarks` CLI 与固定 fixture：RAG 8 docs / 9 queries，记忆 12 cases；三命令可一键复现，并支持 `--output` 归档原始 JSON
- 新增强制取消流：`CancellableAsyncStream` 接入 `GeneralAgent.astream`，`stop_streaming_callback()` 会触发 `request_cancel()`，`last_stream_summary` 记录总时长、取消到终止时长和 Trace ID
- 新增文档：`docs/eval/offline/REPORT_TEMPLATE.md`、`docs/eval/offline/METRICS_DEFINITIONS.md`、`docs/demo/DEMO_SCRIPT.md`、`src/backend/agentchat/benchmarks/README.md`
- 本地验证：`31 passed, 1 skipped`；RAG `mean_recall_at_k=1.0 / mean_mrr=0.9259 / hit_rate_at_k=1.0`；记忆三模式 `hit_rate=1.0`；断流模拟 5/5 通过，`cancel_to_terminate` 均值 `0.102ms`
- 限制：以上为离线/模拟结果，真实服务链路未跑；P2-Live 在 P4 补齐后才能把数字写进简历

## P3：核心能力增强

目标：在稳定基线上做增量，重点服务简历里的“多 Agent”和“Context Engineering”。

- [x] 设计一个真实的多 Agent 触发场景，并让主链路真正调用子 Agent
- [x] 为子 Agent 保留独立 ReAct 链，输出主 Agent 与子 Agent 的分层事件
- [x] 增加多 Agent 场景测试，避免“看起来有，实际不触发”
- [x] 验证记忆去重与合并逻辑，补齐写入失败与重复记忆兜底
- [x] 校准 token 控制策略：阈值、摘要触发点、长对话稳定上限
- [x] RAG 按 benchmark 结果做定向优化：改写、混合检索权重、Rerank 阈值

验收标准：

- 固定 demo 输入可以稳定触发多 Agent 协作
- 记忆 benchmark 与 token 分布结果可展示
- 优化项都有 P2 benchmark 的前后对比，而不是凭感觉改

完成说明（P3）：

- 新增多 Agent 编排层 `core/agents/orchestrator.py`：主 Agent 按固定关键词路由到制度、酒店、项目三个子 Agent，子 Agent 各自持有独立 ReAct 链与工具集，流式事件区分 `agent_start/agent_plan/sub_agent_start/sub_agent_end/agent_end`
- `GeneralAgent.init_agent()` 接入 demo orchestrator，`astream()` 命中固定场景时走多 Agent 分支；新增 4 个 P3 测试模块，整体测试结果 `55 passed`
- 记忆客户端补齐精确 hash 查重、冗余更新跳过、未知 id 跳过、历史写失败不阻断向量写；`memory-duplicate` benchmark 归档 `docs/eval/offline/memory_dedup_p3.json`
- 新增 `token` benchmark 校准长对话 token 策略，40 对样本 / 8560 tokens，归档 `docs/eval/offline/token_budget_p3.json`
- 新增 `rag-optimizer` benchmark：查询改写 + content/summary/tags 混合字段 + rerank 阈值；9 条固定 query 上 `mean_mrr` 从 `0.9259` 提升到 `1.0`，加班硬查询排名从第 3 提升到第 1，归档 `docs/eval/offline/rag_p3_before_after.json`
- 限制：当前多 Agent 为固定 demo 场景，RAG 优化为离线词法基准；真实模型链路与线上检索在 P4 补测后再进入简历

## P3.5：生产链路收敛

目标：把 P3 里“演示可复现”的部分收敛到真实生产路径，避免改动只存在于测试侧。

- [x] 抽离共享 token 预算 helper，`DialogService.update_dialog_summary` 与 benchmark 复用同一分片规则
- [x] 多 Agent 改为配置驱动：默认关闭，显式 `enable_multi_agent=True` 才构造 orchestrator
- [x] RAG 生产 handler 支持 `content+summary` 双字段检索、`rerank_threshold` 配置、`top_k=None` 不丢结果
- [x] 数据库旧库兼容：`init_data.py` 通过 `inspect + ALTER TABLE` 补多 Agent 配置列
- [x] 新增 P3.5 测试与 conftest 离线依赖桩，全量测试 `68 passed`

完成说明（P3.5）：

- `agentchat/utils/message_budget.py` 定义 `message_token_count / pair_messages / pair_token_count / split_messages_by_token / default_summary_cutoff_tokens`，摘要服务与 `benchmarks/token_budget.py` 都委托该 helper
- 子 Agent 输入由纯字符串改为 `SystemMessage(sub_prompt) + 原历史/最新 HumanMessage`，`orchestrator` 支持 `List[BaseMessage]`
- RAG 检索按配置选择 `content` 或 `content+summary` 字段，summary 回退 content 时透传重写结果与配置参数
- 限制：多 Agent 仍默认关闭，固定关键词路由仍是 P4 前用于面试演示的场景；RAG 生产 handler 已接配置，但检索效果离线验证为 mock 数据，不伪造线上指标

## P4：交付与简历对齐

目标：把代码、文档、部署、简历四个版本对齐，避免“说一套、做一套”。

- [x] 校对 API 文档与实际路由一致
- [x] 更新 README：功能清单只写真实支持的范围内
- [x] 提供 Docker 一键启动和环境变量示例
- [x] 整理面试问答材料：每个简历点配解释、设计权衡、失败经验
- [x] 最终核对简历：删除没有实现的“事实性承诺”

验收标准：

- 新环境按文档可以启动
- API 文档与实际实现一致
- 简历里的每一条结论都能找到对应代码路径或评测数据

## 完成说明（P4）

- 新建 `docs/delivery/P4_INTERVIEW_MATERIAL.md`：简历点对照、评测数字、追问口径和简历修改建议；明确多 Agent 默认关闭、固定关键词路由、子 Agent 独立 ReAct 链、RAG 与断流为离线模拟
- 新建 `docs/delivery/DEPLOYMENT.md`：Docker 依赖服务（MySQL 3307 / Redis 6380 / MinIO 9002+9003）+ 本地启动前后端 + `.env` / `config.yaml` 配置示例
- 修正 `api.md`、`agentchat.md`、`migration.md` 的路由口径：`/api/v1/completion`、`PUT /user/update`、`POST /tool/all|user_defined|delete|update`、`GET /history?dialog_id=...`，并新增实测路由清单
- `docker/README.md` 与启动脚本改为只启动依赖服务；`scripts/start.py` 依赖查找修正为 `src/backend/requirements.txt`
- 限制：真实 LLM 断流压测、真实 RAG 线上指标、真实多 Agent 生产场景仍未补测，面试口径不得把离线数据写成线上数据

## P5：真实链路评测

目标：让“离线/模拟可复现”升级为“真实服务链路可复现”。只有真实链路数据落盘后，P2/P3 的数字才允许进入简历或面试口径。

执行环境：
- 后端 Python：`C:\Users\20235\.conda\envs\agentchat\python.exe`
- 依赖服务：Docker 已启动的 MySQL `3307`、Redis `6380`、MinIO `9002/9003`，执行前用 `docker compose ps` 确认 healthy
- 向量库：按 `src/backend/agentchat/config.yaml` 的 `rag.vector_db.mode` 确认可达；当前默认 `chroma`，若切到 Milvus/ES 需要先补齐对应服务
- 后端启动：在 `src/backend` 目录执行 `& 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m uvicorn agentchat.main:app --host 127.0.0.1 --port 7860`
- 评测入口：扩展 `python -m agentchat.benchmarks`，保留现有离线子命令作为 baseline，新增 live 参数和真实链路子命令

数据源决策：
- 首选自建业务语料：复用现有酒店 FAQ、项目手册、内部制度等 fixture，扩充到 20-30 个真实知识库 chunk，手动标注 ground truth
- 公开数据集只作为第二阶段：C-MTEB 中文检索子集、DuReader_retrieval、CMRC2018 可选，需要网络下载许可和格式转换后再接入
- 当前阶段不引入 GAIA/TauBench：本项目多 Agent 是固定关键词路由的 demo 场景，硬套通用 Agent 基准会暴露范围外短板

任务清单：
- [x] P5.1 环境预检：检查 `.env` 与 `config.yaml`，确认 Docker 依赖 healthy，启动后端并访问 `GET /health`
- [x] P5.2 真实知识库灌入：编写 seeding 脚本，把 fixture 文档和扩充语料写入真实知识库，产出 `chunk_id -> ground_truth` 映射文件
- [x] P5.3 RAG 真实召回：`rag` 子命令新增 `--live --knowledge-ids`，用 `LiveRetriever` 走真实向量库与 Embedding，验证召回指标可测；该轮不含 Query Rewrite/Rerank，策略差异留给 P5.9 路线 B，归档 `docs/eval/live/live_rag_*.json`
- [x] P5.4 Completion 端到端：新增真实 `/api/v1/completion` SSE 评测，验证返回完整性、知识库依据、工具调用、`agent_name`、首 token/总延迟、token 消耗，归档 `docs/eval/live/live_completion_*.json`
- [x] P5.5 断流真实链路：真实 SSE 客户端中途断开，测量 `cancel_to_terminate_ms`，归档 `docs/eval/live/live_cancel_*.json`
- [x] P5.6 记忆真实链路：创建真实 user/agent/run，写入并检索记忆，走 `LiveMemoryAdapter`，归档 `docs/eval/live/live_memory_*.json`
- [x] P5.7 多 Agent 真实场景：创建 `enable_multi_agent=True` 的测试 Agent，跑 3-5 个固定业务任务，记录 orchestrator 路由与子 Agent ReAct 事件
- [x] P5.8 证据收敛：更新 `benchmarks/README.md`、`P4_INTERVIEW_MATERIAL.md` 和本文件状态，只有存在原始 JSON 时才把离线数字升级为真实链路口径
- [x] P5.9 RAG 真实 A/B（路线 B）：扩充自建真实知识库到 21 文档 / 102 chunk，人工标注 50 条 query（含 17 条 hard queries），新增 `live_rag_ab.py` 在同一知识库上对比 baseline vs 生产完整链路，正式归档 `docs/eval/live/live_rag_comparison_20260814_182215.json`（Rerank `gte-rerank-v2` 可用 48/50，2 条网络抖动走生产 fallback）；历史 `20260814_173430.json` 保留为 Rerank 403 对照；设计文档已随执行结果更新到 `docs/eval/live/RAG_COMPARISON_DESIGN.md`
- [ ] P5.10 Memory 两层 vs 三层真实对话对比：构造 30 条多轮 Fact Recall 场景，`enable_memory=False/True` 独立 user/agent 各跑一遍，输出 `docs/eval/live/live_memory_comparison_*.json`；设计文档 `docs/eval/upcoming/MEMORY_COMPARISON_DESIGN.md`

验收标准：
- 后端在 conda `agentchat` 环境启动成功，Docker 依赖服务 healthy
- 真实 RAG 结果明确标记 `LiveRetriever`，指标可和离线 baseline 对比
- Completion 评测覆盖至少 10 条脚本化业务问题，且能证明答案来自真实模型/知识库/工具链路
- 断流评测至少 5 轮真实 SSE 断开，记录真实终止耗时
- 记忆和多 Agent 评测各有原始事件/检索 trace
- 所有原始 JSON 按 `docs/eval/offline/`（离线）与 `docs/eval/live/`（真实链路）归档，设计文档在 `docs/eval/upcoming/`，面试材料只引用已归档文件
- P5.9 A/B 两组使用同一真实知识库、同一向量库、同一 ground truth，只改变检索策略；原始 JSON 归档 `docs/eval/live/live_rag_comparison_*.json`
- P5.10 两层与三层使用独立 user/agent 防止记忆污染；原始 JSON 归档 `docs/eval/live/live_memory_comparison_*.json`

完成说明（P5）：
- P5.1/P5.2：后端 `GET /health` 正常；真实知识库 `t_8160a81598c04539` 灌入 3 个 txt，索引 6 个 chunk，生成 30 条 query 的 ground truth：`docs/eval/live/live_seed_result.json`、`docs/eval/live/live_rag_ground_truth.json`
- P5.3：使用 `LiveRetriever -> MixRetrival/Chroma -> merge_documents_by_score` 真实链路跑 30 条 query；`mean_recall_at_k=1.0`、`mean_mrr=0.9167`、`hit_rate_at_k=1.0`、`evidence_hit_rate_joined=1.0`；平均延迟 804.8ms，p50 398.1ms；原始 JSON 归档为 `docs/eval/live/live_rag_20260814_123820.json`
- P5.3 环境修复：conda `agentchat` 环境存在损坏/缺失的 `httpcore`，已重装 `httpcore==1.0.9`、`h11==0.16.0` 并与 `httpx==0.28.1` 对齐；`LiveRetriever` 改为直接走 `MixRetrival`，不再因导入 `RagHandler` 而实例化 `ChatOpenAI`
- P5.4：真实 `/api/v1/completion` SSE 评测完成，15 条 query 全部完成：`stream_completed=15`、`case_ok_rate=1.0`、`knowledge_ok_rate=1.0`、`knowledge_content_ok_rate=1.0`、`rewrite_list_start_case_count=0`、`no_knowledge_evidence_case_count=0`、`tool_error_case_count=0`；`fact_term_coverage_joined=0.9333`、`full_fact_match_rate=0.0`（严格完整串逐字命中口径，其中 `live_q_minibar` 的 expected_facts 为空，作为后续评测口径参考项，不阻塞 P5.4）；平均总延迟 `24841.758ms`、平均首 chunk `18562.339ms`；`51467 input / 10913 output / 45 model calls`；实测模型 `qwen3.7-max`；原始 JSON 归档 `docs/eval/live/live_completion_20260814_134552.json`
- P5.4 代码与依赖修复：`AgentConfig.name` 补默认字段修复 `set_agent_name_context` 调用；RAG handler/retrieval 的字符串参数自动转列表；`general_agent._produce` 丢弃工具调用前的模型中间文本，最终回答只输出自然语言；conda 环境重装 `python-docx==1.1.2` 修复损坏依赖；回归测试 `7 passed`（`test_rag_handler_optimization.py` + `test_agent_config.py`）
- P5.5：真实 SSE 断流链路已完成。验证链路为裸 socket SSE 客户端在约 `5000ms` 时断开 -> 服务端收到 `http.disconnect` -> 触发 `request_cancel` -> `CancellableAsyncStream` 在 `CancelScope(shield=True)` 内完成 `finish_cancelled()` -> 生成 `stream_cancel` 历史事件并落库。正式 5 轮结果：`pass_rate=1.0`、`cancel_summary_found=5/5`、`server_cancelled=5/5`、`terminated_ok=5/5`；`cancel_to_terminate_ms` 均值 `0.485ms`、p50 `0.277ms`、p90 `0.933ms`、p95 `1.145ms`、max `1.356ms`，全部满足 `<=500ms` 阈值；每轮均在关闭前未收到首 chunk（`closed_before_first_chunk=true`），证明取消发生在真实模型生成链路中。原始 JSON 归档 `docs/eval/live/live_cancel_20260814_151430.json`
- P5.5 手动复现（从 `src/backend` 执行）：

  ```powershell
  # 1. 单轮冒烟
  & 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_cancel --rounds 1 --query-ids live_q_cancel --close-after-ms 5000 --history-timeout-ms 20000 --output-dir docs/eval/live

  # 2. 正式 5 轮（可重复执行，每次生成新的时间戳 JSON）
  & 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_cancel --rounds 5 --close-after-ms 5000 --history-timeout-ms 30000 --output-dir docs/eval/live

  # 3. 查看最新归档
  Get-ChildItem docs/eval/live/live_cancel_*.json | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  ```

- P5.6：真实记忆链路已完成。通过真实 `LiveMemoryAdapter` + Chroma + DashScope Embedding + 真实 user/agent/run 写入 5 条显式事实，写入前跨 run 检索结果为 0，写入后返回 5 条且 ID 唯一；同会话检索 `5/5`、跨会话检索 `5/5`，`same_run_hit_rate=1.0`、`cross_run_hit_rate=1.0`、`mean_recall_at_k=1.0`、`mean_mrr=1.0`，检索延迟均值 `250.09ms`。原始 JSON 归档 `docs/eval/live/live_memory_20260814_154252.json`
- P5.6 配套修改：`live_memory.py` 增加 `init_app_settings()` 初始化；记忆作用域按 user/agent/run 三层归档，`AgentConfig` 增加 `agent_id`，completion 记忆检索使用真实用户/Agent/会话作用域
- P5.7：真实多 Agent 链路已完成。创建 `enable_multi_agent=True` 的测试 Agent，跑制度、酒店、项目 5 个固定业务任务：`pass_rate=1.0`、`route_match_rate=1.0`、`sub_agent_pair_count=5/5`、`response_ok_count=5/5`、`error_case_count=0`；5 个子 Agent 均完成工具调用，`tool_start_count=5`、`tool_end_count=5`、`sub_agent_tool_calls_total=5`；总延迟均值 `12294.756ms`、首 chunk 均值 `6614.585ms`。原始 JSON 归档 `docs/eval/live/live_multi_agent_20260814_155110.json`
- P5.7 配套修改：修复 `live_multi_agent.py` 汇总逻辑对 `sub_agent_tool_calls` 字段的强依赖，新增标量 `sub_agent_tool_calls_total` 再汇总，避免运行时 KeyError
- P5.8：`benchmarks/README.md`、`docs/delivery/P4_INTERVIEW_MATERIAL.md` 与本文件状态已更新；`docs/eval` 已拆分为 `offline/live/upcoming` 三个子目录，live 脚本默认输出目录同步改为 `docs/eval/live`；面试材料中的 RAG、Completion、断流、记忆、多 Agent 数字均有 `docs/eval/live/` 真实链路原始 JSON 支撑

P5.6/P5.7 手动复现（从 `src/backend` 执行，先确认后端已启动且 Docker 依赖 healthy）：

  ```powershell
  # 1. 记忆真实链路（可重复执行，每次生成新的时间戳 JSON）
  & 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_memory --output-dir ..\..\docs\eval\live

  # 2. 多 Agent 真实链路（可重复执行，每次生成新的时间戳 JSON）
  & 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_multi_agent --output-dir ..\..\docs\eval\live

  # 3. 查看最新归档
  Get-ChildItem ..\..\docs\eval\live\live_memory_*.json, ..\..\docs\eval\live\live_multi_agent_*.json | Sort-Object LastWriteTime -Descending | Select-Object -First 2
  ```

P5.9/P5.10 进度（P5.9 已完成，P5.10 待执行）：

- P5.9 RAG 真实 A/B 已完成（Rerank 重新配置后重新覆盖）：真实知识库 `t_2aadac46967e4487`（`RagAb0814`）灌入 21 个文档、102 个 chunk，50 条 ground truth（easy 11 / normal 22 / hard 17）已落盘 `docs/eval/live/live_rag_ab_ground_truth.json`；最新 A/B 原始 JSON 为 `docs/eval/live/live_rag_comparison_20260814_182215.json`，旧 `20260814_173430.json` 保留为历史对照。总体 `Recall@5` 从 `0.8467` 到 `0.96`、`MRR@5` 从 `0.6967` 到 `0.7983`、`Hit@1` 从 `0.88` 到 `0.98`；hard 子集 `Recall@5` 从 `0.7843` 到 `0.9412`、`MRR@5` 从 `0.7255` 到 `0.7843`、`Hit@1` 从 `0.8824` 到 `1.0`，证据命中率从 `0.7941` 到 `0.9706`。Rerank 已通过阿里云 MaaS 原生路由接入：`gte-rerank-v2` 可用 `48/50`，2 条因 DNS 瞬时错误自动降级；`min_score` 从 `0.2` 调整为 `0.0` 以匹配 GTE rerank 分数口径。差异来自 Query Rewrite + Rerank + min_score 修正的组合，未做单组件消融，不能表述为“Rerank 单独提升”；设计文档见 `docs/eval/live/RAG_COMPARISON_DESIGN.md`
- P5.10 Memory 两层 vs 三层：真实多轮 Fact Recall 场景，独立 user/agent 防污染；设计见 `docs/eval/upcoming/MEMORY_COMPARISON_DESIGN.md`

P5.9 手动复现（从 `src/backend` 执行，先确认后端已启动且 Docker 依赖 healthy）：

  ```powershell
  # 1. 灌入/复用 P5.9 知识库（首次或需要重建时）
  & 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_seed `
    --queries-file agentchat/benchmarks/fixtures/rag_live_ab/queries.json `
    --user-name live_ab_0814 `
    --email live_ab_0814@bench.local `
    --output-dir ..\..\docs\eval\live

  # 2. 正式 A/B（50 条 query，all 模式）
  & 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_rag_ab `
    --top-k 5 `
    --output-dir ..\..\docs\eval\live

  # 3. 冒烟 5 条（可选，快速确认链路可用）
  & 'C:\Users\20235\.conda\envs\agentchat\python.exe' -m agentchat.benchmarks.live_rag_ab `
    --top-k 5 --limit 5 `
    --output-dir ..\..\docs\eval\live

  # 4. 查看最新归档
  Get-ChildItem ..\..\docs\eval\live\live_rag_comparison_*.json | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  ```

## 协作规则

- 每个阶段完成前不进入下一阶段
- 每完成一个任务，更新本文件对应复选框
- 每个阶段结束提交一次可运行的基线
- 涉及简历数字的改动，必须先有 P2 的评测结果
- 如果某个能力在规定时间内无法做到“真实可用”，优先级高于“把简历写大”
