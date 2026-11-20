"""Site deployment routes (M4B: FR-SITE-3/4, FR-GT-2). Thin wrappers over app/deploy/service.py.

Public routes live under /api and use the unguessable business UUID as the capability, like the
other business routes. Errors use detail={"code", "message"}.

`GET /internal/sites/tls-allowed` is deliberately OUTSIDE /api: the public Caddy only forwards
/api/* to the API (everything else goes to the frontend, which only rewrites /api/*), so this
path is reachable only on the Docker network. It is the permission check for Caddy on-demand
TLS and is additionally protected by a bearer token (INTERNAL_TLS_ASK_TOKEN, constant-time
comparison) and by refusing any request that carries forwarding headers. See docs/deploy.md for
why Caddy needs a loopback hop to send that header.
"""

import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.api.ratelimit import rate_limit
from app.api.schemas import (
    DeploymentOut,
    DeploymentsList,
    DeployRequested,
    SiteOut,
    SiteVersionOut,
)
from app.database import get_session
from app.deploy import service
from app.deploy.hosts import HostError, validate_host
from app.deploy.providers import DeployConfigError, DeployNotConfigured
from app.models import Business, BusinessStatus, Deployment, DomainCandidate, SiteVersion
from app.pipeline.status import BusinessNotFound

SessionDep = Annotated[Session, Depends(get_session)]
router = APIRouter(prefix="/api/businesses", tags=["sites"])
internal = APIRouter(prefix="/internal/sites", tags=["internal"], include_in_schema=False)

# Headers a reverse proxy adds; their presence means the request did not come straight from
# the Docker-internal caller.
FORWARDED_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")
TLS_ASK_STATUSES = (BusinessStatus.DEPLOYING, BusinessStatus.LIVE)


def _err(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _business(session: Session, business_id: uuid.UUID) -> Business:
    business = session.get(Business, business_id)
    if business is None:
        raise _err(404, "not_found", f"Business {business_id} not found")
    return business


@router.post(
    "/{business_id}/deploy",
    response_model=DeployRequested,
    status_code=202,
    dependencies=[Depends(rate_limit)],
)
def request_deploy(
    business_id: uuid.UUID, session: SessionDep, response: Response
) -> DeployRequested:
    """DOMAIN_PENDING -> DEPLOYING; the worker generates, publishes and verifies the site."""
    try:
        outcome = service.request_deploy(session, business_id)
    except DeployNotConfigured:
        raise _err(503, "deploy_not_configured", "Site deployment is not configured yet.") from None
    except DeployConfigError:
        raise _err(
            503, "deploy_misconfigured", "Site deployment is misconfigured; see server logs."
        ) from None
    except BusinessNotFound:
        raise _err(404, "not_found", f"Business {business_id} not found") from None
    except service.NotEligible as exc:
        raise _err(409, "not_eligible", str(exc)) from None
    except service.DeployInputError as exc:
        raise _err(409, "no_claim", str(exc)) from None
    if not outcome.changed:
        response.status_code = 200
    b = outcome.business
    return DeployRequested(
        business_id=b.id, status=b.status, host=b.domain, changed=outcome.changed
    )


def _checks(dep: Deployment) -> list[dict]:
    return [c for c in (dep.health_json or {}).get("checks", []) if isinstance(c, dict)]


@router.get("/{business_id}/site", response_model=SiteOut)
def get_site(business_id: uuid.UUID, session: SessionDep) -> SiteOut:
    b = _business(session, business_id)
    rows = list(
        session.scalars(
            select(SiteVersion)
            .where(SiteVersion.business_id == business_id)
            .order_by(SiteVersion.version)
        )
    )
    live = next((r for r in reversed(rows) if r.status == "deployed"), None)
    level = None
    if live is not None:
        dep = session.scalar(
            select(Deployment)
            .where(Deployment.site_version_id == live.id, Deployment.status == "verified")
            .order_by(Deployment.attempt.desc())
            .limit(1)
        )
        level = dep.verification_level if dep else None
    return SiteOut(
        business_id=b.id,
        status=b.status,
        host=b.domain,
        url=f"https://{b.domain}/" if b.domain and live is not None else None,
        live_version=live.version if live else None,
        verification_level=level,
        publicly_reachable=level == "public",
        versions=[
            SiteVersionOut(
                version=r.version,
                status=r.status,
                content_hash=r.content_hash,
                artifact_hash=r.artifact_hash,
                parent_version=r.parent_version,
                generation_attempt=r.generation_attempt,
                error=r.error,
                deployed_at=r.deployed_at,
                created_at=r.created_at,
            )
            for r in rows
        ],
    )


@router.get("/{business_id}/deployments", response_model=DeploymentsList)
def list_deployments(business_id: uuid.UUID, session: SessionDep) -> DeploymentsList:
    _business(session, business_id)
    rows = session.execute(
        select(Deployment, SiteVersion.version)
        .join(SiteVersion, SiteVersion.id == Deployment.site_version_id)
        .where(Deployment.business_id == business_id)
        .order_by(Deployment.started_at, Deployment.attempt)
    ).all()
    return DeploymentsList(
        business_id=business_id,
        deployments=[
            DeploymentOut(
                id=d.id,
                version=version,
                attempt=d.attempt,
                host=d.host,
                provider=d.provider,
                status=d.status,
                verification_level=d.verification_level,
                publicly_reachable=d.verification_level == "public",
                error=d.error,
                checks=_checks(d),
                started_at=d.started_at,
                finished_at=d.finished_at,
            )
            for d, version in rows
        ],
    )


# --- internal: Caddy on-demand TLS permission ------------------------------------------------


def _bearer_ok(request: Request, token: str) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), token.encode("utf-8"))


@internal.get("/tls-allowed")
def tls_allowed(request: Request, session: SessionDep) -> Response:
    """200 only for an exact, claimed host whose business is DEPLOYING or LIVE. No body."""
    token = config.INTERNAL_TLS_ASK_TOKEN
    if not token:
        return Response(status_code=404)  # endpoint disabled
    if any(h in request.headers for h in FORWARDED_HEADERS):
        return Response(status_code=403)
    if not _bearer_ok(request, token):
        return Response(status_code=401)
    domains = request.query_params.getlist("domain")
    if len(domains) != 1:
        return Response(status_code=400)
    try:
        host = validate_host(domains[0])
    except HostError:
        return Response(status_code=403)
    label = host.split(".", 1)[0]
    allowed = session.scalar(
        select(Business.id)
        .join(DomainCandidate, DomainCandidate.business_id == Business.id)
        .where(
            Business.domain == host,
            Business.status.in_([s.value for s in TLS_ASK_STATUSES]),
            DomainCandidate.selected.is_(True),
            DomainCandidate.slug == label,
        )
        .limit(1)
    )
    return Response(status_code=200 if allowed is not None else 403)
