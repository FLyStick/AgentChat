import time
import uuid
from typing import Any, Dict, Optional

from agentchat.utils.contexts import trace_id as _trace_id


def current_trace_id() -> Optional[str]:
    return _trace_id.get()


def build_stream_event(event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": event_type,
        "event_id": uuid.uuid4().hex,
        "timestamp": time.time(),
        "trace_id": current_trace_id(),
        "data": data,
    }
