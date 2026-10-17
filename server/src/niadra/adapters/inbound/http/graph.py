"""The Console's relationship graph (5.22), served by niadra-admin: `GET /v1/graph`.

The roles that read a customer's profile read it, and one `admin` receipt is left per call, never one per
node. The profile id travels in the query: it is a pseudonymous id, never personal data (7.0).
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from niadra.adapters.inbound.http.auth import require_governance
from niadra.adapters.inbound.http.deps import service
from niadra.contracts.graph import Graph
from niadra.domain.common.errors import InvalidInput
from niadra.domain.common.space import Principal
from niadra.domain.control.records import Role
from niadra.domain.graph import AROUND_LIMIT, MAX_NODES, OVERVIEW_LIMIT

router = APIRouter(prefix="/v1", tags=["governance"])

Reader = Annotated[Principal, Depends(require_governance(Role.SECURITY, Role.INTEGRATION))]
GraphReads = Annotated[Any, Depends(service("graph"))]
AdminAudit = Annotated[Any, Depends(service("admin_audit"))]


@router.get("/graph", response_model=Graph)
async def graph(
    caller: Reader,
    graphs: GraphReads,
    admin_audit: AdminAudit,
    center: UUID | None = None,
    overview: bool = False,
    depth: Annotated[int, Query(ge=1, le=2)] = 1,
    limit: Annotated[int | None, Query(ge=1, le=MAX_NODES)] = None,
) -> Graph:
    """One customer's relations (`center`, `depth` 1 or 2, up to `limit` nodes, 150 by default), or the
    space's most connected customers and companies among the latest active (`overview=1`, 100 by default).
    A profile that was erased or merged into another is not found."""
    if (center is None) == (not overview):
        raise InvalidInput("ask for a center or for the overview, one of them")
    result: Graph
    if center is not None:
        result = await graphs.around(caller, center, depth=depth, limit=limit or AROUND_LIMIT)
    else:
        result = await graphs.overview(caller, limit=limit or OVERVIEW_LIMIT)
    target = f"graph:{len(result.nodes)}"
    await admin_audit.record(caller, action="graph.read", profile_id=center, target=target)
    return result
