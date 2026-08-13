import pytest

from agentchat.utils.permissions import ensure_owner_or_admin


def test_owner_is_allowed():
    ensure_owner_or_admin("u1", "u1", "1")


def test_admin_is_allowed():
    ensure_owner_or_admin("u1", "1", "1")


def test_other_user_is_denied():
    with pytest.raises(ValueError, match="Permission denied"):
        ensure_owner_or_admin("u1", "u2", "1")


def test_custom_error_message_is_used():
    with pytest.raises(ValueError, match="custom message"):
        ensure_owner_or_admin("u1", "u2", "1", message="custom message")
