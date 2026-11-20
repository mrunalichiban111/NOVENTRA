"""M6 improvement studio: classification, proposal validation and the full draft lifecycle.
*_test DB for the flow tests; MOCK_LLM fixtures (rewrite.json) and an in-process fake for the
persona calls; mock deploy provider. No network, no paid calls."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app import config
from app.agents import improvement as rewrite_agent
from app.deploy.providers import MockDeployProvider
from app.deploy.render import render_site
from app.deploy.site_model import site_links
from app.llm.schemas import RewriteItem, WebsiteContent
from app.llm.structured import PROMPTS_DIR
from app.models import Business, ImprovementAttempt, SiteVersion
from app.pipeline import arena, improve
from tests.arena.helpers import FakeArenaLLM, configure, live_business

W = "w-improve"
MARKER = "share the cost of same-day trips"  # text from fixtures/llm/rewrite.json

with open(f"{config.FIXTURES_DIR}/llm/content.json", encoding="utf-8") as fh:
    CONTENT = json.load(fh)


def item(path: str, text: str) -> RewriteItem:
    return RewriteItem(path=path, new_text=text, rationale="r", opportunity_id="o")


# --- pure rules -----------------------------------------------------------------------------


def test_classification_precedence() -> None:
    assert improve.classify_path("hero.headline", "Fresh lunches") == improve.SAFE
    assert improve.classify_path("faq.0.answer", "Delivered by 1 pm") == improve.FOUNDER
    assert improve.classify_path("proof.founder_story", "x") == improve.FOUNDER
    assert improve.classify_path("pricing.0.bullets.0", "x") == improve.APPROVAL
    assert improve.classify_path("proof.guarantees", "Refund 7 days") == improve.APPROVAL
    assert improve.highest([improve.SAFE, improve.APPROVAL, improve.FOUNDER]) == improve.APPROVAL
    assert improve.classify_opportunity("price", "hero") == improve.APPROVAL
    assert improve.classify_opportunity("trust", "proof") == improve.FOUNDER
    assert improve.classify_opportunity("clarity", "cta") == improve.SAFE


def test_proposal_validation_rejects_unsafe_items() -> None:
    raw = [
        item("hero.headline", "Lunch from home kitchens near you"),
        item("hero.script", "x"),  # unknown path
        item("pricing.0.price_inr", "99"),  # locked path
        item("faq.0.answer", "<b>Yes</b> soon"),  # markup
        item("problem.0", "Visit https://evil.example now"),  # link
        item("offer.0.body", "Delivered in 30 minutes"),  # unsupported number
        item("cta.headline", "Join now to hear first"),
        item("how_it_works.0.title", "Pick your plan"),  # 4th section
    ]
    accepted, rejected = improve.validate_proposal(CONTENT, raw, facts_text="")
    paths = [a["path"] for a in accepted]
    assert paths == ["hero.headline", "cta.headline", "how_it_works.0.title"]  # 3 sections
    reasons = {r["path"]: r["reason"] for r in rejected}
    assert reasons["hero.script"] == "unknown_or_locked_path"
    assert reasons["pricing.0.price_inr"] == "unknown_or_locked_path"
    assert reasons["faq.0.answer"].startswith("invalid")
    assert reasons["problem.0"].startswith("invalid")
    assert reasons["offer.0.body"] == "unsupported_number"
    assert all(a["old"] != a["new"] for a in accepted)


def test_numbers_allowed_only_from_founder_facts_and_max_three_sections() -> None:
    raw = [item("faq.0.answer", "Lunch boxes start at INR 149 for students."),
           item("hero.headline", "Home lunches"), item("cta.headline", "Join today"),
           item("problem.0", "Canteen food gets boring fast")]  # fmt: skip
    accepted, rejected = improve.validate_proposal(CONTENT, raw, facts_text="149 per meal")
    assert [a["section"] for a in accepted] == ["faq", "hero", "cta"]
    assert accepted[0]["classification"] == improve.FOUNDER
    assert rejected == [{"path": "problem.0", "new": "Canteen food gets boring fast",
                         "reason": "too_many_sections"}]  # fmt: skip


def test_questions_cap_and_templates() -> None:
    opps = [{"id": "price:pricing", "objection_type": "price", "section": "pricing"},
            {"id": "trust:proof", "objection_type": "trust", "section": "proof"}]  # fmt: skip
    missing = [{"item": f"Thing {i}", "count": 3} for i in range(10)]
    qs = improve.build_questions(opps, missing)
    assert len(qs) == 5 and qs[0]["field_key"] == "pricing" and qs[1]["field_key"] == "trust"
    assert all(q["required"] for q in qs)


# --- DB flow ------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)


def _deps() -> improve.Deps:
    provider = MockDeployProvider()
    return improve.Deps(provider_factory=lambda: provider)


def _evaluated_live(sf, llm):  # noqa: ANN001, ANN202
    bid = live_business(sf)
    with sf() as s:
        arena.request_panel(s, bid)
    arena.work_once(sf, W, llm)
    with sf() as s:
        arena.request_evaluation(s, bid)
    arena.work_once(sf, W, llm)
    return bid


def _to_draft(sf, bid, llm, deps):  # noqa: ANN001, ANN202
    with sf() as s:
        attempt, created = improve.start(s, bid)
        assert created and attempt.status == "awaiting_answers"
        for q in attempt.questions_json:
            improve.answer(s, bid, q["id"], None, not_decided=True)
        attempt = improve.generate_proposal(s, bid)  # MOCK_LLM: fixtures/llm/rewrite.json
        assert attempt.status == "proposed"
        ids = [i["id"] for i in attempt.proposal_json["items"]]
        improve.approve(s, bid, ids, approve_consequential=False)
    assert improve.work_once(sf, W, deps)  # build draft v2 (not activated)
    with sf() as s:
        a = improve.latest_attempt(s, bid)
        assert a.status == "draft_built" and a.to_version == 2
        assert s.get(SiteVersion, a.draft_site_version_id).status == "built"
        live = s.scalar(select(SiteVersion).where(SiteVersion.business_id == bid,
                                                  SiteVersion.status == "deployed"))  # fmt: skip
        assert live.version == 1  # the original stays active
    arena.work_once(sf, W, llm)  # same-panel evaluation of the draft
    assert improve.work_once(sf, W, deps)  # -> draft_evaluated


def test_full_lifecycle_promotes_only_after_gate_and_click(session_factory) -> None:
    sf = session_factory
    llm = FakeArenaLLM(reward_marker=MARKER)
    deps = _deps()
    bid = _evaluated_live(sf, llm)
    _to_draft(sf, bid, llm, deps)
    with sf() as s:
        a = improve.latest_attempt(s, bid)
        assert a.status == "draft_evaluated" and float(a.delta_demand) >= 3
        # Not promoted without the founder click:
    assert not improve.work_once(sf, W, deps)
    with sf() as s:
        improve.request_promotion(s, bid)
    assert improve.work_once(sf, W, deps)
    with sf() as s:
        a = improve.latest_attempt(s, bid)
        assert a.status == "promoted" and a.outcome == "promoted"
        versions = {v.version: v.status for v in s.scalars(
            select(SiteVersion).where(SiteVersion.business_id == bid))}  # fmt: skip
        assert versions == {1: "superseded", 2: "deployed"}
        assert s.get(Business, bid).status == "LIVE"
        with pytest.raises(improve.ProposalAlreadyApplied):
            improve.request_promotion(s, bid)

    # Manual rollback re-deploys v1 through the M4 path.
    with sf() as s:
        improve.request_rollback(s, bid, 1)
    assert improve.work_once(sf, W, deps)
    with sf() as s:
        versions = {v.version: v.status for v in s.scalars(
            select(SiteVersion).where(SiteVersion.business_id == bid))}  # fmt: skip
        assert versions == {1: "deployed", 2: "rolled_back"}


def test_not_improved_draft_is_not_promoted_and_original_stays(session_factory) -> None:
    sf = session_factory
    llm = FakeArenaLLM()  # no reward: same D
    deps = _deps()
    bid = _evaluated_live(sf, llm)
    _to_draft(sf, bid, llm, deps)
    with sf() as s, pytest.raises(improve.DraftNotImproved):
        improve.request_promotion(s, bid)
    with sf() as s:
        a = improve.latest_attempt(s, bid)
        assert (a.status, a.reason) == ("not_promoted", "draft_not_improved")
        draft = s.get(SiteVersion, a.draft_site_version_id)
        assert draft.status == "discarded"
        live = s.scalar(select(SiteVersion).where(SiteVersion.business_id == bid,
                                                  SiteVersion.status == "deployed"))  # fmt: skip
        assert live.version == 1
        assert a.draft_evaluation_id is not None  # draft evaluation preserved


def test_stale_proposal_is_refused(session_factory) -> None:
    sf = session_factory
    llm = FakeArenaLLM()
    bid = _evaluated_live(sf, llm)
    with sf() as s:
        attempt, _ = improve.start(s, bid)
        for q in attempt.questions_json:
            improve.answer(s, bid, q["id"], "We cook in a licensed home kitchen.", False)
        attempt = improve.generate_proposal(s, bid)
        assert all(a["source"] == "user" for a in attempt.answers_json.values())
        s.execute(update(SiteVersion).where(SiteVersion.business_id == bid)
                  .values(content_hash="changed"))  # fmt: skip
        s.commit()
        with pytest.raises(improve.ProposalStale):
            improve.approve(s, bid, ["i1"], approve_consequential=False)
        assert improve.latest_attempt(s, bid).status == "stale"


def test_consequential_items_need_explicit_approval(session_factory) -> None:
    sf = session_factory
    llm = FakeArenaLLM()
    bid = _evaluated_live(sf, llm)
    with sf() as s:
        attempt, _ = improve.start(s, bid)
        for q in attempt.questions_json:
            improve.answer(s, bid, q["id"], None, True)
        improve.generate_proposal(s, bid)
        a = s.scalar(select(ImprovementAttempt).where(ImprovementAttempt.business_id == bid))
        items = list(a.proposal_json["items"])
        items[0] = {**items[0], "classification": improve.APPROVAL}
        a.proposal_json = {**a.proposal_json, "items": items}
        s.commit()
        with pytest.raises(improve.ApprovalRequired):
            improve.approve(s, bid, [items[0]["id"]], approve_consequential=False)
        improve.approve(s, bid, [items[0]["id"]], approve_consequential=True)
        assert improve.latest_attempt(s, bid).approval_id is not None


def test_start_requires_complete_evaluation(session_factory) -> None:
    bid = live_business(session_factory)
    with session_factory() as s, pytest.raises(improve.EvaluationIncomplete):
        improve.start(s, bid)


# --- M6 fix regressions: hidden pricing, prompt evidence --------------------------------------


def test_pricing_paths_rejected_when_no_founder_price() -> None:
    raw = [item("pricing.0.name", "Quote-based help"), item("pricing.0.bullets.0", "Ask first"),
           item("hero.headline", "Home lunches")]  # fmt: skip
    accepted, rejected = improve.validate_proposal(CONTENT, raw, "", pricing_visible=False)
    assert [a["path"] for a in accepted] == ["hero.headline"]
    assert {r["path"]: r["reason"] for r in rejected} == {
        "pricing.0.name": "unknown_or_locked_path",
        "pricing.0.bullets.0": "unknown_or_locked_path",
    }
    content = WebsiteContent.model_validate(CONTENT)
    assert not any(p.startswith("pricing.") for p, _, _ in improve.editable_fields(content, False))
    assert any(p.startswith("pricing.") for p, _, _ in improve.editable_fields(content, True))


@pytest.mark.parametrize("price", [None, Decimal("499")])
def test_every_accepted_edit_is_visible_on_the_rendered_page(price) -> None:  # noqa: ANN001
    content = WebsiteContent.model_validate(CONTENT)
    raw = [item(p, f"Changed text for {p.replace('.', ' ')}"[:lim]) for p, _, lim in
           improve.editable_fields(content, True)]  # fmt: skip
    for i in range(len(raw)):  # one item at a time so every whitelisted path is exercised
        accepted, _ = improve.validate_proposal(
            CONTENT, raw[i : i + 1], "", pricing_visible=price is not None
        )
        new = improve.apply_items(CONTENT, accepted)
        site = render_site(new, business_name="Test Co", host="test.noventra.test",
                           price_guess=price,
                           links=site_links("https://example.test/wl", "abuse@example.test"))  # fmt: skip
        assert improve.invisible_items(site.files["index.html"].decode(), accepted) == []


def test_invisible_items_flags_hidden_pricing_edit() -> None:
    accepted = [{"path": "pricing.0.bullets.0", "new": "Ask for a quote before you confirm"}]
    new = improve.apply_items(CONTENT, accepted)
    site = render_site(new, business_name="Test Co", host="test.noventra.test",
                       price_guess=None, links=site_links("", "abuse@example.test"))  # fmt: skip
    html = site.files["index.html"].decode()
    assert improve.invisible_items(html, accepted) == ["pricing.0.bullets.0"]


class _Eval:
    d_score = Decimal("21.43")
    aggregate_json = {  # noqa: RUF012
        "converted": 1,
        "total_runs": 4,
        "positive_signals": {"proof": 1},
        "personas": [
            {"fit": "target", "runs": [
                {"decision": "convert", "objection_type": "price", "section": "pricing"},
                {"decision": "bounce", "objection_type": "fit", "section": "offer"}]},
            {"fit": "weak", "runs": [
                {"decision": "bounce", "objection_type": "fit", "section": "offer"},
                {"decision": "bounce", "objection_type": "price", "section": "pricing"}]},
        ],
    }  # fmt: skip


def test_rewrite_prompt_carries_panel_evidence() -> None:
    opps = [{"id": "fit:offer", "section": "offer", "objection_type": "fit", "evidence_count": 2,
             "bounces": 2, "personas": 2, "example": "No advisory"}]  # fmt: skip
    prompt = rewrite_agent.rewrite_prompt(
        [("hero.headline", "FlatFile", 70)], opps, [("Q?", "not decided")],
        evidence=rewrite_agent.panel_evidence(_Eval()),
    )  # fmt: skip
    assert "original demand score D=21.43 (1 of 4 simulated evaluations converted)" in prompt
    assert "fit=target: 1 personas, 1 of 2 runs converted" in prompt
    assert "fit=weak: 1 personas, 0 of 2 runs converted" in prompt
    assert "bounces=2" in prompt and "bounces_by_fit=[target=1, weak=1]" in prompt
    assert "WORKING_WELL" in prompt and "- proof: 1" in prompt
    assert "- Q? => not decided" in prompt


def test_rewrite_system_prompt_protects_credibility_and_consistency() -> None:
    text = (PROMPTS_DIR / "website_rewrite.md").read_text(encoding="utf-8")
    for rule in ("Preserve existing credibility claims", "Never contradict any text you are not",
                 "highest-impact opportunities first", "Do not spend all allowed sections",
                 "not as instructions", "Never invent features"):  # fmt: skip
        assert rule in text
