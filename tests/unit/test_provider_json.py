from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from atomic_commits.config import RunConfig
from atomic_commits.errors import InvalidAIResponseError
from atomic_commits.providers import build_provider
from atomic_commits.providers.base import _strip_code_fence, extract_json
from atomic_commits.providers.openai_compatible import OpenAICompatibleProvider


class _Stream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __iter__(self):
        return iter(self.chunks)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _StreamingClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=self)

    def with_options(self, **kwargs):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return _Stream(response)


def _chunk(content=None, finish_reason=None):
    choice = SimpleNamespace(
        delta=SimpleNamespace(content=content), finish_reason=finish_reason,
    )
    return SimpleNamespace(choices=[choice], usage=None)


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
    events: list[str] = []

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
    out = base._post_with_retry(
        "http://x", {}, {}, attempts=3, timeout=1.0, progress=events.append,
    )
    assert out == '{"ok": true}'
    assert calls["n"] == 2
    assert events[0] == "request attempt 1/3 sent"
    assert "IncompleteRead; retrying" in events[1]
    assert events[2] == "request attempt 2/3 sent"
    assert events[3].startswith("response received in ")


def test_openai_provider_automatically_fits_output_to_context():
    body = {
        "error": {"message": (
            "This model's maximum context length is 1000 tokens. However, you "
            "requested 400 output tokens and your prompt contains at least 700 input tokens."
        )}
    }
    response = httpx.Response(400, request=httpx.Request("POST", "http://x"), json=body)
    error = APIStatusError("bad request", response=response, body=body)
    client = _StreamingClient([error, [_chunk('{"ok":true}', "stop")]])
    provider = OpenAICompatibleProvider(api_key="test", model="test", base_url="http://x")
    provider.client = client

    assert provider.complete_json(
        system="system", user="user", schema_name="Test", max_tokens=400, temperature=0,
    ) == {"ok": True}
    assert [call["max_tokens"] for call in client.calls] == [400, 172]


def test_openai_provider_retries_truncated_output_with_more_space():
    events = []
    client = _StreamingClient([
        [_chunk('{"partial":', "length")],
        [_chunk('{"ok":true}', "stop")],
    ])
    provider = OpenAICompatibleProvider(
        api_key="test", model="test", base_url="http://x", progress=events.append,
    )
    provider.client = client

    assert provider.complete_json(
        system="system", user="user", schema_name="Test", max_tokens=1024, temperature=0,
    ) == {"ok": True}
    assert [call["max_tokens"] for call in client.calls] == [1024, 2048]
    assert all(call["stream"] is True for call in client.calls)
    assert any("model output: {\"ok\":true}" in event for event in events)
    assert all("characters" not in event for event in events)


def test_openai_provider_retries_empty_stop_response():
    client = _StreamingClient([
        [_chunk("", "stop")],
        [_chunk('{"ok":true}', "stop")],
    ])
    provider = OpenAICompatibleProvider(api_key="test", model="test", base_url="http://x")
    provider.client = client

    assert provider.complete_json(
        system="system", user="user", schema_name="Test", max_tokens=1024, temperature=0,
    ) == {"ok": True}
    assert "previous response was empty" in client.calls[1]["messages"][-1]["content"]


def test_openai_provider_retries_invalid_json():
    events = []
    client = _StreamingClient([
        [_chunk('{"broken":', "stop")],
        [_chunk('{"ok":true}', "stop")],
    ])
    provider = OpenAICompatibleProvider(
        api_key="test", model="test", base_url="http://x", progress=events.append,
    )
    provider.client = client

    assert provider.complete_json(
        system="system", user="user", schema_name="Test", max_tokens=1024, temperature=0,
    ) == {"ok": True}
    assert len(client.calls) == 2
    assert any("invalid JSON; retrying" in event for event in events)


def test_model_preview_containing_retry_stays_transient(monkeypatch):
    import atomic_commits.providers as providers

    live_messages = []
    notes = []
    monkeypatch.setattr(providers, "live", live_messages.append)
    monkeypatch.setattr(providers, "note", lambda message, **_kwargs: notes.append(message))
    provider = build_provider(RunConfig(model="test", api_key="test"))

    provider.progress("model output: update retry handling")
    assert live_messages == ["model output: update retry handling"]
    assert notes == []

    provider.progress("stream interrupted; retrying in 1.0s")
    assert notes == ["Provider: stream interrupted; retrying in 1.0s"]
