import os

APP_ENV = os.environ.get("APP_ENV", "development")
MOCK_LLM = os.environ.get("MOCK_LLM", "").lower() in {"1", "true"}
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://noventra:noventra@localhost:5432/noventra",
)
SECRET_KEY = os.environ.get("SECRET_KEY", "")
