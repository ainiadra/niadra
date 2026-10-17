"""The ingest boundary on PostgreSQL (WP-A): the stored event is the masked one, its counts sit in the
clear `content_meta`, and the pass over what was stored before rewrites the rows as the system role,
bumping `change_seq` so the continuous export sends them again."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from niadra.adapters.memory.fakes import (
    DictSecrets,
    FixedClock,
    InMemoryHotLayer,
    InMemoryObjectStore,
    StaticConfig,
)
from niadra.app.ingest import IngestService
from niadra.app.sessions import SessionService
from niadra.contracts.ingest import validate_items
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.events import NewEvent
from niadra.domain.memory.values import find_values
from niadra.domain.records import EventRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind, Speaker
from niadra.testing.cell import principal, sample_config
from tests.integration.conftest import CloningUnitOfWork
from tests.unit.ingest.fakes import FakeIdentity

NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)
PAN = "4111111111111111"


def _service(pg_uow: CloningUnitOfWork, space: UUID, clock: FixedClock) -> IngestService:
    config = StaticConfig(sample_config(space))
    return IngestService(
        pg_uow,  # type: ignore[arg-type]
        FakeIdentity(),  # type: ignore[arg-type]
        SessionService(pg_uow, clock, config),  # type: ignore[arg-type]
        pg_uow._keys,
        InMemoryHotLayer(),
        clock,
        config,
        InMemoryObjectStore(),
        DictSecrets(),  # type: ignore[arg-type]
    )


def _message(key: str, text_: str, role: str = "customer") -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": key,
        "channel": "whatsapp",
        "conversation_id": "wa-1",
        "handles": [{"type": "phone_e164", "value": "+5511922221111"}],
        "speaker": {"role": role},
        "content": {"type": "text", "text": text_},
        "occurred_at": NOW.isoformat(),
    }


async def test_the_stored_event_is_the_masked_one_and_the_protocol_survives(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    service = _service(pg_uow, space, FixedClock(NOW))
    items, errors = validate_items(
        [
            _message("m1", "Qual o número do cartão?", role="ai_agent"),
            _message("m2", PAN),
            _message("m3", "cvv 123. O protocolo é 45778204"),
        ]
    )
    assert not errors
    config = sample_config(space)
    response = await service.ingest_items(principal(config=config), items)

    assert response.masked == {"card": 1, "cvv": 1}
    async with pg_uow(space) as uow:
        stored = await uow.ingest.events_between(
            conversation_id="wa-1", since=NOW - timedelta(minutes=1), until=NOW + timedelta(minutes=1)
        )
        counts = await uow.ingest.masked_counts(conversation_id="wa-1", task_id=None)
        metas = (await uow.session.execute(text("SELECT content_meta FROM events"))).scalars().all()
    texts = sorted(e.text or "" for e in stored)
    assert texts == [
        "Qual o número do cartão?",
        "[retido:cartão ****1111]",
        "cvv [retido:cvv]. O protocolo é 45778204",
    ]
    assert counts == {"card": 1, "cvv": 1}
    assert sorted(str(m.get("masked")) for m in metas if m and m.get("masked")) == [
        "{'card': 1}",
        "{'cvv': 1}",
    ]
    [protocol] = find_values(next(t for t in texts if "protocolo" in t))
    assert (protocol.type.value, protocol.raw) == ("protocol", "45778204")


def _old(space: UUID, text_: str, n: int) -> NewEvent:
    at = NOW - timedelta(hours=1)
    record = EventRecord(
        event_id=uuid7(int(at.timestamp() * 1000) + n),
        kind=EventKind.MESSAGE,
        source_id=UUID(int=1),
        channel="whatsapp",
        speaker=Speaker.CUSTOMER,
        occurred_at=at,
        received_at=at,
        handle_ids=(UUID(int=9),),
        text=text_,
        conversation_id="old-1",
        fields={"cvv": "321"} if n == 2 else {},
        content_meta={"type": "text"},
    )
    return NewEvent(space, record, f"old-{n}")


async def test_the_pass_rewrites_old_rows_as_the_system_role(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    service = _service(pg_uow, space, FixedClock(NOW))
    old = [_old(space, "Qual o número do cartão?", 0), _old(space, PAN, 1), _old(space, "ok", 2)]
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events(old)
        seqs = dict((await uow.session.execute(text("SELECT event_id, change_seq FROM events"))).all())

    assert await service.watch_masking(space) == 1
    async with pg_uow.system(space) as uow:
        state = await uow.ingest.masking_state()
        [row] = (
            await uow.session.execute(text("SELECT payload FROM outbox WHERE task = 'mask_events'"))
        ).all()
    assert state is not None
    await service.mask_events(Task(TaskName.MASK_EVENTS, space, dict(row.payload)))
    assert await service.watch_masking(space) == 0

    async with pg_uow(space) as uow:
        after = await uow.ingest.get_events([e.record.event_id for e in old])
        moved = dict((await uow.session.execute(text("SELECT event_id, change_seq FROM events"))).all())
    by_id = {e.event_id: e for e in after}
    assert by_id[old[0].record.event_id].text == "Qual o número do cartão?"
    assert by_id[old[1].record.event_id].text == "[retido:cartão ****1111]"
    assert by_id[old[1].record.event_id].content_meta == {"type": "text", "masked": {"card": 1}}
    assert by_id[old[2].record.event_id].fields == {"cvv": "[retido:cvv]"}
    # Only the masked rows moved in the export's order.
    assert moved[old[0].record.event_id] == seqs[old[0].record.event_id]
    assert moved[old[1].record.event_id] > seqs[old[1].record.event_id]
    assert moved[old[2].record.event_id] > seqs[old[2].record.event_id]
