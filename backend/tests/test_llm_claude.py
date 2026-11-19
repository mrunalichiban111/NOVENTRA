"""ClaudeProvider against a fake SDK client. Never touches the network or the real API."""

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app import config
from app.llm.base import ConfigError, LLMRequest, RefusalError, TransportError
from app.llm.claude import ClaudeProvider

REQ = LLMRequest(
    task="premortem",
    model="claude-sonnet-5-5",
    system="sys",
    user="usr",
    max_tokens=777,
    timeout_s=12.5,
    tool_name="submit_x",
    tool_description="d",
    tool_schema={"type": "object", "properties": {}, "additionalProperties": False},
)


def message(content, stop="tool_use", usage=None):
    usage = usage or SimpleNamespace(
        input_tokens=11, output_tokens=22, cache_read_input_tokens=3, cache_creation_input_tokens=4
    )
    return SimpleNamespace(
        content=content,
        usage=usage,
        stop_reason=stop,
        model="claude-sonnet-5-5",
        _request_id="req_1",
    )


class FakeClient:
    def __init__(self, result):
        self.result, self.kwargs = result, None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def status_error(cls, code):
    resp = httpx.Response(code, request=httpx.Request("POST", "http://test"))
    return cls("boom", response=resp, body=None)


def test_request_uses_strict_auto_tool_effort_and_no_sampling_params():
    tool = SimpleNamespace(type="tool_use", name="submit_x", input={"a": 1})
    client = FakeClient(message([SimpleNamespace(type="text", text="hi"), tool]))
    resp = ClaudeProvider(client).complete(REQ)
    kw = client.kwargs
    assert kw["max_tokens"] == 777 and kw["timeout"] == 12.5 and kw["model"] == "claude-sonnet-5-5"
    assert kw["tool_choice"] == {"type": "auto"}  # Sonnet 5.5 rejects forced tool_choice
    assert kw["tools"][0]["strict"] is True and kw["output_config"] == {"effort": "low"}
    assert not {"temperature", "top_p", "top_k"} & set(kw)
    assert resp.tool_input == {"a": 1} and resp.text == "hi" and resp.request_id == "req_1"
    assert (resp.input_tokens, resp.output_tokens) == (11, 22)
    assert (resp.cache_read_tokens, resp.cache_write_tokens) == (3, 4)
    assert resp.latency_ms >= 0


def test_repair_feedback_is_appended_to_user_message():
    client = FakeClient(message([]))
    ClaudeProvider(client).complete(
        LLMRequest(**{**REQ.__dict__, "repair_feedback": "fix field a"})
    )
    assert "fix field a" in client.kwargs["messages"][0]["content"]


def test_cache_tokens_may_be_none():
    usage = SimpleNamespace(
        input_tokens=1,
        output_tokens=2,
        cache_read_input_tokens=None,
        cache_creation_input_tokens=None,
    )
    resp = ClaudeProvider(FakeClient(message([], usage=usage))).complete(REQ)
    assert (resp.cache_read_tokens, resp.cache_write_tokens) == (0, 0)


def _with_cause(exc: Exception, cause: Exception) -> Exception:
    exc.__cause__ = cause
    return exc


_REQ = httpx.Request("POST", "http://t")


@pytest.mark.parametrize(
    ("exc", "retryable", "outcome"),
    [
        # provider answered with an error status
        (status_error(anthropic.RateLimitError, 429), True, "rejected"),
        (status_error(anthropic.InternalServerError, 500), True, "rejected"),
        (status_error(anthropic.BadRequestError, 400), False, "rejected"),
        (status_error(anthropic.AuthenticationError, 401), False, "rejected"),
        # provably before sending: safe to retry
        (_with_cause(anthropic.APIConnectionError(request=_REQ), httpx.ConnectError("x")), True, "not_sent"),
        (_with_cause(anthropic.APITimeoutError(request=_REQ), httpx.ConnectTimeout("x")), True, "not_sent"),
        # request may have reached the provider: never retried, outcome unknown
        (_with_cause(anthropic.APITimeoutError(request=_REQ), httpx.ReadTimeout("x")), False, "ambiguous"),
        (_with_cause(anthropic.APIConnectionError(request=_REQ), httpx.RemoteProtocolError("x")), False, "ambiguous"),
        (anthropic.APITimeoutError(request=_REQ), False, "ambiguous"),  # unknown cause: conservative
    ],
)  # fmt: skip
def test_transport_error_classification(exc, retryable, outcome):
    with pytest.raises(TransportError) as ei:
        ClaudeProvider(FakeClient(exc)).complete(REQ)
    assert ei.value.retryable is retryable and ei.value.outcome == outcome
    assert ei.value.category == f"transport_{outcome}"


def test_refusal_and_missing_key(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(RefusalError) as ei:
        ClaudeProvider(FakeClient(message([], stop="refusal"))).complete(REQ)
    assert ei.value.response.input_tokens == 11  # usage is known, so it must not be dropped
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    with pytest.raises(ConfigError):
        ClaudeProvider().complete(REQ)


def test_error_text_never_contains_api_key(monkeypatch: pytest.MonkeyPatch):
    exc = status_error(anthropic.AuthenticationError, 401)
    with pytest.raises(TransportError) as ei:
        ClaudeProvider(FakeClient(exc)).complete(REQ)
    assert "sk-" not in str(ei.value)
