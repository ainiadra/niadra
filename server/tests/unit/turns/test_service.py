"""Turn records end to end against the in-memory cell (24.3.5): the write path, the worker that adds them
up, promotion, the viewer's reads and the kept tier's search."""

import copy
from datetime import timedelta
from typing import Any
from uuid import uuid4

import orjson
import pytest

from niadra.app.data_issues import DataIssuesService
from niadra.config.models import WebhookEndpoint
from niadra.contracts.turns import PromoteRequest, TurnSearchRequest
from niadra.domain.common.errors import NotFound, Unavailable
from niadra.domain.common.space import PersonPrincipal
from niadra.domain.ingest.hot import unpack_envelope
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.tasks import TaskName
from niadra.domain.turns.digest import digest
from niadra.domain.turns.issues import IssueQuery
from niadra.domain.turns.records import TakenIntake, intake_aad
from niadra.domain.turns.storage import HMAC_PURPOSE, Tier, by_id_key, record_aad, turn_key
from niadra.domain.vocabulary import ReceiptKind, ReceiptSurface
from niadra.entrypoints.setup import OBJECT_EXPIRY_DAYS
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.turns.conftest import World
from tests.unit.turns.records import QUOTE_RESULT, turn

T1, T2, T3 = (f"0192f5a0-7c3e-7a10-9d2b-5e61a4c0d00{i}" for i in (1, 2, 3))


async def _opened(world: World, key: str, turn_id: str) -> dict[str, Any]:
    version, sealed = unpack_envelope(world.cell.objects.objects[key])
    envelope: dict[str, Any] = orjson.loads(
        await world.cell.keys.decrypt(SPACE_ID, sealed, record_aad(turn_id), version)
    )
    return envelope


async def _conversation(world: World, conversation_id: str) -> bytes:
    return await world.cell.keys.hmac(SPACE_ID, HMAC_PURPOSE, conversation_id.encode())


def _daily(world: World) -> list[Any]:
    return list(world.cell.uow.store.module("turns")["daily"].values())


async def test_a_stored_turn_is_written_once_its_frame_added_up_and_handed_to_the_modules(
    world: World,
) -> None:
    result = await world.service.record(world.caller, [turn(T1)])

    assert (result.accepted, result.duplicates, result.errors) == (1, 0, [])
    conversation = await _conversation(world, "c-8812")
    short = turn_key(Tier.SHORT, SPACE_ID, conversation, T1)
    assert world.turn_objects() == sorted([short, by_id_key(SPACE_ID, T1)])
    assert "c-8812" not in "".join(world.turn_objects()), "no key names a conversation"
    stored = await _opened(world, short, T1)
    assert stored["record"]["blobs"]["b:2"]["content"] == QUOTE_RESULT
    assert stored["source_id"] == str(WHATSAPP_SOURCE)
    [row] = _daily(world)
    assert (row.turns, row.tool_calls, row.model_calls, row.tokens_in, row.cost_micros) == (
        1,
        1,
        1,
        2953,
        36_000,
    )
    assert row.claims == {"price|matched": 1}
    [applied] = world.taken.turns
    assert applied.record.turn_id == T1
    assert all(b.content is None for b in applied.record.blobs.values()), "a module never gets a value"
    assert applied.record.calls[0].observations[0].fields == {"price_full": 511.06}
    async with world.cell.uow(SPACE_ID) as uow:
        assert (await uow.turns.take_intake(10) or TakenIntake([])).rows == []
    assert world.cell.uow.store.module("pipeline")["outbox"] == []


async def test_the_same_turn_sent_twice_is_one_turn(world: World) -> None:
    first = await world.service.record(world.caller, [turn(T1), turn(T1), turn(T2)])
    again = await world.service.record(world.caller, [turn(T1), turn(T3)])

    assert (first.accepted, first.duplicates) == (2, 1)
    assert (again.accepted, again.duplicates) == (1, 1)
    assert sum(row.turns for row in _daily(world)) == 3
    assert [t.record.turn_id for t in world.taken.turns] == [T1, T2, T3]


async def test_the_pointer_mode_never_writes_a_value_to_niadra(world: World) -> None:
    # The canary lives only in the company's bucket: Niadra gets its pointer and its digest, nothing else.
    world.configure(content_mode="pointer", sample_rate=0)
    canary = {"secret_clause": "CANARY-7f3a91e2 the firm's own draft"}
    pointed = turn(T1, mode="pointer", result=canary, flags=["handoff"])
    assert "CANARY-7f3a91e2" not in orjson.dumps(pointed).decode()

    accepted = await world.service.record(world.caller, [pointed])
    leaked = turn(T2, mode="stored", result=canary)
    refused = await world.service.record(world.caller, [leaked])

    assert accepted.accepted == 1
    assert (refused.accepted, [e.code for e in refused.errors]) == (0, ["content_mode_refused"])
    conversation = await _conversation(world, "c-8812")
    for tier in (Tier.SHORT, Tier.KEPT):
        stored = await _opened(world, turn_key(tier, SPACE_ID, conversation, T1), T1)
        assert all("content" not in b for b in stored["record"]["blobs"].values())
        assert stored["record"]["blobs"]["b:2"]["pointer"] == "s3://company-turns/r.json"
        assert stored["record"]["blobs"]["b:2"]["sha256"] == digest(canary)[0]
    assert not any(T2 in key for key in world.turn_objects())
    for key, data in world.cell.objects.objects.items():
        assert b"CANARY-7f3a91e2" not in data, key
    for key in world.turn_objects():
        if key.endswith(".bin"):
            opened = await _opened(world, key, key.rsplit("/", 1)[1].removesuffix(".bin"))
            assert "CANARY-7f3a91e2" not in orjson.dumps(opened).decode()


async def test_a_source_keeps_its_mode_or_less_and_hash_only_keeps_no_value(world: World) -> None:
    world.configure(content_mode="stored", sample_rate=0)
    result = await world.service.record(world.caller, [turn(T1, mode="pointer"), turn(T2, mode="hash_only")])
    assert (result.accepted, result.errors) == (2, [])
    conversation = await _conversation(world, "c-8812")
    only_hashes = await _opened(world, turn_key(Tier.SHORT, SPACE_ID, conversation, T2), T2)
    assert set(only_hashes["record"]["blobs"]["b:1"]) == {"sha256", "size"}


async def test_a_stored_value_must_match_its_digest_and_is_masked_like_events(world: World) -> None:
    forged = turn(T1)
    forged["blobs"]["b:2"]["content"] = {"price_full": 1.0}
    card = {"card_number": "4111 1111 1111 1111", "plan": "family"}
    masked = turn(T2, result=card)

    result = await world.service.record(world.caller, [forged, masked])

    assert [(e.index, e.code) for e in result.errors] == [(0, "blob_digest_mismatch")]
    stored = await _opened(
        world, turn_key(Tier.SHORT, SPACE_ID, await _conversation(world, "c-8812"), T2), T2
    )
    kept = stored["record"]["blobs"]["b:2"]
    assert "4111" not in orjson.dumps(kept).decode()
    assert kept["content"]["plan"] == "family"
    assert (kept["sha256"], kept["size"]) == digest(kept["content"]), "the digest of what is kept"
    assert stored["record"]["calls"][0]["args_hash"] == turn(T2)["calls"][0]["args_hash"]


async def test_a_malformed_turn_is_one_error_and_names_no_value(world: World) -> None:
    broken = turn(T1, agent={"name": ""}, conversation_id=None)
    result = await world.service.record(world.caller, [broken, turn(T2)])
    [error] = result.errors
    assert (error.index, error.code, result.accepted) == (0, "invalid_input", 1)
    assert error.detail is not None
    assert "closing" not in error.detail


async def test_flagged_turns_and_sampled_conversations_go_to_the_kept_tier_at_once(world: World) -> None:
    failed = turn(T1)
    failed["calls"][0]["status"] = "error"
    await world.service.record(world.caller, [failed, turn(T2)])
    world.configure(sample_rate=0.5)
    sampled = [turn(f"s-{i}", conversation_id=f"c-{i}") for i in range(40)]
    await world.service.record(world.caller, sampled)

    async with world.cell.uow(SPACE_ID) as uow:
        [flagged] = await uow.turns.kept([T1])
        assert await uow.turns.kept([T2]) == []
        by_sample = await uow.turns.kept([t["turn_id"] for t in sampled])
    assert (flagged.kept_by.value, flagged.flags, flagged.replayable) == ("flag", ("error",), True)
    assert flagged.kept_until == world.cell.clock.now() + timedelta(days=30)
    assert 5 <= len(by_sample) <= 35
    assert {k.kept_by.value for k in by_sample} == {"sample"}
    conversation = await _conversation(world, "c-8812")
    assert turn_key(Tier.KEPT, SPACE_ID, conversation, T1) in world.cell.objects.objects


async def test_the_short_tier_expires_and_the_promoted_turn_stays(world: World) -> None:
    await world.service.record(world.caller, [turn(T1), turn(T2), turn(T3, conversation_id="c-other")])
    promoted = await world.service.promote(
        world.caller, PromoteRequest(turn_ids=[T1, "never-sent"], reason="complaint")
    )
    again = await world.service.promote(world.caller, PromoteRequest(turn_ids=[T1], reason="complaint"))
    assert (promoted.promoted, promoted.already_kept, promoted.not_found) == (1, 0, 1)
    assert (again.promoted, again.already_kept) == (0, 1)

    # No bucket rule expires a recording: it goes by an erasure or the retention the customer sets, as
    # here, by hand, for the short tier.
    assert not any(prefix.startswith("turns/") for prefix in OBJECT_EXPIRY_DAYS)
    world.cell.clock.advance(days=8)
    await world.cell.objects.delete_prefix("turns/short/")

    view = await world.service.read(world.caller, T1)
    assert (view.tier, view.record.turn_id, view.replayable) == ("kept", T1, True)
    assert view.kept_until is not None
    for gone in (T2, T3):
        with pytest.raises(NotFound):
            await world.service.read(world.caller, gone)
    assert sum(row.turns for row in _daily(world)) == 3, "the day's totals stay"


async def test_promotion_by_conversation_keeps_its_turns(world: World) -> None:
    await world.service.record(world.caller, [turn(T1), turn(T2), turn(T3, conversation_id="c-other")])
    result = await world.service.promote(
        world.caller, PromoteRequest(conversation_id="c-8812", reason="review")
    )
    assert (result.promoted, result.already_kept, result.not_found) == (2, 0, 0)
    found = await world.service.search(world.caller, TurnSearchRequest(conversation_id="c-8812"))
    assert {i.turn_id for i in found.items} == {T1, T2}


async def test_a_conversation_longer_than_a_batch_is_kept_whole(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A promotion kept a conversation's newest 500 turns and the rest expired with the short
    # tier. It keeps every turn now, a batch at a time.
    import niadra.app.turns as module

    monkeypatch.setattr(module, "PROMOTE_BATCH", 1)
    await world.service.record(world.caller, [turn(T1), turn(T2)])
    result = await world.service.promote(
        world.caller, PromoteRequest(conversation_id="c-8812", reason="review")
    )
    assert (result.promoted, result.already_kept, result.not_found) == (2, 0, 0)


async def test_the_viewer_reads_either_tier_and_leaves_a_quality_receipt(world: World) -> None:
    await world.service.record(world.caller, [turn(T1, mode="pointer")])
    view = await world.service.read(world.caller, T1)
    assert (view.tier, view.kept_until) == ("short", None)
    assert view.record.blobs["b:1"].pointer == "s3://company-turns/b.json"
    [receipt] = world.audit.records
    assert receipt["kind"] is ReceiptKind.QUALITY
    assert receipt["surface"] is ReceiptSurface.TURN
    assert receipt["item_ids"] == (T1,)
    vendor = PersonPrincipal(
        world.caller.space, WHATSAPP_SOURCE, frozenset({"integration"}), frozenset({BILLING_SOURCE})
    )
    with pytest.raises(NotFound):
        await world.service.read(vendor, T1)


async def test_search_reads_the_kept_tier_newest_first_a_page_at_a_time(world: World) -> None:
    turns = []
    for i in range(5):
        record = turn(f"t-{i}", flags=["handoff"] if i % 2 else ["negative_feedback"])
        record["started_at"] = f"2026-09-29T13:5{i}:00Z"
        turns.append(record)
    turns.append(turn("t-short"))
    await world.service.record(world.caller, turns)

    first = await world.service.search(world.caller, TurnSearchRequest(limit=2))
    second = await world.service.search(world.caller, TurnSearchRequest(limit=2, cursor=first.next_cursor))
    last = await world.service.search(world.caller, TurnSearchRequest(limit=2, cursor=second.next_cursor))
    handoffs = await world.service.search(world.caller, TurnSearchRequest(flags=["handoff"]))
    elsewhere = await world.service.search(world.caller, TurnSearchRequest(conversation_id="c-other"))

    assert [i.turn_id for i in first.items + second.items + last.items] == ["t-4", "t-3", "t-2", "t-1", "t-0"]
    assert last.next_cursor is None
    assert [i.turn_id for i in handoffs.items] == ["t-3", "t-1"]
    assert elsewhere.items == []
    assert {r["surface"] for r in world.audit.records} == {ReceiptSurface.TURN}


async def test_a_group_that_fails_is_retried_one_intake_at_a_time(world: World) -> None:
    class Failing:
        calls = 0
        broken = True

        async def consume(self, uow: Any, space_id: Any, turns: Any) -> None:
            Failing.calls += 1
            if Failing.broken and any(t.record.turn_id == T2 for t in turns):
                raise ValueError("a module could not take the turn")

    world.service._consumers = type(world.service._consumers)(effects=Failing())
    world.service._issues = DataIssuesService(  # type: ignore[assignment]
        world.cell.uow, world.cell.clock, world.cell.config, RecordingAdminAudit(), world.cell.cache
    )
    world.service._workers = object()  # type: ignore[assignment]
    world.service._hand_over = _no_hand_over  # type: ignore[method-assign]
    await world.service.record(world.caller, [turn(T1)])
    await world.service.record(world.caller, [turn(T2)])
    await world.service.record(world.caller, [turn(T3)])

    for _ in range(4):
        with pytest.raises(ValueError, match="could not take"):
            await world.service.apply_turns(_task())
    async with world.cell.uow(SPACE_ID) as uow:
        assert len((await uow.turns.take_intake(10) or TakenIntake([])).rows) == 2
    with pytest.raises(ValueError, match="could not take"):
        await world.service.apply_turns(_task())
    await world.service.apply_turns(_task())
    async with world.cell.uow(SPACE_ID) as uow:
        assert (await uow.turns.take_intake(10) or TakenIntake([])).rows == []
        # Never deleted: T2's intake is kept sealed, out of the order.
        [kept] = await uow.turns.failed_intake(10)
    assert kept.failed_at is not None
    assert sum(row.turns for row in _daily(world)) == 2, "T1 and T3; T2 is kept aside after 5 attempts"
    # The company hears it as a data issue, named by the source that sent it.
    async with world.cell.uow(SPACE_ID) as uow:
        [issue] = await uow.turns.issues(IssueQuery(kind="row_failed"))
    assert issue.field == "recorded_turns"
    assert issue.source is not None
    [listed] = await world.service.failed(SPACE_ID)
    assert (listed.intake_id, listed.kind) == (kept.intake_id, "turns")

    # Once what made it fail is fixed, a retry adds it up.
    Failing.broken = False
    assert await world.service.retry(SPACE_ID, [kept.intake_id]) == 1
    await world.service.apply_turns(_task())
    assert sum(row.turns for row in _daily(world)) == 3
    async with world.cell.uow(SPACE_ID) as uow:
        assert await uow.turns.failed_intake(10) == []


async def test_a_persons_access_reads_their_turns_in_a_kept_intake(world: World) -> None:
    """Their turns kept aside are theirs to access, by their conversations, as an erasure would take them
    (5.14)."""

    class Failing:
        async def consume(self, uow: Any, space_id: Any, turns: Any) -> None:
            raise ValueError("a module could not take the turn")

    world.service._consumers = type(world.service._consumers)(effects=Failing())
    world.service._workers = object()  # type: ignore[assignment]
    world.service._hand_over = _no_hand_over  # type: ignore[method-assign]
    await world.service.record(world.caller, [turn(T1)])
    for _ in range(5):
        with pytest.raises(ValueError, match="could not take"):
            await world.service.apply_turns(_task())
    async with world.cell.uow(SPACE_ID) as uow:
        [kept] = await uow.turns.failed_intake(10)
    body = orjson.loads(
        await world.cell.keys.decrypt(SPACE_ID, kept.body_ct, intake_aad(kept.intake_id), kept.dek_version)
    )
    conversation = bytes.fromhex(body["turns"][0]["conversation"])

    now = world.cell.clock.now()
    theirs = ErasureScope(
        uuid4(), TargetKind.PROFILE, (), None, None, now, conversation_hashes=(conversation,)
    )
    [row] = await world.service.kept_for(SPACE_ID, theirs)
    assert row["turn"]["turn_id"] == T1
    assert (row["kept_aside"], row["intake_id"]) == (True, kept.intake_id)
    elsewhere = ErasureScope(
        uuid4(), TargetKind.PROFILE, (), None, None, world.cell.clock.now(), conversation_hashes=(b"x" * 32,)
    )
    assert await world.service.kept_for(SPACE_ID, elsewhere) == []


async def test_an_erasure_takes_the_targets_turns_from_a_kept_intake(world: World) -> None:
    class Failing:
        async def consume(self, uow: Any, space_id: Any, turns: Any) -> None:
            raise ValueError("a module could not take the turn")

    world.service._consumers = type(world.service._consumers)(effects=Failing())
    world.service._workers = object()  # type: ignore[assignment]
    world.service._hand_over = _no_hand_over  # type: ignore[method-assign]
    await world.service.record(world.caller, [turn(T1)])
    for _ in range(5):
        with pytest.raises(ValueError, match="could not take"):
            await world.service.apply_turns(_task())
    async with world.cell.uow(SPACE_ID) as uow:
        [kept] = await uow.turns.failed_intake(10)
    body = orjson.loads(
        await world.cell.keys.decrypt(SPACE_ID, kept.body_ct, intake_aad(kept.intake_id), kept.dek_version)
    )
    conversation = bytes.fromhex(body["turns"][0]["conversation"])

    elsewhere = ErasureScope(
        uuid4(), TargetKind.PROFILE, (), None, None, world.cell.clock.now(), conversation_hashes=(b"x" * 32,)
    )
    await world.service.purge(SPACE_ID, elsewhere)
    async with world.cell.uow(SPACE_ID) as uow:
        assert [r.intake_id for r in await uow.turns.failed_intake(10)] == [kept.intake_id]

    target = ErasureScope(
        uuid4(),
        TargetKind.PROFILE,
        (),
        None,
        None,
        world.cell.clock.now(),
        conversation_hashes=(conversation,),
    )
    await world.service.purge(SPACE_ID, target)
    async with world.cell.uow(SPACE_ID) as uow:
        assert await uow.turns.failed_intake(10) == []


async def _no_hand_over(task: Any) -> None:
    return None


def _task() -> Any:
    from niadra.domain.tasks import Task, TaskName

    return Task(TaskName.APPLY_TURNS, SPACE_ID, {})


async def test_the_intake_is_sealed_and_carries_frames_only(world: World) -> None:
    world.service._hand_over = _no_hand_over  # type: ignore[method-assign]
    await world.service.record(world.caller, [turn(T1)])
    async with world.cell.uow(SPACE_ID) as uow:
        [row] = (await uow.turns.take_intake(10) or TakenIntake([])).rows
    assert b"family" not in row.body_ct
    body = orjson.loads(
        await world.cell.keys.decrypt(SPACE_ID, row.body_ct, intake_aad(row.intake_id), row.dek_version)
    )
    [entry] = body["turns"]
    assert all("content" not in b for b in entry["frame"]["blobs"].values())
    assert copy.deepcopy(entry["frame"]["blobs"]["b:1"]) == {
        k: v for k, v in turn(T1)["blobs"]["b:1"].items() if k != "content"
    }


async def test_a_store_that_fails_halfway_leaves_every_turn_new_for_the_retry(world: World) -> None:
    store = world.cell.objects
    put = store.put

    async def failing(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        if key.endswith(f"by-id/{T2}"):
            raise ConnectionError("the store went away")
        await put(key, data, content_type)

    store.put = failing  # type: ignore[method-assign]
    with pytest.raises(Unavailable):
        await world.service.record(world.caller, [turn(T1), turn(T2)])
    store.put = put  # type: ignore[method-assign]
    retried = await world.service.record(world.caller, [turn(T1), turn(T2)])
    assert (retried.accepted, retried.duplicates) == (2, 0)
    assert sum(row.turns for row in _daily(world)) == 2


async def test_a_turn_kept_by_its_flag_is_told_to_the_endpoints_that_subscribe_ids_only(world: World) -> None:
    endpoint = WebhookEndpoint(
        endpoint_id="quality",
        url="https://quality.example/hooks",
        source_id=WHATSAPP_SOURCE,
        event_types=["turn.flagged"],
        secret_refs=[f"spaces/{SPACE_ID}/webhook/quality"],
    )
    config = world.cell.config.space(SPACE_ID)
    world.cell.config.put(config.model_copy(update={"webhook_endpoints": [endpoint]}))
    await world.service.record(
        world.caller, [turn(T1, flags=["handoff"]), turn(T2), turn(T3, conversation_id="+5511999990000")]
    )

    outbox = world.cell.uow.store.module("pipeline")["outbox"]
    [task] = [t for t in outbox if t.name == TaskName.NOTIFY_FLAGGED_TURNS]
    await world.service.notify_flagged(task)

    [delivery] = world.cell.uow.store.module("notify")["deliveries"].values()
    assert delivery.event_type == "turn.flagged"
    data = delivery.body["data"]
    assert (data["turn_id"], data["conversation_id"], data["flags"], data["agent"]) == (
        T1,
        "c-8812",
        ["handoff"],
        "closing",
    )
    assert "511.06" not in orjson.dumps(delivery.body).decode()


async def test_no_task_is_added_when_no_endpoint_subscribes(world: World) -> None:
    await world.service.record(world.caller, [turn(T1, flags=["handoff"])])
    assert not [
        t
        for t in world.cell.uow.store.module("pipeline")["outbox"]
        if t.name == TaskName.NOTIFY_FLAGGED_TURNS
    ]


async def test_a_subjects_recordings_in_every_tier_are_theirs_to_access_each_turn_once(world: World) -> None:
    """Recordings are kept until an erasure or the customer's retention, so the subject's access package holds
    them: every tier an erasure takes, each turn once."""
    await world.service.record(world.caller, [turn(T1), turn(T2), turn(T3, conversation_id="c-other")])
    await world.service.promote(world.caller, PromoteRequest(turn_ids=[T1], reason="complaint"))
    theirs = ErasureScope(
        uuid4(),
        TargetKind.PROFILE,
        (),
        None,
        None,
        world.cell.clock.now(),
        conversation_hashes=(await _conversation(world, "c-8812"),),
    )
    rows = await world.service.recorded_for(SPACE_ID, theirs)
    assert [r["turn_id"] for r in rows] == [T1, T2]
    assert rows[0]["record"]["blobs"]["b:2"]["content"] == QUOTE_RESULT
