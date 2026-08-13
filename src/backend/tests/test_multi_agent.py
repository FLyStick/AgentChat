import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agentchat.settings import app_settings

# Importing the real orchestrator pulls in the database engine at module
# import time, so a syntactically valid MySQL URL must exist before it loads.
app_settings.mysql = {
    "endpoint": "mysql+pymysql://agentchat:agentchat@127.0.0.1:3306/agentchat",
    "async_endpoint": "mysql+aiomysql://agentchat:agentchat@127.0.0.1:3306/agentchat",
}

orchestrator = pytest.importorskip("agentchat.core.agents.orchestrator")
general_agent = pytest.importorskip("agentchat.core.agents.general_agent")


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


class FakeReactAgent:
    def __init__(self):
        self.received = []

    async def astream(self, input_messages):
        self.received = input_messages
        yield {"type": "response_chunk", "data": {"chunk": "answer"}}


def make_config(enable_multi_agent=False):
    return general_agent.AgentConfig(
        user_id="user_1",
        llm_id="",
        mcp_ids=[],
        knowledge_ids=[],
        tool_ids=[],
        agent_skill_ids=[],
        system_prompt="demo prompt",
        enable_multi_agent=enable_multi_agent,
    )


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


def test_multi_agent_defaults_to_disabled():
    config = general_agent.AgentConfig(
        user_id="user_1",
        llm_id="",
        mcp_ids=[],
        knowledge_ids=[],
        tool_ids=[],
        agent_skill_ids=[],
        system_prompt="demo prompt",
    )

    assert config.enable_multi_agent is False


def test_general_agent_builds_orchestrator_only_when_enabled(monkeypatch):
    async def noop_setup(self):
        return None

    async def setup_model(self):
        self.conversation_model = object()

    monkeypatch.setattr(general_agent.GeneralAgent, "setup_mcp_agent_as_tools", noop_setup)
    monkeypatch.setattr(general_agent.GeneralAgent, "setup_tools", noop_setup)
    monkeypatch.setattr(general_agent.GeneralAgent, "setup_agent_skill_as_tools", noop_setup)
    monkeypatch.setattr(general_agent.GeneralAgent, "setup_knowledge_tool", noop_setup)
    monkeypatch.setattr(general_agent.GeneralAgent, "setup_agent_middleware", noop_setup)
    monkeypatch.setattr(general_agent.GeneralAgent, "setup_language_model", setup_model)
    monkeypatch.setattr(general_agent.GeneralAgent, "setup_react_agent", lambda self: None)

    enabled_agent = general_agent.GeneralAgent(make_config(enable_multi_agent=True))
    asyncio.run(enabled_agent.init_agent())
    assert enabled_agent.orchestrator is not None

    disabled_agent = general_agent.GeneralAgent(make_config(enable_multi_agent=False))
    asyncio.run(disabled_agent.init_agent())
    assert disabled_agent.orchestrator is None


def test_general_agent_routes_only_keyword_hits():
    agent = general_agent.GeneralAgent(make_config(enable_multi_agent=True))
    agent.orchestrator = make_orchestrator()

    assert asyncio.run(agent._is_multi_agent_input([HumanMessage(content="hotel wifi")])) is True
    assert asyncio.run(agent._is_multi_agent_input([HumanMessage(content="hello world")])) is False


class RecordingOrchestrator:
    def __init__(self):
        self.captured = None

    async def run(self, user_input):
        self.captured = user_input
        return {
            "run_id": "run_1",
            "routes": [],
            "response": "routed",
            "events": [],
            "subagent_runs": [],
        }


def test_stream_multi_agent_passes_full_message_context():
    agent = general_agent.GeneralAgent(make_config(enable_multi_agent=True))
    recorder = RecordingOrchestrator()
    agent.orchestrator = recorder
    messages = [
        SystemMessage(content="main system prompt"),
        HumanMessage(content="hotel wifi"),
    ]

    async def collect():
        return [event async for event in agent._stream_multi_agent(messages)]

    events = asyncio.run(collect())

    assert recorder.captured is messages
    assert any(event["type"] == "response_chunk" for event in events)


def test_subagent_invoke_receives_system_history_and_latest_human():
    subagent = orchestrator.SubAgent(
        name="hotel_agent",
        display_name="Hotel Agent",
        description="hotel policy",
        keywords=("hotel",),
        model=object(),
        tools=[],
        system_prompt="sub system prompt",
    )
    fake = FakeReactAgent()
    subagent.react_agent = fake
    messages = [
        SystemMessage(content="main system prompt"),
        AIMessage(content="previous answer"),
        HumanMessage(content="hotel wifi"),
    ]

    result = asyncio.run(subagent.invoke(messages))

    assert result["response"] == "answer"
    assert [message.content for message in fake.received] == [
        "sub system prompt",
        "previous answer",
        "hotel wifi",
    ]
