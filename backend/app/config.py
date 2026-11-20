import os

APP_ENV = os.environ.get("APP_ENV", "development")
MOCK_LLM = os.environ.get("MOCK_LLM", "").lower() in {"1", "true"}
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://noventra:noventra@localhost:5432/noventra",
)
SECRET_KEY = os.environ.get("SECRET_KEY", "")

# Pipeline worker (M1). Retry budget counts total failures per business.
WORKER_POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "2"))
WORKER_CLAIM_TTL_SECONDS = int(os.environ.get("WORKER_CLAIM_TTL_SECONDS", "60"))
MAX_STAGE_ATTEMPTS = int(os.environ.get("MAX_STAGE_ATTEMPTS", "3"))

# Public write endpoints: max requests per client IP per minute.
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "20"))
TRUSTED_PROXY_IPS = tuple(
    value.strip()
    for value in (os.environ.get("TRUSTED_PROXY_IPS") or "").split(",")
    if value.strip()
)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true"}

# LLM layer (M2). Model ids come only from the environment; there are no defaults.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL_STRONG = os.environ.get("CLAUDE_MODEL_STRONG", "")
CLAUDE_MODEL_SMALL = os.environ.get("CLAUDE_MODEL_SMALL", "")
# `or` so an empty value (e.g. a blank line in .env) falls back to the default.
PER_RUN_CAP_USD = float(os.environ.get("PER_RUN_CAP_USD") or "2.00")
# Rolling 24h estimated spend across ALL runs, enforced in LLMGuard.before under an advisory
# lock. Configurable; the $5.00 default is pending product-owner approval for production.
DEFAULT_DAILY_CAP_USD = "5.00"
DAILY_SPEND_CAP_USD = float(os.environ.get("DAILY_SPEND_CAP_USD") or DEFAULT_DAILY_CAP_USD)

# Contact delivery. Missing or contradictory settings fail as a delivery error, not at startup.
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
try:
    SMTP_PORT = int(os.environ.get("SMTP_PORT") or "587")
except ValueError:
    SMTP_PORT = 0
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_USE_SSL = _flag("SMTP_USE_SSL")
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "").strip().lower() in {"1", "true"}
CONTACT_TO = os.environ.get("CONTACT_TO", "").strip()
CONTACT_FROM = os.environ.get("CONTACT_FROM", "").strip()

# Apex domain that generated sites are served under as <slug>.<BASE_DOMAIN> (FR-BR-5).
# Empty means subdomains cannot be claimed yet (name selection is refused, M4A).
BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "").strip().lower()

# Real-domain availability (FR-BR-2, read-only). The PAT is a secret: never log or return it.
GODADDY_PAT = os.environ.get("GODADDY_PAT", "")
GODADDY_TLDS = tuple(
    "." + t.strip().lstrip(".").lower()
    for t in (os.environ.get("GODADDY_TLDS") or "in,com,co").split(",")
    if t.strip().lstrip(".")
)
# Deterministic mock provider; defaults to on whenever MOCK_LLM is on.
_mock_domains = os.environ.get("DOMAIN_CHECK_MOCK", "").strip().lower()
DOMAIN_CHECK_MOCK = _mock_domains in {"1", "true"} if _mock_domains else MOCK_LLM


# Site generation and deployment (M4B: FR-SITE-1..6). Empty DEPLOY_PROVIDER means deployment
# is off: the deploy endpoint refuses and the worker never publishes. Values: mock | local.
DEPLOY_PROVIDER = os.environ.get("DEPLOY_PROVIDER", "").strip().lower()
# Root of <host>/releases/vN and the `current` symlink. Only the worker may write here.
SITES_ROOT = os.environ.get("SITES_ROOT") or os.path.join(
    os.path.dirname(__file__), "..", "..", "sites"
)
# DNS handling is read-only: skip | mock | preconfigured (resolve-only poll). Never writes records.
SITE_DNS_MODE = (os.environ.get("SITE_DNS_MODE") or "skip").strip().lower()
# Fetch https://<host>/ and compare the served body hash with the release manifest.
SITE_HTTP_CHECK = _flag("SITE_HTTP_CHECK")
SITE_HEALTH_TIMEOUT_S = float(os.environ.get("SITE_HEALTH_TIMEOUT_S") or "60")
# LIVE requires public verification (DNS + HTTPS 200 + hash). Always on in production.
SITE_REQUIRE_PUBLIC = APP_ENV == "production" or _flag("SITE_REQUIRE_PUBLIC")
# Operator-controlled links rendered into every site (never model output). Empty = omitted.
WAITLIST_POST_URL = os.environ.get("WAITLIST_POST_URL", "").strip()
ABUSE_REPORT_EMAIL = os.environ.get("ABUSE_REPORT_EMAIL", "").strip()
# Bearer token for the internal Caddy on-demand TLS `ask` endpoint. A secret: never log it.
# Empty disables the endpoint.
INTERNAL_TLS_ASK_TOKEN = os.environ.get("INTERNAL_TLS_ASK_TOKEN", "")

# Repo-root fixtures dir for MockProvider. Docker mounts ./fixtures at /app/fixtures.
FIXTURES_DIR = os.environ.get("FIXTURES_DIR") or os.path.join(
    os.path.dirname(__file__), "..", "..", "fixtures"
)
