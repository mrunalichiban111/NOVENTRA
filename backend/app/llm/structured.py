"""run_structured: schema-validated model output with repair retries (FR-PM-2).

Per call: up to 3 validation attempts (1 + 2 repairs, CLAUDE.md), each of which may
use up to 3 transport tries (429/5xx/timeouts, exponential backoff). Every real
provider call is announced to an optional `CallGuard` before and after, which is where
spend reservation and usage persistence plug in (M2 slices 4-5).

Model output is data: it is parsed and validated, never executed or rendered as HTML,
and missing fields are never filled in. Deterministic application numbers are kept
out of this module.
"""

import json
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from anthropic import transform_schema
from pydantic import BaseModel, ValidationError

from app import config
from app.llm.base import (
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    RunIdRequired,
    StructuredOutputError,
    TransportError,
)
from app.llm.mock import MockProvider
from app.llm.routing import get_task, model_for

logger = logging.getLogger(__name__)

MAX_VALIDATION_RETRIES = 2  # repairs after the first attempt
MAX_TRANSPORT_TRIES = 3
BACKOFF_BASE_S = 1.0
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


class CallGuard(Protocol):
    """Hook around every real provider call. `before` may raise (e.g. BudgetExceeded)."""

    def before(self, request: LLMRequest, call_no: int) -> object: ...

    def after(
        self,
        token: object,
        request: LLMRequest,
        call_no: int,
        response: LLMResponse | None,
        status: str,
        error_category: str | None,
    ) -> None: ...


@dataclass
class AttemptRecord:
    call_no: int
    validation_attempt: int
    status: str  # "success" | "failure"
    error_category: str | None = None
    detail: str | None = None
    response: LLMResponse | None = None


@dataclass
class StructuredResult[T: BaseModel]:
    data: T
    model: str
    provider: str
    attempts: list[AttemptRecord] = field(default_factory=list)


def is_mock() -> bool:
    return config.MOCK_LLM


def load_system_prompt(task: str) -> str:
    return (
        (PROMPTS_DIR / "_preamble.md").read_text() + "\n" + (PROMPTS_DIR / f"{task}.md").read_text()
    )


def wrap_untrusted(text: str) -> str:
    """Put untrusted text in a delimited data block; neutralise any literal closing tag."""
    safe = re.sub(r"</\s*untrusted_data\s*>", "</untrusted_data_>", text, flags=re.IGNORECASE)
    return f"<untrusted_data>\n{safe}\n</untrusted_data>"


def parse_json_text(text: str) -> Any:
    """Parse JSON from model text, tolerating a surrounding Markdown fence. Raises ValueError."""
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("empty response")
    m = _FENCE.match(stripped)
    if m:
        stripped = m.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON: {exc.msg} at position {exc.pos}") from exc


def _validation_feedback(exc: ValidationError) -> str:
    """Concise feedback: field paths and messages only, never the offending values."""
    parts = []
    for err in exc.errors()[:6]:
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        parts.append(f"{loc}: {err['msg']}")
    more = exc.error_count() - len(parts)
    return "; ".join(parts) + (f"; and {more} more" if more > 0 else "")


def _transport_call(
    provider: LLMProvider,
    request: LLMRequest,
    call_counter: list[int],
    guard: CallGuard | None,
    sleep: Callable[[float], None],
    attempts: list[AttemptRecord],
    validation_attempt: int,
) -> tuple[LLMResponse, object, int]:
    """One provider call with transport retries. Returns (response, guard token, call_no).

    Every real try is announced to the guard (so it can reserve spend) and failed tries
    are settled here. The successful try is settled by the caller once validation is known.
    """
    last: TransportError | None = None
    for try_no in range(MAX_TRANSPORT_TRIES):
        call_counter[0] += 1
        call_no = call_counter[0]
        token = guard.before(request, call_no) if guard else None
        try:
            return provider.complete(request), token, call_no
        except LLMError as exc:
            # Refusals are real provider answers with known usage: settle with that usage.
            known = getattr(exc, "response", None)
            if guard:
                guard.after(token, request, call_no, known, "failure", exc.category)
            attempts.append(
                AttemptRecord(call_no, validation_attempt, "failure", exc.category, str(exc))
            )
            if not isinstance(exc, TransportError) or not exc.retryable:
                raise
            last = exc
            if try_no < MAX_TRANSPORT_TRIES - 1:
                sleep(BACKOFF_BASE_S * (2**try_no) * (0.5 + random.random() / 2))
    assert last is not None
    raise last


def _evaluate[T: BaseModel](response: LLMResponse, schema: type[T]) -> tuple[T | None, str, str]:
    """Validate one response. Returns (data, category, feedback); data is None on failure."""
    if response.stop_reason == "max_tokens":
        return None, "truncated", "output was cut off at the token limit; be more concise"
    try:
        if response.tool_input is not None:
            candidate: Any = response.tool_input
        else:
            candidate = parse_json_text(response.text)
    except ValueError as exc:
        return None, "parse_error", str(exc)
    try:
        return schema.model_validate(candidate), "", ""
    except ValidationError as exc:
        return None, "validation", _validation_feedback(exc)


def run_structured[T: BaseModel](
    task: str,
    schema: type[T],
    prompt: str,
    *,
    business_id: Any = None,
    run_id: str | None = None,
    provider: LLMProvider | None = None,
    guard: CallGuard | None = None,
    sleep: Callable[[float], None] = time.sleep,
    fixture_scenario: str | None = None,
) -> StructuredResult[T]:
    """Run `task` and return a validated `schema` instance.

    `prompt` is the untrusted task input (idea text, page text); it is wrapped in a
    delimited data block. Raises StructuredOutputError if validation still fails after
    2 repair retries, or the provider's typed error (TransportError, RefusalError, ...).
    `business_id`/`run_id` are carried for the guard and usage records.
    `fixture_scenario` is a controlled id honoured by the mock provider only; for real
    calls it is dropped here so it can never influence a request.
    """
    spec = get_task(task)
    mock = is_mock()
    if guard is None and not mock:
        # Real calls always need a run_id so the per-run cap applies. Resolve it before
        # building a provider or touching anything, and never fall back to "uncapped".
        from app.llm.budget import LLMGuard
        from app.pipeline.runner import current_run_id

        rid = run_id if run_id else current_run_id.get()
        if not rid or rid == "-":
            raise RunIdRequired(
                f"task {task!r}: a real model call needs a run_id (argument or runner context)"
            )
        run_id = rid
    if provider is None:
        if mock:
            provider = MockProvider()
        else:
            from app.llm.claude import ClaudeProvider

            provider = ClaudeProvider()
    model = "mock" if mock else model_for(spec)
    if guard is None and not mock:
        guard = LLMGuard(business_id=business_id, run_id=run_id, provider_name=provider.name)

    tool_name = f"submit_{task}"
    request = LLMRequest(
        task=task,
        model=model,
        system=load_system_prompt(task)
        + f"\nCall the `{tool_name}` tool exactly once with your final answer.",
        user=wrap_untrusted(prompt),
        max_tokens=spec.max_tokens,
        timeout_s=spec.timeout_s,
        effort=spec.effort,
        tool_name=tool_name,
        tool_description=f"Submit the final structured result for the {task} task.",
        tool_schema=transform_schema(schema),
        fixture_scenario=fixture_scenario if mock else None,
    )

    attempts: list[AttemptRecord] = []
    call_counter = [0]
    feedback: str | None = None
    last_error = ""
    for validation_attempt in range(1, MAX_VALIDATION_RETRIES + 2):
        req = request if feedback is None else replace(request, repair_feedback=feedback)
        response, token, call_no = _transport_call(
            provider, req, call_counter, guard, sleep, attempts, validation_attempt
        )
        data, category, feedback_msg = _evaluate(response, schema)
        status = "success" if data is not None else "failure"
        if guard:
            guard.after(token, req, call_no, response, status, category or None)
        attempts.append(
            AttemptRecord(
                call_no,
                validation_attempt,
                status,
                category or None,
                feedback_msg or None,
                response,
            )
        )
        if data is not None:
            return StructuredResult(
                data=data, model=response.model, provider=provider.name, attempts=attempts
            )
        feedback, last_error = feedback_msg, f"{category}: {feedback_msg}"
        logger.warning(
            "structured task=%s attempt=%d rejected: %s", task, validation_attempt, category
        )

    raise StructuredOutputError(
        f"{task}: output failed validation after {MAX_VALIDATION_RETRIES + 1} attempts",
        task=task,
        attempts=MAX_VALIDATION_RETRIES + 1,
        last_error=last_error,
    )
