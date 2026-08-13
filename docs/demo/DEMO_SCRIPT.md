# 面试演示剧本

> 目标：10-15 分钟讲完“知识上传 → RAG 问答 → Skill → MCP → 多轮记忆 → 断流”。
> 原则：每个场景只演示一次，输入固定，输出要在前端和后端日志里都能看到证据。

## 演示准备

- 启动后端、前端、MySQL、Redis、向量库和 ES，确认 `/api/v1` 接口可用
- 准备一个 `hotel_faq.md` 或 PDF 作为知识库样本
- 准备一个已配置的 OpenAPI/Skill 工具，例如天气查询
- 准备一个可调通的 MCP Server，例如自建 SSE 服务，至少暴露一个工具
- 每次演示前清理对话，避免上一轮记忆影响结果
- 录屏时打开浏览器 DevTools Network，能展示 SSE 事件

## 场景 1：知识上传与 RAG 问答（5 分钟）

操作步骤：

1. 创建知识库，上传 `hotel_faq.md`
2. 等待文件解析状态变为 `success`
3. 发起提问：“酒店几点可以办理入住，几点退房？”
4. 在回答旁展示命中的知识来源或检索事件

演示话术：

> 文件上传后会走解析、分块、写入向量库的分片链路；回答时先做检索再注入 prompt，流式事件里能看到 `response_chunk`。

可展示证据：

- `type=response_chunk`，`data.chunk` 累积为完整回复
- 后端日志中 RAG 链路：`RagHandler` / `MixRetrival` / rerank
- 知识库文件 status 从 `process` 到 `success`

## 场景 2：Skill / 工具调用（3 分钟）

操作步骤：

1. 在 Agent 配置中绑定一个 Skill 或用户自定义工具
2. 提问：“帮我查询杭州今天的天气”
3. 在界面上展示“正在调用 XX 工具”的过程，再展示工具结果

演示话术：

> 模型先决策是否需要工具，工具中间件会发出 START/END 事件，并记录 `tool_name`、`tool_type` 和 `duration_ms`；工具结果会以 `ToolMessage` 回到模型上下文继续生成。

可展示证据：

- `type=event`，`data.status=START` / `END` / `ERROR`
- `data.tool_name`、`data.tool_type`、`data.duration_ms`
- 后端日志中的 tool args 与 tool result

## 场景 3：MCP 集成（3 分钟）

操作步骤：

1. 注册一个 MCP Server，确认工具列表被动态加载
2. 让 Agent 调用 MCP 暴露的工具
3. 展示工具事件中的 `tool_type` 指向 MCP

演示话术：

> Agent 会把 MCP Server 的动态工具映射为 LangChain 工具，走同一套 tool middleware；现场可以把 MCP Server 换成已准备好的服务，避免演示外部依赖。

可展示证据：

- MCP Server 列表和工具枚举
- `type=event` 中的 MCP 工具调用耗时
- 若需要鉴权配置，展示 `mcp_user_config` 注入参数

## 场景 4：多轮记忆（3 分钟）

操作步骤：

1. 第一轮：“我叫张三，客户预算上限是 80 万”
2. 第二轮直接问：“客户预算上限是多少，我叫什么？”
3. 展示模型在不重复输入的情况下回答正确

演示话术：

> 记忆分为短期窗口、摘要和长期事实；这个 demo 先验证多轮上下文能命中，P3 会继续优化去重、合并和 token 阈值。

可展示证据：

- 后续轮次上下文包含上一轮事实
- 若当前实现有 `memory` 事件，展示对应记忆写入/检索事件
- 记忆历史表 `memory_history` 或日志中的记忆变更记录

## 场景 5：流式输出与断流取消（2 分钟）

操作步骤：

1. 发起一个长回答请求，让输出正在生成
2. 点击停止，确认前端立即停止产出
3. 展示后端日志中的取消摘要

演示话术：

> 断流不再是循环里检查标志位这么简单：`GeneralAgent.astream` 会用可取消的流包装生产者，停止后真正取消正在运行的任务，并记录 `cancel_to_terminate_ms` 和 Trace ID。

可展示证据：

后端日志：

```text
Stream cancelled. total_duration_ms=... cancel_to_terminate_ms=... trace_id=...
```

> 注意：当前 P2 的 500ms 压测是本地模拟生产者结果；真实模型链路的断流数字需在部署环境中重跑后再用于简历。

## 收尾总结

现场结束前用三句话收口：

1. 我把简历里的 RAG、记忆、断流能力做成了可复现的 benchmark 和固定 demo
2. 每一个面试点都有前端可见效果、后端事件和指标口径对应
3. 目前离线/模拟链路已验证，真实服务链路在整体部署阶段补齐后再对简历数字

## 失败兜底

- RAG 没命中：检查文件解析状态、knowledge_id、embedding/向量库是否启动
- 工具没触发：确认 Agent 已绑定工具，检查模型是否真的输出 tool_calls
- MCP 调用失败：现场切换到备用的本地 SSE Server，避免网络依赖
- 断流没有日志：确认调用走 `GeneralAgent.astream`，并检查 `last_stream_summary`
