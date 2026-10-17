"""A search of the same customer in the same call reads the rows once (`app.history_rows`): the next ones
replay them while the customer's generation holds, answer the same bytes, and a new generation (memory that
changed) is read again."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.adapters.memory.memory import InMemoryMemoryRepository
from niadra.app import history_rows
from niadra.contracts.common import Handle
from niadra.contracts.context import HistoryFilters, SearchRequest
from niadra.domain.common.errors import NotFound
from niadra.domain.vocabulary import Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_valid_until import offer, offers_space


class Counting:
    """Counts the reads of the customer's facts, one per search that reads its rows from the database."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.opened = 0
        real = InMemoryMemoryRepository.facts_for

        async def counted(repo: Any, *args: Any, **kwargs: Any) -> Any:
            self.opened += 1
            return await real(repo, *args, **kwargs)

        monkeypatch.setattr(InMemoryMemoryRepository, "facts_for", counted)


PHONE = {"type": "phone_e164", "value": "+5511944442222"}


async def said(flow: Flow, conversation: str, text: str) -> None:
    now = flow.cell.clock.now().isoformat()
    item = {
        "type": "event",
        "idempotency_key": f"{conversation}-1",
        "channel": "whatsapp",
        "conversation_id": conversation,
        "handles": [PHONE],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": text},
        "occurred_at": now,
    }
    ended = {"type": "conversation.ended", "idempotency_key": f"{conversation}-end",
             "conversation_id": conversation, "occurred_at": now}  # fmt: skip
    assert (await flow.send(WHATSAPP_SOURCE, item, ended)).errors == []
    await flow.drain()


async def settled(cell: Cell) -> Flow:
    # Offers are read without proof (`offers_space`): the searches below see the customer's rows.
    cell.config.put(offers_space())
    flow = Flow.build(cell, ScriptedLlm(offer(), second_offer()))
    await said(flow, "offer-1", "Quero a oferta de fibra 1 giga por 99 que vocês mandaram")
    # Past a first contact's first seconds, when the read process starts keeping who the handle is.
    cell.clock.advance(minutes=1)
    return flow


def second_offer() -> dict[str, Any]:
    answer = offer()
    answer["facts"][0] |= {"value": "oferta fibra 2 giga por 149"}
    return answer


def ask(**extra: Any) -> SearchRequest:
    return SearchRequest(subject=Handle.model_validate(PHONE), query="oferta fibra giga", **extra)


async def test_the_next_search_replays_the_rows_and_answers_the_same(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = await settled(cell)
    agent = principal(WHATSAPP_SOURCE, cell.config.spaces()[0])
    first = await flow.serve.search(agent, ask())
    assert first.items
    counting = Counting(monkeypatch)
    again = await flow.serve.search(agent, ask())
    assert again == first
    assert counting.opened == 0
    # Another view of the rows (expired items shown) is read, not replayed.
    await flow.serve.search(agent, ask(filters=HistoryFilters(show_expired=True)))
    assert counting.opened > 0


async def test_memory_that_changed_is_read_again(cell: Cell) -> None:
    flow = await settled(cell)
    agent = principal(WHATSAPP_SOURCE, cell.config.spaces()[0])
    before = await flow.serve.search(agent, ask())
    flow.cell.clock.advance(minutes=30)
    await said(flow, "offer-2", "E a oferta de fibra 2 giga por 149?")
    after = await flow.serve.search(agent, ask())
    assert {i.id for i in after.items} - {i.id for i in before.items}


async def test_rows_older_than_their_bound_are_read_again(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = await settled(cell)
    agent = principal(WHATSAPP_SOURCE, cell.config.spaces()[0])
    await flow.serve.search(agent, ask())
    monkeypatch.setattr(history_rows, "ROWS_FOR_S", -1.0)
    counting = Counting(monkeypatch)
    await flow.serve.search(agent, ask())
    assert counting.opened > 0


async def test_a_replay_of_a_call_nobody_recorded_misses() -> None:
    rows = history_rows.HistoryRows()
    replay = history_rows.Replay(rows.start(1, 1, datetime(2026, 9, 28, tzinfo=UTC)))
    with pytest.raises(history_rows.Missed):
        await replay.memory.facts_for((UUID(int=1),))


async def test_old_rows_still_serve_and_are_read_again_in_the_background(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = await settled(cell)
    agent = principal(WHATSAPP_SOURCE, cell.config.spaces()[0])
    first = await flow.serve.search(agent, ask())
    monkeypatch.setattr(history_rows, "RENEW_AFTER_S", -1.0)
    counting = Counting(monkeypatch)
    again = await flow.serve.search(agent, ask())
    assert again == first
    await flow.serve.drain()  # the renewal read the rows once, after the answer
    assert counting.opened > 0


class CountingVersions:
    """Counts the reads of an episode's versions, the one call an open makes beyond the customer's rows."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.read = 0
        real = InMemoryMemoryRepository.episode_versions

        async def counted(repo: Any, *args: Any, **kwargs: Any) -> Any:
            self.read += 1
            return await real(repo, *args, **kwargs)

        monkeypatch.setattr(InMemoryMemoryRepository, "episode_versions", counted)


CALL = "call-open"
PROVEN = {"verification": Verification.V2, "conversation_id": CALL}


async def episode_of(flow: Flow, agent: Any) -> str:
    """An episode of the customer (a billing conversation, read at V2), as a voice agent that proved the
    caller finds it."""
    at = flow.cell.clock.now().isoformat()
    proof = {"type": "verify", "idempotency_key": f"{CALL}-proof", "method": "otp_sms", "level": "V2",
             "conversation_id": CALL, "handle": PHONE, "occurred_at": at}  # fmt: skip
    assert (await flow.send(WHATSAPP_SOURCE, proof)).errors == []
    await flow.drain()
    found = await flow.serve.search(agent, ask(**PROVEN))
    return next(i.id for i in found.items if i.kind == "episode")


async def test_an_open_after_a_search_replays_the_rows_and_answers_as_the_database(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = await settled(cell)
    agent = principal(VOICE_SOURCE, cell.config.spaces()[0])
    item = await episode_of(flow, agent)
    subject = Handle.model_validate(PHONE)
    # The database's answer, read with no rows held.
    flow.serve._history._rows = history_rows.HistoryRows()
    from_database = await flow.serve.open_item(agent, item, subject=subject, **PROVEN)
    assert from_database.versions
    await flow.serve.search(
        agent, ask(**PROVEN)
    )  # records the rows, as the agent's search before an open does
    counting, versions = Counting(monkeypatch), CountingVersions(monkeypatch)
    first = await flow.serve.open_item(agent, item, subject=subject, **PROVEN)
    again = await flow.serve.open_item(agent, item, subject=subject, **PROVEN)
    assert first == again == from_database
    assert counting.opened == 0
    assert versions.read <= 1  # the episode's versions are read once, then kept with the rows


async def test_an_open_records_the_rows_for_the_next_opens(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = await settled(cell)
    agent = principal(VOICE_SOURCE, cell.config.spaces()[0])
    item = await episode_of(flow, agent)
    subject = Handle.model_validate(PHONE)
    flow.serve._history._rows = history_rows.HistoryRows()
    first = await flow.serve.open_item(agent, item, subject=subject, **PROVEN)
    counting, versions = Counting(monkeypatch), CountingVersions(monkeypatch)
    assert await flow.serve.open_item(agent, item, subject=subject, **PROVEN) == first
    assert (counting.opened, versions.read) == (0, 0)


async def test_an_open_without_the_customer_or_for_another_profile_reads_the_database(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = await settled(cell)
    agent = principal(VOICE_SOURCE, cell.config.spaces()[0])
    item = await episode_of(flow, agent)
    subject = Handle.model_validate(PHONE)
    await flow.serve.open_item(agent, item, subject=subject, **PROVEN)
    counting = Counting(monkeypatch)
    assert (await flow.serve.open_item(agent, item, **PROVEN)).id == item
    assert counting.opened == 1
    with pytest.raises(NotFound):
        await flow.serve.open_item(agent, item, subject=subject, profile_id=UUID(int=7), **PROVEN)
    assert counting.opened == 1  # the profile does not match before any row is read
    # An id the rows do not show is answered by the database, as any unknown id.
    with pytest.raises(NotFound):
        await flow.serve.open_item(agent, f"episode:{UUID(int=9)}", subject=subject, **PROVEN)


async def test_a_new_generation_is_opened_from_the_database(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = await settled(cell)
    agent = principal(VOICE_SOURCE, cell.config.spaces()[0])
    item = await episode_of(flow, agent)
    subject = Handle.model_validate(PHONE)
    await flow.serve.open_item(agent, item, subject=subject, **PROVEN)
    flow.cell.clock.advance(minutes=30)
    await said(flow, "offer-2", "E a oferta de fibra 2 giga por 149?")
    counting = Counting(monkeypatch)
    await flow.serve.open_item(agent, item, subject=subject, **PROVEN)
    assert counting.opened == 1


async def test_a_first_contacts_handle_is_kept_only_once_its_customer_settled(cell: Cell) -> None:
    """A handle first seen seconds ago may still be joining its customer's other handles (the WhatsApp id a
    first message brings, beside the phone a call verified): its reads resolve it again until it settled."""
    from niadra.app.serve_reads import SETTLING, _subject_key

    flow = await settled(cell)
    reads = flow.serve._reads
    space = cell.config.spaces()[0].space_id
    agent = principal(VOICE_SOURCE, cell.config.spaces()[0])
    newcomer = {"type": "phone_e164", "value": "+5511955553333"}
    at = cell.clock.now().isoformat()
    first = {"type": "event", "idempotency_key": "new-1", "channel": "voice", "conversation_id": "new-call",
             "handles": [newcomer], "speaker": {"role": "customer"},
             "content": {"type": "text", "text": "Oi"}, "occurred_at": at}  # fmt: skip
    assert (await flow.send(VOICE_SOURCE, first)).errors == []
    await flow.drain()
    handle = Handle.model_validate(newcomer)
    await flow.serve.search(agent, SearchRequest(subject=handle, query="oi"))
    assert reads.state.target(space, _subject_key(handle)) is None
    cell.clock.advance(seconds=SETTLING.total_seconds() + 1)
    await flow.serve.search(agent, SearchRequest(subject=handle, query="oi"))
    assert reads.state.target(space, _subject_key(handle)) is not None


async def test_a_renewal_keeps_the_versions_the_opens_read(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = await settled(cell)
    agent = principal(VOICE_SOURCE, cell.config.spaces()[0])
    item = await episode_of(flow, agent)
    subject = Handle.model_validate(PHONE)
    await flow.serve.open_item(agent, item, subject=subject, **PROVEN)
    monkeypatch.setattr(history_rows, "RENEW_AFTER_S", -1.0)
    await flow.serve.open_item(agent, item, subject=subject, **PROVEN)
    await flow.serve.drain()  # the rows were read again in the background, for the same generation
    versions = CountingVersions(monkeypatch)
    await flow.serve.open_item(agent, item, subject=subject, **PROVEN)
    assert versions.read == 0


async def test_a_search_with_times_written_without_an_offset_reads_them_in_the_spaces_zone(
    cell: Cell,
) -> None:
    # A model wrote `since` as "2026-10-01T00:00:00" in a tool call: compared with the rows' instants it
    # raised a TypeError and the search answered 500 (05/10/2026).
    flow = await settled(cell)
    agent = principal(WHATSAPP_SOURCE, cell.config.spaces()[0])
    said_at = cell.clock.now()
    before = said_at.replace(tzinfo=None, year=said_at.year - 1)
    after = said_at.replace(tzinfo=None, year=said_at.year + 1)
    found = await flow.serve.search(agent, ask(filters=HistoryFilters(since=before)))
    assert found.items
    none = await flow.serve.search(agent, ask(filters=HistoryFilters(since=after)))
    assert not none.items
