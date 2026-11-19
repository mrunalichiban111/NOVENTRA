"""ClaudeProvider: one Anthropic Messages API attempt per call.

Notes from the current docs (verified 2026-11-19):
- Sonnet 5.5 rejects forced tool_choice (400), so structured output uses a strict tool
  with tool_choice=auto plus an instruction to call it.
- Sampling parameters (temperature/top_p/top_k) are rejected on Sonnet 5.5: never sent.
- Thinking is kept minimal via output_config.effort (default "low"); max_tokens includes thinking.
- SDK retries are disabled (max_retries=0): retries happen one level up so every real
  attempt is reserved against the spend cap.
Prompts and API keys are never logged.
"""

import logging
import time

import anthropic

from app import config
from app.llm.base import (
    ConfigError,
    LLMRequest,
    LLMResponse,
    RefusalError,
    TransportError,
)

logger = logging.getLogger(__name__)

# Transport errors that occur before any request bytes are sent (connection never established).
# Matched by class name along the MRO because the SDK raises errors from its own HTTP library
# (anthropic 2.x uses `httpx2`, not `httpx`), and this keeps working if that library changes.
_PRE_SEND_ERRORS = frozenset({"ConnectError", "ConnectTimeout", "PoolTimeout"})


def _happened_before_sending(cause: BaseException | None) -> bool:
    return cause is not None and any(c.__name__ in _PRE_SEND_ERRORS for c in type(cause).__mro__)


class ClaudeProvider:
    name = "claude"

    def __init__(self, client: anthropic.Anthropic | None = None):
        self._client = client

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not config.ANTHROPIC_API_KEY:
                raise ConfigError("ANTHROPIC_API_KEY is not set")
            self._client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=0)
        return self._client

    def complete(self, request: LLMRequest) -> LLMResponse:
        client = self._get_client()
        user = request.user
        if request.repair_feedback:
            user += (
                "\n\nYour previous answer was rejected by validation: "
                f"{request.repair_feedback}\nReturn a corrected answer."
            )
        kwargs: dict = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": request.effort},
            "timeout": request.timeout_s,
        }
        if request.tool_name and request.tool_schema is not None:
            kwargs["tools"] = [
                {
                    "name": request.tool_name,
                    "description": request.tool_description,
                    "strict": True,
                    "input_schema": request.tool_schema,
                }
            ]
            kwargs["tool_choice"] = {"type": "auto"}

        started = time.perf_counter()
        try:
            msg = client.messages.create(**kwargs)
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            # Only failures that provably happened before sending are safe to retry.
            if _happened_before_sending(exc.__cause__):
                raise TransportError(
                    "could not reach the API", retryable=True, outcome="not_sent"
                ) from exc
            raise TransportError(
                "request may have been sent; outcome unknown", retryable=False, outcome="ambiguous"
            ) from exc
        except anthropic.APIStatusError as exc:
            code = exc.status_code
            retryable = code == 429 or code >= 500
            raise TransportError(
                f"API status {code}: {type(exc).__name__}",
                retryable=retryable,
                status_code=code,
                outcome="rejected",
            ) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        text_parts: list[str] = []
        tool_input: dict | None = None
        for block in msg.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use" and block.name == request.tool_name:
                tool_input = dict(block.input)

        usage = msg.usage
        response = LLMResponse(
            model=msg.model,
            text="".join(text_parts),
            tool_input=tool_input,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            latency_ms=latency_ms,
            request_id=getattr(msg, "_request_id", None),
            stop_reason=msg.stop_reason,
        )
        if msg.stop_reason == "refusal":
            raise RefusalError("model declined the request", response=response)
        logger.info(
            "llm call task=%s model=%s in=%d out=%d latency_ms=%d request_id=%s",
            request.task,
            response.model,
            response.input_tokens,
            response.output_tokens,
            latency_ms,
            response.request_id,
        )
        return response
