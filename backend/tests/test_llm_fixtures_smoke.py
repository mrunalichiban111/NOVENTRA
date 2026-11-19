"""Fixtures, end-to-end mock mode, network isolation and the smoke-test script's guards.

All pure (no DB). The real fixtures come from the repo-root fixtures/llm directory via the
default FIXTURES_DIR, exactly as dev and Docker use them.
"""

import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from app import config
from app.llm import routing, structured
from app.llm.base import TransportError
from app.llm.claude import ClaudeProvider
from app.llm.schemas import OUTPUT_SCHEMAS, WebsiteContent
from tests.llm_helpers import ScriptedProvider  # noqa: F401  (kept for parity with other modules)

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
FIXTURES = REPO / "fixtures" / "llm"
SMOKE = REPO / "scripts" / "llm_smoke_test.py"


def load(task: str) -> dict:
    return json.loads((FIXTURES / routing.TASKS[task].fixture).read_text())


@pytest.mark.parametrize("task", sorted(routing.TASKS))
def test_each_fixture_validates_against_the_production_schema(task):
    OUTPUT_SCHEMAS[task].model_validate(load(task))


def test_business_name_fixture_has_40_plus_clean_unique_names():
    names = [c["name"] for c in load("business_names")["candidates"]]
    assert len(names) >= 40
    assert len({n.lower() for n in names}) == len(names)
    assert all(re.fullmatch(r"[A-Za-z]{4,14}", n) for n in names)


def test_website_fixture_has_no_invented_proof_html_or_links():
    c = WebsiteContent.model_validate(load("website_content"))
    assert c.proof.missing_proof, "missing proof must be flagged, not invented"
    shown = json.dumps(c.model_dump(exclude={"proof": {"missing_proof"}})).lower()
    for banned in ("testimonial", "review", "rated", "stars", "%", "<", "http", "www.", "@"):
        assert banned not in shown, banned


def test_assumption_fixture_covers_all_six_keys_in_order_low_base_high():
    items = load("assumption_extraction")["assumptions"]
    assert len(items) == 6 and all(i["low"] <= i["base"] <= i["high"] for i in items)


# --- mock mode end to end, offline ---------------------------------------------------


def test_mock_mode_runs_every_task_with_no_key_no_run_id_and_no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access attempted")

    for fn in ("connect", "connect_ex", "send", "sendall"):
        monkeypatch.setattr(socket.socket, fn, boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(config, "MOCK_LLM", True)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "CLAUDE_MODEL_STRONG", "")  # model ids are not needed in mock mode
    monkeypatch.setattr(config, "FIXTURES_DIR", str(FIXTURES.parent))
    for task, schema in OUTPUT_SCHEMAS.items():
        result = structured.run_structured(task, schema, "A neighbourhood delivery idea")
        assert result.provider == "mock" and len(result.attempts) == 1
        assert result.attempts[0].response.input_tokens == 0


def test_network_blocker_catches_a_real_client_call(monkeypatch, network_attempts):
    """Proves the autouse guard works: a real SDK client cannot get any request out."""
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    from app.llm.base import LLMRequest

    req = LLMRequest("premortem", "claude-haiku-5-5", "s", "u", 16, 5.0)
    with pytest.raises(TransportError):
        ClaudeProvider().complete(req)  # builds the real anthropic client
    assert network_attempts == ["api.anthropic.com"]  # attempted, and stopped before the wire
    network_attempts.clear()  # deliberate: this test provoked it


# --- smoke-test script guards (subprocess; never reaches the network) ------------------


def run_smoke(**env: str) -> subprocess.CompletedProcess:
    base = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    return subprocess.run(
        [sys.executable, str(SMOKE)],
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_smoke_script_refuses_mock_mode():
    for flag in ("1", "true"):
        r = run_smoke(MOCK_LLM=flag, ANTHROPIC_API_KEY="sk-x", CLAUDE_MODEL_SMALL="m")
        assert r.returncode == 2 and "MOCK_LLM" in r.stderr


def test_smoke_script_refuses_without_key_or_model():
    r = run_smoke(CLAUDE_MODEL_SMALL="m")
    assert r.returncode == 2 and "ANTHROPIC_API_KEY" in r.stderr
    r = run_smoke(ANTHROPIC_API_KEY="sk-x")
    assert r.returncode == 2 and "CLAUDE_MODEL_SMALL" in r.stderr
    assert "sk-x" not in r.stdout + r.stderr  # the key is never echoed


def test_smoke_script_uses_a_unique_run_id():
    src = SMOKE.read_text()
    assert "uuid.uuid4().hex" in src and "run_id=run_id" in src


def test_empty_env_values_fall_back_to_defaults():
    code = "import app.config as c; print(c.PER_RUN_CAP_USD, bool(c.FIXTURES_DIR))"
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env={"PATH": os.environ.get("PATH", ""), "PER_RUN_CAP_USD": "", "FIXTURES_DIR": ""},
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == "2.0 True", out.stderr


def test_real_sdk_connect_failure_is_classified_pre_send(monkeypatch, network_attempts):
    """Through the real anthropic client: its own (httpx2) ConnectError means 'not sent'."""
    import httpx2

    from app.llm.base import LLMRequest

    def refuse_connect(self, request):
        network_attempts.append("recorded")
        raise httpx2.ConnectError("connection refused")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", refuse_connect)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    req = LLMRequest("premortem", "claude-haiku-5-5", "s", "u", 16, 5.0)
    with pytest.raises(TransportError) as ei:
        ClaudeProvider().complete(req)
    assert (ei.value.outcome, ei.value.retryable) == ("not_sent", True)
    network_attempts.clear()


def test_real_sdk_read_timeout_is_classified_ambiguous(monkeypatch, network_attempts):
    import httpx2

    from app.llm.base import LLMRequest

    def read_timeout(self, request):
        network_attempts.append("recorded")
        raise httpx2.ReadTimeout("slow")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", read_timeout)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    req = LLMRequest("premortem", "claude-haiku-5-5", "s", "u", 16, 5.0)
    with pytest.raises(TransportError) as ei:
        ClaudeProvider().complete(req)
    assert (ei.value.outcome, ei.value.retryable) == ("ambiguous", False)
    network_attempts.clear()
