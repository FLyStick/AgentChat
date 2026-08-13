from agentchat.utils.contexts import trace_id
from agentchat.utils.events import build_stream_event


def test_build_stream_event_has_stable_fields_and_unique_id():
    first = build_stream_event("event", {"status": "START"})
    second = build_stream_event("event", {"status": "START"})
    assert first["type"] == "event"
    assert first["event_id"]
    assert first["event_id"] != second["event_id"]
    assert isinstance(first["timestamp"], float)
    assert first["trace_id"] is None
    assert first["data"] == {"status": "START"}


def test_build_stream_event_carries_trace_id():
    token = trace_id.set("trace-123")
    try:
        event = build_stream_event("event", {"status": "END"})
        assert event["trace_id"] == "trace-123"
    finally:
        trace_id.reset(token)
