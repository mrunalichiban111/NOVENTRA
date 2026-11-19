#!/usr/bin/env python3
"""Manual smoke test: ONE real, cheap Claude request through the full M2 path.

Requires ANTHROPIC_API_KEY and the model env vars; refuses to run in mock mode. It is
never run by tests or container startup. Run from the repo root, entering the key
without echoing it (the key is never printed or logged):

    read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY
    export CLAUDE_MODEL_STRONG=claude-sonnet-5-5 CLAUDE_MODEL_SMALL=claude-haiku-5-5
    export DATABASE_URL=...   # a migrated DB (alembic upgrade head), unless --no-db
    backend/.venv/bin/python scripts/llm_smoke_test.py --tier small
    backend/.venv/bin/python scripts/llm_smoke_test.py --tier strong   # also check Sonnet 5.5

Each run uses a fresh uuid4 run_id, so the per-run cap and usage rows are scoped to it.
`--no-db` skips Postgres (in-memory guard, nothing persisted).
"""

import argparse
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def refuse(msg: str) -> None:
    print(f"refusing to run: {msg}", file=sys.stderr)
    sys.exit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tier", choices=["small", "strong"], default="small")
    parser.add_argument("--no-db", action="store_true", help="in-memory guard; persist nothing")
    args = parser.parse_args()

    # Checks first, before importing anything that could reach the network.
    if os.environ.get("MOCK_LLM", "").lower() in {"1", "true"}:
        refuse("MOCK_LLM is set; the smoke test must make a real request")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        refuse("ANTHROPIC_API_KEY is not set")
    model_var = "CLAUDE_MODEL_STRONG" if args.tier == "strong" else "CLAUDE_MODEL_SMALL"
    if not os.environ.get(model_var):
        refuse(f"{model_var} is not set")

    from typing import Literal

    from pydantic import BaseModel

    from app.llm import budget, pricing, routing, structured
    from app.llm.base import LLMError

    class Capital(BaseModel):
        country: str
        capital: str
        confidence: Literal["high", "medium", "low"]

    # Registered at runtime only: the production routing table keeps just the M2 tasks.
    routing.TASKS["smoke_test"] = routing.TaskSpec("smoke_test", args.tier, 512, 30, "")

    class MemoryGuard:
        """In-memory stand-in for --no-db: same worst-case check, nothing persisted."""

        def before(self, request, call_no):
            budget.worst_case_usd(request)  # fails closed on missing pricing
            return None

        def after(self, *a):
            pass

    run_id = uuid.uuid4().hex
    print(f"run_id={run_id} tier={args.tier}")
    try:
        result = structured.run_structured(
            "smoke_test",
            Capital,
            "France",
            run_id=run_id,
            guard=MemoryGuard() if args.no_db else None,
        )
    except LLMError as exc:
        print(f"FAILED: {type(exc).__name__} ({exc.category}): {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # e.g. database unreachable when persisting usage
        print(
            f"FAILED: {type(exc).__name__}. If the database is the problem, try --no-db.",
            file=sys.stderr,
        )
        return 1

    model = result.model
    in_tok = sum(a.response.input_tokens for a in result.attempts if a.response)
    out_tok = sum(a.response.output_tokens for a in result.attempts if a.response)
    cr = sum(a.response.cache_read_tokens for a in result.attempts if a.response)
    cw = sum(a.response.cache_write_tokens for a in result.attempts if a.response)
    cost = pricing.estimate_cost_usd(
        model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=cr,
        cache_write_tokens=cw,
    )
    print(f"validated: {result.data.model_dump()}")
    print(f"model={model} calls={len(result.attempts)}")
    print(f"tokens: input={in_tok} output={out_tok} cache_read={cr} cache_write={cw}")
    print(f"estimated cost: ${cost:.6f} (an estimate from the price registry, not billing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
