import copy
import time
import asyncio
from loguru import logger
from pydantic import BaseModel
from typing import List, Dict, Any, AsyncGenerator, Callable, NotRequired
from langgraph.runtime import Runtime
from langgraph.types import Command
from langchain_core.tools import BaseTool, tool, StructuredTool
from langchain.tools.tool_node import ToolCallRequest
from langchain.agents import create_agent, AgentState
from langgraph.config import get_stream_writer
from langchain_core.messages import BaseMessage, ToolMessage, HumanMessage, AIMessageChunk
from langchain.agents.middleware import ModelRequest, ModelResponse, AgentMiddleware

from agentchat.api.services.agent_skill import AgentSkillService
from agentchat.core.agents.skill_agent import SkillAgent
from agentchat.core.callbacks import usage_metadata_callback
from agentchat.database import AgentSkill
from agentchat.tools import AgentToolsWithName
from agentchat.api.services.llm import LLMService
from agentchat.core.models.manager import ModelManager
from agentchat.api.services.tool import ToolService
from agentchat.services.rag.handler import RagHandler
from agentchat.core.agents.mcp_agent import MCPAgent, MCPConfig
from agentchat.api.services.mcp_server import MCPService
from agentchat.tools.openapi_tool.adapter import OpenAPIToolAdapter
from agentchat.utils.events import build_stream_event
from agentchat.utils.cancellable_stream import CancellableAsyncStream


class StreamAgentState(AgentState):
    tool_call_count: NotRequired[int]
    model_call_count: NotRequired[int]
    user_id: NotRequired[str]

class AgentConfig(BaseModel):
    user_id: str                     # 用户标识
    llm_id: str                      # 模型ID
    mcp_ids: List[str]               # MCP服务器列表
    knowledge_ids: List[str]         # 知识库ID列表
    tool_ids: List[str]              # 工具ID列表
    agent_skill_ids: List[str]       # 技能ID列表
    system_prompt: str               # 系统提示词
    enable_memory: bool = False      # 记忆开关
    enable_multi_agent: bool = False # 多Agent开关
    name: str = ""                   # Agent名称，来自agent表，供usage统计上下文使用
    



class EmitEventAgentMiddleware(AgentMiddleware):
    """Agent 中间件：在模型调用与工具调用前后注入事件流，并控制执行流程。"""

    def __init__(self, name_resolver_func):
        super().__init__()

        # 工具名解析函数：将原始工具名解析为（类型, 展示名）
        self.name_resolver_func = name_resolver_func

    async def aafter_model(
        self, state: StreamAgentState, runtime: Runtime
    ) -> dict[str, Any] | None:
        """模型调用后的钩子：若模型发起了工具调用则继续执行，否则结束流程。"""
        last_message = state["messages"][-1]
        if last_message.tool_calls:
            # 模型请求了工具调用，累加调用次数并继续
            return {
                "model_call_count": state["model_call_count"] + 1
            }

        # 模型未请求工具，直接跳转到结束节点
        return {
            "jump_to": "end"
        }

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """包装模型调用：捕获异常并记录日志，避免调用失败导致流程中断。"""
        try:
            response = await handler(request)
            self.write_model_call_event(response)
            return response
        except Exception as err:
            logger.error(f"Model call error: {err}")
            raise ValueError(err)

    def write_model_call_event(self, response: ModelResponse) -> None:
        """Emit a stream marker so the producer can hide pre-tool model text."""
        writer = get_stream_writer()
        result = getattr(response, "result", None) or []
        last_message = result[-1] if result else None
        has_tool_calls = bool(getattr(last_message, "tool_calls", None))
        writer({
            "status": "MODEL_CALL",
            "title": "",
            "message": "",
            "tool_name": "",
            "tool_type": "",
            "display_tool_name": "",
            "duration_ms": 0,
            "stream_resolve": not has_tool_calls,
        })

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """包装工具调用：向事件流发送开始/结束/失败事件，并统计调用次数。"""
        writer = get_stream_writer()
        tool_call_count = request.state.get("tool_call_count", 0)
        # 解析工具展示名（类型 + 友好名称）
        tool_type, display_tool_name = self.name_resolver_func(request.tool_call["name"])
        started_at = time.time()
        # 发送工具执行开始事件
        writer({
            "status": "START",
            "title": f"执行可用{tool_type}: {display_tool_name}",
            "message": f"正在调用插件工具 {display_tool_name}...",
            "tool_name": request.tool_call["name"],
            "tool_type": tool_type,
            "display_tool_name": display_tool_name,
            "duration_ms": 0,
        })
        request.state["tool_call_count"] = tool_call_count + 1
        try:
            tool_result = await handler(request)
            # 发送工具执行成功事件（含耗时）
            writer({
                "status": "END",
                "title": f"执行可用{tool_type}: {display_tool_name}",
                "message": str(tool_result.content),
                "tool_name": request.tool_call["name"],
                "tool_type": tool_type,
                "display_tool_name": display_tool_name,
                "duration_ms": round((time.time() - started_at) * 1000, 2),
            })
            return tool_result
        except Exception as err:
            # 发送工具执行失败事件，并返回错误 ToolMessage 供模型感知
            writer({
                "status": "ERROR",
                "title": f"执行可用{tool_type}: {display_tool_name}",
                "message": str(err),
                "tool_name": request.tool_call["name"],
                "tool_type": tool_type,
                "display_tool_name": display_tool_name,
                "duration_ms": round((time.time() - started_at) * 1000, 2),
                "error": {"message": str(err)},
            })
            return ToolMessage(content=str(err), name=request.tool_call["name"], tool_call_id=request.tool_call["id"])

class GeneralAgent:
    """通用聊天 Agent：聚合模型、工具、MCP、技能、知识库与多 Agent 编排能力。"""

    def __init__(self, agent_config: AgentConfig):
        self.agent_config = agent_config

        # 对话模型与 ReAct Agent（init_agent 中初始化）
        self.conversation_model = None
        self.react_agent = None

        # 各类工具集合
        self.tools = []
        self.mcp_agent_as_tools = []
        self.middlewares = []
        self.skill_agent_as_tools = []
        # 工具名 -> {name: 展示名, type: 类型} 的元数据映射
        self.tool_metadata_map: Dict[str, Dict[str, str]] = {}
        # 多 Agent 编排器（可选）
        self.orchestrator = None

        # 流式事件队列
        self.event_queue = asyncio.Queue()
        self.stop_streaming = False
        self.cancellable_stream = None
        self.last_stream_summary = None

    def wrap_event(self, data: Dict[Any, Any]):
        """发送流式事件"""
        return build_stream_event("event", data)

    async def init_agent(self):
        """初始化 Agent：依次装配 MCP、工具、技能、知识库、模型与编排器。"""
        # 1. 将 MCP 服务封装为可调用工具
        self.mcp_agent_as_tools = await self.setup_mcp_agent_as_tools()

        # 2. 加载数据库中的普通工具
        self.tools = await self.setup_tools()

        # 3. 将技能 Agent 封装为工具
        self.skill_agent_as_tools = await self.setup_agent_skill_as_tools()

        # 4. 装配知识库检索工具与对话模型
        await self.setup_knowledge_tool()
        await self.setup_language_model()

        # 5. 可选：构建多 Agent 演示编排器
        if self.agent_config.enable_multi_agent and self.conversation_model is not None:
            from agentchat.core.agents.orchestrator import build_demo_orchestrator

            self.orchestrator = build_demo_orchestrator(self.conversation_model)

        # 6. 装配中间件并创建 ReAct Agent
        self.middlewares = await self.setup_agent_middleware()
        self.react_agent = self.setup_react_agent()

    async def setup_agent_middleware(self):
        """装配 Agent 中间件：用于发送工具执行事件流。"""
        emit_event_middleware = EmitEventAgentMiddleware(self.get_tool_display_name)

        return [emit_event_middleware]


    async def setup_language_model(self):
        """初始化对话模型：优先使用配置的 llm_id，否则使用默认对话模型。"""
        # 普通对话模型
        if self.agent_config.llm_id:
            model_config = await LLMService.get_llm_by_id(self.agent_config.llm_id)
            self.conversation_model = ModelManager.get_user_model(**model_config)
        else:
            self.conversation_model = ModelManager.get_conversation_model()

    def setup_react_agent(self):
        """创建 ReAct Agent：聚合所有工具（普通工具 + MCP + 技能）与中间件。"""
        return create_agent(
            model=self.conversation_model,
            tools=self.tools + self.mcp_agent_as_tools + self.skill_agent_as_tools,
            middleware=self.middlewares,
            state_schema=StreamAgentState
        )

    async def setup_tools(self) -> List[BaseTool]:
        """加载数据库中的工具：用户自定义工具走 OpenAPI 适配，内置工具直接注册。"""
        def create_openapi_tool_executor(tool_adapter, tool_name):
            """闭包创建一个执行 OpenAPI Tool 的方法"""
            async def _execute_wrapper(**kwargs):
                return await tool_adapter.execute(
                    _tool_name=tool_name,
                    **kwargs
                )

            return _execute_wrapper

        tools = []
        db_tools = await ToolService.get_tools_from_id(self.agent_config.tool_ids)
        for db_tool in db_tools:
            if db_tool.is_user_defined:
                # 用户自定义工具：通过 OpenAPI Schema 适配为可执行工具
                tool_adapter = OpenAPIToolAdapter(
                    auth_config=db_tool.auth_config,
                    openapi_schema=db_tool.openapi_schema
                )

                for openapi_tool in tool_adapter.tools:
                    tools.append(
                        StructuredTool(
                            name=openapi_tool["function"].get("name", ""),
                            description=openapi_tool["function"].get("description", ""),
                            coroutine=create_openapi_tool_executor(tool_adapter, openapi_tool["function"].get("name")),
                            args_schema=openapi_tool
                        )
                    )

                    # 记录工具元数据（展示名 + 类型）
                    self.tool_metadata_map[openapi_tool["function"].get("name", "")] = {
                        "name": db_tool.display_name,
                        "type": "工具"
                    }
            else:
                # 内置工具：按名称从注册表获取
                agent_tool = AgentToolsWithName.get(db_tool.name)
                if agent_tool:
                    tools.append(agent_tool)
                self.tool_metadata_map[db_tool.name] = {
                    "name": db_tool.display_name,
                    "type": "工具"
                }

        return tools

    async def setup_agent_skill_as_tools(self) -> List[BaseTool]:
        """将技能 Agent 封装为工具：主 Agent 可通过调用工具触发技能执行。"""
        agent_skill_as_tools = []
        agent_skills = await AgentSkillService.get_agent_skills_by_ids(self.agent_config.agent_skill_ids)

        def create_skill_agent_as_tool(agent_skill: AgentSkill):
            """闭包：为单个技能创建可调用的工具包装。"""

            @tool(agent_skill.as_tool_name, description=agent_skill.description)
            async def call_skill_agent(query: str):
                """调用技能 Agent：将用户问题交给技能 Agent 处理并返回结果。"""
                skill_agent = SkillAgent(agent_skill, self.agent_config.user_id)
                await skill_agent.init_skill_agent()
                messages = await skill_agent.ainvoke([HumanMessage(content=query)])
                return "\n".join([message.content for message in messages])

            return call_skill_agent

        for agent_skill in agent_skills:
            # 记录技能工具元数据（类型为 Skill）
            self.tool_metadata_map[agent_skill.as_tool_name] = {
                "name": agent_skill.name,  # 技能的中文/友好名称
                "type": "Skill"
            }
            agent_skill_as_tools.append(create_skill_agent_as_tool(agent_skill))

        return agent_skill_as_tools


    async def setup_mcp_agent_as_tools(self):
        """将 MCP 服务封装为工具：主 Agent 可通过调用工具触发 MCP 服务执行。"""
        mcp_agent_as_tools = []

        def create_mcp_agent_as_tool(mcp_agent, mcp_as_tool_name, description):
            """闭包：为单个 MCP 服务创建可调用的工具包装。"""
            @tool(mcp_as_tool_name, description=description)
            async def call_mcp_agent(query: str):
                """
                用户想要根据这些 mcp 工具来完成的一些任务
                Args:
                    query: 用户询问的问题
                Returns:
                    根据该 MCP Agent 来完成的一些任务
                """

                messages = await mcp_agent.ainvoke([HumanMessage(content=query)])
                return "\n".join([message.content for message in messages])
            return call_mcp_agent

        for mcp_id in self.agent_config.mcp_ids:
            # 加载 MCP 服务配置并初始化 MCP Agent
            mcp_server = await MCPService.get_mcp_server_from_id(mcp_id)
            mcp_config = MCPConfig(**mcp_server)

            mcp_agent = MCPAgent(mcp_config, self.agent_config.user_id)
            await mcp_agent.init_mcp_agent()

            tool_name = mcp_server.get("mcp_as_tool_name")
            description = mcp_server.get("description")

            # 更新元数据映射（类型为 MCP）
            self.tool_metadata_map[tool_name] = {
                "name": mcp_config.server_name,
                "type": "MCP"
            }

            # 创建并添加工具
            mcp_agent_as_tools.append(
                create_mcp_agent_as_tool(mcp_agent, tool_name, description)
            )
        return mcp_agent_as_tools

    async def setup_knowledge_tool(self):
        """装配知识库检索工具：仅当绑定了知识库 ID 时才注册为工具。"""
        @tool(parse_docstring=True)
        async def retrival_knowledge(query: str) -> str:
            """
            仅检索知识库并返回命中原文，不做改写、解释或总结。

            Args:
                query (str): 用户问题

            Returns:
                str: 知识库命中的原文，多个片段以换行分隔；无结果时返回固定文本
                "No relevant documents found."。不得返回检索过程、候选 query 列表或改写列表。
            """
            # 调用 RAG 处理器检索并重排文档
            knowledge_message = await RagHandler.retrieve_ranked_documents(
                query, self.agent_config.knowledge_ids
            )
            return knowledge_message

        if self.agent_config.knowledge_ids: # 当绑定知识库ID后才 As Tool
            self.tools.append(retrival_knowledge)
            self.tool_metadata_map[retrival_knowledge.name] = {
                "name": "检索知识库",
                "type": "工具"
            }


    async def astream(self, messages: List[BaseMessage]) -> AsyncGenerator[Dict[str, Any], None]:
        """流式调用主方法：支持多 Agent 路由与可取消的流式输出。"""
        self.stop_streaming = False
        self.cancellable_stream = None
        response_content = ""

        # 若命中多 Agent 场景，则走编排器流式输出
        if await self._is_multi_agent_input(messages):
            self.last_stream_summary = None
            async for event in self._stream_multi_agent(messages):
                yield event
            return

        async def _produce(queue: asyncio.Queue) -> None:
            """生产者：从 ReAct Agent 拉取流式输出并写入队列。"""
            producer_content = ""
            pending_chunks = []
            stream_state = "wait"  # wait | emit | discard
            async for token, metadata in self.react_agent.astream(
                    input={"messages": copy.deepcopy(messages), "model_call_count": 0, "user_id": self.agent_config.user_id},
                    config={"callbacks": [usage_metadata_callback]},
                    stream_mode=["messages", "custom"],
            ):
                if self.stop_streaming:
                    break
                if token == "custom":
                    if metadata.get("status") == "MODEL_CALL":
                        if metadata.get("stream_resolve"):
                            if stream_state != "emit":
                                for chunk in pending_chunks:
                                    producer_content += chunk
                                    queue.put_nowait(build_stream_event("response_chunk", {
                                        "chunk": chunk,
                                        "accumulated": producer_content,
                                    }))
                            stream_state = "emit"
                        else:
                            stream_state = "discard"
                        pending_chunks = []
                        continue
                    if metadata.get("status") in ("START", "END"):
                        pending_chunks = []
                    # 自定义事件（工具执行等）直接透传
                    queue.put_nowait(self.wrap_event(metadata))
                elif isinstance(metadata[0], AIMessageChunk) and metadata[0].content:
                    # 文本增量：仅有最终模型回答时透传，调用工具前的中间文本不展示
                    if stream_state == "wait":
                        pending_chunks.append(metadata[0].content)
                    elif stream_state == "discard":
                        pending_chunks.append(metadata[0].content)
                    elif stream_state == "emit":
                        producer_content += metadata[0].content
                        queue.put_nowait(build_stream_event("response_chunk", {
                            "chunk": metadata[0].content,
                            "accumulated": producer_content,
                        }))

        # 使用可取消流包装生产者，支持客户端主动中断
        stream = CancellableAsyncStream(_produce)
        self.cancellable_stream = stream
        if self.stop_streaming:
            stream.request_cancel()

        try:
            async for event in stream:
                if event.get("type") == "response_chunk":
                    response_content += event["data"].get("chunk", "")
                yield event

        # 针对模型回复进行兜底操作，错误类型包括：敏感词，模型问题
        except Exception as err:
            logger.error(f"LLM Model Error: {err}")
            yield self.wrap_event({
                "status": "ERROR",
                "title": "模型执行失败",
                "message": str(err),
                "error": {"message": str(err)},
            })
            yield build_stream_event("response_chunk", {
                "chunk": "您的问题触及到我的知识盲区，请换个问题吧✨",
                "accumulated": response_content,
            })
        finally:
            # 清理流状态并记录取消摘要
            self.stop_streaming = False
            summary = stream.summary()
            self.last_stream_summary = summary
            if summary is not None and summary.get("cancelled"):
                logger.info(
                    f"Stream cancelled. total_duration_ms={summary.get('total_duration_ms')} "
                    f"cancel_to_terminate_ms={summary.get('cancel_to_terminate_ms')} "
                    f"trace_id={summary.get('trace_id')}"
                )
            self.cancellable_stream = None

    def stop_streaming_callback(self):
        """停止流式输出：置停止标记并请求取消当前流。"""
        self.stop_streaming = True
        if self.cancellable_stream is not None:
            self.cancellable_stream.request_cancel()

    def get_tool_display_name(self, tool_name: str):
        """
        根据工具的原始名称，解析出带有类型后缀的展示名称
        例如:
        - "gaode_weather" -> "执行Skill：高德天气"
        - "mcp_filesystem" -> "执行MCP：文件系统"
        - "search" -> "执行工具：search"
        """
        metadata = self.tool_metadata_map.get(tool_name)

        if not metadata:
            # 如果没有记录元数据，直接返回原始名称
            return "工具", tool_name

        friendly_name = metadata.get("name", tool_name)
        tool_type = metadata.get("type", "工具")

        return tool_type, friendly_name

    @staticmethod
    def _extract_user_input(messages: List[BaseMessage]) -> str:
        """提取最近一条人类消息的文本，用于确定性的路由判断。"""
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                content = message.content
                return content if isinstance(content, str) else str(content)
        return ""

    async def _is_multi_agent_input(self, messages: List[BaseMessage]) -> bool:
        """判断当前输入是否命中多 Agent 编排场景。"""
        if self.orchestrator is None:
            return False
        user_input = self._extract_user_input(messages)
        return bool(user_input) and self.orchestrator.should_route(user_input)

    async def _stream_multi_agent(
        self, messages: List[BaseMessage]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """运行演示编排器，输出主/子 Agent 的分层事件流。"""
        try:
            result = await self.orchestrator.run(messages)
            # 透传编排器产生的事件
            for event in result.get("events", []):
                yield self.wrap_event(event)
            response = result.get("response") or "编排 Agent 未返回有效结果。"
            yield build_stream_event("response_chunk", {
                "chunk": response,
                "accumulated": response,
            })
        except Exception as err:
            # 编排失败时输出错误事件与兜底文案
            logger.error(f"Multi-agent orchestration error: {err}")
            yield self.wrap_event({
                "status": "ERROR",
                "title": "多 Agent 编排失败",
                "message": str(err),
                "error": {"message": str(err)},
            })
            fallback = "多 Agent 编排执行失败，请稍后重试。"
            yield build_stream_event("response_chunk", {
                "chunk": fallback,
                "accumulated": fallback,
            })
