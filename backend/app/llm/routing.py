"""Single task -> model routing table. No model selection logic lives anywhere else.

Model ids come only from environment config (CLAUDE_MODEL_STRONG / CLAUDE_MODEL_SMALL).
There is deliberately no temperature column: the Claude 5.x docs say sampling
parameters (temperature, top_p, top_k) return a 400 on Sonnet 5.5, so none are sent.
Add persona and rewrite tasks here in M5/M6.
"""

from dataclasses import dataclass
from typing import Literal

from app import config
from app.llm.base import ConfigError

Tier = Literal["strong", "small"]


@dataclass(frozen=True)
class TaskSpec:
    name: str
    tier: Tier
    max_tokens: int  # includes thinking tokens
    timeout_s: float
    fixture: str  # file under <FIXTURES_DIR>/llm/ used by MockProvider
    effort: str = "low"


TASKS: dict[str, TaskSpec] = {
    t.name: t
    for t in (
        TaskSpec("premortem", "strong", 4096, 90, "pre_mortem.json"),
        TaskSpec("assumption_extraction", "strong", 2048, 60, "assumptions.json"),
        TaskSpec("business_names", "small", 4096, 60, "business_names.json"),
        TaskSpec("website_content", "strong", 4096, 90, "content.json"),
    )
}


def get_task(name: str) -> TaskSpec:
    try:
        return TASKS[name]
    except KeyError:
        raise ConfigError(f"unknown LLM task: {name!r}") from None


def model_for(spec: TaskSpec) -> str:
    """Resolve the configured model id for a task. Read at call time so tests can patch config."""
    model = config.CLAUDE_MODEL_STRONG if spec.tier == "strong" else config.CLAUDE_MODEL_SMALL
    if not model:
        var = "CLAUDE_MODEL_STRONG" if spec.tier == "strong" else "CLAUDE_MODEL_SMALL"
        raise ConfigError(f"{var} is not set")
    return model
