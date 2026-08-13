import asyncio

import pytest

from agentchat.settings import app_settings

# Importing the real orchestrator pulls in the database engine at module
# import time, so a syntactically valid MySQL URL must exist before it loads.
app_settings.mysql = {
    "endpoint": "mysql+pymysql://agentchat:agentchat@127.0.0.1:3306/agentchat",
    "async_endpoint": "mysql+aiomysql://agentchat:agentchat@127.0.0.1:3306/agentchat",
}

orchestrator = pytest.importorskip("agentchat.core.agents.orchestrator")


class FakeSubAgent:
    def __init__(self, name, keywords, response, events=None):
        self.name = name
        self.display_name = name.replace("_", " ").title()
        self.description = f"{name} demo subagent"
        self.keywords = keywords
        self.response = response
        self.events = events or []

    async def invoke(self, user_query):
        return {
            "agent_name": self.name,
            "display_name": self.display_name,
            "response": self.response,
            "events": self.events,
            "tool_calls": 1,
        }


def make_orchestrator():
    return orchestrator.MultiAgentOrchestrator(
        [
            FakeSubAgent(
                "policy_agent",
                ("policy", "overtime"),
                "policy answer",
                events=[{"event_type": "tool_start", "status": "START", "title": "policy tool", "message": "calling"}],
            ),
            FakeSubAgent(
                "hotel_agent",
                ("hotel", "wifi"),
                "hotel answer",
                events=[
                    {
                        "event_type": "tool_start",
                        "status": "START",
                        "title": "hotel tool",
                        "message": "calling",
                    }
                ],
            ),
        ]
    )


def test_keyword_routing_is_deterministic():
    orch = make_orchestrator()

    assert orch.should_route("hotel wifi")
    assert [agent.name for agent in orch.route("hotel")] == ["hotel_agent"]
    assert not orch.should_route("unknown topic")
    assert set(orch.keywords) == {"policy", "overtime", "hotel", "wifi"}


def test_unmatched_input_returns_empty_routes():
    result = asyncio.run(make_orchestrator().run("unknown topic"))

    assert result["routes"] == []
    event_types = [event["event_type"] for event in result["events"]]
    assert event_types == ["agent_start", "agent_end"]


def test_matched_input_emits_layered_events():
    result = asyncio.run(make_orchestrator().run("hotel wifi"))

    assert result["routes"] == ["hotel_agent"]
    event_types = [event["event_type"] for event in result["events"]]
    assert event_types == [
        "agent_start",
        "agent_plan",
        "sub_agent_start",
        "tool_start",
        "sub_agent_end",
        "agent_end",
    ]
    assert "hotel answer" in result["response"]

    nested_events = [event for event in result["events"] if event["event_type"] == "tool_start"]
    assert len(nested_events) == 1
    assert nested_events[0]["agent_type"] == "sub_agent"
    assert nested_events[0]["parent_agent_run_id"] == result["run_id"]


def test_multiple_routes_are_aggregated():
    result = asyncio.run(make_orchestrator().run("policy hotel"))

    assert set(result["routes"]) == {"policy_agent", "hotel_agent"}
    assert "policy answer" in result["response"]
    assert "hotel answer" in result["response"]
    assert len(result["subagent_runs"]) == 2


def test_subagent_result_normalization_keeps_inner_payload():
    nested = {"data": {"data": {"chunk": "inner"}}}
    assert orchestrator._nested_event_data(nested) == {"chunk": "inner"}
    assert orchestrator._nested_event_data({}) == {}
