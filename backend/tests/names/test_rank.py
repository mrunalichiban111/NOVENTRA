"""Pure tests: deterministic ranking, tie-breaks and the candidate pipeline (FR-BR-3)."""

import random

import pytest
from pydantic import ValidationError

from app.llm.schemas import BusinessNames
from app.names import config as c
from app.names.normalize import RawCandidate, normalize_candidates
from app.names.rank import (
    build_candidates,
    idea_keywords,
    length_factor,
    pronounceability_factor,
    score_slug,
    typeability_factor,
)

KW = frozenset({"filing", "freelancers"})


def raw(*names: str, rationale: str = "r") -> list[RawCandidate]:
    return [RawCandidate(n, rationale) for n in names]


def test_weights_sum_to_100() -> None:
    assert sum(c.WEIGHTS.values()) == 100


def test_length_sweet_spot() -> None:
    assert length_factor("abcde") == length_factor("abcdefghij") == 1.0
    assert length_factor("abc") == 0.5 and length_factor("abcd") == 0.75
    assert length_factor("a" * 15) == 0.5 and length_factor("a" * 25) == 0.0


def test_pronounceability_penalises_consonant_and_repeat_runs() -> None:
    assert pronounceability_factor("routely") == 1.0
    assert pronounceability_factor("strngth") < pronounceability_factor("strength")
    assert pronounceability_factor("baaad") < pronounceability_factor("baad")


def test_typeability_penalises_ambiguous_characters() -> None:
    assert typeability_factor("taxpal") == 1.0
    assert typeability_factor("tax0") < 1.0 and typeability_factor("barn") < 1.0


def test_digits_and_hyphens_lower_the_score() -> None:
    clean, _ = score_slug("taxgo", frozenset())
    assert score_slug("tax2go", frozenset())[0] < clean
    assert score_slug("tax-go", frozenset())[0] < clean


def test_keyword_overlap_full_and_stem() -> None:
    assert score_slug("filingo", KW)[1]["keyword_overlap"]["value"] == 1.0
    assert score_slug("freeto", KW)[1]["keyword_overlap"]["value"] == 0.5
    assert score_slug("taxpal", KW)[1]["keyword_overlap"]["value"] == 0.0
    assert "with" not in idea_keywords("help with GST filing") and "filing" in idea_keywords(
        "GST filing"
    )


def test_factor_breakdown_adds_up_to_total() -> None:
    total, factors = score_slug("ledgerly", KW)
    assert set(factors) == set(c.WEIGHTS)
    assert round(sum(f["points"] for f in factors.values()), 2) == total


def test_rationale_never_affects_the_score() -> None:
    a = build_candidates(raw("Taxpal", rationale="short"), keywords=KW, apex=None)[0]
    b = build_candidates(
        raw("Taxpal", rationale="filing freelancers " * 4), keywords=KW, apex=None
    )[0]
    assert (a.score_total, a.score_factors) == (b.score_total, b.score_factors)


def test_ranking_is_deterministic_regardless_of_input_order() -> None:
    names = ["Taxnest", "Filewise", "Ledgerly", "Taxpal", "Sumwise", "Tax2Go", "Go - File", "Api"]
    expected = [(r.slug, r.rank) for r in build_candidates(raw(*names), keywords=KW, apex=None)]
    for seed in range(5):
        shuffled = names[:]
        random.Random(seed).shuffle(shuffled)
        got = build_candidates(raw(*shuffled), keywords=KW, apex=None)
        assert sorted((r.slug, r.rank) for r in got) == sorted(expected)


def test_ties_break_alphabetically_by_slug() -> None:
    # same length/shape, no keywords: identical scores
    out = build_candidates(raw("Zorapa", "Bolapa", "Molapa"), keywords=frozenset(), apex=None)
    assert len({r.score_total for r in out}) == 1
    assert [r.slug for r in out] == ["bolapa", "molapa", "zorapa"]
    assert [r.rank for r in out] == [1, 2, 3]


def test_invalid_candidates_are_kept_unranked_with_reasons() -> None:
    out = build_candidates(
        raw("Taxnest", "Taxnëst", "Api", "--x", "Launchpad"), keywords=KW, apex="launchpad"
    )
    by_name = {r.name: r for r in out}
    assert by_name["Taxnest"].valid and by_name["Taxnest"].rank == 1
    assert by_name["Taxnëst"].invalid_reason == c.DUPLICATE_SLUG
    assert by_name["Api"].invalid_reason == c.RESERVED
    assert by_name["--x"].invalid_reason == c.EDGE_HYPHEN and by_name["--x"].slug == "--x"
    assert by_name["Launchpad"].invalid_reason == c.APEX_LABEL
    assert all(r.rank is None and r.score_total is None for r in out if not r.valid)
    assert out[0].valid and not any(r.valid for r in out[1:])


def test_duplicate_slug_only_follows_a_valid_earlier_slug() -> None:
    out = build_candidates(raw("Api", "API "), keywords=KW, apex=None)
    assert [r.invalid_reason for r in out] == [c.RESERVED, c.RESERVED]


def test_names_schema_validation() -> None:
    ok = {"candidates": [{"name": f"Name{i:02d}x", "rationale": "r"} for i in range(40)]}
    BusinessNames.model_validate(ok)
    with pytest.raises(ValidationError):
        BusinessNames.model_validate({"candidates": ok["candidates"][:39]})
    with pytest.raises(ValidationError):
        BusinessNames.model_validate({"candidates": [{"name": "x" * 31, "rationale": "r"}] * 40})
    with pytest.raises(ValidationError):  # extra fields are refused
        BusinessNames.model_validate(
            {"candidates": [{"name": "Abcd", "rationale": "r", "score": 9}] * 40}
        )


def test_normalize_then_build_preserves_original_display_name() -> None:
    out = build_candidates(
        normalize_candidates([("  Café   Ledger ", "x")]), keywords=KW, apex=None
    )
    assert (out[0].name, out[0].slug, out[0].valid) == ("Café Ledger", "cafe-ledger", True)
