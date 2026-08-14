import json
from typing import List, Callable
import anyio
from fastapi import APIRouter, Depends
from loguru import logger
from langchain_core.messages import HumanMessage, SystemMessage, BaseMessage

from agentchat.core.agents.general_agent import GeneralAgent, AgentConfig
from agentchat.api.services.history import HistoryService
from agentchat.api.services.dialog import DialogService
from agentchat.api.responses.streaming import WatchedStreamingResponse
from agentchat.api.services.user import UserPayload, get_login_user
from agentchat.prompts.completion import SYSTEM_PROMPT
from agentchat.schemas.completion import CompletionReq
from agentchat.services.memory.client import memory_client
from agentchat.utils.common import count_tokens_usage
from agentchat.utils.contexts import set_user_id_context, set_agent_name_context
from agentchat.utils.events import build_stream_event
from agentchat.utils.helpers import build_completion_system_prompt, build_completion_user_input

router = APIRouter(tags=["Completion"])

@router.post("/completion", description="对话接口")
async def completion(
    *,
    req: CompletionReq,
    login_user: UserPayload = Depends(get_login_user)
):
    """
    实时对话接口（SSE流式）
    """

    # Agent 初始化
    db_config = await DialogService.get_agent_by_dialog_id(req.dialog_id)
    agent_config = AgentConfig(**db_config)
    agent_config.user_id = login_user.user_id

    # 设置上下文信息
    set_user_id_context(login_user.user_id)
    set_agent_name_context(agent_config.name)

    chat_agent = GeneralAgent(agent_config)
    await chat_agent.init_agent()

    # 输入处理
    raw_input = req.user_input

    user_input = build_completion_user_input(
        file_url=req.file_url,
        user_input=raw_input
    )

    # Prompt 构建
    system_prompt = agent_config.system_prompt.strip() or SYSTEM_PROMPT
    if agent_config.knowledge_ids:
        system_prompt += (
            "\n\n【知识库工具约束】"
            "\n当问题可以从知识库回答时，必须先调用 retrival_knowledge。"
            "\n只能依据工具返回的原文回答，直接引用其中的事实；"
            "\n不得输出查询改写过程、候选 query 列表或工具内部处理过程。"
            "\n工具返回 No relevant documents found. 时，明确说明未检索到相关内容。"
        )

    short_history = await HistoryService.get_short_term_messages(
        req.dialog_id, login_user.user_id
    )
    history_summary = await DialogService.get_dialog_history_summary(req.dialog_id)

    long_memory = None
    if agent_config.enable_memory:
        memories = await memory_client.search(
            query=raw_input,
            run_id=req.dialog_id
        )
        long_memory = "\n".join(
            m.get("memory", "") for m in memories.get("results", [])
        )

    system_prompt = build_completion_system_prompt(
        system_prompt,
        history_summary,
        long_memory
    )

    messages: List[BaseMessage] = [
        SystemMessage(content=system_prompt),
        *short_history,
        HumanMessage(content=user_input),
    ]

    # 事件 & 流式响应
    events: list = []

    async def stream():
        agent_stream = chat_agent.astream(messages)
        response_content = " "
        try:
            async for event in agent_stream:

                if event.get("type") == "response_chunk":
                    chunk = event["data"].get("chunk", "")
                    response_content += chunk
                else:
                    events.append(event)

                yield f"data: {json.dumps(event)}\n\n"

        finally:
            with anyio.CancelScope(shield=True):
                try:
                    active_stream = getattr(chat_agent, "cancellable_stream", None)
                    if active_stream is not None and active_stream.is_cancelled():
                        await active_stream.finish_cancelled()
                    await agent_stream.aclose()
                except BaseException as exc:
                    logger.warning(
                        f"Failed to finalize agent stream on disconnect: {type(exc).__name__}: {exc}"
                    )

                cancel_summary = getattr(chat_agent, "last_stream_summary", None)
                if cancel_summary is not None and cancel_summary.get("cancelled"):
                    events.append(build_stream_event("stream_cancel", cancel_summary))

                if agent_config.enable_memory:
                    await memory_client.add(
                        messages=[
                            {"role": "user", "content": raw_input},
                            {"role": "assistant", "content": response_content}
                        ],
                        run_id=req.dialog_id
                    )

                await HistoryService.save_chat_history(
                    role="assistant",
                    content=response_content,
                    events=events,
                    dialog_id=req.dialog_id,
                    token_usage=count_tokens_usage(response_content),
                    memory_enable=agent_config.enable_memory
                )

                await DialogService.update_dialog_summary(
                    dialog_id=req.dialog_id,
                    user_id=login_user.user_id,
                )

    # 用户消息先落库
    await HistoryService.save_chat_history(
        role="user",
        content=raw_input,
        events=events,
        dialog_id=req.dialog_id,
        token_usage=count_tokens_usage(raw_input),
        memory_enable=agent_config.enable_memory
    )

    # 返回流式响应
    return WatchedStreamingResponse(
        content=stream(),
        callback=chat_agent.stop_streaming_callback,
        media_type="text/event-stream"
    )
