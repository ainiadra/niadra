"""One id per item: whose it is, its content words, its due date and its object, and the conversation only
when nothing else pins it. The same promise said in other words or in another conversation of the same
person is the same item by construction; nothing is ever merged."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from niadra.contracts.events import Closes, FeedbackRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import OpenItemDraft
from niadra.domain.memory.items import item_id_for
from niadra.domain.records import ObjectKey
from niadra.domain.vocabulary import OpenItemStatus
from niadra.testing.cell import Cell
from tests.unit.memory.test_restated_items import CASE, FRIDAY, items_of, run
from tests.unit.memory.test_service import say, setup

TERM = "Protocolar manifestação sobre planta e termo de entrega"
REWORDED = "Protocolar manifestação sobre a planta e o termo de entrega"


def test_an_items_id_is_whose_it_is_its_content_words_its_due_date_and_its_object() -> None:
    person, session = uuid7(), str(uuid7())
    same = item_id_for(person, session, TERM, FRIDAY, CASE)
    assert same.version == 8
    assert item_id_for(person, session, REWORDED, FRIDAY, CASE) == same
    assert item_id_for(person, session, f"{TERM} até sexta-feira", FRIDAY, CASE) == same
    assert item_id_for(person, str(uuid7()), TERM, FRIDAY, CASE) == same  # another conversation
    assert (
        item_id_for(person, session, "Protocolar manifestação sobre o termo de entrega", FRIDAY, CASE) != same
    )
    assert item_id_for(person, session, TERM, FRIDAY + timedelta(days=1), CASE) != same
    assert item_id_for(person, session, TERM, FRIDAY, ObjectKey("legal_case", "firm", "case-2")) != same
    assert item_id_for(uuid7(), session, TERM, FRIDAY, CASE) != same  # another person
    assert item_id_for(person, session, "Ok.", None, None) != item_id_for(person, session, "Sim.", None, None)


def test_an_item_pinned_by_nothing_is_its_conversations() -> None:
    person = uuid7()
    pinned_by_object = item_id_for(person, "s1", TERM, None, CASE)
    pinned_by_date = item_id_for(person, "s1", TERM, FRIDAY, None)
    assert item_id_for(person, "s2", TERM, None, CASE) == pinned_by_object
    assert item_id_for(person, "s2", TERM, FRIDAY, None) == pinned_by_date
    assert item_id_for(person, "s2", "Ligar de volta", None, None) != item_id_for(
        person, "s1", "Ligar de volta", None, None
    )


async def test_a_reworded_reextraction_keeps_the_items_id(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    session, said = say(s, profile, "protocolo a manifestação sobre a planta e o termo de entrega até sexta")
    await run(s, session, "s1", OpenItemDraft(TERM, "customer", (said.event_id,), due_at=FRIDAY, object=CASE))
    [first] = await items_of(s, profile.handle_ids)
    assert first.item_id == item_id_for(said.handle_ids[0], str(session.session_id), TERM, FRIDAY, CASE)

    await run(
        s, session, "s2", OpenItemDraft(REWORDED, "customer", (said.event_id,), due_at=FRIDAY, object=CASE)
    )

    [again] = await items_of(s, profile.handle_ids)
    assert again.item_id == first.item_id


async def test_the_same_promise_in_another_conversation_is_the_same_item(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    first, said = say(s, profile, "protocolo a manifestação sobre planta e termo até sexta", days_ago=1)
    await run(s, first, "s1", OpenItemDraft(TERM, "customer", (said.event_id,), due_at=FRIDAY, object=CASE))
    recap, again = say(s, profile, "o que falta? a manifestação sobre a planta e o termo de entrega")
    await run(
        s, recap, "s1", OpenItemDraft(REWORDED, "customer", (again.event_id,), due_at=FRIDAY, object=CASE)
    )

    [item] = await items_of(s, profile.handle_ids)
    assert item.item_id == item_id_for(said.handle_ids[0], str(recap.session_id), REWORDED, FRIDAY, CASE)
    assert item.origin["session_id"] == str(first.session_id)


async def test_an_unpinned_request_said_again_another_day_is_a_new_one(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    first, said = say(s, profile, "me liga de volta", days_ago=7)
    await run(s, first, "s1", OpenItemDraft("Ligar de volta para o cliente", "company", (said.event_id,)))
    later, again = say(s, profile, "me liga de volta")
    await run(s, later, "s1", OpenItemDraft("Ligar de volta para o cliente", "company", (again.event_id,)))

    items = await items_of(s, profile.handle_ids, (OpenItemStatus.OPEN,))
    assert len({i.item_id for i in items}) == 2


def test_an_item_is_named_by_its_bare_id_only() -> None:
    subject = {"type": "phone_e164", "value": "+5511999990000"}
    item = uuid7()
    asked = FeedbackRequest.model_validate(
        {"idempotency_key": "k", "subject": subject, "action": "resolve_open_item", "open_item_id": str(item)}
    )
    assert asked.open_item_id == item
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(
            {
                "idempotency_key": "k",
                "subject": subject,
                "action": "resolve_open_item",
                "open_item_id": f"open_item:{item}",
            }
        )
    with pytest.raises(ValidationError):
        Closes.model_validate({"item_id": f"open_item:{item}"})
