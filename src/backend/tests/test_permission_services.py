import asyncio
from types import SimpleNamespace

import pytest

knowledge_service = pytest.importorskip("agentchat.api.services.knowledge")
dialog_service = pytest.importorskip("agentchat.api.services.dialog")


def run(coro):
    return asyncio.run(coro)


def patch_knowledge_owner(monkeypatch, owner_id):
    async def fake_select_user_by_id(cls, knowledge_id):
        return owner_id

    monkeypatch.setattr(
        knowledge_service.KnowledgeService,
        "select_user_by_id",
        classmethod(fake_select_user_by_id),
    )


def test_knowledge_owner_can_access(monkeypatch):
    patch_knowledge_owner(monkeypatch, "u1")
    run(knowledge_service.KnowledgeService.verify_user_permission("k1", "u1"))


def test_knowledge_admin_can_access(monkeypatch):
    patch_knowledge_owner(monkeypatch, "u1")
    run(knowledge_service.KnowledgeService.verify_user_permission("k1", "1"))


def test_knowledge_other_user_is_denied(monkeypatch):
    patch_knowledge_owner(monkeypatch, "u1")
    with pytest.raises(ValueError):
        run(knowledge_service.KnowledgeService.verify_user_permission("k1", "u2"))


def patch_dialog_owner(monkeypatch, owner_id):
    async def fake_get_agent_by_dialog_id(cls, dialog_id):
        return SimpleNamespace(user_id=owner_id)

    monkeypatch.setattr(
        dialog_service.DialogDao,
        "get_agent_by_dialog_id",
        classmethod(fake_get_agent_by_dialog_id),
    )


def test_dialog_owner_can_access(monkeypatch):
    patch_dialog_owner(monkeypatch, "u1")
    run(dialog_service.DialogService.verify_user_permission("d1", "u1"))


def test_dialog_admin_can_access(monkeypatch):
    patch_dialog_owner(monkeypatch, "u1")
    run(dialog_service.DialogService.verify_user_permission("d1", "1"))


def test_dialog_other_user_is_denied(monkeypatch):
    patch_dialog_owner(monkeypatch, "u1")
    with pytest.raises(ValueError):
        run(dialog_service.DialogService.verify_user_permission("d1", "u2"))
