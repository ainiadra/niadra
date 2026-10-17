"""The withholding pass on PostgreSQL (`app/withhold.py`): rows memory stored before the rules
are read page by page as the system role, the ones that carry a document number or a misfiled diagnosis
are rewritten or deleted with a tombstone for the export, the rest stay as they were, and a second pass
changes nothing."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from niadra.adapters.memory.fakes import FixedClock, StaticConfig
from niadra.app.extract import rules_for, session_events
from niadra.app.withhold import KINDS, WithholdPass
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import (
    EpisodeDraft,
    ExtractionResult,
    ExtractionRun,
    FactCommand,
    FactOp,
    MemoryRef,
    RunStatus,
)
from niadra.domain.extract.withhold import WITHHOLD_VERSION
from niadra.domain.ingest.events import NewEvent
from niadra.domain.memory.facts import normalize_value
from niadra.domain.memory.values import SettledValue, ValueOrigin, ValueType
from niadra.domain.records import Episode, EventRecord, Fact, OpenItem, ProfileAttr
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind, FactStatus, OpenItemStatus, Sensitivity, Speaker
from niadra.testing.cell import sample_config
from tests.integration.conftest import CloningUnitOfWork

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
DOCUMENT = "82661763"
HANDLE = UUID(int=9)
SOURCE = UUID(int=1)


def _event(space: UUID, text_: str, n: int) -> NewEvent:
    at = NOW - timedelta(hours=1)
    record = EventRecord(
        event_id=uuid7(int(at.timestamp() * 1000) + n),
        kind=EventKind.MESSAGE,
        source_id=SOURCE,
        channel="whatsapp",
        speaker=Speaker.CUSTOMER,
        occurred_at=at,
        received_at=at,
        handle_ids=(HANDLE,),
        text=text_,
        conversation_id="old-1",
        content_meta={"type": "text"},
    )
    return NewEvent(space, record, f"old-{n}")


def _fact(value: str, predicate: str = "disputed_invoice", category: str = "billing") -> Fact:
    return Fact(
        fact_id=uuid7(),
        origin_handle_id=HANDLE,
        predicate=predicate,
        value=value,
        category=category,
        sensitivity=Sensitivity.NORMAL,
        speaker=Speaker.CUSTOMER,
        confidence=0.8,
        valid_at=NOW,
        created_at=NOW,
        status=FactStatus.ACTIVE,
        last_confirmed_at=NOW,
    )


def _episode(version: int, summary: str, episode_id: UUID, session_id: UUID) -> Episode:
    return Episode(
        episode_id=episode_id,
        version=version,
        origin_handle_id=HANDLE,
        session_id=session_id,
        channel="whatsapp",
        source_id=SOURCE,
        summary=summary,
        intent="complaint",
        category="invoice",
        normalized_category="billing",
        outcome="pending",
        started_at=NOW,
        ended_at=NOW,
        resolution=f"Documento {DOCUMENT} anotado." if version == 2 else None,
    )


def _value(raw: str, type_: ValueType, evidence: tuple[UUID, ...], session_id: UUID) -> SettledValue:
    return SettledValue(
        value_id=uuid7(),
        origin_handle_id=HANDLE,
        type=type_,
        raw=raw,
        said_at=NOW,
        speaker=Speaker.CUSTOMER,
        session_id=session_id,
        channel="whatsapp",
        source_id=SOURCE,
        created_at=NOW,
        evidence_event_ids=evidence,
        origin=ValueOrigin.EXTRACTION,
    )


async def _stored(pg_uow: CloningUnitOfWork, space: UUID) -> dict[str, Any]:
    async with pg_uow.system(space) as uow:
        runs = await uow.extract.runs_page(None, 50)
        return {
            "facts": sorted(f.value for f in await uow.memory.facts_page(None, 50)),
            "episodes": [
                (e.summary, e.resolution, e.category) for e in await uow.memory.episodes_page(None, 50)
            ],
            "items": [i.description for i in await uow.memory.items_page(None, 50)],
            "values": sorted(v.raw for v in await uow.memory.values_page(None, 50)),
            "attrs": [a.value for a in await uow.memory.attrs_page(None, 50)],
            "runs": [r.result.to_dict() if r.result else None for r in runs],
            "seq": (await uow.session.execute(text("SELECT max(change_seq) FROM facts"))).scalar(),
        }


async def _run_pass(service: WithholdPass, space: UUID) -> None:
    for kind in KINDS:
        payload = {"version": WITHHOLD_VERSION, "kind": kind, "after": None, "changed": 0}
        await service.page(Task(TaskName.WITHHOLD_STORED, space, payload))


async def test_the_pass_scrubs_old_rows_and_a_second_pass_changes_nothing(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    clock = FixedClock(NOW)
    config = StaticConfig(sample_config(space))
    service = WithholdPass(pg_uow, config, clock, rules_for, session_events)  # type: ignore[arg-type]
    said = _event(space, f"O número do meu documento é {DOCUMENT}. O protocolo é 45778204.", 1)
    evidence = (said.record.event_id,)
    session_id, episode_id = uuid7(), uuid7()
    alone, kept, misfiled = _fact(DOCUMENT), _fact("Bia", "preferred_name", "contact"), _fact("asma crônica")
    result = ExtractionResult(
        episode=EpisodeDraft(f"Cliente informou o documento {DOCUMENT}", "complaint", "invoice", "pending"),
        facts=(FactCommand(FactOp.ADD, "disputed_invoice", evidence, value=DOCUMENT, valid_at=NOW),),
    )
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([said])
        for f in (alone, kept, misfiled):
            assert await uow.memory.insert_fact(f, normalize_value(f.value))
        await uow.memory.add_mentions(_ref(alone), evidence)
        first = _episode(1, f"Cliente informou o documento {DOCUMENT}", episode_id, session_id)
        await uow.memory.add_episode_version(first)
        await uow.memory.add_episode_version(
            _episode(
                2, f"Cliente informou o documento {DOCUMENT}; protocolo 45778204", episode_id, session_id
            ),
            first,
        )
        await uow.memory.insert_open_item(
            OpenItem(
                item_id=uuid7(),
                origin_handle_id=HANDLE,
                description=f"Conferir o documento {DOCUMENT}",
                owner="company",
                status=OpenItemStatus.OPEN,
                created_at=NOW,
                origin={"session_id": str(session_id)},
            )
        )
        await uow.memory.insert_values(
            [
                _value(DOCUMENT, ValueType.CODE, evidence, session_id),
                _value("45778204", ValueType.PROTOCOL, evidence, session_id),
            ]
        )
        await uow.memory.put_attr(ProfileAttr(HANDLE, "preferred_channel", f"doc {DOCUMENT}", SOURCE, NOW))
        await uow.extract.save_run(ExtractionRun(session_id, "x1.p6.s1", RunStatus.OK, NOW, result=result))

    assert await service.watch(space) == 1
    assert await service.watch(space) == 0
    await _run_pass(service, space)
    once = await _stored(pg_uow, space)

    assert DOCUMENT not in repr({k: v for k, v in once.items() if k != "seq"})
    assert once["facts"] == ["Bia"]
    assert once["episodes"] == [
        ("Cliente informou o documento [retido:documento]", None, "invoice"),
        (
            "Cliente informou o documento [retido:documento]; protocolo 45778204",
            "Documento [retido:documento] anotado.",
            "invoice",
        ),
    ]
    assert once["items"] == ["Conferir o documento [retido:documento]"]
    assert once["values"] == ["45778204"]
    assert once["attrs"] == []
    [stored_result] = once["runs"]
    assert stored_result["facts"] == []
    assert stored_result["dropped"] == 1
    async with pg_uow.system(space) as uow:
        tombstones = (await uow.session.execute(text("SELECT count(*) FROM tombstones"))).scalar()
        work = (await uow.session.execute(text("SELECT count(*) FROM profile_work"))).scalar()
    assert tombstones is not None
    assert tombstones >= 2  # the two facts, for the continuous export
    assert work is not None

    await _run_pass(service, space)
    assert await _stored(pg_uow, space) == once


async def test_a_rewritten_fact_that_another_row_already_holds_is_refused(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    a, b = _fact("ID [retido:documento]"), _fact(f"ID {DOCUMENT}")
    async with pg_uow(space) as uow:
        for f in (a, b):
            await uow.memory.insert_fact(f, normalize_value(f.value))
    async with pg_uow.system(space) as uow:
        assert not await uow.memory.rewrite_fact(replace(b, value=a.value), normalize_value(a.value))
        moved = replace(b, value="ID [retido:documento] pendente")
        assert await uow.memory.rewrite_fact(moved, normalize_value(moved.value))
        found = await uow.memory.find_fact(HANDLE, "disputed_invoice", normalize_value(moved.value))
    assert found is not None
    assert found.fact_id == b.fact_id


def _ref(fact: Fact) -> MemoryRef:
    return MemoryRef(fact.origin_handle_id, fact.fact_id)
