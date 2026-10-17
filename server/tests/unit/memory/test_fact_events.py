"""`fact.added` and `fact.retracted` (7.5): what memory learned or stopped holding, ids only."""

from dataclasses import replace

from niadra.config.models import WebhookEndpoint
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import ExtractionResult, FactCommand, FactOp, MemoryRef
from niadra.domain.records import EventRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind, Sensitivity, Speaker
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell
from tests.unit.memory.test_service import EPISODE, add, delivered, facts, reconcile, say, setup


def subscribe_agent(cell: Cell, *event_types: str) -> None:
    config = cell.config.space(SPACE_ID)
    endpoint = WebhookEndpoint(
        endpoint_id="agent-hooks",
        url="https://agent.example/hooks",
        source_id=WHATSAPP_SOURCE,
        event_types=list(event_types),
        secret_refs=["spaces/agent/webhook/agent-hooks"],
    )
    cell.config.put(config.model_copy(update={"webhook_endpoints": [endpoint]}))


async def test_extraction_announces_the_new_fact_and_the_one_it_replaced(cell: Cell) -> None:
    s = setup(cell)
    subscribe_agent(cell, "fact.added", "fact.retracted")
    profile = s.world.customer()
    first, a = say(s, profile, "me chame de Bya", days_ago=3)
    await reconcile(s, first, ExtractionResult(episode=EPISODE, facts=(add("Bya", a),)))
    [born] = delivered(cell, "fact.added")
    bya = (await facts(s, profile.handle_ids))["Bya"]
    data = born["data"]
    assert data["fact_id"] == str(bya.fact_id)
    assert (data["predicate"], data["status"], data["by"]) == ("preferred_name", "active", "extraction")
    assert data["replaces"] == []
    assert data["source_id"] == str(first.source_id)
    # Ids and labels only: the value the customer said stays home.
    assert "Bya" not in str(born)
    assert born["links"]["lineage"] == f"/v1/lineage/fact/{bya.fact_id}"

    # The same value seen again is not news.
    again, b = say(s, profile, "Bya mesmo", days_ago=2)
    await reconcile(s, again, ExtractionResult(episode=EPISODE, facts=(add("Bya", b),)))
    assert len(delivered(cell, "fact.added")) == 1

    third, c = say(s, profile, "agora e Bia", days_ago=1)
    retire = FactCommand(
        FactOp.RETIRE,
        "preferred_name",
        (c.event_id,),
        value="Bya",
        valid_at=c.occurred_at,
        target=MemoryRef(bya.origin_handle_id, bya.fact_id),
    )
    await reconcile(s, third, ExtractionResult(episode=EPISODE, facts=(retire, add("Bia", c))))
    bia = (await facts(s, profile.handle_ids))["Bia"]
    added = delivered(cell, "fact.added")[-1]["data"]
    [left] = delivered(cell, "fact.retracted")
    assert added["fact_id"] == str(bia.fact_id)
    assert added["replaces"] == [str(bya.fact_id)]
    assert left["data"]["fact_id"] == str(bya.fact_id)
    assert left["data"]["status"] == "retracted"
    assert left["data"]["replaced_by"] == [str(bia.fact_id)]


async def test_a_correction_is_announced_as_feedback(cell: Cell) -> None:
    s = setup(cell)
    subscribe_agent(cell, "*")
    profile = s.world.customer()
    session, said = say(s, profile, "me chame de Bya", days_ago=1)
    await reconcile(s, session, ExtractionResult(episode=EPISODE, facts=(add("Bya", said),)))
    bya = (await facts(s, profile.handle_ids))["Bya"]
    event = EventRecord(
        uuid7(),
        EventKind.SYSTEM_EVENT,
        WHATSAPP_SOURCE,
        "feedback",
        Speaker.SYSTEM,
        cell.clock.now(),
        cell.clock.now(),
        profile.handle_ids,
        conversation_id=session.conversation_id,
        canonical_type="feedback.correct_fact",
        fields={"action": "correct_fact", "fact_id": str(bya.fact_id), "value": "Bia"},
    )
    s.world.events.append(event)
    await s.memory.reconcile(
        Task(TaskName.RECONCILE, SPACE_ID, {"event_id": str(event.event_id)}, profile_id=profile.profile_id)
    )
    bia = (await facts(s, profile.handle_ids))["Bia"]
    corrected = delivered(cell, "fact.added")[-1]["data"]
    [retracted] = [d["data"] for d in delivered(cell, "fact.retracted")]
    assert (corrected["by"], corrected["event_id"]) == ("feedback", str(event.event_id))
    assert corrected["replaces"] == [str(bya.fact_id)]
    assert retracted["replaced_by"] == [str(bia.fact_id)]


async def test_nothing_is_read_or_sent_without_a_subscriber(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    session, said = say(s, profile, "me chame de Bya", days_ago=1)
    await reconcile(s, session, ExtractionResult(episode=EPISODE, facts=(add("Bya", said),)))
    assert delivered(cell, "fact.added") == []


async def test_a_fact_the_endpoints_source_may_not_read_is_not_announced_to_it(cell: Cell) -> None:
    s = setup(cell)
    subscribe_agent(cell, "fact.added")
    profile = s.world.customer()
    session, said = say(s, profile, "tomo remedio", days_ago=1)
    health = replace(add("losartana", said, "note"), category="health", sensitivity=Sensitivity.SENSITIVE)
    await reconcile(s, session, ExtractionResult(episode=EPISODE, facts=(health,)))
    assert (await facts(s, profile.handle_ids))["losartana"].category == "health"
    # Health is for care agents at V3; the WhatsApp support agent's endpoint hears nothing of it.
    assert delivered(cell, "fact.added") == []
