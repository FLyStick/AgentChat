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
    user_id: str
    llm_id: str
    mcp_ids: List[str]
    knowledge_ids: List[str]
    tool_ids: List[str]
    agent_skill_ids: List[str]
    system_prompt: str
    enable_memory: bool = False
    name: str = None
    enable_multi_agent: bool = True



class EmitEventAgentMiddleware(AgentMiddleware):
    def __init__(self, name_resolver_func):
        super().__init__()

        self.name_resolver_func = name_resolver_func

    async def aafter_model(
        self, state: StreamAgentState, runtime: Runtime
    ) -> dict[str, Any] | None:
        last_message = state["messages"][-1]
        if last_message.tool_calls:
            return {
                "model_call_count": state["model_call_count"] + 1
            }

        return {
            "jump_to": "end"
        }

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        try:
            response = await handler(request)
            return response
        except Exception as err:
            logger.error(f"Model call error: {err}")
            raise ValueError(err)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        writer = get_stream_writer()
        tool_call_count = request.state.get("tool_call_count", 0)
        # 发送工具分析开始事件
        tool_type, display_tool_name = self.name_resolver_func(request.tool_call["name"])
        started_at = time.time()
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
    def __init__(self, agent_config: AgentConfig):
        self.agent_config = agent_config

        self.conversation_model = None
        self.react_agent = None

        self.tools = []
        self.mcp_agent_as_tools = []
        self.middlewares = []
        self.skill_agent_as_tools = []
        self.tool_metadata_map: Dict[str, Dict[str, str]] = {}
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
        self.mcp_agent_as_tools = await self.setup_mcp_agent_as_tools()

        self.tools = await self.setup_tools()

        self.skill_agent_as_tools = await self.setup_agent_skill_as_tools()

        await self.setup_knowledge_tool()
        await self.setup_language_model()

        if self.agent_config.enable_multi_agent and self.conversation_model is not None:
            from agentchat.core.agents.orchestrator import build_demo_orchestrator

            self.orchestrator = build_demo_orchestrator(self.conversation_model)

        self.middlewares = await self.setup_agent_middleware()
        self.react_agent = self.setup_react_agent()

    async def setup_agent_middleware(self):
        emit_event_middleware = EmitEventAgentMiddleware(self.get_tool_display_name)

        return [emit_event_middleware]


    async def setup_language_model(self):
        # 普通对话模型
        if self.agent_config.llm_id:
            model_config = await LLMService.get_llm_by_id(self.agent_config.llm_id)
            self.conversation_model = ModelManager.get_user_model(**model_config)
        else:
            self.conversation_model = ModelManager.get_conversation_model()

    def setup_react_agent(self):
        return create_agent(
            model=self.conversation_model,
            tools=self.tools + self.mcp_agent_as_tools + self.skill_agent_as_tools,
            middleware=self.middlewares,
            state_schema=StreamAgentState
        )

    async def setup_tools(self) -> List[BaseTool]:
        def create_openapi_tool_executor(tool_adapter, tool_name):
            """闭包创建一个执行OpenAPI Tool的方法"""
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

                    self.tool_metadata_map[openapi_tool["function"].get("name", "")] = {
                        "name": db_tool.display_name,
                        "type": "工具"
                    }
            else:
                agent_tool = AgentToolsWithName.get(db_tool.name)
                if agent_tool:
                    tools.append(agent_tool)
                self.tool_metadata_map[db_tool.name] = {
                    "name": db_tool.display_name,
                    "type": "工具"
                }

        return tools

    async def setup_agent_skill_as_tools(self) -> List[BaseTool]:
        agent_skill_as_tools = []
        agent_skills = await AgentSkillService.get_agent_skills_by_ids(self.agent_config.agent_skill_ids)

        def create_skill_agent_as_tool(agent_skill: AgentSkill):

            @tool(agent_skill.as_tool_name, description=agent_skill.description)
            async def call_skill_agent(query: str):
                """调用技能Agent"""
                skill_agent = SkillAgent(agent_skill, self.agent_config.user_id)
                await skill_agent.init_skill_agent()
                messages = await skill_agent.ainvoke([HumanMessage(content=query)])
                return "\n".join([message.content for message in messages])

            return call_skill_agent

        for agent_skill in agent_skills:
            self.tool_metadata_map[agent_skill.as_tool_name] = {
                "name": agent_skill.name,  # 技能的中文/友好名称
                "type": "Skill"
            }
            agent_skill_as_tools.append(create_skill_agent_as_tool(agent_skill))

        return agent_skill_as_tools


    async def setup_mcp_agent_as_tools(self):
        mcp_agent_as_tools = []

        def create_mcp_agent_as_tool(mcp_agent, mcp_as_tool_name, description):
            @tool(mcp_as_tool_name, description=description)
            async def call_mcp_agent(query: str):
                """
                用户想要根据这些mcp工具来完成的一些任务
                Args:
                    query: 用户询问的问题
                Returns:
                    根据该MCP Agent来完成的一些任务
                """

                messages = await mcp_agent.ainvoke([HumanMessage(content=query)])
                return "\n".join([message.content for message in messages])
            return call_mcp_agent

        for mcp_id in self.agent_config.mcp_ids:
            mcp_server = await MCPService.get_mcp_server_from_id(mcp_id)
            mcp_config = MCPConfig(**mcp_server)

            mcp_agent = MCPAgent(mcp_config, self.agent_config.user_id)
            await mcp_agent.init_mcp_agent()

            tool_name = mcp_server.get("mcp_as_tool_name")
            description = mcp_server.get("description")

            # 更新元数据映射
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
        @tool(parse_docstring=True)
        async def retrival_knowledge(query: str) -> str:
            """
            通过检索知识库来获取信息

            Args:
                query (str): 用户问题

            Returns:
                str: 返回从知识库检索来的信息
            """
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
        """流式调用主方法"""
        self.stop_streaming = False
        self.cancellable_stream = None
        response_content = ""

        if await self._is_multi_agent_input(messages):
            self.last_stream_summary = None
            async for event in self._stream_multi_agent(messages):
                yield event
            return

        async def _produce(queue: asyncio.Queue) -> None:
            producer_content = ""
            async for token, metadata in self.react_agent.astream(
                    input={"messages": copy.deepcopy(messages), "model_call_count": 0, "user_id": self.agent_config.user_id},
                    config={"callbacks": [usage_metadata_callback]},
                    stream_mode=["messages", "custom"],
            ):
                if self.stop_streaming:
                    break
                if token == "custom":
                    queue.put_nowait(self.wrap_event(metadata))
                elif isinstance(metadata[0], AIMessageChunk) and metadata[0].content:
                    producer_content += metadata[0].content
                    queue.put_nowait(build_stream_event("response_chunk", {
                        "chunk": metadata[0].content,
                        "accumulated": producer_content,
                    }))

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
        """Extract the latest human message text for deterministic routing."""
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                content = message.content
                return content if isinstance(content, str) else str(content)
        return ""

    async def _is_multi_agent_input(self, messages: List[BaseMessage]) -> bool:
        if self.orchestrator is None:
            return False
        user_input = self._extract_user_input(messages)
        return bool(user_input) and self.orchestrator.should_route(user_input)

    async def _stream_multi_agent(
        self, messages: List[BaseMessage]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Run the demo orchestrator and emit layered main/sub-agent events."""
        user_input = self._extract_user_input(messages)
        try:
            result = await self.orchestrator.run(user_input)
            for event in result.get("events", []):
                yield self.wrap_event(event)
            response = result.get("response") or "编排 Agent 未返回有效结果。"
            yield build_stream_event("response_chunk", {
                "chunk": response,
                "accumulated": response,
            })
        except Exception as err:
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
