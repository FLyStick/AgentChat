from agentchat.benchmarks.token_budget import (
    TokenMessage,
    analyze_cutoff,
    build_long_conversation,
    pair_messages,
    run_token_budget_benchmark,
    split_messages_by_token,
)


def _messages(*sizes):
    messages = []
    for index, tokens in enumerate(sizes):
        messages.append(
            TokenMessage(
                role="user" if index % 2 == 0 else "assistant",
                content=f"message_{index}",
                token_usage=tokens,
            )
        )
    return messages


def test_pair_messages_only_groups_user_assistant():
    messages = _messages(100, 100, 50, 50, 20)
    pairs = pair_messages(messages)
    assert len(pairs) == 2
    assert [message.token_usage for pair in pairs for message in pair] == [100, 100, 50, 50]


def test_split_keeps_newest_pairs_inside_cutoff():
    old_messages, kept_messages = split_messages_by_token(_messages(100, 100, 50, 50), 120)

    assert len(old_messages) == 2
    assert len(kept_messages) == 2
    assert old_messages[0].content == "message_0"
    assert kept_messages[0].content == "message_2"


def test_split_never_forces_summary_for_oversized_single_pair():
    old_messages, kept_messages = split_messages_by_token(_messages(300, 300), 200)

    assert old_messages == []
    assert kept_messages == []


def test_split_returns_empty_when_everything_fits():
    messages = build_long_conversation(2)
    old_messages, kept_messages = split_messages_by_token(messages, 10**6)

    assert old_messages == []
    assert kept_messages == []


def test_analysis_reports_stable_capacity_when_everything_fits():
    messages = build_long_conversation(2)
    report = analyze_cutoff(messages, 10**6)

    assert report["summary_triggered"] is False
    assert report["kept_pair_count"] == report["total_pair_count"]
    assert report["kept_token_count"] == sum(message.token_count() for message in messages)


def test_token_budget_benchmark_is_deterministic():
    first = run_token_budget_benchmark(pair_count=40, cutoffs=(1000, 3000, 5000))
    second = run_token_budget_benchmark(pair_count=40, cutoffs=(1000, 3000, 5000))

    assert first == second
    assert first["pair_count"] == 40
    assert len(first["cutoffs"]) == 3
    assert first["pair_tokens"]["min"] == 100
    assert first["insights"]["summary_first_triggers_at"] == 1000
