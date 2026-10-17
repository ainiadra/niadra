"""The agents' working state (24.3.9; the agent-state spec) on the in-memory cell: compare-and-swap, merge by
key with removal, a state kept whole, the declared fields, masking, read-your-writes across the stores,
the batch writer and the retention of a field."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from niadra.adapters.memory.erasure import InMemoryErasureMarks
from niadra.adapters.memory.state import InMemoryAgentStateLog, InMemoryAgentStateStore
from niadra.app.agent_state import AgentStateService
from niadra.app.raw_index import sealed_tokens
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import AgentStateReadRequest, AgentStateWrite
from niadra.domain.common.errors import AgentStateConflict, InvalidInput
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.identity.handles import NewHandle, normalize_handle
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.privacy.holds import Held
from niadra.domain.privacy.raw_index import ERASED, handle_tokens
from niadra.domain.state.agent_state import DurableState
from niadra.domain.vocabulary import HandleStatus, HandleType, SubjectKind
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal

ASSISTANT = {
    "type": "closing",
    "ownership": "agent",
    "agent_state": {"scope": "conversation", "write": "both"},
    "fields": {
        "step": {"type": "enum"},
        "offer": {"type": "string"},
        "shortlist": {"type": "list"},
        "recipient_name": {"type": "string", "pii": True},
    },
    "retention": {"state": "30d", "shortlist": "1d"},
}
SCOPE = {"kind": "conversation", "id": "+5511900001234"}


class Fixture:
    def __init__(self, cell: Cell) -> None:
        space = cell.config.space(SPACE_ID)
        cell.config.put(
            space.model_copy(
                update={"object_types": ObjectTypeRegistry.model_validate({"types": [ASSISTANT]})}
            )
        )
        self.cell = cell
        self.store = InMemoryAgentStateStore()
        self.log = InMemoryAgentStateLog()
        self.marks = InMemoryErasureMarks()
        self.service = AgentStateService(
            cell.uow, self.store, cell.cache, self.log, cell.keys, cell.clock, cell.config, marks=self.marks
        )
        self.caller: SourcePrincipal = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))

    async def put(self, mode: str, body: dict[str, Any], if_version: int | None = None, **extra: Any) -> Any:
        write = {"scope": SCOPE, "agent": "closing", "mode": mode, "body": body, "if_version": if_version}
        return await self.service.write(self.caller, AgentStateWrite.model_validate(write | extra))

    async def get(self, agent: str = "closing") -> Any:
        return await self.service.read(self.caller, AgentStateReadRequest(scope=SCOPE, agent=agent))  # type: ignore[arg-type]


@pytest.fixture
def f(cell: Cell) -> Fixture:
    return Fixture(cell)


async def test_a_state_never_written_reads_empty_at_version_zero(f: Fixture) -> None:
    state = await f.get()
    assert (state.body, state.version, state.updated_at) == ({}, 0, None)


async def test_compare_and_swap_creates_then_moves_only_from_the_version_read(f: Fixture) -> None:
    created = await f.put("cas", {"step": "quote", "offer": "family"}, if_version=0)
    assert (created.stored, created.version) == (True, 1)
    with pytest.raises(AgentStateConflict):
        await f.put("cas", {"step": "close"}, if_version=0)
    moved = await f.put("cas", {"step": "close"}, if_version=1)
    assert moved.version == 2
    assert (await f.get()).body == {"step": "close"}  # a field left out of a compare-and-swap is gone


async def test_merge_by_key_keeps_what_others_wrote_and_a_removal_stays_removed(f: Fixture) -> None:
    await f.put("merge_by_key", {"step": "quote", "offer": "family"})
    await f.put("merge_by_key", {"shortlist": ["p1", "p2"]})  # another sub-agent, another field
    await f.put("merge_by_key", {"offer": {"$delete": True}, "step": "close"})
    await f.put("merge_by_key", {"shortlist": ["p1"]})
    state = await f.get()
    assert state.body == {"step": "close", "shortlist": ["p1"]}
    assert state.version == 4


async def test_a_large_state_is_stored_whole_and_reaches_the_database(f: Fixture) -> None:
    """Memory never cuts: a write past what used to be the type's cap (16 KB by default) dropped
    the new state and kept the previous one."""
    await f.put("merge_by_key", {"step": "quote"})
    large = await f.put("merge_by_key", {"offer": "x" * 70_000})
    assert (large.stored, large.version, large.reason) == (True, 2, None)
    assert (await f.get()).body == {"step": "quote", "offer": "x" * 70_000}
    assert len(f.log.pending) == 2


async def test_a_field_the_schema_does_not_declare_is_refused_after_the_version(f: Fixture) -> None:
    await f.put("cas", {"step": "quote"}, if_version=0)
    refused = await f.put("merge_by_key", {"mood": "happy"})
    assert (refused.stored, refused.version, refused.reason) == (False, 1, "not_declared_field")
    with pytest.raises(AgentStateConflict):
        await f.put("cas", {"mood": "happy"}, if_version=0)


async def test_personal_data_is_masked_before_it_is_stored(f: Fixture) -> None:
    card = "cartao 4111 1111 1111 1111"
    await f.put("merge_by_key", {"recipient_name": card, "offer": card})
    body = (await f.get()).body
    assert "4111 1111 1111" not in body["recipient_name"]
    assert body["offer"] == card  # only the fields the schema marks as personal data


async def test_a_read_sees_its_write_and_the_stores_fall_back_in_order(f: Fixture) -> None:
    await f.put("merge_by_key", {"step": "quote"})
    await f.put("merge_by_key", {"step": "close"})
    assert (await f.get()).version == 2
    # The cache lost the copy: the state store answers, and the copy comes back.
    f.cell.cache.values.clear()
    assert (await f.get()).body == {"step": "close"}
    assert f.cell.cache.values
    # Both stores lost it: the database answers, once the batch writer stored it.
    await f.service.run_writer()
    assert f.log.acked == 2
    f.cell.cache.values.clear()
    f.store.states.clear()
    restored = await f.get()
    assert (restored.body, restored.version) == ({"step": "close"}, 2)
    # A write after the loss continues from the version the database had.
    assert (await f.put("merge_by_key", {"offer": "family"})).version == 3


async def test_the_durable_message_is_sealed(f: Fixture) -> None:
    await f.put("merge_by_key", {"offer": "family plan"})
    [durable] = f.log.pending
    assert b"family plan" not in durable.body_ct
    assert durable.version == 1
    assert (durable.valid_until - durable.updated_at).days == 30


@pytest.mark.parametrize(("retention", "kept_days"), [(None, None), ("400d", 400)])
async def test_a_state_is_kept_as_long_as_its_type_names_and_until_it_names_a_retention(
    cell: Cell, retention: str | None, kept_days: int | None
) -> None:
    """Memory never cuts: a type that names no retention keeps its states until it names one, and
    a named one is kept whole. They went 30 days after the last write, and a named one was cut to 180 days."""
    f = Fixture(cell)
    named = {**ASSISTANT, "retention": {"shortlist": "1d"} | ({"state": retention} if retention else {})}
    space = cell.config.space(SPACE_ID)
    cell.config.put(
        space.model_copy(update={"object_types": ObjectTypeRegistry.model_validate({"types": [named]})})
    )
    await f.put("merge_by_key", {"step": "quote"})
    [durable] = f.log.pending
    if kept_days is None:
        assert durable.valid_until is None
    else:
        assert durable.valid_until is not None
        assert (durable.valid_until - durable.updated_at).days == kept_days
    async with cell.uow(SPACE_ID) as uow:
        await uow.agent_state.upsert([durable])
    async with cell.uow(SPACE_ID) as uow:
        assert await uow.agent_state.expire(durable.updated_at + timedelta(days=365), limit=10) == 0


async def test_a_mode_the_type_does_not_allow_is_refused(cell: Cell) -> None:
    f = Fixture(cell)
    only_merge = {**ASSISTANT, "agent_state": {**ASSISTANT["agent_state"], "write": "merge_by_key"}}  # type: ignore[dict-item]
    space = cell.config.space(SPACE_ID)
    cell.config.put(
        space.model_copy(update={"object_types": ObjectTypeRegistry.model_validate({"types": [only_merge]})})
    )
    with pytest.raises(InvalidInput):
        await f.put("cas", {"step": "quote"}, if_version=0)


async def test_a_field_leaves_the_state_once_its_own_retention_passed(f: Fixture) -> None:
    await f.put("merge_by_key", {"step": "quote", "shortlist": ["p1"]})
    f.cell.clock.advance(days=2)
    assert (await f.get()).body == {"step": "quote"}
    await f.put("merge_by_key", {"offer": "family"})
    assert set((await f.get()).body) == {"step", "offer"}


async def test_a_customer_under_a_legal_hold_keeps_their_working_states_past_retention(cell: Cell) -> None:
    held, free = uuid7(), uuid7()
    at = cell.clock.now()

    def state(owner: Any) -> DurableState:
        return DurableState(
            SPACE_ID,
            owner.bytes,
            "conversation",
            WHATSAPP_SOURCE,
            "closing",
            1,
            b"x",
            1,
            1,
            at,
            at,
            None,
            None,
            owner,
        )

    async with cell.uow(SPACE_ID) as uow:
        await uow.agent_state.upsert([state(held), state(free)])
    async with cell.uow(SPACE_ID) as uow:
        later = at + timedelta(seconds=1)
        older = await uow.agent_state.older_than(later, limit=10, keep={held})
        assert [s.origin_handle_id for s in older] == [free]
        assert await uow.agent_state.expire(at, limit=10, keep={held}) == 1
        [left] = await uow.agent_state.older_than(later, limit=10)
    assert left.origin_handle_id == held


def _erasure(
    kind: TargetKind, *, handles: tuple[Any, ...] = (), conversation: str | None = None
) -> ErasureScope:
    return ErasureScope(uuid7(), kind, handles, None, conversation, datetime(2026, 9, 29, tzinfo=UTC))


async def test_an_erasure_while_a_write_waits_in_the_queue_leaves_no_row_after_the_flush(f: Fixture) -> None:
    await f.put("merge_by_key", {"step": "quote", "offer": "family"})
    assert f.log.pending  # the batch writer has not drained it yet
    assert f.store.states
    scope = _erasure(TargetKind.CONVERSATION, conversation=SCOPE["id"])
    await f.service.fence(SPACE_ID, scope)
    # The copies go with the fence: a read no longer serves the erased state.
    assert f.store.states == {}
    assert not [k for k in f.cell.cache.values if k.startswith("asc:")]
    async for batch in f.log.batches(500):
        await f.service._write_batch(batch.states)
        await batch.ack()
    await f.service.purge(SPACE_ID, scope)
    async with f.cell.uow(SPACE_ID) as uow:
        assert await uow.agent_state.older_than(f.cell.clock.now() + timedelta(days=1), limit=10) == []
    assert (await f.get()).version == 0


async def test_an_erasure_of_the_customer_skips_their_states_on_the_way_and_keeps_the_others(
    f: Fixture,
) -> None:
    async with f.cell.uow(SPACE_ID) as uow:
        key = normalize_handle(HandleType.PHONE_E164, "+5511900005678").key
        await uow.identity.add_handles(
            [NewHandle(key, SubjectKind.PERSON, HandleStatus.ACTIVE)], f.cell.clock.now()
        )
        handle = await uow.identity.find_handle(key.type, key.scope, key.value)
    assert handle is not None
    subject = {"type": "phone_e164", "value": "+5511900005678"}
    await f.put("merge_by_key", {"step": "quote"}, subject=subject)
    other = {"kind": "conversation", "id": "another-conversation"}
    write = {"scope": other, "agent": "closing", "mode": "merge_by_key", "body": {"step": "close"}}
    await f.service.write(f.caller, AgentStateWrite.model_validate(write))
    scope = _erasure(TargetKind.HANDLE, handles=(handle.handle_id,))
    await f.service.fence(SPACE_ID, scope)
    async for batch in f.log.batches(500):
        await f.service._write_batch(batch.states)
        await batch.ack()
    await f.service.purge(SPACE_ID, scope)
    async with f.cell.uow(SPACE_ID) as uow:
        left = await uow.agent_state.older_than(f.cell.clock.now() + timedelta(days=1), limit=10)
    assert [s.subject_key for s in left] == [None]


async def test_a_state_written_after_the_erasure_is_new_activity_and_lands(f: Fixture) -> None:
    await f.put("merge_by_key", {"step": "quote"})
    scope = _erasure(TargetKind.CONVERSATION, conversation=SCOPE["id"])
    await f.service.fence(SPACE_ID, scope)
    await f.service.purge(SPACE_ID, scope)
    f.cell.clock.advance(seconds=5)
    await f.put("merge_by_key", {"step": "new offer"})
    async for batch in f.log.batches(500):
        await f.service._write_batch(batch.states)
        await batch.ack()
    async with f.cell.uow(SPACE_ID) as uow:
        left = await uow.agent_state.older_than(f.cell.clock.now() + timedelta(days=1), limit=10)
    # The state queued before the erasure was dropped; the one written after it is the only row.
    assert [s.updated_at for s in left] == [f.cell.clock.now()]


CLARA_EMAIL = "clara@exemplo.com"


async def clara(f: Fixture) -> tuple[Any, frozenset[str], frozenset[bytes]]:
    """A person the other customers' states name, with the tokens an erasure looks her up by."""
    async with f.cell.uow(SPACE_ID) as uow:
        key = normalize_handle(HandleType.EMAIL, CLARA_EMAIL).key
        await uow.identity.add_handles(
            [NewHandle(key, SubjectKind.PERSON, HandleStatus.ACTIVE)], f.cell.clock.now()
        )
        handle = await uow.identity.find_handle(key.type, key.scope, key.value)
    assert handle is not None
    plain = handle_tokens([(HandleType.EMAIL, CLARA_EMAIL)])
    return handle, plain, await sealed_tokens(f.cell.keys, SPACE_ID, plain)


async def stored_body(f: Fixture) -> dict[str, Any]:
    async with f.cell.uow(SPACE_ID) as uow:
        [state] = await uow.agent_state.older_than(f.cell.clock.now() + timedelta(days=1), limit=10)
    aad = f"agent_state:{state.row}:body_ct".encode()
    document = json.loads(await f.cell.keys.decrypt(SPACE_ID, state.body_ct, aad, state.dek_version))
    return dict(document["fields"])


async def test_another_customers_stored_state_that_names_the_person_is_redacted(f: Fixture) -> None:
    await f.put("merge_by_key", {"step": "quote", "offer": f"enviar para {CLARA_EMAIL}"})
    await f.service.run_writer()
    handle, plain, tokens = await clara(f)
    scope = _erasure(TargetKind.HANDLE, handles=(handle.handle_id,))

    done = await f.service.redact_named(SPACE_ID, scope, plain, tokens, Held())

    assert done.rows == 1
    assert (await stored_body(f))["offer"] == f"enviar para {ERASED}"
    assert (await f.get()).body["offer"] == f"enviar para {ERASED}", "the copies went; the read takes the row"


async def test_a_state_on_its_way_when_the_person_was_erased_is_stored_redacted(f: Fixture) -> None:
    await f.put("merge_by_key", {"step": "quote", "offer": f"enviar para {CLARA_EMAIL}"})
    assert f.log.pending
    handle, _, _ = await clara(f)
    await f.service.fence(SPACE_ID, _erasure(TargetKind.HANDLE, handles=(handle.handle_id,)))

    await f.service.run_writer()

    assert (await stored_body(f)) == {"step": "quote", "offer": f"enviar para {ERASED}"}


async def test_a_state_on_its_way_reads_redacted_right_after_the_erasure(f: Fixture) -> None:
    """The state was queued when the erasure ran: before the writer stores it, the copies the reads take
    already name no erased identifier, at the version written."""
    await f.put("merge_by_key", {"step": "quote", "offer": f"enviar para {CLARA_EMAIL}"})
    assert f.log.pending
    handle, plain, tokens = await clara(f)
    scope = _erasure(TargetKind.HANDLE, handles=(handle.handle_id,))
    await f.service.fence(SPACE_ID, scope)

    await f.service.redact_named(SPACE_ID, scope, plain, tokens, Held())

    read = await f.get()
    assert (read.body["offer"], read.version) == (f"enviar para {ERASED}", 1)
    f.cell.cache.values.clear()  # the state store's copy, not only the cache's
    again = await f.get()
    assert (again.body["offer"], again.version) == (f"enviar para {ERASED}", 1)
    await f.service.run_writer()
    assert (await stored_body(f))["offer"] == f"enviar para {ERASED}"


async def test_a_newer_state_on_its_way_is_redacted_without_going_back_to_the_stored_row(f: Fixture) -> None:
    """The database holds version 1 and version 2 waits in the queue: the erasure redacts both, and the
    reads keep version 2, never the older row the database had."""
    await f.put("merge_by_key", {"step": "quote", "offer": f"enviar para {CLARA_EMAIL}"})
    await f.service.run_writer()
    await f.put("merge_by_key", {"step": "close"})
    handle, plain, tokens = await clara(f)
    scope = _erasure(TargetKind.HANDLE, handles=(handle.handle_id,))
    await f.service.fence(SPACE_ID, scope)

    await f.service.redact_named(SPACE_ID, scope, plain, tokens, Held())

    read = await f.get()
    assert (read.body, read.version) == ({"step": "close", "offer": f"enviar para {ERASED}"}, 2)
