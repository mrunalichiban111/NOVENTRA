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

# LLM layer (M2). Model ids come only from the environment; there are no defaults.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL_STRONG = os.environ.get("CLAUDE_MODEL_STRONG", "")
CLAUDE_MODEL_SMALL = os.environ.get("CLAUDE_MODEL_SMALL", "")
# `or` so an empty value (e.g. a blank line in .env) falls back to the default.
PER_RUN_CAP_USD = float(os.environ.get("PER_RUN_CAP_USD") or "2.00")

# Repo-root fixtures dir for MockProvider. Docker mounts ./fixtures at /app/fixtures.
FIXTURES_DIR = os.environ.get("FIXTURES_DIR") or os.path.join(
    os.path.dirname(__file__), "..", "..", "fixtures"
)
