import time
import uuid
from typing import Any, Dict, Optional

from agentchat.utils.contexts import trace_id as _trace_id


def current_trace_id() -> Optional[str]:
    """获取当前请求的 trace_id（来自上下文变量，可能为 None）。"""
    return _trace_id.get()


def build_stream_event(event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    构建统一的 SSE 流式事件结构。

    Args:
        event_type (str): 事件类型，如 response_chunk / agent_start / tool_call 等
        data (Dict[str, Any]): 事件携带的业务数据

    Returns:
        Dict[str, Any]: 标准事件字典，包含类型、唯一ID、时间戳、trace_id 与业务数据
    """
    return {
        "type": event_type,                    # 事件类型
        "event_id": uuid.uuid4().hex,          # 事件唯一 ID（用于前端去重/追踪）
        "timestamp": time.time(),              # 事件产生时间戳（秒）
        "trace_id": current_trace_id(),        # 关联的请求链路 ID，便于日志串联
        "data": data,                          # 事件业务数据
    }
