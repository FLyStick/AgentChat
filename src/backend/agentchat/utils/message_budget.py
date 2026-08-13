"""Shared token-budget helpers for production summary and offline benchmark."""

from typing import Any, Dict, List, Sequence, Tuple


def message_token_count(message: Any) -> int:
    """Return a message's token estimate, preferring recorded usage."""
    token_usage = getattr(message, "token_usage", None)
    if token_usage is not None and int(token_usage) > 0:
        return int(token_usage)
    content = getattr(message, "content", "") or ""
    return max(1, len(str(content)) // 4)


def pair_messages(messages: Sequence[Any]) -> List[Tuple[Any, Any]]:
    """Group consecutive user/assistant messages into pairs."""
    pairs: List[Tuple[Any, Any]] = []
    index = 0
    while index < len(messages) - 1:
        if (
            getattr(messages[index], "role", None) == "user"
            and getattr(messages[index + 1], "role", None) == "assistant"
        ):
            pairs.append((messages[index], messages[index + 1]))
            index += 2
        else:
            index += 1
    return pairs


def pair_token_count(pair: Tuple[Any, Any]) -> int:
    return message_token_count(pair[0]) + message_token_count(pair[1])


def split_messages_by_token(
    messages: Sequence[Any],
    cutoff_tokens: int,
) -> Tuple[List[Any], List[Any]]:
    """Split into ``(old_messages, kept_messages)`` from newest to oldest."""
    if not messages or cutoff_tokens <= 0:
        return [], []

    pairs = pair_messages(messages)
    if not pairs:
        return [], []

    total_tokens = 0
    kept_pairs: List[Tuple[Any, Any]] = []
    for pair in reversed(pairs):
        tokens = pair_token_count(pair)
        if total_tokens + tokens > cutoff_tokens:
            break
        kept_pairs.append(pair)
        total_tokens += tokens
    kept_pairs = list(reversed(kept_pairs))

    if not kept_pairs:
        # Match DialogService: with multiple pairs, keep the newest pair even
        # when it exceeds the cutoff so the model never gets an empty context.
        if len(pairs) == 1:
            return [], []
        kept_pairs = [pairs[-1]]

    cutoff_index = len(pairs) - len(kept_pairs)
    old_pairs = pairs[:cutoff_index]
    if not old_pairs:
        return [], []

    old_messages = [message for pair in old_pairs for message in pair]
    new_messages = [message for pair in kept_pairs for message in pair]
    return old_messages, new_messages


def default_summary_cutoff_tokens(default_config: Dict[str, Any]) -> int:
    """Resolve the dialog summary cutoff from production settings."""
    raw_value = (default_config or {}).get("dialog_summary_cutoff_tokens", 3000)
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        parsed = 3000
    return parsed if parsed > 0 else 3000
