from fastapi import FastAPI

from app.api.businesses import router as businesses_router
from app.api.contact import router as contact_router
from app.api.names import router as names_router
from app.api.sites import internal as internal_sites_router
from app.api.sites import router as sites_router

app = FastAPI(title="NOVENTRA API")
app.include_router(businesses_router)
app.include_router(names_router)
app.include_router(sites_router)
app.include_router(internal_sites_router)  # outside /api on purpose (docs/deploy.md)
app.include_router(contact_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
