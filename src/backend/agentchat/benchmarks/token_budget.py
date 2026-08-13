"""Token budget calibration for long conversations.

The split logic delegates to ``agentchat.utils.message_budget`` so the offline
calibration and the production ``DialogService`` path use the same semantics.
"""

from dataclasses import dataclass
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

from agentchat.benchmarks.metrics import percentile
from agentchat.utils.message_budget import (
    pair_messages as _shared_pair_messages,
    pair_token_count,
    split_messages_by_token as _shared_split_messages_by_token,
)


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
    """Delegate grouping to the shared production helper."""
    return _shared_pair_messages(messages)


def pair_tokens(pair: Tuple[TokenMessage, TokenMessage]) -> int:
    """Delegate token counting to the shared production helper."""
    return pair_token_count(pair)


def split_messages_by_token(
    messages: Sequence[TokenMessage],
    cutoff_tokens: int,
) -> Tuple[List[TokenMessage], List[TokenMessage]]:
    """Delegate splitting to the shared production helper."""
    return _shared_split_messages_by_token(messages, cutoff_tokens)


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
