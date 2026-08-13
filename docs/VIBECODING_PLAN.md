# AgentChat 完善计划 v1

> 目标：不是为了堆功能，而是让简历里的每一条能力都变成“可演示、可解释、可量化”。
> 协作方式：按阶段推进，每阶段完成后更新本文件中的状态，再进入下一阶段。

## 阶段总览

| 阶段 | 主题 | 核心目标 | 预估 | 状态 |
| --- | --- | --- | --- | --- |
| P0 | 基线修复 | 修复会导致功能失效的已知问题，收敛权限和配置安全 | 3-5 天 | 已完成（静态验证） |
| P1 | 测试与可观测性 | 建立测试骨架，打通可观测链路，先保证工程可信 | 2-3 天 | 已完成（纯逻辑测试） |
| P2 | 评测与演示证据 | 让 RAG、记忆、断流三项指标可复现，准备面试 demo | 3-5 天 | 已完成（离线/模拟可复现，真实链路待补） |
| P3 | 核心能力增强 | 真多 Agent 协作、记忆质量、检索质量的增量优化 | 4-6 天 | 未开始 |
| P4 | 交付与简历对齐 | 文档收敛、部署验证、简历措辞与实测结果对齐 | 2-3 天 | 未开始 |

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
- 新增文档：`docs/eval/REPORT_TEMPLATE.md`、`docs/eval/METRICS_DEFINITIONS.md`、`docs/demo/DEMO_SCRIPT.md`、`src/backend/agentchat/benchmarks/README.md`
- 本地验证：`31 passed, 1 skipped`；RAG `mean_recall_at_k=1.0 / mean_mrr=0.9259 / hit_rate_at_k=1.0`；记忆三模式 `hit_rate=1.0`；断流模拟 5/5 通过，`cancel_to_terminate` 均值 `0.102ms`
- 限制：以上为离线/模拟结果，真实服务链路未跑；P2-Live 在 P4 补齐后才能把数字写进简历

## P3：核心能力增强

目标：在稳定基线上做增量，重点服务简历里的“多 Agent”和“Context Engineering”。

- [ ] 设计一个真实的多 Agent 触发场景，并让主链路真正调用子 Agent
- [ ] 为子 Agent 保留独立 ReAct 链，输出主 Agent 与子 Agent 的分层事件
- [ ] 增加多 Agent 场景测试，避免“看起来有，实际不触发”
- [ ] 验证记忆去重与合并逻辑，补齐写入失败与重复记忆兜底
- [ ] 校准 token 控制策略：阈值、摘要触发点、长对话稳定上限
- [ ] RAG 按 benchmark 结果做定向优化：改写、混合检索权重、Rerank 阈值

验收标准：

- 固定 demo 输入可以稳定触发多 Agent 协作
- 记忆 benchmark 与 token 分布结果可展示
- 优化项都有 P2 benchmark 的前后对比，而不是凭感觉改

## P4：交付与简历对齐

目标：把代码、文档、部署、简历四个版本对齐，避免“说一套、做一套”。

- [ ] 校对 API 文档与实际路由一致
- [ ] 更新 README：功能清单只写真实支持的范围内
- [ ] 提供 Docker 一键启动和环境变量示例
- [ ] 整理面试问答材料：每个简历点配解释、设计权衡、失败经验
- [ ] 最终核对简历：删除没有实现的“事实性承诺”

验收标准：

- 新环境按文档可以启动
- API 文档与实际实现一致
- 简历里的每一条结论都能找到对应代码路径或评测数据

## 协作规则

- 每个阶段完成前不进入下一阶段
- 每完成一个任务，更新本文件对应复选框
- 每个阶段结束提交一次可运行的基线
- 涉及简历数字的改动，必须先有 P2 的评测结果
- 如果某个能力在规定时间内无法做到“真实可用”，优先级高于“把简历写大”
