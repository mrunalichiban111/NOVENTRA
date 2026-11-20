"""Name, slug and ranking configuration (FR-BR-1, FR-BR-3, FR-BR-5). See docs/names.md.

Every weight, threshold and reserved label lives here so the ranking stays explainable and
deterministic. Changing a value here changes rankings; record why in docs/names.md.
"""

# --- Slug rules ------------------------------------------------------------------------

SLUG_MIN_LEN = 3
SLUG_MAX_LEN = 30
PUNYCODE_PREFIX = "xn--"
# Characters dropped (not replaced) when building a slug: apostrophes inside words.
DROPPED_CHARS = frozenset("'’‘")

# Stable machine-readable reason codes, in the order they are checked.
UNSUPPORTED_CHAR = "unsupported_char"
PUNYCODE = "punycode"
EDGE_HYPHEN = "edge_hyphen"
DOUBLE_HYPHEN = "double_hyphen"
TOO_SHORT = "too_short"
TOO_LONG = "too_long"
RESERVED = "reserved"
APEX_LABEL = "apex_label"
DUPLICATE_SLUG = "duplicate_slug"  # assigned across a candidate list, not per slug
LEGACY_UNVALIDATED = "legacy_unvalidated"  # set by migration 0004 on pre-M4 rows only

REASON_CODES = (
    UNSUPPORTED_CHAR, PUNYCODE, EDGE_HYPHEN, DOUBLE_HYPHEN, TOO_SHORT, TOO_LONG,
    RESERVED, APEX_LABEL, DUPLICATE_SLUG, LEGACY_UNVALIDATED,
)  # fmt: skip

# Infrastructure, mail, auth and brand labels that can never be claimed as a subdomain.
RESERVED_SLUGS: frozenset[str] = frozenset(
    """
    www www1 www2 api app apps mail mail1 mail2 email webmail admin administrator ftp sftp
    smtp imap pop pop3 mx ns ns1 ns2 ns3 ns4 dns dev staging stage test testing qa uat prod
    production preview demo sandbox beta alpha static cdn assets img images media files
    upload uploads download downloads status support help abuse postmaster hostmaster
    webmaster root noventra noventras login logout signin signup register auth oauth sso
    account accounts billing dashboard console panel cpanel whm portal internal intranet
    local localhost gateway proxy vpn git ci db mysql postgres redis metrics grafana monitor
    health security privacy legal terms about contact info noreply autodiscover autoconfig
    ws wss socket graphql rest docs blog caddy worker site sites b
    """.split()
)

# --- Ranking (scored on the slug only; the model's rationale is never scored) ----------

# Points per factor; they sum to 100 so score_total reads as a percentage.
WEIGHTS: dict[str, int] = {
    "length": 25,
    "pronounceability": 25,
    "no_digits": 10,
    "no_hyphens": 15,
    "typeability": 15,
    "keyword_overlap": 10,
}

LENGTH_SWEET_MIN = 5
LENGTH_SWEET_MAX = 10
LENGTH_SHORT_PENALTY = 0.25  # per character below the sweet spot
LENGTH_LONG_SPAN = 10  # factor reaches 0 this many characters above the sweet spot

VOWELS = frozenset("aeiouy")  # y counts as a vowel for pronounceability
VOWEL_RATIO_MIN = 0.30
VOWEL_RATIO_MAX = 0.60
VOWEL_RATIO_SLOPE = 2.0  # factor lost per unit of ratio outside the band
CONSONANT_RUN = 3  # runs of this many consonants or more are penalised
REPEAT_RUN = 3  # the same letter this many times in a row is penalised
RUN_PENALTY = 0.25  # per letter beyond the allowed run length

AMBIGUOUS_CHARS = frozenset("01")  # read as o / l / i
AMBIGUOUS_SEQUENCES = ("rn", "vv", "cl", "ii")  # look like m / w / d, or are mistyped
AMBIGUITY_PENALTY = 0.34  # per occurrence

KEYWORD_MIN_LEN = 4
KEYWORD_STEM_LEN = 4  # a keyword's first letters count for half credit
KEYWORD_FULL = 1.0
KEYWORD_STEM = 0.5
STOPWORDS: frozenset[str] = frozenset(
    """
    also each from have help into just like make more most only over such than that their
    them then they this very what when which while with your yours will would about after
    before through simple small large every other these those where there being been does
    india indian online people customers customer business businesses service services
    """.split()
)

FACTOR_PRECISION = 4
TOTAL_PRECISION = 2
