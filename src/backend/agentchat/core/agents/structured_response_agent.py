from pydantic import BaseModel
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from agentchat.core.callbacks import usage_metadata_callback
from agentchat.core.models.manager import ModelManager


class StructuredResponseAgent:
    """结构化响应 Agent：强制模型按指定 Pydantic 格式输出结果。"""

    def __init__(self, response_format):
        self.response_format = response_format
        # 创建时即构建结构化输出 Agent
        self.structured_agent = self._create_structured_agent()

    def _create_structured_agent(self):
        """创建 Agent：通过 ToolStrategy 将响应格式绑定为工具输出。"""
        return create_agent(
            model=ModelManager.get_conversation_model(),
            response_format=ToolStrategy(self.response_format)
        )

    def get_structured_response(self, messages):
        """调用 Agent 获取结构化响应（已按 response_format 校验）。"""
        result = self.structured_agent.invoke(
            input={"messages": messages},
            config={"callbacks": [usage_metadata_callback]}
        )
        return result["structured_response"]