"""Requests to review an automated decision (24.5.3; LGPD, art. 20).

The subject asks through the controller, which files the request here: it goes to the controller's data
protection officer as `review_request.created`, with the explanation package built from receipts, and to the
Console's list. The controller reviews; Niadra records the resolution and sends `review_request.resolved`.
"""

from typing import Any
from uuid import UUID

from niadra.app.notify import enqueue_event
from niadra.contracts.signals import ReviewRequest, ReviewRequestCreate, ReviewRequestPage, ReviewResolution
from niadra.domain.common.errors import Conflict, NotFound
from niadra.domain.common.space import Principal
from niadra.domain.ingest.hot import pack_envelope, unpack_envelope
from niadra.domain.notify.cursor import decode_cursor, encode_cursor
from niadra.domain.notify.webhooks import WebhookSubject
from niadra.domain.privacy.reviews import (
    EXPLAINED,
    ReviewRequestRecord,
    explanation,
    is_decision,
    reason_aad,
    request_id_for,
)
from niadra.ports.infra import Clock, ConfigProvider, KeyVault
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

LOOKED_AT = 200
"""The subject's latest receipts searched for decisions when the request names none."""


class ReviewRequestsService:
    def __init__(self, uow: UnitOfWorkFactory, keys: KeyVault, clock: Clock, config: ConfigProvider) -> None:
        self._uow = uow
        self._keys = keys
        self._clock = clock
        self._config = config

    async def create(
        self, principal: Principal, profile_id: UUID, body: ReviewRequestCreate, *, idempotency_key: str
    ) -> ReviewRequest:
        """`POST /v1/profiles/{id}/review-requests`: the Idempotency-Key names the request, so a retry
        returns the same one and sends nothing again."""
        space_id = principal.space.space_id
        request_id = request_id_for(space_id, idempotency_key)
        sealed, version = await self._keys.encrypt(
            space_id, body.reason.encode(), reason_aad(request_id, "reason_ct")
        )
        async with self._uow(space_id) as uow:
            known = await uow.review_requests.get(request_id)
            if known is not None:
                return await request_out(self._keys, space_id, known)
            profile = await uow.identity.get_profile(profile_id)
            if profile is None:
                raise NotFound("unknown profile")
            receipts = await self._decisions(uow, profile_id, body.decision_receipt_id)
            record = ReviewRequestRecord(
                request_id,
                profile_id,
                sealed,
                version,
                self._clock.now(),
                body.decision_receipt_id,
                explanation(receipts),
            )
            stored = await uow.review_requests.add(record)
            subject = WebhookSubject(profile.pseudonym, profile.kind.value)
            await enqueue_event(
                uow, self._config.space(space_id), "review_request.created", subject, _event(stored)
            )
        return await request_out(self._keys, space_id, stored)

    async def page(self, principal: Principal, *, cursor: str | None, limit: int) -> ReviewRequestPage:
        """`GET /v1/review-requests`: the Console's list, newest first."""
        space_id = principal.space.space_id
        async with self._uow(space_id) as uow:
            found = await uow.review_requests.page(status=None, before=decode_cursor(cursor), limit=limit + 1)
        page = found[:limit]
        more = len(found) > limit
        return ReviewRequestPage(
            items=[await request_out(self._keys, space_id, r) for r in page],
            next_cursor=encode_cursor(page[-1].created_at, page[-1].request_id) if more else None,
        )

    async def resolve(
        self, principal: Principal, request_id: str, body: ReviewResolution, *, by: str
    ) -> ReviewRequest:
        """`POST /v1/review-requests/{id}/resolve`: the controller's decision, recorded once."""
        space_id = principal.space.space_id
        try:
            wanted = UUID(request_id)
        except ValueError as exc:
            raise NotFound("unknown review request") from exc
        note = None
        if body.note is not None:
            sealed, version = await self._keys.encrypt(
                space_id, body.note.encode(), reason_aad(wanted, "resolution_ct")
            )
            note = pack_envelope(version, sealed)
        async with self._uow(space_id) as uow:
            resolved = await uow.review_requests.resolve(
                wanted, outcome=body.outcome, resolution_ct=note, at=self._clock.now(), by=by
            )
            if resolved is None:
                if await uow.review_requests.get(wanted) is None:
                    raise NotFound("unknown review request")
                raise Conflict("the review request was resolved already")
            profile = await uow.identity.get_profile(resolved.profile_id)
            subject = WebhookSubject(profile.pseudonym, profile.kind.value) if profile else None
            await enqueue_event(
                uow, self._config.space(space_id), "review_request.resolved", subject, _event(resolved)
            )
        return await request_out(self._keys, space_id, resolved)

    async def _decisions(
        self, uow: UnitOfWork, profile_id: UUID, receipt_id: UUID | None
    ) -> list[dict[str, object]]:
        """The receipt named, which must be the subject's; else the subject's latest decisions."""
        if receipt_id is not None:
            stored = await uow.audit.get_receipt(uow.space_id, receipt_id)
            if stored is None or stored.draft.profile_id != profile_id:
                raise NotFound("unknown receipt")
            return [{"receipt_id": receipt_id, **stored.as_dict()}]
        latest = await uow.audit.receipts_for_profile(profile_id, limit=LOOKED_AT)
        return [r for r in latest if is_decision(r)][:EXPLAINED]


async def request_out(keys: KeyVault, space_id: UUID, record: ReviewRequestRecord) -> ReviewRequest:
    reason = await keys.decrypt(
        space_id, record.reason_ct, reason_aad(record.request_id, "reason_ct"), record.dek_version
    )
    note = None
    if record.resolution_ct is not None:
        version, sealed = unpack_envelope(record.resolution_ct)
        aad = reason_aad(record.request_id, "resolution_ct")
        note = (await keys.decrypt(space_id, sealed, aad, version)).decode()
    return ReviewRequest(
        request_id=str(record.request_id),
        profile_id=str(record.profile_id),
        status=record.status,
        created_at=record.created_at,
        decision_receipt_id=record.decision_receipt_id,
        reason=reason.decode(),
        explanation=dict(record.explanation),
        outcome=record.outcome,
        resolution=note,
        resolved_at=record.resolved_at,
    )


def _event(record: ReviewRequestRecord) -> dict[str, object]:
    """Ids, the outcome and the explanation's receipts by reference: never the subject's words."""
    data: dict[str, object] = {
        "request_id": str(record.request_id),
        "status": record.status,
        "decision_receipt_id": str(record.decision_receipt_id) if record.decision_receipt_id else None,
        "receipt_ids": [r["receipt_id"] for r in record.explanation.get("receipts", [])],
    }
    if record.outcome is not None:
        data["outcome"] = record.outcome
    return data


async def exported_review_requests(
    uow: UnitOfWork, keys: KeyVault, space_id: UUID, profile_id: UUID
) -> list[Any]:
    """The subject's requests with their words, the explanation, the outcome and the reviewer's note."""
    return [
        (await request_out(keys, space_id, record)).model_dump(mode="json", exclude_none=True)
        for record in await uow.review_requests.for_profiles([profile_id])
    ]
