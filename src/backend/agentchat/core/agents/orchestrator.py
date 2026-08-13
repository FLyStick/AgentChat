"""通用聊天 Agent 的多智能体编排层。

主 Agent 将用户请求路由到一个或多个子 Agent。每个子 Agent 维护自己独立的
ReAct 链，并发出分层事件，使前端能够区分主 Agent 的规划与子 Agent 的执行。
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, Iterable, List, Optional, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool

from agentchat.core.agents.react_agent import ReactAgent


def _nested_event_data(nested_event: Dict[str, Any]) -> Dict[str, Any]:
    """ReactAgent 在流式模式下会将子事件包装两层，这里归一化到内层数据。"""
    data = nested_event.get("data")
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data or {}


def _extract_text(user_input: Any) -> str:
    """从字符串或对话消息列表中提取最新用户文本。"""
    if isinstance(user_input, str):
        return user_input
    for message in reversed(user_input or []):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


@dataclass
class SubAgent:
    """子 Agent：拥有独立的模型、系统提示词和工具链。"""

    name: str
    display_name: str
    description: str
    keywords: Sequence[str]
    model: Any
    tools: Sequence[BaseTool]
    system_prompt: str
    fallback_content: str = ""

    def __post_init__(self) -> None:
        # 初始化时构建子 Agent 自己的 ReAct 执行链
        self.react_agent = ReactAgent(
            model=self.model,
            system_prompt=self.system_prompt,
            tools=list(self.tools),
        )

    async def invoke(self, user_query: Any) -> Dict[str, Any]:
        """运行子 Agent 的 ReAct 链，并收集其分层执行事件。"""
        events: List[Dict[str, Any]] = []
        chunks: List[str] = []
        tool_calls = 0

        if isinstance(user_query, list):
            input_messages = [
                SystemMessage(content=self.system_prompt),
                *(
                    message
                    for message in user_query
                    if not isinstance(message, SystemMessage)
                ),
            ]
        else:
            input_messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=user_query),
            ]

        async for nested_event in self.react_agent.astream(
            input_messages
        ):
            if nested_event["type"] == "event":
                data = _nested_event_data(nested_event)
                events.append(data)
                title = str(data.get("title", ""))
                # 统计工具调用次数：以“执行工具”开头的 START 事件为准
                if data.get("status") == "START" and "执行工具" in title:
                    tool_calls += 1
            elif nested_event["type"] == "response_chunk":
                chunks.append(str(_nested_event_data(nested_event).get("chunk", "")))

        response = "".join(chunks).strip()
        if not response:
            response = self.fallback_content or f"{self.display_name} 已完成检索，但未生成有效回复。"

        return {
            "agent_name": self.name,
            "display_name": self.display_name,
            "response": response,
            "events": events,
            "tool_calls": tool_calls,
        }


class MultiAgentOrchestrator:
    """确定性演示路由器：将请求委托给匹配的子 Agent。"""

    def __init__(self, subagents: Iterable[SubAgent]) -> None:
        self.subagents = {subagent.name: subagent for subagent in subagents}

    @property
    def keywords(self) -> List[str]:
        """汇总所有子 Agent 的关键词，便于上层判断是否可路由。"""
        merged: List[str] = []
        for subagent in self.subagents.values():
            merged.extend(subagent.keywords)
        return merged

    def route(self, user_input: Any) -> List[SubAgent]:
        """按固定意图关键词路由，保证演示输入的结果是确定性的。"""
        text = _extract_text(user_input)
        matched: List[SubAgent] = []
        for subagent in self.subagents.values():
            if any(keyword in text for keyword in subagent.keywords):
                matched.append(subagent)
        return matched

    def should_route(self, user_input: Any) -> bool:
        """判断当前输入是否命中任意子 Agent。"""
        return bool(self.route(user_input))

    async def run(self, user_input: Any) -> Dict[str, Any]:
        """执行匹配到的子 Agent，并返回聚合后的主运行结果。"""
        main_run_id = uuid.uuid4().hex
        events: List[Dict[str, Any]] = []

        def emit(event_type: str, agent_type: str, agent_name: str, status: str, title: str, message: str, **extra: Any) -> None:
            """向事件列表追加一条结构化事件，供前端渲染。"""
            events.append(
                {
                    "event_type": event_type,
                    "agent_type": agent_type,
                    "agent_name": agent_name,
                    "agent_run_id": main_run_id,
                    "status": status,
                    "title": title,
                    "message": message,
                    **extra,
                }
            )

        emit(
            "agent_start",
            "main_agent",
            "main",
            "START",
            "主 Agent 开始编排",
            "正在分析用户请求并分配子 Agent。",
        )

        matched = self.route(user_input)
        if not matched:
            emit(
                "agent_end",
                "main_agent",
                "main",
                "END",
                "主 Agent 未命中子 Agent",
                "当前输入未命中任何固定演示场景。",
            )
            return {
                "run_id": main_run_id,
                "routes": [],
                "response": "当前输入未命中固定演示场景。",
                "events": events,
                "subagent_runs": [],
            }

        plan_message = "、".join(sub.display_name for sub in matched)
        emit(
            "agent_plan",
            "main_agent",
            "main",
            "INFO",
            "主 Agent 路由计划",
            f"命中子 Agent：{plan_message}",
            routes=[sub.name for sub in matched],
        )

        subagent_runs: List[Dict[str, Any]] = []
        for subagent in matched:
            sub_run_id = uuid.uuid4().hex
            emit(
                "sub_agent_start",
                "sub_agent",
                subagent.name,
                "START",
                f"子 Agent 启动：{subagent.display_name}",
                subagent.description,
                parent_agent_run_id=main_run_id,
                agent_run_id=sub_run_id,
            )

            result = await subagent.invoke(user_input)
            # 将子 Agent 的内部事件透传到主事件流，并标记父运行 ID
            for nested in result["events"]:
                nested = dict(nested)
                events.append(
                    {
                        "event_type": nested.get("event_type", "agent_event"),
                        "agent_type": "sub_agent",
                        "agent_name": subagent.name,
                        "agent_run_id": sub_run_id,
                        "parent_agent_run_id": main_run_id,
                        "status": nested.get("status", "INFO"),
                        "title": f"{subagent.display_name} · {nested.get('title', '')}",
                        "message": nested.get("message", ""),
                    }
                )

            emit(
                "sub_agent_end",
                "sub_agent",
                subagent.name,
                "END",
                f"子 Agent 完成：{subagent.display_name}",
                result["response"],
                parent_agent_run_id=main_run_id,
                agent_run_id=sub_run_id,
                tool_calls=result["tool_calls"],
            )
            subagent_runs.append(
                {
                    "agent_name": subagent.name,
                    "display_name": subagent.display_name,
                    "agent_run_id": sub_run_id,
                    "tool_calls": result["tool_calls"],
                    "response": result["response"],
                }
            )

        # 汇总各子 Agent 的回复，按展示名分段拼接
        response = "\n\n".join(
            f"【{sub.display_name}】\n{run['response']}" for sub, run in zip(matched, subagent_runs)
        )
        emit(
            "agent_end",
            "main_agent",
            "main",
            "END",
            "主 Agent 完成编排",
            response,
            sub_agent_runs=[run["agent_name"] for run in subagent_runs],
        )

        return {
            "run_id": main_run_id,
            "routes": [sub.name for sub in matched],
            "response": response,
            "events": events,
            "subagent_runs": subagent_runs,
        }


def _policy_tools() -> List[BaseTool]:
    """构建制度 Agent 使用的查询工具集。"""
    @tool(parse_docstring=True)
    async def query_leave_policy(question: str) -> str:
        """查询请假、病假、事假等员工考勤制度。

        Args:
            question: 用户关于请假的原始问题。

        Returns:
            请假制度原文。
        """
        return "员工请假需提前1个工作日提交申请，病假可当日提交并附医院证明；请假由直属主管审批。"

    @tool(parse_docstring=True)
    async def query_expense_policy(question: str) -> str:
        """查询报销、发票、打款等财务制度。

        Args:
            question: 用户关于报销的原始问题。

        Returns:
            报销制度原文。
        """
        return "报销单需附原始发票，财务审核通过后5个工作日内打款；单笔超过5000元需要部门总监复核。"

    @tool(parse_docstring=True)
    async def query_overtime_policy(question: str) -> str:
        """查询加班、调休、时薪补偿等人力制度。

        Args:
            question: 用户关于加班的原始问题。

        Returns:
            加班制度原文。
        """
        return "加班需提前申请，工作日晚间加班按1.5倍时薪补偿，周末加班优先安排调休。"

    return [query_leave_policy, query_expense_policy, query_overtime_policy]


def _hotel_tools() -> List[BaseTool]:
    """构建酒店 Agent 使用的查询工具集。"""
    @tool(parse_docstring=True)
    async def query_hotel_checkin(question: str) -> str:
        """查询酒店入住、退房时间与行李寄存规则。

        Args:
            question: 用户关于入住退房的原始问题。

        Returns:
            酒店入住规则。
        """
        return "酒店入住时间为下午14:00，退房时间为中午12:00。前台24小时值班，行李可以寄存。"

    @tool(parse_docstring=True)
    async def query_hotel_wifi(question: str) -> str:
        """查询酒店无线网络名称和密码。

        Args:
            question: 用户关于网络的原始问题。

        Returns:
            酒店 WiFi 信息。
        """
        return "客房无线网络名称为GrandHotel-Guest，密码为CheckIn2026，公共区域免费连接。"

    @tool(parse_docstring=True)
    async def query_hotel_breakfast(question: str) -> str:
        """查询酒店早餐供应时间和价格。

        Args:
            question: 用户关于早餐的原始问题。

        Returns:
            酒店早餐信息。
        """
        return "自助早餐供应时间为06:30至10:00，地点在二楼餐厅；住客含双早，额外早餐每人68元。"

    return [query_hotel_checkin, query_hotel_wifi, query_hotel_breakfast]


def _project_tools() -> List[BaseTool]:
    """构建项目 Agent 使用的查询工具集。"""
    @tool(parse_docstring=True)
    async def query_deploy_command(question: str) -> str:
        """查询 AgentChat 后端启动部署命令。

        Args:
            question: 用户关于项目启动的原始问题。

        Returns:
            部署命令说明。
        """
        return "后端启动命令：python -m uvicorn agentchat.main:app --host 0.0.0.0 --port 8000；启动前需要配置数据库和向量库环境变量。"

    @tool(parse_docstring=True)
    async def query_rag_chain(question: str) -> str:
        """查询项目 RAG 检索链路组件。

        Args:
            question: 用户关于 RAG 链路的原始问题。

        Returns:
            RAG 链路说明。
        """
        return "知识库问答使用RAG链路：文档上传后切成固定长度分块，写入Chroma向量库；可选同步Elasticsearch关键词索引，检索后经过重排返回结果。"

    return [query_deploy_command, query_rag_chain]


def build_demo_orchestrator(model: Any) -> MultiAgentOrchestrator:
    """构建通用 Agent 使用的固定演示编排器。"""
    return MultiAgentOrchestrator(
        [
            SubAgent(
                name="policy_agent",
                display_name="制度 Agent",
                description="负责查询请假、报销、加班等公司制度，并格式化回答。",
                keywords=("请假", "病假", "事假", "报销", "加班", "审批"),
                model=model,
                tools=_policy_tools(),
                system_prompt="你是制度 Agent，只能使用制度查询工具；先调用对应工具，再用检索到的原文回答。",
                fallback_content="制度 Agent 未检索到有效内容。",
            ),
            SubAgent(
                name="hotel_agent",
                display_name="酒店 Agent",
                description="负责查询酒店入住、WiFi、早餐等信息，并格式化回答。",
                keywords=("酒店", "入住", "退房", "Wi-Fi", "wifi", "早餐", "客房"),
                model=model,
                tools=_hotel_tools(),
                system_prompt="你是酒店 Agent，只能使用酒店查询工具；先调用对应工具，再用检索到的原文回答。",
                fallback_content="酒店 Agent 未检索到有效内容。",
            ),
            SubAgent(
                name="project_agent",
                display_name="项目 Agent",
                description="负责查询 AgentChat 项目启动命令和 RAG 链路信息。",
                keywords=("项目", "启动命令", "RAG", "部署", "uvicorn"),
                model=model,
                tools=_project_tools(),
                system_prompt="你是项目 Agent，只能使用项目查询工具；先调用对应工具，再用检索到的原文回答。",
                fallback_content="项目 Agent 未检索到有效内容。",
            ),
        ]
    )
