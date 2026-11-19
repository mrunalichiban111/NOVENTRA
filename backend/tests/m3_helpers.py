"""Shared helpers for M3 tests: fixture ideas, a spying LLM runner and pipeline drivers."""

import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.llm.structured import run_structured
from app.models import Assumption, AuditLog, Business, Scorecard
from app.pipeline import repository
from app.pipeline.handlers import m3_stages
from app.pipeline.runner import current_run_id, run_business
from app.pipeline.status import PAUSE_STAGES, Actor

IDEAS = Path(__file__).resolve().parent.parent.parent / "fixtures" / "ideas"


def idea(name: str) -> str:
    return json.loads((IDEAS / f"{name}.json").read_text())["idea"]


def budget(name: str) -> float | None:
    """Starting budget stored with the sample idea (what the sample button will submit)."""
    return json.loads((IDEAS / f"{name}.json").read_text()).get("starting_budget")


class SpyLLM:
    """Drop-in for run_structured that records every call and can inject failures per task."""

    def __init__(self, failures: dict[str, list[Exception]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.failures = failures or {}

    def tasks(self) -> list[str]:
        return [c["task"] for c in self.calls]

    def __call__(self, task: str, schema: Any, prompt: str, **kw: Any) -> Any:
        self.calls.append(
            {
                "task": task,
                "prompt": prompt,
                "run_id": kw.get("run_id"),
                "business_id": kw.get("business_id"),
                "scenario": kw.get("fixture_scenario"),
                "context_run_id": current_run_id.get(),
            }
        )
        queue = self.failures.get(task)
        if queue:
            raise queue.pop(0)
        return run_structured(task, schema, prompt, **kw)


def new_business(
    sf: sessionmaker[Session], text: str, *, budget: float | None = None, price: float | None = None
) -> uuid.UUID:
    with sf() as s:
        b = repository.create_business(
            s,
            idea_text=text,
            market="India",
            price_guess=price,
            starting_budget=budget,
            actor=Actor.user(),
        )
        s.commit()
        return b.id


def run(sf: sessionmaker[Session], bid: uuid.UUID, llm: SpyLLM | None = None, worker: str = "w1"):
    """Claim (so we hold the lease) and run the M3 stages. Returns the last status."""
    with sf() as s:
        repository.claim_next(s, worker, 60, exclude=PAUSE_STAGES)
    return run_business(sf, bid, worker_id=worker, stages=m3_stages(llm or SpyLLM()))


def get(sf: sessionmaker[Session], bid: uuid.UUID) -> Business:
    with sf() as s:
        return s.get(Business, bid)


def assumptions(sf: sessionmaker[Session], bid: uuid.UUID) -> list[Assumption]:
    with sf() as s:
        return list(s.scalars(select(Assumption).where(Assumption.business_id == bid)))


def scorecards(sf: sessionmaker[Session], bid: uuid.UUID) -> list[Scorecard]:
    with sf() as s:
        return list(
            s.scalars(
                select(Scorecard).where(Scorecard.business_id == bid).order_by(Scorecard.version)
            )
        )


def audit(sf: sessionmaker[Session], bid: uuid.UUID) -> list[AuditLog]:
    with sf() as s:
        return list(
            s.scalars(select(AuditLog).where(AuditLog.business_id == bid).order_by(AuditLog.id))
        )
