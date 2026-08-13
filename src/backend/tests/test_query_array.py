from agentchat.utils.query_array import parse_query_list


def test_parses_raw_json_list():
    assert parse_query_list('["what is RAG", "how to demo"]', "fallback") == [
        "what is RAG",
        "how to demo",
    ]


def test_parses_fenced_json():
    content = '```json\n["q1", "q2"]\n```'
    assert parse_query_list(content, "fallback") == ["q1", "q2"]


def test_parses_array_embedded_in_text():
    content = 'Here are queries:\n["q1", "q2"]\nPlease use them.'
    assert parse_query_list(content, "fallback") == ["q1", "q2"]


def test_falls_back_on_invalid_json():
    assert parse_query_list("not json", "fallback") == ["fallback"]


def test_falls_back_on_non_list_json():
    assert parse_query_list('{"query": "q1"}', "fallback") == ["fallback"]


def test_falls_back_on_empty_or_non_string():
    assert parse_query_list("", "user input") == ["user input"]
    assert parse_query_list(None, "user input") == ["user input"]


def test_returns_existing_list_without_mutation():
    queries = ["a", "b"]
    assert parse_query_list(queries, "fallback") is queries
