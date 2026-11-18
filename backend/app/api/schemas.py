import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

MAX_IDEA_CHARS = 2000
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def count_sentences(text: str) -> int:
    return len([part for part in _SENTENCE_SPLIT.split(text.strip()) if part.strip()])


class BusinessCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idea_text: str = Field(max_length=MAX_IDEA_CHARS)
    market: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ] = "India"
    price_guess: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    starting_budget: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)

    @field_validator("idea_text")
    @classmethod
    def _two_to_five_sentences(cls, value: str) -> str:
        value = value.strip()
        n = count_sentences(value)
        if not 2 <= n <= 5:
            raise ValueError(f"idea_text must be 2-5 sentences (found {n})")
        return value


class BusinessCreated(BaseModel):
    id: uuid.UUID
    status: str


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor: str
    action: str
    reason: str | None
    version_ref: str | None
    from_status: str | None
    to_status: str | None
    ts: datetime


class PipelineInfo(BaseModel):
    current_stage: str
    next_stage: str | None
    terminal: bool
    in_progress: bool
    attempts: int
    max_attempts: int
    failed_stage: str | None
    last_error: str | None
    retryable: bool
    history: list[AuditEntry]


class BusinessDetail(BaseModel):
    id: uuid.UUID
    idea_text: str
    market: str
    price_guess: Decimal | None
    starting_budget: Decimal | None
    target_customer: str | None
    status: str
    domain: str | None
    created_at: datetime
    updated_at: datetime
    pipeline: PipelineInfo
