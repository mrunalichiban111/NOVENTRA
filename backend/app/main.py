from fastapi import FastAPI

from app.api.businesses import router as businesses_router

app = FastAPI(title="NOVENTRA API")
app.include_router(businesses_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
