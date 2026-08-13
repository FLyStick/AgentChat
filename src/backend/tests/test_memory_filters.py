import pytest

from agentchat.services.memory.filters import build_filters_and_metadata


def test_builds_metadata_and_filters_with_session_ids():
    metadata, filters = build_filters_and_metadata(
        user_id="u", agent_id="a", run_id="r"
    )
    assert metadata == {"user_id": "u", "agent_id": "a", "run_id": "r"}
    assert filters == {"user_id": "u", "agent_id": "a", "run_id": "r"}


def test_explicit_actor_id_overrides_filter_actor_id():
    metadata, filters = build_filters_and_metadata(
        user_id="u", actor_id="m2", input_filters={"actor_id": "m1"}
    )
    assert filters["actor_id"] == "m2"
    assert "actor_id" not in metadata


def test_actor_id_from_filters_is_preserved():
    metadata, filters = build_filters_and_metadata(
        user_id="u", input_filters={"actor_id": "m1"}
    )
    assert filters["actor_id"] == "m1"
    assert "actor_id" not in metadata


def test_requires_at_least_one_session_id():
    with pytest.raises(ValueError):
        build_filters_and_metadata(actor_id="m1")


def test_does_not_mutate_input_dicts():
    source_metadata = {"tenant": "t"}
    source_filters = {"scope": "s"}
    metadata, filters = build_filters_and_metadata(
        user_id="u", input_metadata=source_metadata, input_filters=source_filters
    )
    metadata["extra"] = True
    filters["extra"] = True
    assert source_metadata == {"tenant": "t"}
    assert source_filters == {"scope": "s"}
