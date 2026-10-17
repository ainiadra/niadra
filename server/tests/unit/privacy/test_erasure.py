from typing import Any
from uuid import UUID, uuid4

import pytest

from niadra.app.pipeline import PipelineService
from niadra.contracts.common import Handle
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.common.errors import Conflict, InvalidInput, NotFound, Unavailable
from niadra.domain.extract.result import EpisodeDraft, ExtractionResult, ExtractionRun, RunStatus
from niadra.domain.privacy.export import ExportRun, ExportStatus
from niadra.domain.privacy.raw_index import body_tokens, handle_tokens
from niadra.domain.review.sampling import ReviewSampleRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.traits.evidence import EvidenceRef
from niadra.domain.triggers.firing import Firing
from niadra.domain.vocabulary import HandleType
from niadra.ports.infra import HotTurn
from niadra.testing.cell import SPACE_ID, Cell

from .world import T0, World


def seed(world: World) -> dict[str, UUID]:
    """Marina (the target) and Joao (bystander) with events, memory, traits, objects, links and packs."""
    marina, marina_profile = world.person("+5511999990001")
    joao, joao_profile = world.person("+5511999990002")
    e1 = world.event(marina, "c-1", "my invoice is wrong")
    e2 = world.event(marina, "c-2", "still wrong")
    e3 = world.event(joao, "c-3", "hello")
    kept_fact = world.fact(marina, "c-1", e1, e2)
    only_c1 = world.fact(marina, "c-1", e1)
    joao_fact = world.fact(joao, "c-3", e3)
    world.episodes |= {
        uuid4(): {"origin": marina, "conversation": "c-1"},
        uuid4(): {"origin": marina, "conversation": "c-2"},
        uuid4(): {"origin": joao, "conversation": "c-3"},
    }
    world.open_items[uuid4()] = marina
    trait = uuid4()
    world.traits[trait] = marina_profile
    world.trait_evidence.add((trait, e1))
    world.objects[uuid4()] = marina
    world.actions[uuid4()] = {"handle": marina, "result": "credited 50"}
    world.links[uuid4()] = {"person": marina, "org": uuid4(), "valid_to": None}
    world.packs |= {"pack-m": marina_profile, "pack-j": joao_profile}
    world.context_use |= {"cu-m": marina_profile, "cu-j": joao_profile}
    return {
        "marina": marina,
        "marina_profile": marina_profile,
        "joao": joao,
        "joao_profile": joao_profile,
        "e1": e1,
        "e2": e2,
        "e3": e3,
        "kept_fact": kept_fact,
        "only_c1": only_c1,
        "joao_fact": joao_fact,
        "trait": trait,
    }


def by_handle(value: str) -> ForgetRequest:
    return ForgetRequest(target="handle", handle=Handle(type=HandleType.PHONE_E164, value=value))


async def test_forgetting_a_handle_runs_the_whole_cascade(cell, world, make_privacy) -> None:
    ids = seed(world)
    privacy = make_privacy(batch_size=1)
    await cell.hot.add(SPACE_ID, ids["marina"], [HotTurn(uuid4(), 1, b"ct")], cap_ms=1000)
    cell.objects.objects[f"raw/{SPACE_ID}/{ids['marina']}/{ids['e1']}.json"] = b"{}"
    cell.objects.objects[f"media/{SPACE_ID}/{ids['marina']}/voice.ogg"] = b"ogg"
    cell.objects.objects[f"raw/{SPACE_ID}/{ids['joao']}/{ids['e3']}.json"] = b"{}"
    sample = ReviewSampleRecord(uuid4(), uuid4(), "base", None, T0, profile_id=ids["marina_profile"])
    marina_session, joao_session = uuid4(), uuid4()
    world.sessions |= {marina_session: ids["marina_profile"], joao_session: ids["joao_profile"]}
    extracted = ExtractionResult(
        EpisodeDraft("Disse que a fatura veio errada", "complaint", "invoice", "open")
    )
    async with cell.uow(SPACE_ID) as uow:
        for session_id in (marina_session, joao_session):
            await uow.extract.save_run(
                ExtractionRun(session_id, "v1", RunStatus.OK, T0, input_tokens=10, result=extracted)
            )
        evidence = (EvidenceRef("event", ids["e1"], ids["marina"]),)
        await uow.triggers.add_firing(
            Firing(uuid4(), "r", "v1", ids["marina_profile"], "w", b"k", evidence, 1, T0)
        )
        await uow.review.add_sample(sample)

    accepted = await privacy.forget(
        SPACE_ID, by_handle("+5511999990001"), idempotency_key="k-1", requested_by="user:u"
    )
    assert accepted.status == "pending"
    assert accepted.target_id == str(ids["marina"])
    assert "+5511" not in accepted.model_dump_json()
    (task,) = cell.uow.store.module("pipeline")["outbox"]
    assert task.name is TaskName.ERASE
    assert task.profile_id == ids["marina_profile"]
    cell.uow.store.module("pipeline")["outbox"].clear()

    await privacy.erase(task)

    assert {e["text"] for i, e in world.events.items() if i != ids["e3"]} == {None}
    assert world.events[ids["e3"]]["text"] == "hello"
    assert set(world.facts) == {ids["joao_fact"]}
    assert [ep["origin"] for ep in world.episodes.values()] == [ids["joao"]]
    assert world.open_items == {}
    assert world.traits == {}
    assert world.objects == {}
    assert [a["result"] for a in world.actions.values()] == [None]
    assert all(link["valid_to"] is not None for link in world.links.values())
    assert world.packs == {"pack-j": ids["joao_profile"]}
    assert world.context_use == {"cu-j": ids["joao_profile"]}
    assert (SPACE_ID, ids["marina"]) not in cell.hot.turns
    # What stays: the other person's raw payload, and the ledger a restore reads to erase again.
    assert sorted(cell.objects.objects) == [
        f"privacy/erasures/{SPACE_ID}/2026-06-10/{accepted.request_id}.json",
        f"raw/{SPACE_ID}/{ids['joao']}/{ids['e3']}.json",
    ]

    status = await privacy.erasure_status(SPACE_ID, UUID(accepted.request_id))
    assert status.status == "completed"
    assert status.receipt_hash
    assert status.erased["events"] == 2
    assert status.erased["facts"] == 2
    assert status.erased["profile_links"] == 1
    assert status.erased["packs"] == 1
    assert world.privacy_version[ids["marina_profile"]] == 3  # bumped when it starts and when it ends
    feed = cell.uow.store.module("privacy")["feed"]
    assert [row["privacy_version"] for row in feed] == [2, 3]
    assert all(row["handle_ids"] == [ids["marina"]] for row in feed)

    tombstones = cell.uow.store.module("privacy")["tombstones"][SPACE_ID]
    assert {"table": "events", "key": {"event_id": str(ids["e1"])}} in [
        {"table": t.table, "key": t.key} for t in tombstones
    ]
    outbox = cell.uow.store.module("pipeline")["outbox"]
    assert {t.name for t in outbox} == {
        TaskName.DELIVER_WEBHOOK,
        TaskName.ERASE_COLD,
        TaskName.COMPILE,
        TaskName.DERIVE_TRAITS,
        TaskName.PURGE_SIGNALS,  # what subject signals keep outside the database
    }
    assert {t.profile_id for t in outbox if t.name in (TaskName.COMPILE, TaskName.DERIVE_TRAITS)} == {
        ids["marina_profile"]
    }
    async with cell.uow(SPACE_ID) as uow:
        cleared = await uow.extract.get_run(marina_session, "v1")
        kept = await uow.extract.get_run(joao_session, "v1")
    assert cleared is not None
    assert (cleared.result, cleared.input_tokens) == (None, 10), "the words go, the usage stays"
    assert kept is not None
    assert kept.result == extracted
    assert set(world.sessions) == {marina_session, joao_session}, "forgetting a handle keeps the sessions"
    async with cell.uow(SPACE_ID) as uow:
        [delivery] = await uow.notify.list_deliveries()
        assert await uow.triggers.list_firings() == [], "the target's firing went with it"
        assert await uow.review.get_sample(sample.sample_id) is None
    assert (delivery.endpoint_id, delivery.event_type) == ("dpo", "erasure.completed")
    assert delivery.body["data"] == {
        "event_type": "erasure.completed",
        "request_id": accepted.request_id,
        "target_kind": "handle",
        "target_id": str(ids["marina"]),
        "erased": status.erased,
        "export_run_ids": [],
        "receipt_hash": status.receipt_hash,
    }
    assert delivery.body["subject"] is None
    assert "still wrong" not in str(delivery.body)
    assert "+5511" not in str(delivery.body)
    assert status.erased["trigger_firings"] == status.erased["review_samples"] == 1
    assert not any(t.table in ("trigger_firings", "review_samples") for t in tombstones), "never exported"


async def test_forgetting_a_handle_takes_the_unmapped_payloads_that_name_it(
    cell, world, make_privacy
) -> None:
    # A payload no mapping matched is kept under no handle; the erasure reads each for the person's handles,
    # however the body writes them, and leaves the other person's.
    seed(world)
    unmapped = f"raw/{SPACE_ID}/_/2026-01-02/"
    cell.objects.objects |= {
        f"{unmapped}a": b'{"type": "lead.created", "from": "+55 (11) 99999-0001"}',
        f"{unmapped}b": b'{"contact": {"phone": 11999990001}}',
        f"{unmapped}c": b'{"from": "+55 11 99999-0002"}',
        f"{unmapped}d": b"not json, sent by 5511999990001",
    }
    privacy = make_privacy()
    accepted = await privacy.forget(
        SPACE_ID, by_handle("+5511999990001"), idempotency_key="k-u", requested_by="user:u"
    )
    (task,) = cell.uow.store.module("pipeline")["outbox"]
    await privacy.erase(task)
    assert sorted(k for k in cell.objects.objects if k.startswith(unmapped)) == [f"{unmapped}c"]
    async with cell.uow(SPACE_ID) as uow:
        request = await uow.privacy.get_erasure(SPACE_ID, UUID(accepted.request_id))
    assert request is not None
    assert request.counts["unmapped_payloads"] == 3


def test_a_body_names_a_handle_however_it_writes_it_and_never_by_a_tiny_id_or_a_domain() -> None:
    target = handle_tokens(
        [
            (HandleType.PHONE_E164, "+5511999990001"),
            (HandleType.EMAIL, "Ana@Example.com"),
            (HandleType.SYSTEM_ID, "cus_123456"),
            (HandleType.APP_USER_ID, "42"),
            (HandleType.EMAIL_DOMAIN, "example.com"),
        ]
    )

    def named_in(body: str) -> bool:
        return bool(target & body_tokens(body.encode()))

    assert named_in('{"to": "ANA@example.com"}')
    assert named_in('{"customer": "cus_123456"}')
    assert named_in("ligou do (11) 99999-0001 ontem")
    assert not named_in('{"customer": "cus_1234567"}')
    assert not named_in('{"qty": 42, "site": "example.com"}')
    assert not named_in('{"from": "+55 11 99999-0002"}')


def test_a_short_id_names_its_owner_only_as_a_whole_value_and_a_document_however_it_is_written() -> None:
    short = handle_tokens([(HandleType.SYSTEM_ID, "A7F2")])
    assert short & body_tokens(b'{"customer": "A7F2"}')
    assert not short & body_tokens(b'{"note": "code A7F2 sent"}')
    cpf = handle_tokens([(HandleType.GOV_ID_HMAC, "01234567890")])
    for body in (b'{"cpf": "012.345.678-90"}', b'{"cpf": 1234567890}', b"cpf 01234567890 no texto"):
        assert cpf & body_tokens(body)


async def test_forgetting_a_conversation_keeps_the_rest_of_the_person(cell, world, make_privacy) -> None:
    ids = seed(world)
    privacy = make_privacy()
    accepted = await privacy.forget(
        SPACE_ID,
        ForgetRequest(target="conversation", conversation_id="c-1"),
        idempotency_key="k-c",
        requested_by="user:u",
    )
    (task,) = cell.uow.store.module("pipeline")["outbox"]
    await privacy.erase(task)

    assert world.events[ids["e1"]]["text"] is None
    assert world.events[ids["e2"]]["text"] == "still wrong"
    assert ids["kept_fact"] in world.facts, "evidence from c-2 remains"
    assert ids["only_c1"] not in world.facts
    assert {ep["conversation"] for ep in world.episodes.values()} == {"c-2", "c-3"}
    assert len(world.objects) == 1
    assert len(world.open_items) == 1
    assert world.traits == {}, "its only evidence was in c-1"
    status = await privacy.erasure_status(SPACE_ID, UUID(accepted.request_id))
    assert status.target_id == "c-1"
    assert status.status == "completed"
    assert not any(k.startswith("raw/") for k in cell.objects.objects)


class RecordingFence:
    """A module with a writer outside its transaction: what it sees of the erasure, and when."""

    def __init__(self, world: World, fact_id: UUID) -> None:
        self.world = world
        self.fact_id = fact_id
        self.calls: list[tuple[str, str, bool]] = []

    async def fence(self, space_id: UUID, scope: Any) -> None:
        self.calls.append(("fence", scope.kind.value, self.fact_id in self.world.facts))

    async def purge(self, space_id: UUID, scope: Any) -> None:
        self.calls.append(("purge", scope.kind.value, self.fact_id in self.world.facts))


async def test_the_fences_close_before_the_first_row_goes_and_purge_once_the_cascade_is_done(
    cell, world, make_privacy
) -> None:
    ids = seed(world)
    fence = RecordingFence(world, ids["only_c1"])
    privacy = make_privacy(fences=[fence])
    await privacy.forget(SPACE_ID, by_handle("+5511999990001"), idempotency_key="k-f", requested_by="user:u")
    (task,) = cell.uow.store.module("pipeline")["outbox"]
    await privacy.erase(task)
    assert fence.calls == [("fence", "handle", True), ("purge", "handle", False)]


async def test_forget_is_idempotent_by_key(cell, world, make_privacy) -> None:
    seed(world)
    privacy = make_privacy()
    first = await privacy.forget(
        SPACE_ID, by_handle("+5511999990001"), idempotency_key="k", requested_by="user:u"
    )
    again = await privacy.forget(
        SPACE_ID, by_handle("+5511999990001"), idempotency_key="k", requested_by="user:u"
    )
    assert first == again
    assert len(cell.uow.store.module("pipeline")["outbox"]) == 1
    with pytest.raises(Conflict):
        await privacy.forget(
            SPACE_ID,
            ForgetRequest(target="conversation", conversation_id="c-3"),
            idempotency_key="k",
            requested_by="user:u",
        )
    with pytest.raises(InvalidInput):
        await privacy.forget(SPACE_ID, by_handle("+5511999990001"), idempotency_key="", requested_by="user:u")
    with pytest.raises(NotFound):
        await privacy.forget(
            SPACE_ID, by_handle("+5500000000000"), idempotency_key="k2", requested_by="user:u"
        )


async def test_a_second_person_under_the_same_key_is_refused_never_replayed(
    cell, world, make_privacy
) -> None:
    # The old check compared only the target kind, so a second phone under the same key
    # replayed the first erasure and the second person stayed in memory.
    seed(world)
    privacy = make_privacy()
    first = await privacy.forget(
        SPACE_ID, by_handle("+5511999990001"), idempotency_key="k", requested_by="user:u"
    )
    with pytest.raises(Conflict):
        await privacy.forget(
            SPACE_ID, by_handle("+5511999990002"), idempotency_key="k", requested_by="user:u"
        )
    # The same person written another way is the same body: a replay.
    again = await privacy.forget(
        SPACE_ID, by_handle("+55 11 99999-0001"), idempotency_key="k", requested_by="user:u"
    )
    assert again == first
    assert len(cell.uow.store.module("pipeline")["outbox"]) == 1
    stored = next(iter(cell.uow.store.module("privacy")["erasures"].values()))
    assert "+5511999990001" not in str(stored.counts)  # the body is kept only as a keyed hash


class CompileOutage:
    async def erase_for(self, scope: object, *, limit: int) -> list[object]:
        raise Unavailable("database connection lost")


async def test_a_failing_participant_fails_the_task_and_a_retry_finishes(cell, world, make_privacy) -> None:
    ids = seed(world)
    broken = make_privacy(batch_size=1, compile=CompileOutage())
    await broken.forget(SPACE_ID, by_handle("+5511999990001"), idempotency_key="k", requested_by="user:u")
    (task,) = cell.uow.store.module("pipeline")["outbox"]
    with pytest.raises(Unavailable):
        await broken.erase(task)
    assert world.facts.keys() == {ids["joao_fact"]}, "steps before the missing one already ran"

    await make_privacy(batch_size=1).erase(task)
    status = await make_privacy().erasure_status(SPACE_ID, request_id(cell))
    assert status.status == "completed"
    assert status.erased["events"] == 2, "counted once across both attempts"


def request_id(cell: Cell) -> UUID:
    ((_, rid),) = cell.uow.store.module("privacy")["erasures"].keys()
    return rid


async def test_the_receipt_lists_export_runs_that_carried_erased_rows(cell, world, make_privacy) -> None:
    ids = seed(world)
    privacy = make_privacy()
    carrying = ExportRun(uuid4(), "lake", 0, world.events[ids["e2"]]["seq"], ExportStatus.COMPLETED, T0)
    after = ExportRun(uuid4(), "lake", 100, 200, ExportStatus.COMPLETED, T0)
    async with cell.uow(SPACE_ID) as uow:
        await uow.privacy.create_export_run(SPACE_ID, carrying)
        await uow.privacy.create_export_run(SPACE_ID, after)
    await privacy.forget(SPACE_ID, by_handle("+5511999990001"), idempotency_key="k", requested_by="user:u")
    (task,) = cell.uow.store.module("pipeline")["outbox"]
    await privacy.erase(task)
    status = await privacy.erasure_status(SPACE_ID, request_id(cell))
    assert status.export_run_ids == [str(carrying.run_id)]


async def test_a_completed_request_is_not_erased_twice(cell, world, make_privacy) -> None:
    seed(world)
    privacy = make_privacy()
    await privacy.forget(SPACE_ID, by_handle("+5511999990001"), idempotency_key="k", requested_by="user:u")
    (task,) = cell.uow.store.module("pipeline")["outbox"]
    await privacy.erase(task)
    versions = dict(world.privacy_version)
    await privacy.erase(task)
    assert world.privacy_version == versions


async def test_forget_by_profile_does_not_recompile_it(cell, world, make_privacy) -> None:
    ids = seed(world)
    privacy = make_privacy()
    await privacy.forget(
        SPACE_ID,
        ForgetRequest(target="profile", profile_id=ids["marina_profile"]),
        idempotency_key="k",
        requested_by="user:u",
    )
    (task,) = cell.uow.store.module("pipeline")["outbox"]
    cell.uow.store.module("pipeline")["outbox"].clear()
    await privacy.erase(task)
    names = {t.name for t in cell.uow.store.module("pipeline")["outbox"]}
    assert names == {
        TaskName.DELIVER_WEBHOOK,
        TaskName.ERASE_COLD,
        TaskName.PURGE_SIGNALS,
    }
    assert world.traits == {}


async def test_unknown_request_is_ignored(make_privacy) -> None:
    await make_privacy().erase(Task(TaskName.ERASE, SPACE_ID, {"request_id": str(uuid4())}))
    with pytest.raises(NotFound):
        await make_privacy().erasure_status(SPACE_ID, uuid4())


async def test_an_erasure_out_of_attempts_says_failed_until_a_replay_completes_it(
    cell, world, make_privacy
) -> None:
    # The status `failed` was never written; a dead erasure read `running` for good.
    seed(world)
    privacy = make_privacy()
    accepted = await privacy.forget(
        SPACE_ID, by_handle("+5511999990001"), idempotency_key="k-dead", requested_by="user:u"
    )
    (task,) = cell.uow.store.module("pipeline")["outbox"]
    await PipelineService(cell.uow, cell.bus, cell.clock).bury(task, "RuntimeError")
    status = await privacy.erasure_status(SPACE_ID, UUID(accepted.request_id))
    assert status.status == "failed"
    await privacy.erase(task)  # the replay from the dead tasks
    assert (await privacy.erasure_status(SPACE_ID, UUID(accepted.request_id))).status == "completed"
