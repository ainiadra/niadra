"""Test doubles and builders for the write path."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from niadra.adapters.memory.intake import TransactionIntakeLog
from niadra.app.collaborators import AttachResult, PendingMerge
from niadra.app.ingest import IngestService
from niadra.app.sessions import SessionService
from niadra.contracts.common import Handle
from niadra.domain.common.errors import InvalidInput
from niadra.domain.common.ids import uuid7
from niadra.domain.identity.handles import NewHandle, normalize_handle
from niadra.domain.records import OpenItem
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, OpenItemStatus, SubjectKind, Verification
from niadra.testing.cell import SPACE_ID, Cell
from tests.unit.compile.factories import fact

BLOCKED = "+5511000000000"


def handle_id(handle: Handle) -> UUID:
    return uuid5(NAMESPACE_URL, f"{handle.type}:{handle.scope or ''}:{handle.value}")


@dataclass
class Attach:
    handles: tuple[Handle, ...]
    subject_kind: SubjectKind
    method: AssertionMethod
    origin_event_id: UUID | None
    level: Verification


@dataclass
class FakeIdentity:
    """Every handle is its own profile, except handles listed in `same` that share one."""

    calls: list[Attach] = field(default_factory=list)
    profiles: dict[UUID, UUID] = field(default_factory=dict)
    links: list[tuple[UUID, UUID]] = field(default_factory=list)
    merge: PendingMerge | None = None
    """What the next attach with `merge_after_commit` answers it wrote."""
    merged: list[PendingMerge] = field(default_factory=list)

    async def attach(
        self,
        uow: Any,
        handles: Sequence[Handle],
        *,
        subject_kind: SubjectKind,
        method: AssertionMethod,
        origin_event_id: UUID | None,
        level: Verification,
        at: datetime,
        merge_after_commit: bool = False,
    ) -> AttachResult:
        if any(h.value == BLOCKED for h in handles):
            raise InvalidInput("blocked handle value")
        self.calls.append(Attach(tuple(handles), subject_kind, method, origin_event_id, level))
        ids = tuple(dict.fromkeys(handle_id(h) for h in handles))
        profile = self.profiles.setdefault(ids[0], uuid5(NAMESPACE_URL, f"profile:{ids[0]}"))
        merge = self.merge if merge_after_commit else None
        return AttachResult(
            handle_ids=ids,
            profile_id=profile,
            pending_merge=merge is not None,
            anchor_handle_id=ids[0],
            merge=merge,
        )

    async def merge_now(self, space_id: UUID, merge: PendingMerge) -> bool:
        self.merged.append(merge)
        return True

    async def link_subjects(
        self,
        uow: Any,
        person_handle_id: UUID,
        org_handle_ids: Sequence[UUID],
        *,
        origin_event_id: UUID | None,
        at: datetime,
    ) -> None:
        self.links.extend((person_handle_id, org) for org in org_handle_ids)


def build(
    cell: Cell, identity: FakeIdentity | None = None
) -> tuple[IngestService, SessionService, FakeIdentity]:
    identity = identity or FakeIdentity()
    sessions = SessionService(cell.uow, cell.clock, cell.config)
    ingest = IngestService(
        cell.uow,
        identity,
        sessions,
        cell.keys,
        cell.hot,
        cell.clock,
        cell.config,
        cell.objects,
        cell.secrets,
        intake=TransactionIntakeLog(cell.uow),
        cache=cell.cache,
    )
    return ingest, sessions, identity


def message(key: str, text: str = "a fatura veio errada", **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "event",
        "idempotency_key": key,
        "channel": "whatsapp",
        "conversation_id": "conv-1",
        "handles": [{"type": "phone_e164", "value": "+5511988887777"}],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": text},
        "occurred_at": "2026-09-22T16:59:00Z",
    }
    item.update(overrides)
    return item


def stop_storing(ingest: IngestService) -> None:
    """Plays a process that answered batches and died before handing them to the workers."""
    for task in ingest._handing:
        task.cancel()
    ingest._handing.clear()


async def held(cell: Cell, handle: Handle, *, item: bool = False) -> str:
    """A fact (or, with `item`, an open item) the subject's memory holds, so a correction names something
    memory can apply; its id."""
    key = normalize_handle(handle.type, handle.value, handle.scope, handle.subject_kind).key
    now = cell.clock.now()
    async with cell.uow(SPACE_ID) as uow:
        found = await uow.identity.find_handle(key.type, key.scope, key.value)
        if found is None:
            [found] = await uow.identity.add_handles(
                [NewHandle(key, SubjectKind.PERSON, HandleStatus.ACTIVE)], now
            )
        profile = await uow.identity.profile_of(found.handle_id)
        if profile is None:
            profile = await uow.identity.create_profile(uuid7(), SubjectKind.PERSON, now)
            await uow.identity.set_membership([found.handle_id], profile.profile_id, 1)
        if item:
            noted = OpenItem(
                uuid7(), found.handle_id, "Enviar o boleto", "company", OpenItemStatus.OPEN, now, {}
            )
            await uow.memory.insert_open_item(noted)
            return str(noted.item_id)
        stored = fact(5, "preferred_name", "Ana", "contact", handle=found.handle_id)
        await uow.memory.insert_fact(stored, "ana")
        return str(stored.fact_id)
