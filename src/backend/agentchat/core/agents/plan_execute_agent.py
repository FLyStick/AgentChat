import asyncio
import json
from typing import List
from loguru import logger
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import BaseTool

from agentchat.api.services.mcp_server import MCPService
from agentchat.api.services.mcp_user_config import MCPUserConfigService
from agentchat.core.models.manager import ModelManager
from agentchat.prompts.completion import FIX_JSON_PROMPT, PLAN_CALL_TOOL_PROMPT, SINGLE_PLAN_CALL_PROMPT
from agentchat.schemas.completion import PlanToolFlow
from agentchat.core.agents.structured_response_agent import StructuredResponseAgent
from agentchat.services.mcp.manager import MCPManager
from agentchat.utils.convert import convert_mcp_config

# Plan-and-Execute（先规划后执行）Agent 执行范式
class PlanExecuteAgent:
    """
    基于规划策略的对话式 AI Agent：通过战略规划来执行工具和函数。

    PlanExecuteAgent 用于分析用户查询、制定执行计划并编排工具调用，以提供全面的回答。
    它同时支持插件函数和 MCP（Model Context Protocol）工具，具备实时事件流和错误处理能力。

    核心特性：
        - 工具执行前先进行战略规划
        - 同时支持同步与异步函数
        - 集成 MCP（Model Context Protocol）工具
        - 实时事件流
        - 对格式错误的响应自动进行 JSON 修复
        - 完善的错误处理与日志记录

    属性：
        user_id (str): 用户标识，用于个性化与配置
        tools (List[BaseTool]): Agent 可用的工具列表
        mcp_ids (List[str]): 需要集成的 MCP 服务器 ID 列表
        mcp_manager (MCPManager): MCP 工具与配置的管理器
        mcp_tools (List[BaseTool]): 动态加载的 MCP 工具
        conversation_model: 通用对话模型
        tool_call_model: 专门用于工具调用的模型

    示例：
        工具的基本用法：

        ```python
        from langchain_core.tools import tool

        @tool
        def get_weather(city: str) -> str:
            '''获取某个城市的当前天气'''
            return f"Weather in {city}: 22°C, sunny"

        agent = PlanExecuteAgent(
            user_id="user_123",
            tools=[get_weather],
            mcp_ids=["mcp_server_1"]
        )

        messages = [HumanMessage(content="What's the weather like in Tokyo?")]
        response = await agent.ainvoke(messages)
        print(response)
        ```

    注意：
        - 工具应包含合适的描述，以便有效规划
        - MCP 服务器必须正确配置且可访问
        - Agent 会自动尝试修复 JSON 解析错误
        - 规划阶段先于工具执行，用于战略决策
    """
    def __init__(self,
                 user_id: str,
                 tools: List[BaseTool],
                 mcp_ids: List[str]):
        self.tools = tools
        self.user_id = user_id
        self.mcp_ids = mcp_ids
        self.mcp_manager: MCPManager = None

        self.mcp_tools = []
        self.conversation_model = ModelManager.get_conversation_model()
        self.tool_call_model = ModelManager.get_tool_invocation_model()

    async def setup_mcp_tools(self):
        """加载 MCP 工具：首次调用时初始化 MCP 管理器。"""
        if not self.mcp_manager:
            mcp_servers = []
            for mcp_id in self.mcp_ids:
                mcp_server = await MCPService.get_mcp_server_from_id(mcp_id)
                mcp_servers.append(mcp_server)
                self.mcp_servers = mcp_servers
            self.mcp_manager = MCPManager(convert_mcp_config(mcp_servers))

        return await self.mcp_manager.get_mcp_tools()

    async def _plan_agent_actions(self, messages: List[BaseMessage]):
        """规划阶段：让模型输出结构化执行计划（JSON），解析失败时自动修复。"""
        structured_response_agent = StructuredResponseAgent(response_format=PlanToolFlow)

        call_messages: List[BaseMessage] = []
        call_messages.extend(messages)

        if isinstance(call_messages[0], SystemMessage):
            call_messages[0] = SystemMessage(
                content=PLAN_CALL_TOOL_PROMPT.format(user_query=messages[-1].content,
                                                     tools_info="\n\n".join([str(tool.args_schema.model_dump()) for tool in self.tools + self.mcp_tools])))
        else:
            call_messages.insert(0, SystemMessage(content=PLAN_CALL_TOOL_PROMPT.format(user_query=messages[-1].content, tools_info="\n\n".join([str(tool_schema) for tool_schema in self.plugin_tools_schema + self.mcp_tools_schema]))))

        response = structured_response_agent.get_structured_response(call_messages)

        try:
            content = json.loads(response.content)
            self.agent_plans = content
            return content
        except Exception as err:
            # 解析失败：将错误信息回传给模型进行 JSON 修复
            fix_message = HumanMessage(
                content=FIX_JSON_PROMPT.format(json_content=response.content, json_error=str(err)))
            fix_response = await self.conversation_model.ainvoke([fix_message])

            try:
                fix_content = json.loads(fix_response.content)
                self.agent_plans = fix_content
                # 修复成功，返回修复后的计划
                return fix_content
            except Exception as fix_err:
                # 修复失败：抛出异常
                raise ValueError(fix_err)

    async def _execute_agent_actions(self, agent_plans):
        """执行阶段：按计划逐步调用工具，收集工具结果。"""
        tool_call_model = self.tool_call_model.bind_tools(self.tools + self.mcp_tools)

        tool_results: List[BaseMessage] = []
        for step, plan in agent_plans.items():
            # 计划要求询问用户时，直接返回计划内容并结束
            if plan[0].get("tool_name") == "call_user":
                tool_results.append(AIMessage(content=str(plan)))
                break

            # 为每一步构造独立的调用提示词
            call_tool_messages = []
            system_message = HumanMessage(content=SINGLE_PLAN_CALL_PROMPT.format(plan_actions=str(plan)))
            call_tool_messages.append(system_message)
            call_tool_messages.extend(tool_results)

            response = await tool_call_model.ainvoke(call_tool_messages)
            # 判断是否有可调用的工具
            if response.tool_calls:
                return response
            else:
                # 无可用工具：构造空结果消息
                ai_message = AIMessage(content="No available tools found")

            tool_messages = await self._execute_tool(ai_message)
            tool_results.append(ai_message)
            tool_results.extend(tool_messages)

        return tool_results

    async def _execute_tool(self, message: AIMessage):
        """工具执行：负责具体工具调用的子流程。"""
        tool_calls = message.tool_calls
        tool_messages: List[BaseMessage] = []

        for tool_call in tool_calls:
            is_mcp_tool, use_tool = self._find_tool_use(tool_call["name"])
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_call_id = tool_call["id"]

            try:
                if hasattr(use_tool, "coroutine") and use_tool.coroutine is not None:
                    # 判断是否需要补充用户个人配置（如鉴权信息）
                    if is_mcp_tool:
                        personal_config = await MCPUserConfigService.get_mcp_user_config(self.user_id, self._get_mcp_id_by_tool(tool_name))
                        tool_args.update(personal_config)

                    tool_result, _ = await use_tool.coroutine(**tool_args)
                else:
                    # 同步函数转为异步执行
                    tool_result = await asyncio.to_thread(use_tool.func, **tool_args)

                tool_messages.append(
                    ToolMessage(content=tool_result, name=tool_name, tool_call_id=tool_call_id))
                logger.info(f"Plugin Tool {tool_name}, Args: {tool_args}, Result: {tool_result}")

            except Exception as err:
                logger.error(f"Plugin Tool {tool_name} Error: {str(err)}")
                tool_messages.append(
                    ToolMessage(content=str(err), name=tool_name, tool_call_id=tool_call_id))

        return tool_messages

    async def astream(self, messages: List[BaseMessage]):
        """流式版本：规划 -> 执行工具 -> 对话模型流式输出最终回答。"""
        await self.setup_mcp_tools()

        agent_plans = await self._plan_agent_actions(messages)
        if agent_plans:
            tool_results = await self._execute_agent_actions(agent_plans)
        else:
            tool_results = []

        messages.extend(tool_results)
        try:
            response_content = ""
            async for chunk in self.conversation_model.astream(messages):
                if chunk.content:
                    response_content += chunk.content
                    yield {
                        "content": chunk.content
                    }
        except Exception as err:
            logger.error(f"LLM stream error: {err}")


    async def ainvoke(self, messages: List[BaseMessage]):
        """非流式版本：规划 -> 执行工具 -> 对话模型生成最终回答。"""
        await self.setup_mcp_tools()

        agent_plans = await self._plan_agent_actions(messages)
        if agent_plans:
            tool_results = await self._execute_agent_actions(agent_plans)
        else:
            tool_results = []

        messages.extend(tool_results)
        response = await self.conversation_model.ainvoke(messages)
        return response.content

    def _get_mcp_id_by_tool(self, tool_name):
        """根据工具名反查其所属的 MCP 服务器 ID。"""
        for server in self.mcp_servers:
            if tool_name in server["tools"]:
                return server["mcp_server_id"]
        return None

    def _find_tool_use(self, tool_name):
        """查找工具：返回 (是否为 MCP 工具, 工具实例)。"""
        if tool_name in [tool.name for tool in self.tools]:
            return True, self.tools[tool_name]
        elif tool_name in [tool.name for tool in self.mcp_tools]:
            return True, self.mcp_tools[tool_name]
        return False, None