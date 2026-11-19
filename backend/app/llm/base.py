"""Provider interface, request/response types and typed exceptions for the LLM layer.

A provider performs exactly ONE network attempt per `complete()` call. Retries
(transport and validation) and budget checks live in `structured.py`, so that every
real attempt can be reserved against the per-run cap.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(Exception):
    """Base class for all LLM-layer errors."""

    category = "llm_error"


class ConfigError(LLMError):
    category = "config"


class TransportError(LLMError):
    """Network/API failure, classified by what we know about the request's fate.

    outcome:
      "not_sent"  - failed before the request left us (e.g. could not connect). Nothing billed.
      "rejected"  - the provider answered with an error status (429/5xx/4xx). No usage returned.
      "ambiguous" - timeout/interruption after the request may have been transmitted; the
                    provider may have processed (and billed) it. Never retried automatically.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        outcome: str = "rejected",
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.outcome = outcome

    @property
    def category(self) -> str:  # type: ignore[override]
        return f"transport_{self.outcome}"


class RefusalError(LLMError):
    """The model declined. The provider did answer, so usage is known and carried here."""

    category = "refusal"

    def __init__(self, message: str, *, response: "LLMResponse | None" = None):
        super().__init__(message)
        self.response = response


class RunIdRequired(LLMError):
    """A real (non-mock) model call needs a run_id so the per-run spend cap applies."""

    category = "run_id_required"


class StructuredOutputError(LLMError):
    """Validation still failed after all repair attempts. Never carries fabricated data."""

    category = "validation"

    def __init__(self, message: str, *, task: str, attempts: int, last_error: str):
        super().__init__(message)
        self.task = task
        self.attempts = attempts
        self.last_error = last_error


class BudgetExceeded(LLMError):
    category = "budget"


class PricingMissing(LLMError):
    category = "pricing_missing"


class InputTooLarge(LLMError):
    category = "input_too_large"


@dataclass(frozen=True)
class LLMRequest:
    task: str
    model: str
    system: str
    user: str
    max_tokens: int
    timeout_s: float
    effort: str = "low"
    # When set, the provider asks for a structured result via a single strict tool.
    tool_name: str | None = None
    tool_description: str = ""
    tool_schema: dict[str, Any] | None = None
    # Concise validation feedback for a repair attempt. Appended to the user message; the
    # model's earlier invalid output is deliberately not echoed back.
    repair_feedback: str | None = None
    # MockProvider only: a controlled scenario id (see llm/scenarios.py) selecting a fixture
    # subdirectory. Real providers never read this field.
    fixture_scenario: str | None = None


@dataclass(frozen=True)
class LLMResponse:
    model: str
    text: str = ""
    tool_input: dict[str, Any] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    request_id: str | None = None
    stop_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> LLMResponse:
        """One attempt. Raises TransportError / RefusalError / ConfigError."""
        ...
