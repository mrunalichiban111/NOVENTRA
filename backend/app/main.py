from fastapi import FastAPI

app = FastAPI(title="NOVENTRA API")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
