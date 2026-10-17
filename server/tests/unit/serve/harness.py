"""A read process over the in-memory cell, with the other modules' ports faked (World)."""

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from niadra.adapters.memory.pipeline import InMemoryTaskStore
from niadra.app.compile import CompileService
from niadra.app.read_state import ReadState
from niadra.app.serve import ServeService, SlotSource
from niadra.app.serve_reads import RECEIPT_TIMEOUT_S
from niadra.contracts.common import Handle
from niadra.domain.ingest.hot import hot_aad, pack_envelope
from niadra.domain.records import HandleRecord
from niadra.domain.serve.cache_keys import CacheEpoch
from niadra.domain.serve.receipt_buffer import ReceiptBuffer
from niadra.domain.serve.subject_token import SubjectTokens
from niadra.domain.spaces.directory import DbStatus
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import HandleStatus, HandleType, SubjectKind, Verification
from niadra.ports.infra import HotTurn
from niadra.testing.cell import SPACE_ID, Cell, PatchedUnitOfWorkFactory
from tests.unit.compile.factories import PHONE, PROFILE, T0, WA, marina
from tests.unit.compile.fakes import RecordingReceipts, World

PHONE_VALUE = "+5511999990001"
SUBJECT = Handle(type=HandleType.PHONE_E164, value="+55 11 99999-0001")
SECRET = b"s" * 32


def handle_record(
    handle_id: UUID,
    type: HandleType,
    value: str,
    *,
    scope: str = "",
    kind: SubjectKind = SubjectKind.PERSON,
    level: Verification = Verification.V1,
    days: int = 400,
) -> HandleRecord:
    seen = T0 - timedelta(days=days)
    return HandleRecord(handle_id, type, scope, kind, value, HandleStatus.ACTIVE, level, seen, seen)


def default_world() -> World:
    # The conversation being served proved V1: the customer wrote from the number (2.2).
    return World(
        snapshots=[marina()],
        handles=[
            handle_record(PHONE, HandleType.PHONE_E164, PHONE_VALUE),
            handle_record(WA, HandleType.WA_ID, "5511999990001", days=30),
        ],
        proven=Verification.V1,
    )


class ReceiptSpool:
    """The outbox as the read path sees it: receipts' tasks can be refused, as when the database flaps."""

    def __init__(self, cell: Cell) -> None:
        self._store = InMemoryTaskStore(cell.uow.store)
        self.down = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._store, name)

    async def enqueue(self, tasks: Sequence[Task]) -> None:
        if self.down and any(t.name is TaskName.RECORD_RECEIPTS for t in tasks):
            raise ConnectionError("database unreachable")
        await self._store.enqueue(tasks)


class Harness:
    def __init__(
        self,
        cell: Cell,
        world: World | None = None,
        *,
        capacity: int = 10_000,
        receipt_timeout_s: float = RECEIPT_TIMEOUT_S,
        slot_sources: Sequence[SlotSource] = (),
        read_state: ReadState | None = None,
    ) -> None:
        self.cell = cell
        self.world = world or default_world()
        self.receipts = RecordingReceipts()
        self.epoch = CacheEpoch()
        self.buffer = ReceiptBuffer(capacity)
        self.spool = ReceiptSpool(cell)
        self.uow = PatchedUnitOfWorkFactory(cell.uow, **self.world.repos(), pipeline=self.spool)
        self.compiler = CompileService(
            self.uow, cell.keys, cell.cache, cell.clock, cell.config, hot=cell.hot, epoch=self.epoch
        )
        self.serve = ServeService(
            self.uow,
            cell.cache,
            cell.hot,
            cell.keys,
            self.receipts,
            self.compiler,
            cell.models,
            cell.clock,
            cell.config,
            "read-test",
            tokens=SubjectTokens(SECRET),
            epoch=self.epoch,
            buffer=self.buffer,
            databases=cell.databases,
            receipt_timeout_s=receipt_timeout_s,
            slot_sources=slot_sources,
            read_state=read_state,
        )
        # This process serves the space, so its privacy feed is followed.
        cell.databases.statuses[SPACE_ID] = DbStatus.READY

    async def compile(self, profile_id: UUID = PROFILE) -> None:
        await self.compiler.compile(Task(TaskName.COMPILE, SPACE_ID, profile_id=profile_id))

    async def add_turn(
        self,
        handle_id: UUID,
        *,
        minutes_ago: int,
        text: str,
        conversation_id: str = "wa-9",
        visibility: str = "public",
        event: int = 1,
    ) -> UUID:
        event_id = UUID(int=event)
        at: datetime = T0 - timedelta(minutes=minutes_ago)
        doc = {
            "event_id": str(event_id),
            "kind": "message",
            "source_id": str(UUID(int=99)),
            "channel": "whatsapp",
            "speaker": "customer",
            "occurred_at": at.isoformat(),
            "visibility": visibility,
            "text": text,
            "conversation_id": conversation_id,
        }
        ciphertext, version = await self.cell.keys.encrypt(
            SPACE_ID, json.dumps(doc).encode(), hot_aad(SPACE_ID, event_id)
        )
        turn = HotTurn(event_id, int(at.timestamp() * 1000), pack_envelope(version, ciphertext))
        await self.cell.hot.add(SPACE_ID, handle_id, [turn], cap_ms=72 * 3600 * 1000)
        return event_id

    def outbox(self, name: TaskName) -> list[Task]:
        return [t for t in self.cell.uow.store.module("pipeline").get("outbox", []) if t.name == name]
