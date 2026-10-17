"""A contact window (the suppression-list spec, 6.1): a suppression that holds only in its local hours. "Não
liguem antes das 9h" defers a call at 07:00 to 09:00 and lets one at 12:00 go, on the voice channel only, for
every purpose; the list carries the window to every reader, a reload keeps it, and the hours a customer said
in a conversation reach their phone without a coordination document that declares anything."""

from datetime import UTC, datetime

import fakeredis
import pytest

from niadra.app.coordinate import STATED_SOURCE
from niadra.app.ingest import normalized
from niadra.domain.common.errors import InvalidInput
from niadra.domain.coordinate.window import Window
from niadra.domain.identity.handles import NewHandle
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import HandleStatus, SubjectKind
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, principal
from tests.unit.coordinate.support import PHONE, SAME_PHONE, World, make_world

BEFORE_NINE = {"from": "00:00", "to": "09:00", "tz": "America/Sao_Paulo", "days": None}
AT_SEVEN = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
"""07:00 in Sao Paulo."""
NINE = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
async def world() -> World:
    return await make_world()


async def not_before_nine(world: World) -> None:
    await world.declare(
        kind="suppression.added",
        subject=PHONE.model_dump(),
        detail={"purpose": "any", "channel": "voice", "reason": "opt_out", "window": BEFORE_NINE},
    )


async def test_a_window_defers_a_call_inside_its_hours_and_lets_it_go_outside(world: World) -> None:
    await not_before_nine(world)
    # 12:00 in Sao Paulo: outside the window, the call goes.
    noon = await world.check(subject=SAME_PHONE.model_dump(), channel="voice", intent="reply")
    assert (noon.decision, noon.reasons, noon.contact_window_until) == ("allow", [], None)
    world.cell.clock.at = AT_SEVEN
    early = await world.check(subject=SAME_PHONE.model_dump(), channel="voice", intent="reply")
    assert (early.decision, early.reasons, early.contact_window_until) == ("defer", ["contact_window"], NINE)
    # Every purpose, and only on the channel it names.
    collection = await world.check(subject=PHONE.model_dump(), channel="voice", purpose="transactional")
    assert (collection.decision, collection.reasons) == ("defer", ["contact_window"])
    whatsapp = await world.check(subject=PHONE.model_dump(), channel="whatsapp", intent="reply")
    assert "contact_window" not in whatsapp.reasons
    # A window never shows as a purpose suppressed at every hour, and never denies.
    assert early.suppressions == []
    world.cell.clock.at = NINE
    assert (await world.check(subject=PHONE.model_dump(), channel="voice")).decision == "allow"


async def test_the_list_carries_the_window_and_a_reload_keeps_it(world: World) -> None:
    await not_before_nine(world)
    await world.drain()
    page = await world.service.suppressions(principal(VOICE_SOURCE), None, 200)
    [entry] = page.items
    assert (entry.purpose, entry.channel) == ("any", "voice")
    assert entry.window is not None
    assert entry.window.model_dump(by_alias=True) == BEFORE_NINE
    await fakeredis.FakeAsyncRedis(server=world.server).flushall()
    world.cell.clock.at = AT_SEVEN
    world.advance(seconds=60)  # past the minute a reloaded store stays closed
    reloaded = await world.check(subject=PHONE.model_dump(), channel="voice", intent="reply")
    assert (reloaded.decision, reloaded.reasons) == ("defer", ["contact_window"])


async def test_a_window_that_is_not_one_is_refused(world: World) -> None:
    for window in (
        {**BEFORE_NINE, "to": "00:00", "from": "00:00"},
        {**BEFORE_NINE, "tz": "GMT-3"},
        {**BEFORE_NINE, "days": [0]},
    ):
        with pytest.raises((InvalidInput, ValueError)):
            await world.declare(
                kind="suppression.added",
                subject=PHONE.model_dump(),
                detail={"purpose": "any", "channel": "voice", "reason": "opt_out", "window": window},
            )


async def test_no_document_declares_a_purpose_named_any() -> None:
    from niadra.config.coordination import CoordinationSettings, PurposeRule
    from niadra.domain.coordinate.model import FailDirection

    with pytest.raises(ValueError, match="no purpose is named any"):
        CoordinationSettings(purposes=[PurposeRule(name="any", fail=FailDirection.OPEN)])


async def test_the_hours_a_customer_said_reach_each_of_their_destinations_and_a_lift_ends_them(
    world: World,
) -> None:
    async with world.cell.uow(SPACE_ID) as uow:
        records = await uow.identity.add_handles(
            [NewHandle(normalized(PHONE, SubjectKind.PERSON).key, SubjectKind.PERSON, HandleStatus.ACTIVE)],
            world.cell.clock.now(),
        )
    ids = [str(r.handle_id) for r in records]
    window = Window.said("09:00", None, "America/Sao_Paulo").to_json()
    await world.service.stated_window(
        Task(TaskName.CONTACT_WINDOW, SPACE_ID, {"handle_ids": ids, "channel": "voice", "window": window})
    )
    world.cell.clock.at = AT_SEVEN
    early = await world.check(
        subject=SAME_PHONE.model_dump(), channel="voice", purpose="marketing", intent="p"
    )
    assert (early.decision, early.reasons) == ("defer", ["contact_window"])
    await world.drain()
    first = await world.service.suppressions(principal(VOICE_SOURCE), None, 200)
    [entry] = first.items
    assert entry.window is not None
    assert entry.window.to == "09:00"
    # Said again, the same entry changes; lifted, the call goes.
    await world.service.stated_window(
        Task(TaskName.CONTACT_WINDOW, SPACE_ID, {"handle_ids": ids, "channel": "voice", "window": None})
    )
    later = await world.check(subject=PHONE.model_dump(), channel="voice", intent="reply")
    assert "contact_window" not in later.reasons
    await world.drain()
    page = await world.service.suppressions(principal(VOICE_SOURCE), first.next_cursor, 200)
    assert [(i.id, i.removed) for i in page.items] == [(entry.id, True)]


async def test_a_vendors_source_never_lifts_the_hours_a_customer_said(world: World) -> None:
    async with world.cell.uow(SPACE_ID) as uow:
        records = await uow.identity.add_handles(
            [NewHandle(normalized(PHONE, SubjectKind.PERSON).key, SubjectKind.PERSON, HandleStatus.ACTIVE)],
            world.cell.clock.now(),
        )
    window = Window.said("09:00", None, "America/Sao_Paulo").to_json()
    await world.service.stated_window(
        Task(
            TaskName.CONTACT_WINDOW,
            SPACE_ID,
            {"handle_ids": [str(r.handle_id) for r in records], "channel": "voice", "window": window},
        )
    )
    lifted = await world.declare(
        kind="suppression.lifted", subject=PHONE.model_dump(), detail={"purpose": "any", "channel": "voice"}
    )
    assert not lifted.accepted
    assert STATED_SOURCE.version == 7
