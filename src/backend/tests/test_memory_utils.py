from agentchat.services.memory.utils import extract_json, parse_messages, remove_code_blocks


def test_parse_messages_formats_roles():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert parse_messages(messages) == "system: sys\nuser: hi\nassistant: hello\n"


def test_remove_code_blocks_with_and_without_language():
    assert remove_code_blocks("```python\nprint(1)\n```") == "print(1)"
    assert remove_code_blocks("```\nprint(2)\n```") == "print(2)"
    assert remove_code_blocks("plain") == "plain"


def test_extract_json_from_fenced_block_and_raw_text():
    assert extract_json('```json\n{"facts": []}\n```') == '{"facts": []}'
    assert extract_json('{"facts": []}') == '{"facts": []}'
