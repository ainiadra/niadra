"""Governance routes of receipts, lineage, forget, exports and secrets (5.13, 5.14, 7.1); niadra-admin."""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import Response as RawResponse

from niadra.adapters.inbound.http.auth import require_governance
from niadra.adapters.inbound.http.deps import service
from niadra.app.admin_audit import AdminAudit
from niadra.contracts.audit import ChainVerification, FactViewers, ReceiptLineage, ReceiptOut, ReceiptSearch
from niadra.contracts.common import Handle, Page
from niadra.contracts.events import BatchResponse, ItemError
from niadra.contracts.privacy import (
    CorrectionBatchRequest,
    CorrectionRequest,
    ErasureOut,
    ExportPackageOut,
    ExportRequest,
    ExportRunOut,
    FactRestoreRequest,
    ForgetRequest,
    SecretIn,
    SecretWritten,
)
from niadra.domain.common.errors import Conflict, InvalidInput, NotFound
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.control.records import Role
from niadra.domain.identity.rules import choose_anchor
from niadra.domain.privacy.erasure import ErasureStatus
from niadra.domain.vocabulary import HandleStatus, ReceiptKind
from niadra.ports.repos.audit import ReceiptQuery

router = APIRouter(prefix="/v1", tags=["governance"])

Caller = Annotated[Principal, Depends(require_governance(Role.SECURITY))]
Audit = Annotated[Any, Depends(service("audit"))]
Privacy = Annotated[Any, Depends(service("privacy"))]
Purposes = Annotated[Any, Depends(service("purposes"))]
Ingest = Annotated[Any, Depends(service("ingest"))]
Identity = Annotated[Any, Depends(service("identity"))]
Memory = Annotated[Any, Depends(service("memory"))]
# Every action here leaves an `admin` receipt once the use case succeeded (5.22).
Actions = Annotated[AdminAudit, Depends(service("admin_audit"))]


def _actor(principal: Principal) -> str:
    if isinstance(principal, SourcePrincipal):
        return f"key:{principal.key_id}"
    return f"user:{principal.user_id}"


@router.get("/receipts", response_model=Page[ReceiptOut])
async def list_receipts(
    caller: Caller,
    audit: Audit,
    profile_id: UUID | None = None,
    source_id: UUID | None = None,
    conversation_id: str | None = None,
    kind: ReceiptKind | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[ReceiptOut]:
    query = ReceiptQuery(profile_id, source_id, conversation_id, kind, since, until, cursor, limit)
    page: Page[ReceiptOut] = await audit.list_receipts(caller.space.space_id, query)
    return page


@router.post("/receipts/search", response_model=Page[ReceiptOut])
async def search_receipts(body: ReceiptSearch, caller: Caller, audit: Audit) -> Page[ReceiptOut]:
    """The same as `GET /v1/receipts`, with the filters in the body: a conversation id may be a phone
    number or an e-mail, and it never travels in a URL (7.0)."""
    query = ReceiptQuery(
        body.profile_id,
        body.source_id,
        body.conversation_id,
        body.kind,
        body.since,
        body.until,
        body.cursor,
        body.limit,
    )
    page: Page[ReceiptOut] = await audit.list_receipts(caller.space.space_id, query)
    return page


@router.get("/receipts/verify", response_model=ChainVerification)
async def verify_receipts(caller: Caller, audit: Audit, day: date) -> ChainVerification:
    result: ChainVerification = await audit.verify_chain(caller.space.space_id, day)
    return result


@router.get("/lineage/receipt/{receipt_id}", response_model=ReceiptLineage)
async def receipt_lineage(caller: Caller, audit: Audit, actions: Actions, receipt_id: UUID) -> ReceiptLineage:
    lineage: ReceiptLineage = await audit.receipt_lineage(caller.space.space_id, receipt_id)
    # A person reading why an agent knew something reads customer memory too.
    profile = UUID(lineage.receipt.profile_id) if lineage.receipt.profile_id else None
    await actions.record(
        caller, action="lineage.receipt.read", profile_id=profile, target=f"receipt:{receipt_id}"
    )
    return lineage


@router.get("/lineage/fact/{fact_id}", response_model=FactViewers)
async def fact_viewers(
    caller: Caller,
    audit: Audit,
    fact_id: UUID,
    actions: Actions,
    handle: Annotated[UUID | None, Query(description="Origin handle id.")] = None,
) -> FactViewers:
    viewers: FactViewers = await audit.fact_viewers(caller.space.space_id, fact_id, handle)
    await actions.record(caller, action="lineage.fact.read", target=f"fact:{fact_id}")
    return viewers


@router.post("/forget", response_model=ErasureOut, status_code=202)
async def forget(
    caller: Caller,
    privacy: Privacy,
    purposes: Purposes,
    actions: Actions,
    body: ForgetRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)],
) -> ErasureOut:
    if body.purpose is not None:
        # An objection to one purpose (13, 6.6): carried out in the request, so the answer is final.
        objection: ErasureOut = await purposes.object(
            caller.space.space_id, body, idempotency_key=idempotency_key
        )
        await actions.record(
            caller,
            action="privacy.object",
            profile_id=body.profile_id,
            target=f"purpose:{body.purpose}",
        )
        return objection
    erasure: ErasureOut = await privacy.forget(
        caller.space.space_id, body, idempotency_key=idempotency_key, requested_by=_actor(caller)
    )
    await actions.record(
        caller, action="privacy.forget", profile_id=body.profile_id, target=f"erasure:{erasure.request_id}"
    )
    return erasure


@router.post("/corrections", response_model=BatchResponse, status_code=202)
async def correct(
    caller: Caller,
    ingest: Ingest,
    identity: Identity,
    actions: Actions,
    body: CorrectionRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)],
) -> BatchResponse:
    """Correction, a data subject's right: the fact is retracted or replaced, or the item
    resolved, by an event the memory module applies like an agent's feedback. A replayed key is a
    duplicate, not a second correction."""
    return await _correct_one(caller, ingest, identity, actions, body, idempotency_key)


@router.post("/corrections/batch", response_model=BatchResponse, status_code=202)
async def correct_many(
    caller: Caller,
    ingest: Ingest,
    identity: Identity,
    actions: Actions,
    body: CorrectionBatchRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> BatchResponse:
    """Up to 100 corrections, each as `POST /v1/corrections` with the key `<Idempotency-Key>:<n>`: an item
    that fails is listed by its index and the others still apply; each accepted one leaves its receipt."""
    accepted = duplicates = 0
    errors: list[ItemError] = []
    for index, item in enumerate(body.items):
        try:
            one = await _correct_one(caller, ingest, identity, actions, item, f"{idempotency_key}:{index}")
        except (NotFound, Conflict, InvalidInput) as refused:
            errors.append(ItemError(index=index, code=refused.code, detail=refused.detail))
            continue
        accepted += one.accepted
        duplicates += one.duplicates
        errors += [e.model_copy(update={"index": index}) for e in one.errors]
    return BatchResponse(accepted=accepted, duplicates=duplicates, errors=errors)


async def _correct_one(
    caller: Principal,
    ingest: Any,
    identity: Any,
    actions: AdminAudit,
    body: CorrectionRequest,
    idempotency_key: str,
) -> BatchResponse:
    subject = await _subject(identity, caller, body.profile_id)
    result: BatchResponse = await ingest.record_correction(
        caller.space, _actor(caller), subject, body, idempotency_key
    )
    if result.accepted:
        target = f"fact:{body.fact_id}" if body.fact_id else f"open_item:{body.open_item_id}"
        await actions.record(
            caller, action=f"memory.{body.action}", profile_id=body.profile_id, target=target
        )
    return result


@router.post("/facts/{fact_id}/restore", response_model=BatchResponse, status_code=202)
async def restore_fact(
    fact_id: UUID,
    caller: Caller,
    memory: Memory,
    ingest: Ingest,
    identity: Identity,
    actions: Actions,
    body: FactRestoreRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)],
) -> BatchResponse:
    """Undoes a retraction or a correction: the fact comes back as it was, the correction that replaced
    it is retracted, and the fact's history keeps every step. An erased fact is gone and answers 404. One
    that came through an unconfirmed link, one merged into another, one that is not retracted, and a
    correction that a restore of the fact it corrected already undid (the detail says to correct that
    fact again) are refused with 409. Like a correction it becomes an event the memory module applies
    within seconds; a replayed key is a duplicate."""
    await memory.check_restore(caller, body.profile_id, fact_id)
    subject = await _subject(identity, caller, body.profile_id)
    result: BatchResponse = await ingest.record_restore(
        caller.space, _actor(caller), subject, fact_id, body.reason, idempotency_key
    )
    if result.accepted:
        await actions.record(
            caller, action="memory.restore_fact", profile_id=body.profile_id, target=f"fact:{fact_id}"
        )
    return result


async def _subject(identity: Any, caller: Principal, profile_id: UUID) -> Handle:
    """The handle a governance event names its customer by: an active one when the profile has it."""
    summary = await identity.profile_summary(caller.space.space_id, profile_id)
    usable = [h for h in summary.handles if h.status is HandleStatus.ACTIVE] or list(summary.handles)
    if not usable:
        raise Conflict("the profile has no handle left to correct")
    anchor = choose_anchor(usable)
    return Handle(type=anchor.type, value=anchor.value, scope=anchor.scope or None)


@router.get("/forget", response_model=Page[ErasureOut])
async def list_erasures(
    caller: Caller,
    privacy: Privacy,
    status: ErasureStatus | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[ErasureOut]:
    page: Page[ErasureOut] = await privacy.erasures(
        caller.space.space_id, status=status, cursor=cursor, limit=limit
    )
    return page


@router.get("/forget/{request_id}", response_model=ErasureOut)
async def erasure_status(caller: Caller, privacy: Privacy, request_id: UUID) -> ErasureOut:
    erasure: ErasureOut = await privacy.erasure_status(caller.space.space_id, request_id)
    return erasure


@router.post("/export", response_model=ExportPackageOut, status_code=201)
async def export_package(
    caller: Caller, privacy: Privacy, actions: Actions, body: ExportRequest
) -> ExportPackageOut:
    package: ExportPackageOut = await privacy.export_package(
        caller.space.space_id, body, requested_by=_actor(caller)
    )
    await actions.record(
        caller,
        action="privacy.export",
        profile_id=UUID(package.profile_id),
        target=f"export_run:{package.run_id}",
    )
    return package


@router.get("/exports/{run_id}/download", response_class=RawResponse)
async def download_package(caller: Caller, privacy: Privacy, actions: Actions, run_id: UUID) -> Response:
    data: bytes = await privacy.download_package(caller.space.space_id, run_id)
    await actions.record(caller, action="privacy.export_download", target=f"export_run:{run_id}")
    headers = {
        "Content-Disposition": f'attachment; filename="export-{run_id}.zip"',
        "Cache-Control": "no-store",
    }
    return Response(content=data, media_type="application/zip", headers=headers)


@router.get("/exports/runs", response_model=Page[ExportRunOut])
async def export_runs(
    caller: Caller,
    privacy: Privacy,
    schedule_id: str | None = None,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[ExportRunOut]:
    page: Page[ExportRunOut] = await privacy.export_runs(
        caller.space.space_id, schedule_id=schedule_id, cursor=str(cursor) if cursor else None, limit=limit
    )
    return page


@router.put("/exports/destinations/{schedule_id}/credential", response_model=SecretWritten)
async def put_destination_credential(
    caller: Caller, privacy: Privacy, actions: Actions, schedule_id: str, body: SecretIn
) -> SecretWritten:
    version: int = await privacy.put_destination_credential(caller.space.space_id, schedule_id, body.value)
    await actions.record(
        caller, action="privacy.destination_credential", target=f"export_schedule:{schedule_id}"
    )
    return SecretWritten(version=version)


@router.put("/secrets/{kind}/{secret_id}", response_model=SecretWritten)
async def put_secret(
    caller: Caller,
    privacy: Privacy,
    actions: Actions,
    kind: Literal["webhook", "model", "gateway"],
    secret_id: str,
    body: SecretIn,
) -> SecretWritten:
    version: int = await privacy.put_secret(caller.space.space_id, kind, secret_id, body.value)
    await actions.record(caller, action="privacy.secret", target=f"{kind}_secret:{secret_id}")
    return SecretWritten(version=version)
