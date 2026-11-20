"""Pure tests: name normalization, slug derivation and every reason code (FR-BR-1, FR-BR-5)."""

import pytest

from app.names import config as c
from app.names.normalize import RawCandidate, clean_text, normalize_candidates
from app.names.slug import apex_label, derive_slug, slugify, validate_slug


def test_clean_text_nfkc_trim_and_collapse() -> None:
    assert clean_text("  Ledger \t\n  Lane  ") == "Ledger Lane"
    assert clean_text("Ｔａｘ") == "Tax"  # full-width letters fold under NFKC
    assert clean_text("   ") == ""


def test_normalize_drops_empty_and_case_insensitive_duplicates_keeping_first() -> None:
    out = normalize_candidates(
        [
            ("Filewise", "a"),
            ("  ", "empty"),
            ("FILEWISE", "dup"),
            ("filewise ", "dup2"),
            ("Taxpal", " b "),
        ]
    )
    assert out == [RawCandidate("Filewise", "a"), RawCandidate("Taxpal", "b")]


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Café Ledger", "cafe-ledger"),
        ("Taxnëst", "taxnest"),
        ("Ledger Lane", "ledger-lane"),
        ("Hisaab's", "hisaabs"),
        ("TAX2GO", "tax2go"),
        ("--x", "--x"),  # never repaired
        ("Go - File", "go---file"),
    ],
)
def test_derive_slug_is_deterministic_and_never_repairs_structure(name: str, slug: str) -> None:
    assert derive_slug(name) == slug


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("Tax & File", c.UNSUPPORTED_CHAR),
        ("कर", c.UNSUPPORTED_CHAR),  # non-Latin is not transliterated: no IDN
        ("Straße", c.UNSUPPORTED_CHAR),
        ("tax.file", c.UNSUPPORTED_CHAR),
        ("xn--taxfile", c.PUNYCODE),
        ("XN--abc", c.PUNYCODE),
        ("--x", c.EDGE_HYPHEN),
        ("taxly-", c.EDGE_HYPHEN),
        ("tax--file", c.DOUBLE_HYPHEN),
        ("Go - File", c.DOUBLE_HYPHEN),
        ("Tx", c.TOO_SHORT),
        ("'''", c.TOO_SHORT),
        ("a" * 31, c.TOO_LONG),
        ("Api", c.RESERVED),
        ("NOVENTRA", c.RESERVED),
        ("b", c.TOO_SHORT),  # reserved too, but length is checked first
    ],
)
def test_reason_codes(name: str, reason: str) -> None:
    assert slugify(name).reason == reason


def test_boundaries_and_valid_slugs() -> None:
    assert slugify("abc").valid and slugify("a" * 30).valid
    assert slugify("tax-file").valid and slugify("tax2go").valid


def test_all_required_reserved_names_present() -> None:
    required = (
        "www api app mail admin ftp smtp imap pop ns1 ns2 dev staging test static cdn assets "
        "status support help abuse postmaster hostmaster webmaster root noventra noventras "
        "login dashboard b"
    ).split()
    assert set(required) <= c.RESERVED_SLUGS
    assert all(s == s.lower() for s in c.RESERVED_SLUGS)


def test_apex_label_rejection() -> None:
    assert apex_label("LaunchPad.in.") == "launchpad"
    assert apex_label("") is None and apex_label(None) is None
    assert slugify("Launchpad", apex="launchpad").reason == c.APEX_LABEL
    assert slugify("Launchpad", apex=None).valid
    assert validate_slug("noventra", apex="noventra") == c.RESERVED  # reserved wins


def test_every_reason_code_is_documented() -> None:
    assert len(set(c.REASON_CODES)) == len(c.REASON_CODES) == 10
