"""Identity governance routes (2.5, 7.1), served by niadra-admin.

Every change to membership is queued for `identity_apply` and answers 202 with the op id; links are
written at once. Handles only travel in bodies, and handle values leave masked by the caller's role. Every
change leaves an admin receipt with ids only (5.13).
"""

import hashlib
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from niadra.adapters.inbound.http.auth import governance_or_scope, require_governance
from niadra.adapters.inbound.http.deps import service
from niadra.app.identity import IdentityService, Operation, ProfileSummary, SuggestionView
from niadra.app.profile_names import Shown
from niadra.contracts.common import Page
from niadra.contracts.identity import (
    AcceptIn,
    AssertionIn,
    AssertionOut,
    AssertionStateName,
    BlockIn,
    HandleOut,
    LinkEndIn,
    LinkIn,
    LinkOut,
    ListedHandle,
    MergeIn,
    OperationOut,
    ProfileListed,
    ProfileMatch,
    ProfileOut,
    ProfileSearchIn,
    ProfileSearchOut,
    RetractIn,
    SuggestionOut,
    UnmergeIn,
)
from niadra.domain.common.errors import Forbidden
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.identity.handles import is_partial
from niadra.domain.identity.listing import ListedProfile, ListOrder
from niadra.domain.identity.masking import mask_listed, mask_value, reveals_contacts
from niadra.domain.identity.model import AssertionRecord, AssertionState
from niadra.domain.identity.rules import handle_weight
from niadra.domain.records import HandleRecord, LinkRecord
from niadra.domain.vocabulary import AssertionMethod, Scope, SubjectKind

router = APIRouter(prefix="/v1", tags=["identity"])

Caller = Annotated[Principal, Depends(require_governance("security", "integration"))]
# Linking a person to an organization, and ending the link: governance, or a source key with `identity:link`
# (a system of record that knows who works for whom). Such a key never grants `can_see_contacts`.
Linker = Annotated[Principal, Depends(governance_or_scope(Scope.IDENTITY_LINK, "security", "integration"))]
Identity = Annotated[IdentityService, Depends(service("identity"))]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]
Compute = Callable[[], Awaitable[tuple[int, dict[str, Any]]]]


@router.get("/identity/assertions", response_model=Page[AssertionOut])
async def list_assertions(
    caller: Caller,
    identity: Identity,
    profile_id: UUID | None = None,
    state: AssertionStateName | None = None,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[AssertionOut]:
    rows, next_cursor = await identity.list_assertions(
        caller.space.space_id,
        profile_id=profile_id,
        state=AssertionState(state) if state else None,
        after=cursor,
        limit=limit,
    )
    return Page[AssertionOut](
        items=[_assertion(a) for a in rows], next_cursor=str(next_cursor) if next_cursor else None
    )


@router.post("/identity/assertions", response_model=OperationOut, status_code=202)
async def create_assertion(
    request: Request, caller: Caller, identity: Identity, body: AssertionIn, key: IdempotencyKey
) -> JSONResponse:
    async def compute() -> tuple[int, dict[str, Any]]:
        op = await identity.create_assertion(
            caller.space.space_id,
            body.handles,
            method=body.method,
            subject_kind=body.subject_kind,
            actor=_actor(caller),
            reason=body.reason,
        )
        target = f"link:{op.link_id}" if op.link_id else f"assertion:{op.assertion_id}"
        await _receipt(request, caller, "identity.assertion.created", target=target)
        return _operation(op)

    return await _once(request, caller, key, compute)


@router.post("/identity/assertions/{assertion_id}/retract", response_model=OperationOut, status_code=202)
async def retract_assertion(
    request: Request,
    caller: Caller,
    identity: Identity,
    assertion_id: UUID,
    body: RetractIn,
    key: IdempotencyKey,
) -> JSONResponse:
    async def compute() -> tuple[int, dict[str, Any]]:
        op = await identity.retract(
            caller.space.space_id, assertion_id, actor=_actor(caller), reason=body.reason
        )
        await _receipt(request, caller, "identity.assertion.retracted", target=f"assertion:{assertion_id}")
        return _operation(op)

    return await _once(request, caller, key, compute)


@router.post("/identity/merge", response_model=OperationOut, status_code=202)
async def merge(
    request: Request, caller: Caller, identity: Identity, body: MergeIn, key: IdempotencyKey
) -> JSONResponse:
    async def compute() -> tuple[int, dict[str, Any]]:
        op = await identity.merge(
            caller.space.space_id,
            body.profile_ids,
            force=body.force,
            actor=_actor(caller),
            reason=body.reason,
        )
        await _receipt(request, caller, "identity.merge.requested", target=f"op:{op.op_id}")
        return _operation(op)

    return await _once(request, caller, key, compute)


@router.post("/identity/unmerge", response_model=OperationOut, status_code=202)
async def unmerge(
    request: Request, caller: Caller, identity: Identity, body: UnmergeIn, key: IdempotencyKey
) -> JSONResponse:
    async def compute() -> tuple[int, dict[str, Any]]:
        op = await identity.unmerge(
            caller.space.space_id,
            body.profile_id,
            handle_ids=body.handle_ids,
            assertion_ids=body.assertion_ids,
            actor=_actor(caller),
            reason=body.reason,
        )
        await _receipt(
            request, caller, "identity.unmerge.requested", profile_id=body.profile_id, target=f"op:{op.op_id}"
        )
        return _operation(op)

    return await _once(request, caller, key, compute)


@router.post("/identity/handles/block", response_model=OperationOut, status_code=202)
async def block_handle(
    request: Request, caller: Caller, identity: Identity, body: BlockIn, key: IdempotencyKey
) -> JSONResponse:
    async def compute() -> tuple[int, dict[str, Any]]:
        op = await identity.block(
            caller.space.space_id, body.handle, actor=_actor(caller), reason=body.reason
        )
        await _receipt(request, caller, "identity.handle.blocked", target=f"op:{op.op_id}")
        return _operation(op)

    return await _once(request, caller, key, compute)


@router.post("/identity/links", response_model=LinkOut, status_code=201)
async def create_link(
    request: Request, caller: Linker, identity: Identity, body: LinkIn, key: IdempotencyKey
) -> JSONResponse:
    if body.can_see_contacts and isinstance(caller, SourcePrincipal) and not caller.allows(Scope.ADMIN):
        # Seeing the organization's other contacts is a privacy decision: a person, or a key with `admin`.
        raise Forbidden("can_see_contacts needs a person or a key with the admin scope")

    async def compute() -> tuple[int, dict[str, Any]]:
        link = await identity.create_link(
            caller.space.space_id,
            body.person,
            body.organization,
            role=body.role,
            can_see_contacts=body.can_see_contacts,
            valid_from=body.valid_from,
            valid_to=body.valid_to,
            method=AssertionMethod(body.method),
        )
        await _receipt(request, caller, "identity.link.created", target=f"link:{link.link_id}")
        return 201, _link(link).model_dump(mode="json")

    return await _once(request, caller, key, compute)


@router.post("/identity/links/{link_id}/end", response_model=LinkOut)
async def end_link(
    request: Request, caller: Linker, identity: Identity, link_id: UUID, body: LinkEndIn, key: IdempotencyKey
) -> JSONResponse:
    async def compute() -> tuple[int, dict[str, Any]]:
        link = await identity.end_link(caller.space.space_id, link_id, body.valid_to)
        await _receipt(request, caller, "identity.link.ended", target=f"link:{link_id}")
        return 200, _link(link).model_dump(mode="json")

    return await _once(request, caller, key, compute)


@router.get("/identity/suggestions", response_model=Page[SuggestionOut])
async def list_suggestions(
    caller: Caller,
    identity: Identity,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[SuggestionOut]:
    views, next_cursor = await identity.open_suggestions(caller.space.space_id, after=cursor, limit=limit)
    reveal = reveals_contacts(caller)
    return Page[SuggestionOut](
        items=[_suggestion(v, reveal_contacts=reveal) for v in views],
        next_cursor=str(next_cursor) if next_cursor else None,
    )


@router.post("/identity/suggestions/{suggestion_id}/accept", response_model=OperationOut, status_code=202)
async def accept_suggestion(
    request: Request,
    caller: Caller,
    identity: Identity,
    suggestion_id: UUID,
    body: AcceptIn,
    key: IdempotencyKey,
) -> JSONResponse:
    """The pair becomes an `accepted_suggestion` assertion, queued for `identity_apply` (5.3)."""

    async def compute() -> tuple[int, dict[str, Any]]:
        op = await identity.accept_suggestion(
            caller.space.space_id, suggestion_id, actor=_actor(caller), reason=body.reason
        )
        await _receipt(request, caller, "identity.suggestion.accepted", target=f"suggestion:{suggestion_id}")
        return _operation(op)

    return await _once(request, caller, key, compute)


@router.post("/identity/suggestions/{suggestion_id}/dismiss", response_model=SuggestionOut)
async def dismiss_suggestion(
    request: Request, caller: Caller, identity: Identity, suggestion_id: UUID, key: IdempotencyKey
) -> JSONResponse:
    async def compute() -> tuple[int, dict[str, Any]]:
        view = await identity.dismiss_suggestion(caller.space.space_id, suggestion_id)
        await _receipt(request, caller, "identity.suggestion.dismissed", target=f"suggestion:{suggestion_id}")
        return 200, _suggestion(view, reveal_contacts=reveals_contacts(caller)).model_dump(mode="json")

    return await _once(request, caller, key, compute)


@router.get("/profiles", response_model=Page[ProfileListed])
async def list_profiles(
    request: Request,
    caller: Caller,
    identity: Identity,
    order: ListOrder = ListOrder.LAST_SEEN_DESC,
    kind: SubjectKind | None = None,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[ProfileListed]:
    """The customers of the space by their latest activity, for a person to browse: the name and contact
    to show each by, as the caller's role sees them, the kind and masked identifiers. An erased profile is
    not listed. A page may hold fewer rows than `limit`; the list ends where `next_cursor` is null. One
    receipt per page."""
    rows, next_cursor = await identity.list_profiles(
        caller.space.space_id, kind=kind, order=order, cursor=cursor, limit=limit
    )
    shown = await identity.shown(caller, [row.profile_id for row in rows])
    await _receipt(request, caller, "profiles.listed", target=f"profiles:{len(rows)}")
    return Page[ProfileListed](
        items=[_listed(row, shown.get(row.profile_id, Shown())) for row in rows], next_cursor=next_cursor
    )


@router.post("/profiles/search", response_model=ProfileSearchOut)
async def search_profiles(caller: Caller, identity: Identity, body: ProfileSearchIn) -> ProfileSearchOut:
    """A read, so no receipt; the query is in the body and never logged (7.0)."""
    found = await identity.search_profiles(
        caller.space.space_id, body.query, type=body.type, scope=body.scope, limit=body.limit, viewer=caller
    )
    shown = await identity.shown(caller, [summary.profile.profile_id for summary in found])
    reveal = reveals_contacts(caller)
    return ProfileSearchOut(
        items=[
            _match(summary, shown.get(summary.profile.profile_id, Shown()), reveal_contacts=reveal)
            for summary in found
        ]
    )


@router.get("/profiles/{profile_id}", response_model=ProfileOut)
async def get_profile(request: Request, caller: Caller, identity: Identity, profile_id: UUID) -> ProfileOut:
    summary = await identity.profile_summary(caller.space.space_id, profile_id)
    shown = await identity.shown(caller, [profile_id])
    await _receipt(request, caller, "profile.read", profile_id=profile_id)
    return _profile(summary, shown.get(profile_id, Shown()), reveal_contacts=reveals_contacts(caller))


async def _once(request: Request, caller: Principal, key: str, compute: Compute) -> JSONResponse:
    """Governance POSTs replay their first answer for the same Idempotency-Key (7.0); the receipt is
    written inside `compute`, so a replay does not write a second one."""
    body_hash = hashlib.sha256(request.url.path.encode() + b"\n" + await request.body()).digest()
    edge = request.app.state.container.edge
    status, body = await edge.idempotent(caller.space.space_id, key, body_hash, compute)
    return JSONResponse(body, status_code=status)


async def _receipt(
    request: Request,
    caller: Principal,
    action: str,
    *,
    profile_id: UUID | None = None,
    target: str | None = None,
) -> None:
    await request.app.state.container.admin_audit.record(
        caller, action=action, profile_id=profile_id, target=target
    )


def _actor(principal: Principal) -> str:
    if isinstance(principal, SourcePrincipal):
        return f"key:{principal.key_id}"
    return f"user:{principal.user_id}"


def _operation(op: Operation) -> tuple[int, dict[str, Any]]:
    out = OperationOut(op_id=op.op_id, status=op.status, assertion_id=op.assertion_id, link_id=op.link_id)
    return (202 if op.status == "queued" else 200), out.model_dump(mode="json")


def _assertion(a: AssertionRecord) -> AssertionOut:
    return AssertionOut(
        assertion_id=a.assertion_id,
        handle_a=a.handle_a,
        handle_b=a.handle_b,
        method=a.method,
        state=a.state.value,
        confidence=a.confidence,
        level_at_capture=a.level_at_capture,
        created_at=a.created_at,
        origin_event_id=a.origin_event_id,
        actor=a.actor,
        reason=a.reason,
        retracted_at=a.retracted_at,
    )


def _link(link: LinkRecord) -> LinkOut:
    return LinkOut(
        link_id=link.link_id,
        person_handle_id=link.person_handle_id,
        org_handle_id=link.org_handle_id,
        role=link.role,
        can_see_contacts=link.can_see_contacts,
        valid_from=link.valid_from,
        valid_to=link.valid_to,
    )


def _handle(h: HandleRecord, *, reveal_contacts: bool) -> HandleOut:
    return HandleOut(
        handle_id=h.handle_id,
        type=h.type,
        scope=h.scope,
        subject_kind=h.subject_kind,
        value=mask_value(h.type, h.value, reveal_contacts=reveal_contacts),
        status=h.status,
        level=h.level,
        weight=handle_weight(h),
        first_seen=h.first_seen,
        last_seen=h.last_seen,
        provisional=h.provisional,
    )


def _suggestion(view: SuggestionView, *, reveal_contacts: bool) -> SuggestionOut:
    s = view.suggestion
    a, b = view.handles.get(s.handle_a), view.handles.get(s.handle_b)
    return SuggestionOut(
        suggestion_id=s.suggestion_id,
        handle_a=s.handle_a,
        handle_b=s.handle_b,
        profile_a=view.profiles.get(s.handle_a),
        profile_b=view.profiles.get(s.handle_b),
        identifier_a=_handle(a, reveal_contacts=reveal_contacts) if a else None,
        identifier_b=_handle(b, reveal_contacts=reveal_contacts) if b else None,
        score=s.score,
        features=s.features,
        status=s.status,
        created_at=s.created_at,
    )


def _match(summary: ProfileSummary, shown: Shown, *, reveal_contacts: bool) -> ProfileMatch:
    profile = summary.profile
    return ProfileMatch(
        profile_id=profile.profile_id,
        pseudonym=profile.pseudonym,
        kind=profile.kind,
        display_name=shown.name,
        display_contact=shown.contact,
        partial=is_partial({h.type for h in summary.handles}),
        handles=[_handle(h, reveal_contacts=reveal_contacts) for h in summary.handles],
        last_seen=summary.last_seen,
    )


def _listed(row: ListedProfile, shown: Shown) -> ProfileListed:
    return ProfileListed(
        profile_id=row.profile_id,
        pseudonym=row.pseudonym,
        kind=row.kind,
        display_name=shown.name,
        display_contact=shown.contact,
        handles=[
            ListedHandle(type=h.type, value=mask_listed(h.type, h.value), last_seen=h.last_seen)
            for h in row.handles
        ],
        handle_count=row.handle_count,
        last_seen=row.last_seen,
    )


def _profile(summary: ProfileSummary, shown: Shown, *, reveal_contacts: bool) -> ProfileOut:
    profile = summary.profile
    return ProfileOut(
        profile_id=profile.profile_id,
        kind=profile.kind,
        pseudonym=profile.pseudonym,
        display_name=shown.name,
        display_contact=shown.contact,
        membership_version=profile.membership_version,
        privacy_version=profile.privacy_version,
        partial=is_partial({h.type for h in summary.handles}),
        handles=[_handle(h, reveal_contacts=reveal_contacts) for h in summary.handles],
        links=[_link(link) for link in summary.links],
    )
