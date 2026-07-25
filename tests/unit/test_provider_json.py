import io
import json
from urllib.error import HTTPError

import pytest

from atomic_commits.errors import InvalidAIResponseError
from atomic_commits.providers.base import _strip_code_fence, extract_json
from atomic_commits.providers.openai_compatible import OpenAICompatibleProvider


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


def test_strip_code_fence_keeps_inline_json_on_opener():
    """A fence opener sharing its line with JSON must preserve the JSON."""
    fenced = '```json {"a": 1}\n```'
    assert _strip_code_fence(fenced) == '{"a": 1}'


def test_strip_code_fence_plain_opener_drops_line():
    """A pure '```json' opener (no inline content) drops the whole line."""
    fenced = '```json\n{"a": 1}\n```'
    assert _strip_code_fence(fenced) == '{"a": 1}'


def test_strip_code_fence_no_fence_unchanged():
    assert _strip_code_fence('{"a": 1}') == '{"a": 1}'


def test_post_with_retry_catches_incomplete_read(monkeypatch):
    """Mid-body read errors (IncompleteRead) must be retried, not crash."""
    import http.client

    import atomic_commits.providers.base as base

    calls = {"n": 0}

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise http.client.IncompleteRead(b"partial")
        return _Resp(b'{"ok": true}')

    monkeypatch.setattr(base, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        base,
        "time",
        type(
            "t",
            (),
            {
                "sleep": staticmethod(lambda *a, **k: None),
                "monotonic": staticmethod(__import__("time").monotonic),
            },
        )(),
    )
    out = base._post_with_retry("http://x", {}, {}, attempts=3, timeout=1.0)
    assert out == '{"ok": true}'
    assert calls["n"] == 2


def test_openai_provider_automatically_fits_output_to_context(monkeypatch):
    import atomic_commits.providers.base as base

    requested: list[int] = []

    class _Resp:
        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        max_tokens = json.loads(req.data)["max_tokens"]
        requested.append(max_tokens)
        if len(requested) == 1:
            body = json.dumps({
                "error": {"message": (
                    "This model's maximum context length is 1000 tokens. However, you "
                    "requested 400 output tokens and your prompt contains at least 700 input tokens."
                )}
            }).encode()
            raise HTTPError(req.full_url, 400, "bad request", {}, io.BytesIO(body))
        return _Resp(b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}')

    monkeypatch.setattr(base, "urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider(api_key="test", model="test", base_url="http://x")

    assert provider.complete_json(
        system="system", user="user", schema_name="Test", max_tokens=400, temperature=0,
    ) == {"ok": True}
    assert requested == [400, 172]


def test_openai_provider_retries_truncated_output_with_more_space(monkeypatch):
    import atomic_commits.providers.base as base

    requested: list[int] = []

    class _Resp:
        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        requested.append(json.loads(req.data)["max_tokens"])
        if len(requested) == 1:
            return _Resp(
                b'{"choices":[{"message":{"content":"{\\"partial\\":"},'
                b'"finish_reason":"length"}]}'
            )
        return _Resp(b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}')

    monkeypatch.setattr(base, "urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider(api_key="test", model="test", base_url="http://x")

    assert provider.complete_json(
        system="system", user="user", schema_name="Test", max_tokens=1024, temperature=0,
    ) == {"ok": True}
    assert requested == [1024, 2048]


def test_openai_provider_retries_empty_stop_response(monkeypatch):
    import atomic_commits.providers.base as base

    calls = 0

    class _Resp:
        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _Resp(
                b'{"choices":[{"message":{"content":""},"finish_reason":"stop"}]}'
            )
        body = json.loads(req.data)
        assert "previous response was empty" in body["messages"][-1]["content"]
        return _Resp(b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}')

    monkeypatch.setattr(base, "urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider(api_key="test", model="test", base_url="http://x")

    assert provider.complete_json(
        system="system", user="user", schema_name="Test", max_tokens=1024, temperature=0,
    ) == {"ok": True}
    assert calls == 2
