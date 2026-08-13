from typing import List, Optional
from pydantic import BaseModel

from langchain.tools import BaseTool
from langchain.agents import create_agent
from langgraph.config import get_stream_writer
from langgraph.prebuilt.tool_node import ToolCallRequest
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langchain.agents.middleware import AgentState, wrap_tool_call, before_agent

from agentchat.api.services.mcp_user_config import MCPUserConfigService
from agentchat.core.models.manager import ModelManager
from agentchat.prompts.completion import CALL_END_PROMPT
from agentchat.services.mcp.manager import MCPManager
from agentchat.utils.convert import convert_mcp_config


class MCPConfig(BaseModel):
    """MCP 服务配置：连接地址、传输类型、可用工具列表与服务标识。"""
    url: str
    type: str = "sse"
    tools: List[str] = []
    server_name: str
    mcp_server_id: str


class MCPAgent:
    """MCP Agent：将 MCP 服务封装为可对话的 Agent，支持工具调用与事件流。"""

    def __init__(self, mcp_config: MCPConfig, user_id: str):
        self.mcp_config = mcp_config
        # 初始化 MCP 管理器（负责连接与工具发现）
        self.mcp_manager = MCPManager([convert_mcp_config(mcp_config.model_dump())])

        self.user_id = user_id
        self.mcp_tools: List[BaseTool] = []

        # 对话模型与工具调用模型
        self.conversation_model = None
        self.tool_invocation_model = None

        self.react_agent = None
        self.middlewares = None

    async def init_mcp_agent(self):
        """初始化 MCP Agent：加载工具、模型、中间件并创建 ReAct Agent。"""
        if self.mcp_config:
            self.mcp_tools = await self.setup_mcp_tools()

        await self.setup_language_model()

        self.middlewares = await self.setup_agent_middlewares()

        self.react_agent = self.setup_react_agent()

    async def emit_event(self, event):
        """向当前流写入一条事件（供中间件发送工具执行状态）。"""
        writer = get_stream_writer()
        writer(event)

    async def setup_language_model(self):
        # 普通对话模型
        self.conversation_model = ModelManager.get_conversation_model()

        # 工具调用模型
        self.tool_invocation_model = ModelManager.get_tool_invocation_model()

    async def setup_mcp_tools(self):
        """从 MCP 管理器获取该服务暴露的工具列表。"""
        mcp_tools = await self.mcp_manager.get_mcp_tools()
        return mcp_tools

    async def setup_agent_middlewares(self):
        """装配中间件：在工具调用前后注入用户配置并发送事件流。"""

        @wrap_tool_call
        async def add_tool_call_args(
            request: ToolCallRequest,
            handler
        ):
            # 发送工具调用开始事件
            await self.emit_event(
                {
                    "status": "START",
                    "title": f"Sub-Agent - {self.mcp_config.server_name}执行可用工具: {request.tool_call["name"]}",
                    "messages": f"正在调用工具 {request.tool_call["name"]}..."
                }
            )

            # 针对鉴权的MCP Server需要用户的单独配置，例如飞书、邮箱
            mcp_config = await MCPUserConfigService.get_mcp_user_config(self.user_id, self.mcp_config.mcp_server_id)
            request.tool_call["args"].update(mcp_config)

            tool_result = await handler(request)

            # 发送工具调用结束事件
            await self.emit_event(
                {
                    "status": "END",
                    "title": f"Sub-Agent - {self.mcp_config.server_name}执行可用工具: {request.tool_call["name"]}",
                    "messages": f"{tool_result}"
                }
            )
            return tool_result

        return [add_tool_call_args]

    def setup_react_agent(self):
        """创建 ReAct Agent：使用对话模型 + MCP 工具 + 中间件。"""
        return create_agent(
            model=self.conversation_model,
            tools=self.mcp_tools,
            middleware=self.middlewares,
            system_prompt=CALL_END_PROMPT
        )


    async def ainvoke(self, messages: List[BaseMessage]) -> List[BaseMessage] | str:
        """非流式版本"""
        result = await self.react_agent.ainvoke({"messages": messages})
        messages = []

        for message in result["messages"][:-1]:
            if not isinstance(message, HumanMessage) and not isinstance(message, SystemMessage):
                messages.append(message)
        return messages
