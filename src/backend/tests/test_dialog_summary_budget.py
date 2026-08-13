import asyncio
from datetime import datetime
from types import SimpleNamespace

from agentchat.api.services.dialog import DialogService
from agentchat.database.dao.dialog import DialogDao
from agentchat.database.dao.history import HistoryDao
from agentchat.settings import app_settings
from agentchat.utils.message_budget import split_messages_by_token as shared_split


def _message(role, content, token_usage, create_time=None):
    return SimpleNamespace(
        role=role,
        content=content,
        token_usage=token_usage,
        create_time=create_time,
    )


def test_dialog_service_wrapper_matches_shared_budget_semantics():
    messages = [
        _message("user", "old_q", 100),
        _message("assistant", "old_a", 100),
        _message("user", "new_q", 50),
        _message("assistant", "new_a", 50),
    ]

    service_result = DialogService.split_messages_by_token(messages, 120)
    shared_result = shared_split(messages, 120)

    assert [message.content for message in service_result[0]] == ["old_q", "old_a"]
    assert [message.content for message in service_result[1]] == ["new_q", "new_a"]
    assert service_result == shared_result


def test_update_dialog_summary_uses_configured_cutoff(monkeypatch):
    messages = [
        _message(
            "user",
            "old_q",
            100,
            datetime(2026, 1, 1, 0, 0, 0),
        ),
        _message(
            "assistant",
            "old_a",
            100,
            datetime(2026, 1, 1, 0, 0, 1),
        ),
        _message(
            "user",
            "new_q",
            50,
            datetime(2026, 1, 1, 0, 0, 2),
        ),
        _message(
            "assistant",
            "new_a",
            50,
            datetime(2026, 1, 1, 0, 0, 3),
        ),
    ]
    captured = {}

    async def fake_history(cls, dialog_id, k):
        captured["history_k"] = k
        return messages

    async def fake_dialog(cls, dialog_id):
        return SimpleNamespace(
            user_id="user_1",
            summary="existing summary",
            summary_last_time=datetime(1970, 1, 1),
        )

    async def fake_summary(cls, messages_prompt):
        captured["prompt"] = messages_prompt
        return "new summary"

    async def fake_update(cls, dialog_id, summary, summary_last_time):
        captured.update(
            {
                "dialog_id": dialog_id,
                "summary": summary,
                "summary_last_time": summary_last_time,
            }
        )

    monkeypatch.setattr(
        app_settings, "default_config", {"dialog_summary_cutoff_tokens": 120}
    )
    monkeypatch.setattr(
        HistoryDao, "select_history_from_time", classmethod(fake_history)
    )
    monkeypatch.setattr(DialogDao, "select_dialog_by_id", classmethod(fake_dialog))
    monkeypatch.setattr(
        DialogService, "_generate_messages_summary", classmethod(fake_summary)
    )
    monkeypatch.setattr(
        DialogDao, "update_dialog_summary", classmethod(fake_update)
    )

    asyncio.run(DialogService.update_dialog_summary("dialog_1", "user_1"))

    assert captured["history_k"] == 10000
    assert captured["dialog_id"] == "dialog_1"
    assert captured["summary"] == "new summary"
    assert captured["summary_last_time"] == datetime(2026, 1, 1, 0, 0, 1)
    assert "old_q" in captured["prompt"]
    assert "old_a" in captured["prompt"]
    assert "new_q" not in captured["prompt"]
    assert "new_a" not in captured["prompt"]
