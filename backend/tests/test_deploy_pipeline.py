"""M4B pipeline integration: DOMAIN_PENDING -> DEPLOYING -> LIVE through the real worker runner
and status machine. *_test databases only; mock LLM fixtures; mock or tmp_path providers only
(no VPS, no DNS, no Caddy, no network)."""

import threading
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app import config
from app.deploy import service
from app.deploy.providers import (
    DeployConfigError,
    DeployNotConfigured,
    DeploySettings,
    LocalReleaseProvider,
    MockDeployProvider,
)
from app.deploy.site_model import SiteLinks
from app.deploy.storage import ReleaseStore
from app.llm.base import StructuredOutputError
from app.models import (
    AuditLog,
    Business,
    BusinessStatus,
    Deployment,
    DomainCandidate,
    LlmUsage,
    SiteVersion,
)
from app.pipeline import repository, status
from app.pipeline.handlers import pipeline_stages
from app.pipeline.runner import run_business
from app.pipeline.status import RETRYABLE_STAGES, Actor
from tests.m3_helpers import SpyLLM

S = BusinessStatus
BASE = "example.in"
HOST = f"tiffo.{BASE}"


@pytest.fixture(autouse=True)
def _deploy_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "BASE_DOMAIN", BASE)
    monkeypatch.setattr(config, "DEPLOY_PROVIDER", "mock")
    monkeypatch.setattr(config, "APP_ENV", "development")
    monkeypatch.setattr(config, "SITE_REQUIRE_PUBLIC", False)
    monkeypatch.setattr(config, "WAITLIST_POST_URL", "")
    monkeypatch.setattr(config, "ABUSE_REPORT_EMAIL", "")


DEV = DeploySettings(
    provider="mock",
    base_domain=BASE,
    sites_root="/nonexistent",
    dns_mode="skip",
    http_check=False,
    require_public=False,
    production=False,
    health_timeout_s=60,
)


# --- helpers ---------------------------------------------------------------------------------


def pending_business(sf: sessionmaker[Session], *, price: str | None = "1299") -> uuid.UUID:
    """A business that has claimed tiffo.example.in (state reached through M4A)."""
    with sf() as s:
        b = Business(
            idea_text="Home-cooked tiffins for hostel students in Pune.",
            market="India",
            price_guess=None if price is None else Decimal(price),
            status=S.DOMAIN_PENDING,
            domain=HOST,
            premortem_json={"target_customer": "Hostel students in Pune", "competitors": []},
        )
        s.add(b)
        s.flush()
        s.add(
            DomainCandidate(
                business_id=b.id, name="Tiffo", slug="tiffo", valid=True, selected=True, rank=1
            )
        )
        s.commit()
        return b.id


def request(sf: sessionmaker[Session], bid: uuid.UUID) -> service.DeployRequest:
    with sf() as s:
        return service.request_deploy(s, bid, settings=DEV)


def run(
    sf: sessionmaker[Session],
    bid: uuid.UUID,
    provider,  # noqa: ANN001
    llm: SpyLLM | None = None,
    *,
    worker: str = "w1",
    require_public: bool = False,
) -> S | None:
    stages = pipeline_stages(
        llm or SpyLLM(),
        provider_factory=lambda: provider,
        links=SiteLinks(),
        require_public=require_public,
    )
    halted = RETRYABLE_STAGES - stages.keys()
    with sf() as s:
        repository.claim_next(s, worker, 60, exclude=halted)
    return run_business(sf, bid, worker_id=worker, stages=stages)


def retry(sf: sessionmaker[Session], bid: uuid.UUID) -> None:
    with sf() as s:
        status.retry(s, bid, actor=Actor.user(), reason="test retry")
        s.commit()


def get(sf: sessionmaker[Session], bid: uuid.UUID) -> Business:
    with sf() as s:
        return s.get(Business, bid)


def versions(sf: sessionmaker[Session], bid: uuid.UUID) -> list[SiteVersion]:
    with sf() as s:
        return list(
            s.scalars(
                select(SiteVersion)
                .where(SiteVersion.business_id == bid)
                .order_by(SiteVersion.version)
            )
        )


def deployments(sf: sessionmaker[Session], bid: uuid.UUID) -> list[Deployment]:
    with sf() as s:
        return list(
            s.scalars(
                select(Deployment)
                .where(Deployment.business_id == bid)
                .order_by(Deployment.started_at, Deployment.attempt)
            )
        )


def actions(sf: sessionmaker[Session], bid: uuid.UUID) -> list[str]:
    with sf() as s:
        return list(
            s.scalars(
                select(AuditLog.action).where(AuditLog.business_id == bid).order_by(AuditLog.id)
            )
        )


def assert_claim_preserved(sf: sessionmaker[Session], bid: uuid.UUID) -> None:
    with sf() as s:
        b = s.get(Business, bid)
        selected = s.scalars(
            select(DomainCandidate).where(
                DomainCandidate.business_id == bid, DomainCandidate.selected.is_(True)
            )
        ).all()
    assert b.domain == HOST
    assert [c.slug for c in selected] == ["tiffo"]


def site_calls(llm: SpyLLM) -> int:
    return llm.tasks().count("website_content")


# --- happy paths ----------------------------------------------------------------------------


def test_full_mock_lifecycle_reaches_live(session_factory) -> None:
    bid = pending_business(session_factory)
    assert request(session_factory, bid).changed
    assert get(session_factory, bid).status == "DEPLOYING"

    provider, llm = MockDeployProvider(), SpyLLM()
    assert run(session_factory, bid, provider, llm) == S.LIVE

    b = get(session_factory, bid)
    assert (b.status, b.locked_by, b.attempts) == ("LIVE", None, 0)
    (v1,) = versions(session_factory, bid)
    assert (v1.version, v1.status, v1.host, v1.generation_attempt) == (1, "deployed", HOST, 1)
    assert v1.content_json["hero"]["headline"] and len(v1.content_hash) == 64
    assert v1.manifest_json["files"]["index.html"]["sha256"] and v1.deployed_at is not None
    (dep,) = deployments(session_factory, bid)
    assert (dep.status, dep.verification_level, dep.attempt) == ("verified", "simulated", 1)
    assert dep.health_json["publicly_reachable"] is False
    assert provider.active_version(HOST) == 1

    assert site_calls(llm) == 1
    (call,) = [c for c in llm.calls if c["task"] == "website_content"]
    assert call["run_id"] == call["context_run_id"] and call["business_id"] == bid
    assert actions(session_factory, bid) == [
        "deploy.requested",
        "site.generation_started",
        "site.content_generated",
        "site.built",
        "deploy.activated",
        "deploy.verified",
        "stage.deploying",
    ]
    with session_factory() as s:
        live = s.scalars(select(AuditLog).where(AuditLog.to_status == "LIVE")).one()
    assert live.version_ref == "site-v1" and "simulated" in live.reason
    assert "NOT publicly verified" in live.reason
    assert_claim_preserved(session_factory, bid)


def test_local_provider_lifecycle_writes_an_immutable_release(session_factory, tmp_path) -> None:
    root = tmp_path / "sites"
    root.mkdir()
    root.chmod(0o755)
    provider = LocalReleaseProvider(ReleaseStore(root, BASE))
    bid = pending_business(session_factory)
    request(session_factory, bid)
    assert run(session_factory, bid, provider) == S.LIVE
    current = root / HOST / "current"
    assert current.is_symlink() and current.readlink() == Path("releases/v1")
    assert b"Tiffo" in (current / "index.html").read_bytes()
    assert b"\xe2\x82\xb91,299" in (current / "index.html").read_bytes()  # founder price, ₹
    (dep,) = deployments(session_factory, bid)
    assert dep.verification_level == "local" and dep.provider == "local"
    (v1,) = versions(session_factory, bid)
    assert v1.path == f"{HOST}/releases/v1"


def test_live_is_a_pause_stage_and_never_claimed(session_factory) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    run(session_factory, bid, MockDeployProvider())
    halted = RETRYABLE_STAGES - pipeline_stages().keys()
    assert S.LIVE in halted and S.DOMAIN_PENDING in halted and S.DEPLOYING not in halted
    with session_factory() as s:
        assert repository.claim_next(s, "w9", 60, exclude=halted) is None


def test_domain_pending_is_never_claimed_without_a_request(session_factory) -> None:
    bid = pending_business(session_factory)
    halted = RETRYABLE_STAGES - pipeline_stages().keys()
    with session_factory() as s:
        assert repository.claim_next(s, "w1", 60, exclude=halted) is None
    assert get(session_factory, bid).status == "DOMAIN_PENDING"


# --- deploy requests --------------------------------------------------------------------------


def test_concurrent_deploy_requests_transition_exactly_once(session_factory) -> None:
    bid = pending_business(session_factory)
    barrier = threading.Barrier(8)
    results: list = []

    def worker() -> None:
        barrier.wait()
        try:
            results.append(request(session_factory, bid).changed)
        except Exception as exc:  # pragma: no cover - surfaced by the assertion
            results.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False] * 7 + [True]
    assert actions(session_factory, bid).count("deploy.requested") == 1
    assert get(session_factory, bid).status == "DEPLOYING"


def test_repeat_request_after_live_is_a_noop(session_factory) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    run(session_factory, bid, MockDeployProvider())
    assert request(session_factory, bid).changed is False
    assert get(session_factory, bid).status == "LIVE"


def test_request_refused_when_deployment_is_not_configured(session_factory) -> None:
    import dataclasses

    bid = pending_business(session_factory)
    with session_factory() as s, pytest.raises(DeployNotConfigured):
        service.request_deploy(s, bid, settings=dataclasses.replace(DEV, provider=""))
    prod_mock = dataclasses.replace(DEV, production=True, require_public=True)
    with session_factory() as s, pytest.raises(DeployConfigError):
        service.request_deploy(s, bid, settings=prod_mock)
    assert get(session_factory, bid).status == "DOMAIN_PENDING"
    assert actions(session_factory, bid) == []


def test_request_refused_for_ineligible_or_unclaimed_business(session_factory) -> None:
    bid = pending_business(session_factory)
    with session_factory() as s:
        s.execute(update(Business).where(Business.id == bid).values(status="GATED"))
        s.commit()
    with session_factory() as s, pytest.raises(service.NotEligible):
        service.request_deploy(s, bid, settings=DEV)

    other = pending_business_without_claim(session_factory)
    with session_factory() as s, pytest.raises(service.DeployInputError):
        service.request_deploy(s, other, settings=DEV)
    assert get(session_factory, other).status == "DOMAIN_PENDING"


def pending_business_without_claim(sf: sessionmaker[Session]) -> uuid.UUID:
    with sf() as s:
        b = Business(idea_text="An idea.", status=S.DOMAIN_PENDING, domain="other.example.in")
        s.add(b)
        s.commit()
        return b.id


# --- failures at each stage, retry and recovery ------------------------------------------------


def test_generation_failure_fails_visibly_and_retry_regenerates(session_factory) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    err = StructuredOutputError("bad", task="website_content", attempts=3, last_error="x")
    llm = SpyLLM({"website_content": [err]})
    assert run(session_factory, bid, MockDeployProvider(), llm) == S.FAILED
    b = get(session_factory, bid)
    assert (b.status, b.failed_stage) == ("FAILED", "DEPLOYING")
    (v1,) = versions(session_factory, bid)
    assert (v1.status, v1.content_json) == ("abandoned", None)
    assert "StructuredOutputError" in v1.error
    assert_claim_preserved(session_factory, bid)

    retry(session_factory, bid)
    provider = MockDeployProvider()
    assert run(session_factory, bid, provider, llm) == S.LIVE
    (v1,) = versions(session_factory, bid)  # same version number, second attempt
    assert (v1.version, v1.status, v1.generation_attempt) == (1, "deployed", 2)
    assert site_calls(llm) == 2


def test_publish_failure_keeps_the_draft_and_retry_makes_no_new_model_call(
    session_factory,
) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    llm, provider = SpyLLM(), MockDeployProvider(fail={"publish"})
    assert run(session_factory, bid, provider, llm) == S.FAILED
    (v1,) = versions(session_factory, bid)
    assert v1.status == "draft" and v1.content_json is not None
    assert "simulated publish failure" in get(session_factory, bid).last_error
    assert_claim_preserved(session_factory, bid)

    retry(session_factory, bid)
    provider.fail = set()
    assert run(session_factory, bid, provider, llm) == S.LIVE
    assert site_calls(llm) == 1  # the saved draft was reused


def test_activation_failure_records_history_and_retry_redeploys(session_factory) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    llm, provider = SpyLLM(), MockDeployProvider(fail={"activate"})
    assert run(session_factory, bid, provider, llm) == S.FAILED
    (v1,) = versions(session_factory, bid)
    assert v1.status == "failed" and v1.artifact_hash
    (dep,) = deployments(session_factory, bid)
    assert (dep.status, dep.attempt) == ("failed", 1) and "Activation" in dep.error
    assert_claim_preserved(session_factory, bid)

    retry(session_factory, bid)
    provider.fail = set()
    publishes = [c for c in provider.calls if c[0] == "publish"]
    assert run(session_factory, bid, provider, llm) == S.LIVE
    assert [c for c in provider.calls if c[0] == "publish"] == publishes  # release reused
    assert [(d.attempt, d.status) for d in deployments(session_factory, bid)] == [
        (1, "failed"),
        (2, "verified"),
    ]
    assert site_calls(llm) == 1


def test_health_check_failure_fails_and_retry_recovers(session_factory) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    llm, provider = SpyLLM(), MockDeployProvider(fail={"verify"})
    assert run(session_factory, bid, provider, llm) == S.FAILED
    b = get(session_factory, bid)
    assert "Health check failed" in b.last_error
    (dep,) = deployments(session_factory, bid)
    assert dep.status == "failed" and dep.health_json["ok"] is False
    assert versions(session_factory, bid)[0].status == "failed"
    assert_claim_preserved(session_factory, bid)

    retry(session_factory, bid)
    provider.fail = set()
    assert run(session_factory, bid, provider, llm) == S.LIVE
    assert site_calls(llm) == 1
    assert actions(session_factory, bid).count("deploy.failed") == 1


def test_require_public_refuses_live_for_simulated_or_local_verification(session_factory) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    provider = MockDeployProvider()
    assert run(session_factory, bid, provider, require_public=True) == S.FAILED
    b = get(session_factory, bid)
    assert "does not meet the required 'public' level" in b.last_error
    (dep,) = deployments(session_factory, bid)
    assert (dep.status, dep.verification_level) == ("failed", "simulated")
    assert_claim_preserved(session_factory, bid)


def test_production_config_without_public_verification_fails_the_stage(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    monkeypatch.setattr(config, "APP_ENV", "production")  # DEPLOY_PROVIDER is still "mock"
    stages = pipeline_stages(SpyLLM(), links=SiteLinks())  # real get_provider()
    with session_factory() as s:
        repository.claim_next(s, "w1", 60)
    assert run_business(session_factory, bid, worker_id="w1", stages=stages) == S.FAILED
    assert "not allowed in production" in get(session_factory, bid).last_error
    assert versions(session_factory, bid) == []  # refused before any paid call
    assert_claim_preserved(session_factory, bid)


def test_missing_claim_fails_without_a_model_call(session_factory) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    with session_factory() as s:  # simulate a corrupted claim
        s.execute(
            update(DomainCandidate).where(DomainCandidate.business_id == bid).values(selected=False)
        )
        s.commit()
    llm = SpyLLM()
    assert run(session_factory, bid, MockDeployProvider(), llm) == S.FAILED
    assert (
        site_calls(llm) == 0
        and "No business name is selected" in get(session_factory, bid).last_error
    )


# --- crash recovery (R4) ----------------------------------------------------------------------


def crashed_generation(sf: sessionmaker[Session], bid: uuid.UUID) -> None:
    """State left by a worker that died during the paid call: a generating row from another run,
    with that run's recorded spend."""
    with sf() as s:
        s.add(
            SiteVersion(
                business_id=bid,
                version=1,
                status="generating",
                host=HOST,
                generation_run_id="deadrun",
                generation_attempt=1,
            )
        )
        s.add(
            LlmUsage(
                business_id=bid,
                run_id="deadrun",
                task="website_content",
                provider="anthropic",
                model="m",
                attempt=1,
                estimated_cost_usd=Decimal("0.031"),
                status="success",
            )
        )
        s.commit()


def test_interrupted_generation_is_abandoned_without_paying_again(session_factory) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    crashed_generation(session_factory, bid)
    llm = SpyLLM()
    assert run(session_factory, bid, MockDeployProvider(), llm) == S.FAILED
    assert site_calls(llm) == 0
    (v1,) = versions(session_factory, bid)
    assert v1.status == "abandoned" and "$0.031" in v1.error
    with session_factory() as s:
        ev = s.scalars(select(AuditLog).where(AuditLog.action == "site.generation_abandoned")).one()
    assert ev.details["spend_usd"] == "0.031000" and ev.details["run_id"] == "deadrun"
    assert "interrupted" in get(session_factory, bid).last_error
    assert_claim_preserved(session_factory, bid)

    retry(session_factory, bid)  # explicit, audited permission to pay again
    assert run(session_factory, bid, MockDeployProvider(), llm) == S.LIVE
    assert site_calls(llm) == 1
    assert versions(session_factory, bid)[0].generation_attempt == 2


def test_abandoned_generation_needs_an_explicit_retry(session_factory) -> None:
    """Even if a business is resumed in DEPLOYING (e.g. a lost-lease resume) an abandoned
    generation is not regenerated without a pipeline.retry after it was abandoned."""
    bid = pending_business(session_factory)
    request(session_factory, bid)
    crashed_generation(session_factory, bid)
    with session_factory() as s:
        s.execute(
            update(SiteVersion).where(SiteVersion.business_id == bid).values(status="abandoned")
        )
        s.commit()
    llm = SpyLLM()
    assert run(session_factory, bid, MockDeployProvider(), llm) == S.FAILED
    assert site_calls(llm) == 0


# --- lease loss ---------------------------------------------------------------------------------


def steal_lease(sf: sessionmaker[Session], bid: uuid.UUID, to: str = "w2") -> None:
    with sf() as s:
        s.execute(
            update(Business)
            .where(Business.id == bid)
            .values(locked_by=None, locked_until=text("now() - interval '1 second'"))
        )
        s.commit()
        assert repository.claim_next(s, to, 60) is not None


def test_lease_lost_during_generation_writes_nothing_and_new_owner_does_not_repay(
    session_factory,
) -> None:
    class StealingLLM(SpyLLM):
        def __call__(self, task, schema, prompt, **kw):  # noqa: ANN001, ANN003, ANN204
            if task == "website_content":
                steal_lease(session_factory, kw["business_id"])
            return super().__call__(task, schema, prompt, **kw)

    bid = pending_business(session_factory)
    request(session_factory, bid)
    llm = StealingLLM()
    assert run(session_factory, bid, MockDeployProvider(), llm) == S.DEPLOYING  # quietly
    (v1,) = versions(session_factory, bid)
    assert v1.status == "generating" and v1.content_json is None  # w1 could not store
    b = get(session_factory, bid)
    assert (b.status, b.locked_by, b.attempts) == ("DEPLOYING", "w2", 0)

    # w2 resumes: the paid result may be lost, so it fails visibly instead of paying again
    spy = SpyLLM()
    stages = pipeline_stages(spy, provider_factory=MockDeployProvider, links=SiteLinks())
    assert run_business(session_factory, bid, worker_id="w2", stages=stages) == S.FAILED
    assert site_calls(spy) == 0 and versions(session_factory, bid)[0].status == "abandoned"


def test_lease_lost_during_verification_commits_nothing_and_resume_completes(
    session_factory,
) -> None:
    class StealingProvider(MockDeployProvider):
        steal = True

        def verify(self, host, version, manifest):  # noqa: ANN001, ANN201
            if self.steal:
                self.steal = False
                steal_lease(session_factory, bid)
            return super().verify(host, version, manifest)

    bid = pending_business(session_factory)
    request(session_factory, bid)
    provider, llm = StealingProvider(), SpyLLM()
    assert run(session_factory, bid, provider, llm) == S.DEPLOYING
    (dep,) = deployments(session_factory, bid)
    assert dep.status == "activated"  # verified writes were never committed
    assert versions(session_factory, bid)[0].status == "built"
    assert "deploy.verified" not in actions(session_factory, bid)

    stages = pipeline_stages(llm, provider_factory=lambda: provider, links=SiteLinks())
    assert run_business(session_factory, bid, worker_id="w2", stages=stages) == S.LIVE
    assert [(d.attempt, d.status) for d in deployments(session_factory, bid)] == [
        (1, "failed"),
        (2, "verified"),
    ]
    assert "Interrupted" in deployments(session_factory, bid)[0].error
    assert site_calls(llm) == 1


# --- rollback to the previous verified release ----------------------------------------------------


def test_failed_new_version_restores_the_previous_verified_release(session_factory) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    provider, llm = MockDeployProvider(), SpyLLM()
    assert run(session_factory, bid, provider, llm) == S.LIVE

    # Simulate the M6 path that starts a v2 deployment (no API exists for it yet).
    with session_factory() as s:
        s.execute(update(Business).where(Business.id == bid).values(status="DEPLOYING"))
        s.commit()
    provider.fail = {"verify"}
    assert run(session_factory, bid, provider, llm) == S.FAILED

    v1, v2 = versions(session_factory, bid)
    assert (v1.status, v2.status, v2.parent_version) == ("deployed", "failed", 1)
    assert provider.active_version(HOST) == 1  # atomically restored
    failed = deployments(session_factory, bid)[-1]
    assert failed.health_json["restored_version"] == 1
    with session_factory() as s:
        ev = s.scalars(select(AuditLog).where(AuditLog.action == "deploy.failed")).one()
    assert ev.details["restored_version"] == 1 and ev.version_ref == "site-v2"
    assert_claim_preserved(session_factory, bid)


def test_successful_new_version_supersedes_the_old_one(session_factory) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    provider = MockDeployProvider()
    run(session_factory, bid, provider)
    with session_factory() as s:
        s.execute(update(Business).where(Business.id == bid).values(status="DEPLOYING"))
        s.commit()
    assert run(session_factory, bid, provider) == S.LIVE
    assert [(v.version, v.status) for v in versions(session_factory, bid)] == [
        (1, "superseded"),
        (2, "deployed"),
    ]
    assert provider.active_version(HOST) == 2
    # two distinct DEPLOYING -> LIVE transitions, keyed by their site versions
    v1, v2 = versions(session_factory, bid)
    with session_factory() as s:
        lives = s.scalars(
            select(AuditLog).where(AuditLog.business_id == bid, AuditLog.to_status == "LIVE")
        ).all()
    assert [a.version_ref for a in lives] == ["site-v1", "site-v2"]
    assert [a.idempotency_key for a in lives] == [
        f"{bid}:DEPLOYING->LIVE:0:site:{v1.id}",
        f"{bid}:DEPLOYING->LIVE:0:site:{v2.id}",
    ]


# --- idempotency and duplicates -----------------------------------------------------------------


def test_handler_on_a_live_business_changes_nothing(session_factory) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    provider = MockDeployProvider()
    run(session_factory, bid, provider)
    before = (versions(session_factory, bid), deployments(session_factory, bid))
    handler = pipeline_stages(SpyLLM(), provider_factory=lambda: provider, links=SiteLinks())[
        S.DEPLOYING
    ]
    with session_factory() as s:
        b = s.get(Business, bid)
        with pytest.raises(status.StaleState):
            handler(s, b)
        s.rollback()
    after = (versions(session_factory, bid), deployments(session_factory, bid))
    assert [v.id for v in before[0]] == [v.id for v in after[0]]
    assert [d.id for d in before[1]] == [d.id for d in after[1]]


def test_audit_keys_are_unique_across_failures_and_retries(session_factory) -> None:
    bid = pending_business(session_factory)
    request(session_factory, bid)
    provider = MockDeployProvider(fail={"verify"})
    run(session_factory, bid, provider)
    retry(session_factory, bid)
    run(session_factory, bid, provider)  # fails again: attempt 2
    retry(session_factory, bid)
    provider.fail = set()
    assert run(session_factory, bid, provider) == S.LIVE
    with session_factory() as s:
        keys = s.scalars(select(AuditLog.idempotency_key).where(AuditLog.business_id == bid)).all()
    assert len(keys) == len(set(keys))
    acts = actions(session_factory, bid)
    assert acts.count("deploy.failed") == 2 and acts.count("deploy.verified") == 1
    assert acts.count("site.content_generated") == 1 and acts.count("site.built") == 1
    assert [d.attempt for d in deployments(session_factory, bid)] == [1, 2, 3]


def test_duplicate_live_transition_for_the_same_version_is_refused(session_factory) -> None:
    """Same logical transition (same site version, same failure count) executed twice: the
    second one collides on the idempotency key and records nothing."""
    bid = pending_business(session_factory)
    request(session_factory, bid)
    run(session_factory, bid, MockDeployProvider())
    (v1,) = versions(session_factory, bid)
    with session_factory() as s:  # a stale executor that never saw the first LIVE
        s.execute(update(Business).where(Business.id == bid).values(status="DEPLOYING"))
        s.commit()
    with session_factory() as s:
        with pytest.raises(status.StaleState, match="already recorded"):
            status.transition(
                s,
                bid,
                S.LIVE,
                actor=Actor.worker("w9"),
                action="stage.deploying",
                expected_from=S.DEPLOYING,
                key_ref=f"site:{v1.id}",
            )
        s.rollback()
    with session_factory() as s:
        count = s.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.business_id == bid, AuditLog.to_status == "LIVE")
        )
    assert count == 1
