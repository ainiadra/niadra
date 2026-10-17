"""Legal holds (24.5.1; LGPD, art. 16): governance places a hold on a handle, an object or a conversation,
with its reason sealed, and releases it; the modules that purge ask what is held through `LegalHolds`.

A release enqueues `release_held`, which finishes what completed erasures left waiting under the hold.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niadra.contracts.signals import LegalHold, LegalHoldCreate, LegalHoldRelease
from niadra.domain.common.errors import Conflict, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import Principal
from niadra.domain.identity.handles import normalize_handle
from niadra.domain.ingest.hot import pack_envelope
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.privacy.holds import Held, LegalHoldRecord, hold_id_for, reason_aad
from niadra.domain.records import ObjectKey
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.storage import HMAC_PURPOSE as TURN_HMAC
from niadra.ports.infra import Clock, KeyVault
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


def hold_out(hold: LegalHoldRecord) -> LegalHold:
    return LegalHold(
        hold_id=str(hold.hold_id),
        scope=hold.scope,
        status=hold.status,
        created_at=hold.created_at,
        released_at=hold.released_at,
    )


class LegalHoldsService:
    def __init__(self, uow: UnitOfWorkFactory, keys: KeyVault, clock: Clock) -> None:
        self._uow = uow
        self._keys = keys
        self._clock = clock

    async def create(
        self, principal: Principal, body: LegalHoldCreate, *, idempotency_key: str, by: str
    ) -> LegalHold:
        """`POST /v1/legal-holds`: the Idempotency-Key names the hold, so a retry returns the same one."""
        space_id = principal.space.space_id
        hold_id = hold_id_for(space_id, idempotency_key)
        sealed, version = await self._keys.encrypt(
            space_id, body.reason.encode(), reason_aad(hold_id, "reason_ct")
        )
        handle_id: UUID | None = None
        object_id: UUID | None = None
        conversation: bytes | None = None
        async with self._uow(space_id) as uow:
            if body.handle is not None:
                key = normalize_handle(
                    body.handle.type, body.handle.value, body.handle.scope, body.handle.subject_kind
                ).key
                found = await uow.identity.find_handle(key.type, key.scope, key.value)
                if found is None:
                    raise NotFound("unknown handle")
                handle_id = found.handle_id
            elif body.object is not None:
                ref = body.object
                obj = await uow.objects.get_object(ObjectKey(ref.type, ref.namespace, ref.id))
                if obj is None:
                    raise NotFound("unknown object")
                object_id = obj.object_id
            else:
                assert body.conversation_id is not None
                conversation = await self._keys.hmac(space_id, TURN_HMAC, body.conversation_id.encode())
            scope = "handle" if handle_id else "object" if object_id else "conversation"
            hold = LegalHoldRecord(
                hold_id, scope, self._clock.now(), by, sealed, version, handle_id, object_id, conversation
            )
            stored = await uow.legal_holds.add(hold)
        return hold_out(stored)

    async def release(
        self, principal: Principal, hold_id: str, body: LegalHoldRelease, *, by: str
    ) -> LegalHold:
        """Releases the hold and hands `release_held` what erasures left waiting under it."""
        space_id = principal.space.space_id
        try:
            held_id = UUID(hold_id)
        except ValueError as exc:
            raise NotFound("unknown hold") from exc
        sealed, version = await self._keys.encrypt(
            space_id, body.reason.encode(), reason_aad(held_id, "release_reason_ct")
        )
        async with self._uow(space_id) as uow:
            released = await uow.legal_holds.release(
                held_id, at=self._clock.now(), by=by, reason_ct=pack_envelope(version, sealed)
            )
            if released is None:
                if await uow.legal_holds.get(held_id) is None:
                    raise NotFound("unknown hold")
                raise Conflict("the hold was released already")
            await uow.pipeline.enqueue([Task(TaskName.RELEASE_HELD, space_id, {"hold_id": hold_id})])
        return hold_out(released)

    async def held(self, space_id: UUID) -> Held:
        """What the space's active holds hold: one statement, and one more per held handle for the
        conversations it started, which hold its turn records."""
        conversations: dict[bytes, list[UUID]] = defaultdict(list)
        handles: dict[UUID, list[UUID]] = defaultdict(list)
        objects: dict[UUID, list[UUID]] = defaultdict(list)
        async with self._uow(space_id) as uow:
            active = await uow.legal_holds.active()
            names = {
                h.hold_id: await uow.ingest.conversation_names(
                    ErasureScope(uuid7(), TargetKind.HANDLE, (h.handle_id,), None, None, self._clock.now())
                )
                for h in active
                if h.handle_id is not None
            }
        for hold in active:
            if hold.conversation_hmac is not None:
                conversations[hold.conversation_hmac].append(hold.hold_id)
            if hold.object_id is not None:
                objects[hold.object_id].append(hold.hold_id)
            if hold.handle_id is None:
                continue
            handles[hold.handle_id].append(hold.hold_id)
            for name in names[hold.hold_id]:
                conversations[await self._keys.hmac(space_id, TURN_HMAC, name.encode())].append(hold.hold_id)
        return Held(
            {k: tuple(v) for k, v in conversations.items()},
            {k: tuple(v) for k, v in handles.items()},
            {k: tuple(v) for k, v in objects.items()},
        )

    async def held_conversations(self, space_id: UUID) -> frozenset[bytes]:
        return frozenset((await self.held(space_id)).conversations)


async def exported_holds(
    uow: UnitOfWork, keys: KeyVault, space_id: UUID, handles: Sequence[UUID]
) -> list[Any]:
    """The holds on the subject's handles and conversations: what they hold and when, never their reason,
    which is the controller's legal matter."""
    scope = ErasureScope(uuid7(), TargetKind.HANDLE, tuple(handles), None, None, EPOCH)
    names = await uow.ingest.conversation_names(scope) if handles else []
    conversations = [await keys.hmac(space_id, TURN_HMAC, name.encode()) for name in names]
    return [
        {
            "hold_id": str(h.hold_id),
            "scope": h.scope,
            "status": h.status,
            "created_at": h.created_at.isoformat(),
            "released_at": h.released_at.isoformat() if h.released_at else None,
        }
        for h in await uow.legal_holds.holds_on(handles, conversations)
    ]
