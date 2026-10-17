"""Memory bound to an object on PostgreSQL: the rows about an object across the handles of
its participants, read through the two partial indexes, and an erasure that names the objects it touched."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import BusinessObject, Episode, Fact, ObjectKey, OpenItem
from niadra.domain.vocabulary import (
    FactStatus,
    MemoryAudience,
    OpenItemKind,
    OpenItemStatus,
    Sensitivity,
    Speaker,
)
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
CASE = ObjectKey("lawsuit", "court", "case-1")
OTHER = ObjectKey("lawsuit", "court", "case-2")


def fact(handle: UUID, value: str, about: ObjectKey | None, audience: MemoryAudience | None) -> Fact:
    return Fact(
        fact_id=uuid7(),
        origin_handle_id=handle,
        predicate="observation",
        value=value,
        category="object",
        sensitivity=Sensitivity.NORMAL,
        speaker=Speaker.CUSTOMER,
        confidence=0.9,
        valid_at=NOW,
        created_at=NOW,
        status=FactStatus.ACTIVE,
        last_confirmed_at=NOW,
        object_ref=about,
        audience=audience,
    )


def item(handle: UUID, text: str, about: ObjectKey | None, kind: OpenItemKind) -> OpenItem:
    return OpenItem(
        item_id=uuid7(),
        origin_handle_id=handle,
        description=text,
        owner="customer",
        status=OpenItemStatus.OPEN,
        created_at=NOW,
        origin={},
        object=about,
        kind=kind,
        audience=MemoryAudience.SHARED if about else None,
    )


async def test_the_rows_about_an_object_come_from_every_participant_and_name_it_on_erasure(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    lawyer, client, stranger = uuid4(), uuid4(), uuid4()
    async with pg_uow(SPACE_ID) as uow:
        obj = await uow.objects.add_object(BusinessObject(uuid7(), CASE, {}, NOW, WHATSAPP_SOURCE, client))
        await uow.objects.add_participants(obj.object_id, [(lawyer, "lawyer"), (stranger, "participant")])
        await uow.memory.insert_fact(fact(lawyer, "a tese é fraca", CASE, MemoryAudience.INTERNAL), "a\\x1f1")
        await uow.memory.insert_fact(fact(client, "chaves entregues", CASE, MemoryAudience.SHARED), "c\\x1f1")
        await uow.memory.insert_fact(fact(lawyer, "outro caso", OTHER, MemoryAudience.SHARED), "o\\x1f2")
        await uow.memory.insert_fact(fact(lawyer, "solto", None, None), "s")
        await uow.memory.insert_open_item(item(lawyer, "Mandar os extratos", CASE, OpenItemKind.PROMISE))
        await uow.memory.insert_open_item(item(client, "Cobrança contestada", CASE, OpenItemKind.DISPUTE))
        await uow.memory.insert_open_item(item(lawyer, "Sem objeto", None, OpenItemKind.REQUEST))
        episode = Episode(
            episode_id=uuid7(),
            version=1,
            origin_handle_id=lawyer,
            session_id=uuid7(),
            channel="app",
            source_id=WHATSAPP_SOURCE,
            summary="Orientou sobre o processo",
            intent="other",
            category="other",
            normalized_category="other",
            outcome="resolved",
            started_at=NOW - timedelta(minutes=5),
            ended_at=NOW,
            object_refs=(CASE,),
        )
        await uow.memory.add_episode_version(episode)

    async with pg_uow(SPACE_ID) as uow:
        # A handle with a role of its own before a bare participant, whatever their ids.
        parts = await uow.objects.participants_of([obj.object_id], per_object=1)
        assert parts == {obj.object_id: [(lawyer, "lawyer")]}
        assert await uow.objects.participant_count(obj.object_id) == 2
        handles = [lawyer, client]
        facts = await uow.memory.object_facts([CASE], handles, per_object=20)
        assert sorted(f.value for f in facts) == ["a tese é fraca", "chaves entregues"]
        assert {f.audience for f in facts} == {MemoryAudience.INTERNAL, MemoryAudience.SHARED}
        items = await uow.memory.object_items([CASE], handles, per_object=20)
        assert sorted((i.description, i.kind) for i in items) == [
            ("Cobrança contestada", OpenItemKind.DISPUTE),
            ("Mandar os extratos", OpenItemKind.PROMISE),
        ]
        [stored] = await uow.memory.episodes_for([lawyer])
        assert stored.object_refs == (CASE,)

    scope = ErasureScope(uuid7(), TargetKind.HANDLE, (lawyer,), None, None, NOW)
    async with pg_uow(SPACE_ID) as uow:
        erased = []
        while batch := await uow.memory.erase_for(scope, limit=50):
            erased += batch
    bound = [e.object_ref for e in erased if e.object_ref is not None]
    assert sorted(bound) == ["lawsuit:court:case-1", "lawsuit:court:case-1", "lawsuit:court:case-2"]


async def test_items_kept_since_a_time_come_after_the_open_ones_and_older_ones_never(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    # 05/10/2026: the court fees were paid and the lawyer's promise to pay them closed, but an object's line
    # said only the open items, so the client's agent answered that the receipt had not come (Q10).
    lawyer = uuid4()
    open_one = item(lawyer, "Mandar o comprovante ao financeiro", CASE, OpenItemKind.PROMISE)
    kept_one = replace(
        item(lawyer, "Recolher as custas do preparo", CASE, OpenItemKind.PROMISE),
        status=OpenItemStatus.RESOLVED,
        closed_at=NOW - timedelta(days=1),
    )
    long_ago = replace(
        item(lawyer, "Juntar a procuração", CASE, OpenItemKind.PROMISE),
        status=OpenItemStatus.RESOLVED,
        closed_at=NOW - timedelta(days=30),
    )
    async with pg_uow(SPACE_ID) as uow:
        for each in (kept_one, open_one, long_ago):
            await uow.memory.insert_open_item(each)
    async with pg_uow(SPACE_ID) as uow:
        only_open = await uow.memory.object_items([CASE], [lawyer], per_object=20)
        assert [i.description for i in only_open] == ["Mandar o comprovante ao financeiro"]
        both = await uow.memory.object_items(
            [CASE], [lawyer], per_object=20, kept_since=NOW - timedelta(days=7)
        )
        assert [(i.description, i.status) for i in both] == [
            ("Mandar o comprovante ao financeiro", OpenItemStatus.OPEN),
            ("Recolher as custas do preparo", OpenItemStatus.RESOLVED),
        ]
