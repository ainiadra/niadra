"""Business objects on the read API (`niadra-read`) and a customer's memory in the Console (`niadra-admin`).

`router` serves `/v1/objects/...` (7.1): typed state and timeline under the source's purpose, each read
leaving a receipt of kind `read` and surface `object` (5.13) through the read process's receipt path.
The object ids in the path are system record ids, never personal data (7.0).

`profiles_router` serves the Console's customer screen (5.22): memory and business objects by the
pseudonymous profile id, read under the policy of the person's role, each read leaving an `admin` receipt.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from niadra.adapters.inbound.http.auth import require_governance, require_source
from niadra.adapters.inbound.http.deps import service
from niadra.contracts.memory import FactHistory, ProfileMemory
from niadra.contracts.objects import ObjectTimeline, ProfileObjects
from niadra.contracts.state import ObjectRead
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.control.records import Role
from niadra.domain.records import ObjectKey
from niadra.domain.vocabulary import ReceiptKind, ReceiptSurface, Scope

router = APIRouter(prefix="/v1/objects", tags=["objects"])
profiles_router = APIRouter(prefix="/v1/profiles", tags=["governance"])

Reader = Annotated[SourcePrincipal, Depends(require_source(Scope.CONTEXT))]
MemoryReader = Annotated[Principal, Depends(require_governance(Role.SECURITY, Role.REVIEW, Role.ANALYSIS))]
ObjectsReader = Annotated[
    Principal, Depends(require_governance(Role.SECURITY, Role.REVIEW, Role.ANALYSIS, Role.INTEGRATION))
]
Objects = Annotated[Any, Depends(service("objects"))]
Memory = Annotated[Any, Depends(service("memory"))]
Serve = Annotated[Any, Depends(service("serve"))]
AdminAudit = Annotated[Any, Depends(service("admin_audit"))]


# Declared first and with `:path`: system ids may contain `/` (7.0), so the plain read below would
# otherwise swallow the `/timeline` suffix.
@router.get("/{object_type}/{namespace}/{external_id:path}/timeline", response_model=ObjectTimeline)
async def object_timeline(
    object_type: str,
    namespace: str,
    external_id: str,
    caller: Reader,
    objects: Objects,
    serve: Serve,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ObjectTimeline:
    read = await objects.timeline(
        caller, ObjectKey(object_type, namespace, external_id), cursor=cursor, limit=limit
    )
    await _receipt(serve, caller, read.object_id, read.profile_id, read.item_ids)
    timeline: ObjectTimeline = read.timeline
    return timeline


@router.get("/{object_type}/{namespace}/{external_id:path}", response_model=ObjectRead)
async def read_object(
    object_type: str, namespace: str, external_id: str, caller: Reader, objects: Objects, serve: Serve
) -> ObjectRead:
    """The object as a `display` state read serves it (object-state v1), derived fields included."""
    guard = await serve.guarded(caller.space.space_id)
    read = await objects.read_object(caller, ObjectKey(object_type, namespace, external_id), guard)
    await _receipt(serve, caller, read.object_id, read.profile_id, ())
    state: ObjectRead = read.state
    return state


@profiles_router.get("/{profile_id}/memory", response_model=ProfileMemory)
async def profile_memory(
    profile_id: UUID,
    caller: MemoryReader,
    memory: Memory,
    admin_audit: AdminAudit,
    timeline_cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> ProfileMemory:
    """A customer's memory as the Console shows it; the timeline is paged by `timeline_cursor`."""
    result: ProfileMemory = await memory.profile_memory(caller, profile_id, timeline_cursor=timeline_cursor)
    await admin_audit.record(caller, action="profile.memory.read", profile_id=profile_id)
    return result


@profiles_router.get("/{profile_id}/facts/{fact_id}/history", response_model=FactHistory)
async def fact_history(
    profile_id: UUID, fact_id: UUID, caller: MemoryReader, memory: Memory, admin_audit: AdminAudit
) -> FactHistory:
    """What a fact's slot held before and after it: the values, when each held, where each came from."""
    result: FactHistory = await memory.fact_history(caller, profile_id, fact_id)
    await admin_audit.record(
        caller, action="profile.fact_history.read", profile_id=profile_id, target=f"fact:{fact_id}"
    )
    return result


@profiles_router.get("/{profile_id}/objects", response_model=ProfileObjects)
async def profile_objects(
    profile_id: UUID,
    caller: ObjectsReader,
    objects: Objects,
    admin_audit: AdminAudit,
    objects_cursor: Annotated[str | None, Query(max_length=200)] = None,
    actions_cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> ProfileObjects:
    """A customer's objects and actions as the Console shows them, each list paged by its cursor."""
    result: ProfileObjects = await objects.profile_objects(
        caller, profile_id, objects_cursor=objects_cursor, actions_cursor=actions_cursor
    )
    await admin_audit.record(caller, action="profile.objects.read", profile_id=profile_id)
    return result


async def _receipt(
    serve: Any, caller: SourcePrincipal, object_id: UUID, profile_id: UUID | None, item_ids: tuple[str, ...]
) -> None:
    await serve.emit_receipt(
        caller,
        kind=ReceiptKind.READ,
        surface=ReceiptSurface.OBJECT,
        profile_id=profile_id,
        object_id=object_id,
        item_ids=item_ids,
    )
