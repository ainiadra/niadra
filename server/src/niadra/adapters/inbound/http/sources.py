"""Source routes of the cell's governance API (7.1), served by niadra-admin: the instant local cut of a
source (4.7), its coverage (5.2) and whether any event arrived."""

import hashlib
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from niadra.adapters.inbound.http.auth import require_governance, require_source
from niadra.adapters.inbound.http.deps import service
from niadra.contracts.edge import KeyIdentity, SourceRevocationOut
from niadra.contracts.ingest import CoverageOut, EventsArrived
from niadra.domain.common.space import Principal, SourcePrincipal

router = APIRouter(prefix="/v1/sources", tags=["sources"])

Security = Annotated[Principal, Depends(require_governance("security"))]
Analyst = Annotated[Principal, Depends(require_governance("integration", "analysis"))]
AnyRole = Annotated[Principal, Depends(require_governance())]
Ingest = Annotated[Any, Depends(service("ingest"))]
AnyKey = Annotated[SourcePrincipal, Depends(require_source())]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]


@router.get("/me", response_model=KeyIdentity)
async def whoami(caller: AnyKey, request: Request) -> KeyIdentity:
    """Any key, whatever its scopes: the space, the source and what the key may do. No customer data."""
    config = request.app.state.container.config.space(caller.space.space_id)
    source = config.source(caller.source_id)
    return KeyIdentity(
        space_id=caller.space.space_id,
        tenant_id=caller.space.tenant_id,
        region=caller.space.region,
        environment=caller.space.environment,
        source_id=caller.source_id,
        source_name=source.name if source else None,
        vendor=caller.vendor,
        key_id=caller.key_id,
        scopes=sorted(s.value for s in caller.scopes),
        audience=caller.audience,
        verification_ceiling=caller.verification_ceiling,
        purposes=sorted(caller.purposes),
        agent_memory=config.agent_memory.enabled,
    )


@router.get("/coverage", response_model=CoverageOut)
async def coverage(
    caller: Analyst, ingest: Ingest, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> CoverageOut:
    result: CoverageOut = await ingest.coverage(caller.space.space_id, days)
    return result


@router.get("/arrived", response_model=EventsArrived)
async def events_arrived(
    caller: AnyRole, ingest: Ingest, days: Annotated[int, Query(ge=1, le=90)] = 90
) -> EventsArrived:
    """Any Console role: whether the space received an event in the window, and nothing else of the
    coverage, which stays with the roles that read it."""
    result: EventsArrived = await ingest.events_arrived(caller.space.space_id, days)
    return result


@router.post("/{source_id}/revoke", response_model=SourceRevocationOut)
async def revoke_source(
    request: Request, caller: Security, source_id: UUID, key: IdempotencyKey
) -> JSONResponse:
    """Every key of the source stops authenticating in this cell within seconds; the control plane's
    revocation, sent by the Console with the same key, makes it permanent."""
    container = request.app.state.container

    async def compute() -> tuple[int, dict[str, Any]]:
        key_ids, at = await container.edge.revoke_source(caller.space.space_id, source_id)
        await container.admin_audit.record(caller, action="source.revoked", target=f"source:{source_id}")
        out = SourceRevocationOut(source_id=source_id, revoked_key_ids=key_ids, revoked_at=at)
        return 200, out.model_dump(mode="json")

    body_hash = hashlib.sha256(request.url.path.encode()).digest()
    status, body = await container.edge.idempotent(caller.space.space_id, key, body_hash, compute)
    return JSONResponse(body, status_code=status)
