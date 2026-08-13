"""Token budget calibration for long conversations.

The split logic mirrors ``DialogService.update_dialog_summary``: messages are
grouped into user/assistant pairs, the newest pairs that fit inside the token
cutoff are kept, and a forced last pair protects the model from receiving an
empty context.
"""

from dataclasses import dataclass
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

from agentchat.benchmarks.metrics import percentile


@dataclass
class TokenMessage:
    """A minimal message shape with an optional known token count."""

    role: str
    content: str
    token_usage: Optional[int] = None

    def token_count(self) -> int:
        if self.token_usage is not None and self.token_usage > 0:
            return self.token_usage
        return max(1, len(self.content) // 4)


def pair_messages(messages: Sequence[TokenMessage]) -> List[Tuple[TokenMessage, TokenMessage]]:
    """Group messages into consecutive user/assistant pairs."""
    pairs: List[Tuple[TokenMessage, TokenMessage]] = []
    index = 0
    while index < len(messages) - 1:
        if messages[index].role == "user" and messages[index + 1].role == "assistant":
            pairs.append((messages[index], messages[index + 1]))
            index += 2
        else:
            index += 1
    return pairs


def pair_tokens(pair: Tuple[TokenMessage, TokenMessage]) -> int:
    return pair[0].token_count() + pair[1].token_count()


def split_messages_by_token(
    messages: Sequence[TokenMessage],
    cutoff_tokens: int,
) -> Tuple[List[TokenMessage], List[TokenMessage]]:
    """Return ``(old_messages, kept_messages)`` or two empty lists.

    A single pair that exceeds the cutoff is intentionally not summarized,
    exactly like the production dialog summary path.
    """
    if not messages or cutoff_tokens <= 0:
        return [], []

    pairs = pair_messages(messages)
    if not pairs:
        return [], []

    total_tokens = 0
    kept_pairs: List[Tuple[TokenMessage, TokenMessage]] = []
    for pair in reversed(pairs):
        tokens = pair_tokens(pair)
        if total_tokens + tokens > cutoff_tokens:
            break
        kept_pairs.append(pair)
        total_tokens += tokens
    kept_pairs = list(reversed(kept_pairs))

    if not kept_pairs:
        return [], []

    cutoff_index = len(pairs) - len(kept_pairs)
    old_pairs = pairs[:cutoff_index]
    if not old_pairs:
        return [], []

    old_messages = [message for pair in old_pairs for message in pair]
    new_messages = [message for pair in kept_pairs for message in pair]
    return old_messages, new_messages


def build_long_conversation(pair_count: int = 40) -> List[TokenMessage]:
    """Create a deterministic long conversation with varied pair sizes."""
    messages: List[TokenMessage] = []
    for index in range(pair_count):
        user_tokens = 40 + (index * 17) % 90
        assistant_tokens = 60 + (index * 23) % 140
        messages.append(TokenMessage(role="user", content=f"question_{index}", token_usage=user_tokens))
        messages.append(
            TokenMessage(role="assistant", content=f"answer_{index}", token_usage=assistant_tokens)
        )
    return messages


def analyze_cutoff(
    messages: Sequence[TokenMessage],
    cutoff_tokens: int,
) -> Dict:
    old_messages, kept_messages = split_messages_by_token(messages, cutoff_tokens)
    old_pairs = len(old_messages) // 2
    kept_pairs = len(kept_messages) // 2
    old_token_count = sum(message.token_count() for message in old_messages)
    kept_token_count = sum(message.token_count() for message in kept_messages)

    all_pairs = pair_messages(messages)
    total_pairs = len(all_pairs)
    total_token_count = sum(message.token_count() for message in messages)
    if not old_messages and not kept_messages and total_token_count <= cutoff_tokens:
        # Production returns two empty lists when nothing needs summarizing;
        # the calibration report restores the "everything fits" representation.
        kept_pairs = total_pairs
        kept_token_count = total_token_count

    return {
        "cutoff_tokens": cutoff_tokens,
        "total_pair_count": total_pairs,
        "old_pair_count": old_pairs,
        "kept_pair_count": kept_pairs,
        "old_token_count": old_token_count,
        "kept_token_count": kept_token_count,
        "summary_triggered": bool(old_messages),
        "summary_trigger_kept_ratio": round(
            kept_token_count / max(1, old_token_count + kept_token_count), 4
        ),
        "stable_capacity_tokens": cutoff_tokens,
    }


def run_token_budget_benchmark(
    pair_count: int = 40,
    cutoffs: Sequence[int] = (1000, 2000, 3000, 4000, 5000),
) -> Dict:
    messages = build_long_conversation(pair_count)
    all_pairs = pair_messages(messages)
    pair_sizes = [pair_tokens(pair) for pair in all_pairs]
    rows = [analyze_cutoff(messages, cutoff) for cutoff in cutoffs]

    triggered = [row for row in rows if row["summary_triggered"]]
    stable_rows = [row for row in rows if not row["summary_triggered"]]

    return {
        "framework": "token_budget",
        "pair_count": pair_count,
        "total_tokens": sum(pair_sizes),
        "pair_tokens": {
            "min": min(pair_sizes),
            "p50": round(median(pair_sizes), 1),
            "p90": percentile(pair_sizes, 90),
            "max": max(pair_sizes),
        },
        "cutoffs": rows,
        "insights": {
            "summary_first_triggers_at": (
                triggered[0]["cutoff_tokens"] if triggered else None
            ),
            "largest_conversation_without_summarization": max(
                (
                    row["kept_token_count"]
                    for row in stable_rows
                    if row["kept_pair_count"] == row["total_pair_count"]
                ),
                default=0,
            ),
            "retained_context_never_exceeds_cutoff_plus_one_pair": max(
                row["kept_token_count"] for row in rows
            ),
        },
    }
