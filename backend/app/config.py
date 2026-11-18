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
