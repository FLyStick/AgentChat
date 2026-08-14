from agentchat.core.agents.general_agent import AgentConfig


def test_agent_config_keeps_agent_table_name():
    db_config = {
        "id": "agent_1",
        "name": "policy_agent",
        "description": "policy helper",
        "logo_url": "",
        "user_id": "user_1",
        "is_custom": True,
        "system_prompt": "you are a policy agent",
        "llm_id": "llm_1",
        "enable_memory": True,
        "enable_multi_agent": False,
        "mcp_ids": [],
        "tool_ids": [],
        "agent_skill_ids": [],
        "knowledge_ids": [],
        "update_time": "2026-08-14T00:00:00",
        "create_time": "2026-08-14T00:00:00",
    }

    config = AgentConfig(**db_config)

    assert config.agent_id == "agent_1"
    assert config.name == "policy_agent"
