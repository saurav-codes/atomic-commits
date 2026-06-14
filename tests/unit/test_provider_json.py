import pytest

from atomic_commits.errors import InvalidAIResponseError
from atomic_commits.providers.base import extract_json


def test_extract_json_accepts_plain_object():
    assert extract_json('{"ok": true}') == {"ok": True}


def test_extract_json_accepts_fenced_object():
    assert extract_json('```json\n{"ok": true}\n```') == {"ok": True}


def test_extract_json_uses_balanced_object_after_prose():
    text = 'Here is the plan:\n{"ok": true, "message": "contains { braces }"}'

    assert extract_json(text) == {"ok": True, "message": "contains { braces }"}


def test_extract_json_skips_invalid_example_before_real_object():
    text = """Example:
{version: "1"}

Actual:
{"version": "1", "groups": []}
"""

    assert extract_json(text) == {"version": "1", "groups": []}


def test_extract_json_recovers_object_after_stray_leading_brace():
    text = '{\n{"chunk_id": "chunk-0", "summary": "ok"}'

    assert extract_json(text) == {"chunk_id": "chunk-0", "summary": "ok"}


def test_extract_json_rejects_json_array():
    with pytest.raises(InvalidAIResponseError, match="non-object"):
        extract_json('[{"ok": true}]')
