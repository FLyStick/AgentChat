from loguru import logger
from langchain_core.messages import HumanMessage, SystemMessage

from agentchat.core.models.manager import ModelManager
from agentchat.prompts.rewrite import system_query_rewrite
from agentchat.prompts.rewrite import user_query_write
from agentchat.utils.query_array import parse_query_list

class QueryRewrite:
    def __init__(self):
        self.client = ModelManager.get_conversation_model()

    async def rewrite(self, user_input):
        rewrite_prompt = user_query_write.format(user_input=user_input)
        response = self.client.invoke([SystemMessage(content=system_query_rewrite), HumanMessage(content=rewrite_prompt)])
        return self._extract_query_list(response.content, user_input)

    @staticmethod
    def _extract_query_list(content, user_input):
        return parse_query_list(content, user_input)

query_rewriter = QueryRewrite()
