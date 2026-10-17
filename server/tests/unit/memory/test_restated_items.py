"""A session extracted again says its items again, often in other words: the new item updates the one it
restates instead of adding a twin (B21)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import ExtractionResult, ExtractionRun, OpenItemDraft, RunStatus
from niadra.domain.extract.rules import EXTRACTOR_VERSION, PROMPT_VERSION
from niadra.domain.memory.items import item_words, restates
from niadra.domain.records import EventRecord, ObjectKey, OpenItem, SessionRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import OpenItemStatus
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, Cell
from tests.unit.extract.world import Services, add_action
from tests.unit.memory.test_service import EPISODE, INVOICE, say, setup

CASE = ObjectKey("legal_case", "firm", "case-1")
OTHER_CASE = ObjectKey("legal_case", "firm", "case-2")
FRIDAY = datetime(2026, 10, 9, 23, 59, tzinfo=UTC)
EVERY = (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE, OpenItemStatus.RESOLVED)


def earlier(description: str, event: UUID, **fields: object) -> OpenItem:
    item = OpenItem(
        item_id=uuid7(),
        origin_handle_id=uuid7(),
        description=description,
        owner="customer",
        status=OpenItemStatus.OPEN,
        created_at=datetime(2026, 10, 3, 2, 28, tzinfo=UTC),
        origin={"session_id": str(uuid7()), "event_ids": [str(event)]},
    )
    return replace(item, **fields)  # type: ignore[arg-type]


def test_item_words_leave_out_numbers_and_function_words() -> None:
    # The words that say when are the item's due date, never what it is.
    assert item_words("Protocolar a réplica até sexta-feira, 09/10/2026.") == {"protocolar", "replica"}
    assert item_words("Protocolar a manifestação amanhã de manhã.") == {"protocolar", "manifestacao"}
    assert item_words("Enviar habite-se para juntada aos autos") == {"enviar", "habite", "juntada", "autos"}


def test_a_shorter_or_longer_wording_of_the_same_item_restates_it() -> None:
    turn = uuid7()
    replica = earlier("Protocolar a réplica até sexta-feira, 09/10/2026.", turn, due_at=FRIDAY, object=CASE)
    assert restates("Protocolar a réplica.", (turn,), FRIDAY, CASE, replica)
    summary = earlier("Preparar resumo para audiência de instrução após receber o termo", turn, object=CASE)
    # The twin the integrator saw had no object: an object on one side only sets nothing apart.
    assert restates("Preparar resumo para audiência após receber o termo", (turn,), None, None, summary)


def test_items_that_differ_in_what_they_are_about_never_restate_each_other() -> None:
    turn = uuid7()
    plan = earlier("Enviar planta do imóvel para juntada aos autos", turn)
    assert not restates("Enviar habite-se para juntada aos autos", (turn,), None, None, plan)
    replica = earlier("Protocolar a réplica.", turn, due_at=FRIDAY, object=CASE)
    assert not restates("Protocolar a réplica.", (uuid7(),), FRIDAY, CASE, replica)  # no turn in common
    assert not restates("Protocolar a réplica.", (turn,), FRIDAY + timedelta(days=3), CASE, replica)
    assert not restates("Protocolar a réplica.", (turn,), FRIDAY, OTHER_CASE, replica)


async def run(s: Services, session: SessionRecord, run_key: str, *drafts: OpenItemDraft) -> None:
    """One extraction of the session applied to memory; another `run_key` is a re-extraction."""
    key = f"{EXTRACTOR_VERSION}.{PROMPT_VERSION}.{run_key}"
    result = ExtractionResult(episode=EPISODE, open_items=drafts)
    async with s.cell.uow(SPACE_ID) as uow:
        await uow.extract.save_run(
            ExtractionRun(session.session_id, key, RunStatus.OK, s.cell.clock.now(), result=result)
        )
    payload = {"session_id": str(session.session_id), "versions_key": key}
    await s.memory.reconcile(Task(TaskName.RECONCILE, SPACE_ID, payload, profile_id=session.profile_id))


async def items_of(
    s: Services, handles: tuple[UUID, ...], statuses: tuple[OpenItemStatus, ...] = EVERY
) -> list[OpenItem]:
    async with s.cell.uow(SPACE_ID) as uow:
        return sorted(await uow.memory.open_items_for(handles, statuses), key=lambda i: i.created_at)


async def tombstones(s: Services) -> list[str]:
    async with s.cell.uow(SPACE_ID) as uow:
        return [t.key["item_id"] for t in await uow.privacy.tombstones_between(SPACE_ID, 0, None)]


async def seed(
    s: Services, session: SessionRecord, said: EventRecord, description: str, **fields: object
) -> OpenItem:
    """An item an earlier extraction of the session wrote, the way it stored it."""
    item = OpenItem(
        item_id=uuid7(),
        origin_handle_id=said.handle_ids[0],
        description=description,
        owner="customer",
        status=OpenItemStatus.OPEN,
        created_at=s.cell.clock.now(),
        origin={
            "session_id": str(session.session_id),
            "source_id": str(session.source_id),
            "event_ids": [str(said.event_id)],
            "noted_at": said.occurred_at.isoformat(),
        },
    )
    item = replace(item, **fields)  # type: ignore[arg-type]
    async with s.cell.uow(SPACE_ID) as uow:
        assert await uow.memory.insert_open_item(item)
    return item


async def test_a_reextraction_tags_the_item_it_restates_instead_of_adding_a_twin(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    session, said = say(s, profile, "vou protocolar a réplica até sexta-feira, 09/10/2026")
    first = OpenItemDraft(
        "Protocolar a réplica até sexta-feira, 09/10/2026.", "customer", (said.event_id,), due_at=FRIDAY
    )
    await run(s, session, "s1", first)
    [before] = await items_of(s, profile.handle_ids)

    again = OpenItemDraft(
        "Protocolar a réplica.", "customer", (said.event_id,), object=CASE, expected_operation="file"
    )
    await run(s, session, "s2", again)

    [after] = await items_of(s, profile.handle_ids)
    assert after.item_id == before.item_id
    assert after.description == before.description  # what the agents already saw
    assert (after.expected_operation, after.object, after.due_at) == ("file", CASE, FRIDAY)
    assert after.status is OpenItemStatus.OPEN


async def test_an_item_that_restates_two_items_updates_the_oldest_and_leaves_the_other(cell: Cell) -> None:
    """Two items told apart when written stay two: merging them would lose a promise."""
    s = setup(cell)
    profile = s.world.customer()
    session, said = say(s, profile, "vou enviar a planta e o termo até sexta-feira, 09/10/2026")
    plan = await seed(s, session, said, "Enviar a planta", due_at=FRIDAY)
    cell.clock.advance(hours=2)
    term = await seed(s, session, said, "Enviar o termo", due_at=FRIDAY)

    both = OpenItemDraft(
        "Enviar a planta e o termo", "customer", (said.event_id,), due_at=FRIDAY, expected_operation="send"
    )
    await run(s, session, "s2", both)

    first, second = await items_of(s, profile.handle_ids)
    assert (first.item_id, first.expected_operation) == (plan.item_id, "send")
    assert (second.item_id, second.expected_operation) == (term.item_id, None)
    assert await tombstones(s) == []


async def test_items_of_one_turn_about_different_things_stay_apart(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    session, said = say(s, profile, "envio a planta e o habite-se para juntar aos autos")
    plan = OpenItemDraft("Enviar planta do imóvel para juntada aos autos", "customer", (said.event_id,))
    await run(s, session, "s1", plan)

    permit = OpenItemDraft("Enviar habite-se para juntada aos autos", "customer", (said.event_id,))
    await run(s, session, "s2", replace(plan, expected_operation="deliver_document"), permit)

    found = await items_of(s, profile.handle_ids)
    assert sorted((i.description, i.expected_operation or "") for i in found) == [
        ("Enviar habite-se para juntada aos autos", ""),
        ("Enviar planta do imóvel para juntada aos autos", "deliver_document"),
    ]


async def test_a_closed_item_said_again_stays_closed(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    session, said = say(s, profile, "vou protocolar a réplica")
    closed = await seed(
        s, session, said, "Protocolar a réplica.", status=OpenItemStatus.RESOLVED, closed_at=cell.clock.now()
    )
    await run(s, session, "s2", OpenItemDraft("Protocolar a réplica", "customer", (said.event_id,)))

    [item] = await items_of(s, profile.handle_ids)
    assert (item.item_id, item.status) == (closed.item_id, OpenItemStatus.RESOLVED)


async def test_an_item_that_learns_its_operation_closes_on_what_was_already_done(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    session, said = say(s, profile, "contesto a 0823")
    await run(
        s, session, "s1", OpenItemDraft("Creditar a fatura 0823", "company", (said.event_id,), object=INVOICE)
    )
    credited = add_action(
        s.world, INVOICE, "credit", said.occurred_at + timedelta(minutes=1), source_id=BILLING_SOURCE
    )

    again = OpenItemDraft(
        "Creditar a fatura 0823", "company", (said.event_id,), object=INVOICE, expected_operation="credit"
    )
    await run(s, session, "s2", again)

    [item] = await items_of(s, profile.handle_ids)
    assert item.status is OpenItemStatus.RESOLVED
    assert item.closed_by_action_id == credited.action_id


async def test_the_nets_generic_item_takes_the_words_of_the_item_that_carries_its_deadline(
    cell: Cell,
) -> None:
    """juridico-sandbox, 03/10/2026: the session's first extraction wrote no item for the deadline, so the net
    wrote "Prazo pedido pelo cliente"; the next one writes the item that carries it, which becomes that item,
    under its id, with its words and a vector made again from them."""
    s = setup(cell)
    profile = s.world.customer()
    session, said = say(s, profile, "preciso protocolar a contestação até sexta-feira, 09/10/2026")
    generic = await seed(
        s,
        session,
        said,
        "Prazo pedido pelo cliente (Prazo da contestação)",
        due_at=FRIDAY + timedelta(hours=3),
    )
    vectors = cell.uow.store.module("memory")["vectors"]
    vectors[("open_items", generic.origin_handle_id, generic.item_id)] = ((0.1, 0.2), "enc", 2)

    real = OpenItemDraft("Deixar pronta a minuta da contestação", "customer", (said.event_id,), due_at=FRIDAY)
    await run(s, session, "s2", real)

    [kept] = await items_of(s, profile.handle_ids)
    assert (kept.item_id, kept.description) == (generic.item_id, "Deixar pronta a minuta da contestação")
    assert ("open_items", generic.origin_handle_id, generic.item_id) not in vectors


async def test_the_nets_item_stays_when_it_alone_carries_the_deadline(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    session, said = say(s, profile, "preciso disso até sexta-feira, 09/10/2026")
    generic = await seed(s, session, said, "Prazo pedido pelo cliente", due_at=FRIDAY)
    await run(s, session, "s2")
    [kept] = await items_of(s, profile.handle_ids)
    assert kept.item_id == generic.item_id


async def test_a_conversation_going_over_pending_promises_adds_no_twin(cell: Cell) -> None:
    """juridico-sandbox, 03/10/2026: a later conversation listed the person's pending promises again, and each
    became a second item of another session, same case, same date."""
    s = setup(cell)
    profile = s.world.customer()
    first, said = say(
        s, profile, "envio a planta do imóvel para juntar aos autos até quarta, 07/10", days_ago=1
    )
    original = await seed(
        s, first, said, "Enviar planta do imóvel para juntada aos autos", due_at=FRIDAY, object=CASE
    )
    recap, again = say(s, profile, "o que falta? a planta do imóvel para juntada, até quarta")
    draft = OpenItemDraft(
        "Enviar planta do imóvel para juntada aos autos",
        "customer",
        (again.event_id,),
        due_at=FRIDAY,
        object=CASE,
        expected_operation="deliver_document",
    )
    await run(s, recap, "s1", draft)

    [kept] = await items_of(s, profile.handle_ids)
    assert kept.item_id == original.item_id
    assert kept.expected_operation == "deliver_document"


async def test_items_of_two_conversations_with_other_deadlines_stay_apart(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    first, said = say(s, profile, "vou mandar o documento até sexta", days_ago=1)
    await seed(s, first, said, "Enviar o documento", due_at=FRIDAY)
    later, again = say(s, profile, "vou mandar o documento na outra semana")
    draft = OpenItemDraft(
        "Enviar o documento", "customer", (again.event_id,), due_at=FRIDAY + timedelta(days=5)
    )
    await run(s, later, "s1", draft)
    assert len(await items_of(s, profile.handle_ids)) == 2


async def test_a_word_more_across_conversations_is_another_promise(cell: Cell) -> None:
    """juridico-sandbox, 03/10/2026: "Protocolar manifestação sobre planta e termo de entrega", the item the
    integrator held, went as a twin of "Protocolar manifestação sobre o termo de entrega" of another
    conversation, same case, due a day apart: two filings. Neither goes now, and neither takes the other's
    date."""
    s = setup(cell)
    profile = s.world.customer()
    first, said = say(s, profile, "protocolo a manifestação sobre o termo de entrega até quinta", days_ago=1)
    thursday = FRIDAY - timedelta(days=1)
    term = await seed(
        s, first, said, "Protocolar manifestação sobre o termo de entrega", due_at=thursday, object=CASE
    )
    later, again = say(s, profile, "protocolo a manifestação sobre a planta e o termo de entrega até sexta")
    both = "Protocolar manifestação sobre planta e termo de entrega"
    await run(s, later, "s1", OpenItemDraft(both, "customer", (again.event_id,), due_at=FRIDAY, object=CASE))
    found = {i.description: i for i in await items_of(s, profile.handle_ids)}
    assert set(found) == {term.description, both}
    assert found[term.description].due_at == thursday


async def test_a_pending_promise_said_again_without_its_case_is_the_same_item(cell: Cell) -> None:
    """juridico-sandbox, 03/10/2026: a conversation going over what is pending named no case, and "Protocolar
    a manifestação amanhã de manhã." came back as a new item next to the one of the case, due the same day."""
    s = setup(cell)
    profile = s.world.customer()
    first, said = say(s, profile, "protocolo a manifestação amanhã de manhã", days_ago=1)
    text = "Protocolar a manifestação amanhã de manhã."
    original = await seed(s, first, said, text, due_at=FRIDAY, object=CASE)
    recap, again = say(s, profile, "o que falta? protocolar a manifestação amanhã de manhã")
    draft = OpenItemDraft(text, "customer", (again.event_id,), due_at=FRIDAY, expected_operation="file")
    await run(s, recap, "s1", draft)
    [kept] = await items_of(s, profile.handle_ids)
    assert (kept.item_id, kept.object, kept.expected_operation) == (original.item_id, CASE, "file")


async def test_a_restated_item_keeps_its_values_and_fills_only_what_it_lacked(cell: Cell) -> None:
    """The kept item took 10-09 from another conversation's item (juridico-sandbox, 03/10/2026): its own due
    date, object and operation now stay; only what it lacked is filled."""
    s = setup(cell)
    profile = s.world.customer()
    session, said = say(s, profile, "vou protocolar a réplica até quinta, 08/10/2026")
    thursday = FRIDAY - timedelta(days=1)
    kept = await seed(s, session, said, "Protocolar a réplica.", due_at=thursday, expected_operation="file")
    draft = OpenItemDraft(
        "Protocolar a réplica",
        "customer",
        (said.event_id,),
        due_at=FRIDAY,
        object=CASE,
        expected_operation="revise",
    )
    await run(s, session, "s2", draft)
    [item] = await items_of(s, profile.handle_ids)
    assert (item.item_id, item.due_at, item.expected_operation, item.object) == (
        kept.item_id,
        thursday,
        "file",
        CASE,
    )
