from typing import Any


def ensure_owner_or_admin(
    owner_id: Any,
    user_id: Any,
    admin_user_id: Any,
    message: str = "Permission denied",
) -> None:
    if user_id not in (admin_user_id, owner_id):
        raise ValueError(message)
