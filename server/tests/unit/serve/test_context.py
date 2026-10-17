"""`context()` (5.11): every tier, the control group, ETags, pins, delta, privacy feed and receipts."""

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest

from niadra.app.audit import AuditService, _project
from niadra.app.serve import LIVE_READ, LIVE_TURNS
from niadra.config.models import Experiment, PolicyCondition, PolicyRule, SourceConfig, SpaceConfig
from niadra.config.starter import STARTER_POLICY
from niadra.contracts.common import Handle, ObjectRef
from niadra.contracts.context import ContextRequest, TargetModel
from niadra.domain.common.errors import Forbidden, NotFound, Unavailable
from niadra.domain.compile import Variant
from niadra.domain.compile.model import MemorySnapshot
from niadra.domain.receipts import ReceiptDraft
from niadra.domain.records import BusinessObject, LinkRecord, ObjectKey
from niadra.domain.serve.cache_keys import gen_key, pin_key
from niadra.domain.serve.records import PIN_TTL, AgentWatermark, ConversationPin
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import (
    Audience,
    DeliveryPath,
    HandleStatus,
    HandleType,
    OpenItemStatus,
    ReceiptKind,
    ReceiptSurface,
    Scope,
    SubjectKind,
    Verification,
)
from niadra.ports.repos.audit import ReceiptQuery
from niadra.testing.cell import (
    BILLING_SOURCE,
    SPACE_ID,
    VOICE_SOURCE,
    WHATSAPP_SOURCE,
    Cell,
    principal,
    sample_config,
)
from tests.unit.compile.factories import PHONE, PROFILE, T0, WA, fact, marina, open_item, profile, uid
from tests.unit.serve.harness import SUBJECT, Harness, default_world, handle_record


class NoSource:
    async def batches(self, max_items: int):  # type: ignore[no-untyped-def]
        return
        yield


AGENT = principal()
CARE_SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c0a1")


def ask(**extra: object) -> ContextRequest:
    # Reads name their conversation; what it proved is the world's `proven`, V1 by default (2.2).
    defaults = {"subject": SUBJECT, "verification": Verification.V1, "conversation_id": "wa-now"}
    return ContextRequest(**(defaults | extra))  # type: ignore[arg-type]


async def test_a_first_contact_gets_an_empty_answer_and_a_receipt(cell: Cell) -> None:
    h = Harness(cell)
    stranger = Handle(type=HandleType.PHONE_E164, value="+5511888887777")
    response = await h.serve.context(AGENT, ask(subject=stranger))
    assert (response.path, response.text, response.degraded) == (DeliveryPath.T4, None, False)
    assert [r.kind for r in h.receipts.sent] == [ReceiptKind.READ]


@pytest.mark.parametrize(
    "status", [HandleStatus.AMBIGUOUS, HandleStatus.BLOCKED, HandleStatus.SUSPECTED_RECYCLED]
)
async def test_a_handle_in_doubt_reads_as_a_first_contact(cell: Cell, status: HandleStatus) -> None:
    # A shared, blocked or possibly recycled number tells nothing of the profile it sits in.
    world = default_world()
    world.handles = [replace(h, status=status) if h.handle_id == PHONE else h for h in world.handles]
    h = Harness(cell, world)
    response = await h.serve.context(AGENT, ask())
    assert (response.path, response.text, response.withheld) == (DeliveryPath.T4, None, 0)
    [receipt] = h.receipts.sent
    assert (receipt.profile_id, receipt.item_ids) == (None, ())


async def test_without_a_stored_pack_the_read_compiles_on_demand(cell: Cell) -> None:
    h = Harness(cell)
    response = await h.serve.context(AGENT, ask())
    assert response.path is DeliveryPath.T2
    assert response.text is not None
    assert "[Cliente] Marina" in response.text
    assert response.verification.effective is Verification.V1
    assert response.withheld == 4
    assert response.as_of is not None
    assert set(response.timing) >= {"privacy", "resolve", "pack", "live", "receipt"}
    receipt = h.receipts.sent[0]
    assert (receipt.kind, receipt.surface, receipt.profile_id) == (
        ReceiptKind.READ,
        ReceiptSurface.PACK,
        PROFILE,
    )
    assert receipt.pack_hash is not None
    assert receipt.item_ids
    assert receipt.withheld == 4
    # No generation in the cache yet: the read asks for a reseed once, through the outbox.
    assert len(h.outbox(TaskName.SEED_GEN)) == 1
    await h.serve.context(AGENT, ask())
    assert len(h.outbox(TaskName.SEED_GEN)) == 1


async def test_after_compilation_the_cache_answers(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    response = await h.serve.context(AGENT, ask())
    assert response.path is DeliveryPath.T0
    assert response.version == "g1"
    assert h.outbox(TaskName.SEED_GEN) == []


async def test_a_lost_cache_falls_back_to_the_stored_pack_and_asks_for_a_reseed(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    cell.cache.values.clear()
    first = await h.serve.context(AGENT, ask())
    assert first.path is DeliveryPath.T1
    assert len(h.outbox(TaskName.SEED_GEN)) == 1
    # T1 never writes T0 without a generation to prove it current (06, 3).
    assert not any(k.startswith("t0:") for k in cell.cache.values)
    await h.compiler.seed_generation(h.outbox(TaskName.SEED_GEN)[0])
    assert (await h.serve.context(AGENT, ask())).path is DeliveryPath.T0


async def test_a_new_cache_epoch_hides_old_keys(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    h.world.epoch = 7
    assert await h.serve.refresh_cache_epoch() == 7
    assert (await h.serve.context(AGENT, ask())).path is DeliveryPath.T1
    assert gen_key(7, SPACE_ID, PROFILE) not in cell.cache.values


async def test_known_etag_returns_not_modified_without_text(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    first = await h.serve.context(AGENT, ask())
    again = await h.serve.context(AGENT, ask(known_etag=first.etag))
    assert (again.not_modified, again.path, again.text, again.etag) == (
        True,
        DeliveryPath.NOT_MODIFIED,
        None,
        first.etag,
    )
    assert h.receipts.sent[-1].extra.get("not_modified") == "1"


async def test_the_level_is_capped_by_the_source_and_by_what_was_proven(cell: Cell) -> None:
    h = Harness(cell)
    capped = await h.serve.context(AGENT, ask(verification=Verification.V3))
    assert (capped.verification.effective, capped.verification.reason) == (Verification.V1, "not_proven")
    h.world.proven = Verification.V2
    proven = await h.serve.context(AGENT, ask(verification=Verification.V3, conversation_id="c1"))
    assert (proven.verification.effective, proven.verification.reason) == (Verification.V2, "source_ceiling")
    assert "fatura de agosto" in (proven.text or "")


async def test_a_conversation_keeps_its_bytes_and_gets_changes_as_delta(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    first = await h.serve.context(AGENT, ask(conversation_id="c1"))
    # The visit was done; a new promise takes its place in the skeleton's pending lines.
    h.world.replace_profile(replace(marina(), open_items=(open_item(2, "Enviar segunda via"),)))
    await h.compile()
    pinned = await h.serve.context(AGENT, ask(conversation_id="c1", delta=True))
    assert pinned.text == first.text
    assert pinned.path is DeliveryPath.T0
    assert pinned.delta is not None
    assert "Enviar segunda via" in pinned.delta
    assert [r.surface for r in h.receipts.sent[-2:]] == [ReceiptSurface.PACK, ReceiptSurface.DELTA]
    fresh = await h.serve.context(AGENT, ask(conversation_id="c2"))
    assert "Enviar segunda via" in (fresh.text or "")


async def test_two_sources_in_one_conversation_keep_their_own_bytes(cell: Cell) -> None:
    # The pin is per source; the billing agent reading the same conversation and view no longer
    # takes the customer agent's pin away.
    h = Harness(cell)
    await h.compile()
    agent_first = await h.serve.context(AGENT, ask(conversation_id="c1"))
    billing = principal(BILLING_SOURCE)
    internal = ask(conversation_id="c1", verification=Verification.NO_CUSTOMER)
    billing_first = await h.serve.context(billing, internal)
    assert billing_first.etag != agent_first.etag
    h.world.replace_profile(
        replace(marina(), open_items=(*marina().open_items, open_item(2, "Enviar segunda via")))
    )
    await h.compile()
    assert (await h.serve.context(AGENT, ask(conversation_id="c1"))).etag == agent_first.etag
    assert (await h.serve.context(billing, internal)).etag == billing_first.etag
    assert (await h.serve.context(AGENT, ask(conversation_id="c1"))).etag == agent_first.etag


async def test_the_delta_base_never_moves_back_and_is_kept_per_conversation(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    await h.serve.context(AGENT, ask(conversation_id="c1"))
    await h.serve.context(AGENT, ask(conversation_id="c2"))
    # The visit was done; a new promise takes its place in the skeleton's pending lines.
    h.world.replace_profile(replace(marina(), open_items=(open_item(2, "Enviar segunda via"),)))
    await h.compile()
    seen = await h.serve.context(AGENT, ask(conversation_id="c1", delta=True))
    assert seen.delta is not None
    # A plain read of c1 serves its older pinned pack; the mark stays on what the agent already saw.
    await h.serve.context(AGENT, ask(conversation_id="c1"))
    again = await h.serve.context(AGENT, ask(conversation_id="c1", delta=True))
    assert again.delta is None
    # The same agent talking to the same customer in c2 has not seen the new promise there.
    other = await h.serve.context(AGENT, ask(conversation_id="c2", delta=True))
    assert other.delta is not None
    assert "Enviar segunda via" in other.delta
    delta_receipt = h.receipts.sent[-1]
    assert (delta_receipt.surface, delta_receipt.extra) == (ReceiptSurface.DELTA, {"generation": "2"})


async def test_a_new_conversation_gets_what_changed_since_the_agent_last_read_elsewhere(cell: Cell) -> None:
    # "only what changed since the last time it talked to the customer", also in a conversation
    # the agent has not read in yet, and also when the last read compiled on demand (T2).
    h = Harness(cell)
    first = await h.serve.context(AGENT, ask(conversation_id="c1"))
    assert first.path is DeliveryPath.T2
    # The visit was done; a new promise takes its place in the skeleton's pending lines.
    h.world.replace_profile(replace(marina(), open_items=(open_item(2, "Enviar segunda via"),)))
    await h.compile()
    fresh = await h.serve.context(AGENT, ask(conversation_id="c2", delta=True))
    assert fresh.delta is not None
    assert "Enviar segunda via" in fresh.delta
    assert "Visita técnica remarcada" not in fresh.delta

    # The cache lost the marks: the receipt writer's projection gives the same base.
    cell.cache.values.clear()
    async with cell.uow(SPACE_ID) as uow:
        await uow.serve.project_watermarks(
            [
                AgentWatermark(
                    source_id=WHATSAPP_SOURCE,
                    profile_id=PROFILE,
                    manifest_hash=first.manifest_hash or "",
                    seen_at=T0,
                    thread="c1",
                    generation=0,
                )
            ]
        )
    again = await h.serve.context(AGENT, ask(conversation_id="c3", delta=True))
    assert again.delta is not None
    assert "Enviar segunda via" in again.delta
    # A source that never read this customer gets no delta: there is nothing to compare with.
    voice = await h.serve.context(principal(VOICE_SOURCE), ask(conversation_id="c4", delta=True))
    assert voice.delta is None


async def test_a_lost_pin_comes_back_from_the_receipt_writer_projection(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    first = await h.serve.context(AGENT, ask(conversation_id="c1"))
    receipt = next(r for r in h.receipts.sent if r.surface is ReceiptSurface.PACK)
    variant = Variant(verification=Verification.V1, purposes=frozenset({"support"}))
    assert receipt.extra["view"] == "chat"
    assert receipt.extra["pack_key"] == f"{PROFILE}/{variant.key}"
    assert receipt.pack_hash is not None
    # What the receipt writer does with the receipt (5.11): the pin survives a cache failover.
    async with h.uow(SPACE_ID) as uow:
        await uow.serve.project_pins(
            [
                ConversationPin(
                    conversation_id="c1",
                    view=receipt.extra["view"],
                    pack_key=receipt.extra["pack_key"],
                    pack_hash=receipt.pack_hash,
                    generation=int(receipt.extra["generation"]),
                    pinned_at=receipt.read_at,
                    expires_at=receipt.read_at + PIN_TTL,
                )
            ]
        )
    cell.cache.values.clear()
    again = await h.serve.context(AGENT, ask(conversation_id="c1"))
    assert again.text == first.text
    assert again.path is DeliveryPath.T0


async def test_the_control_group_gets_nothing_and_a_shadow_manifest_is_recorded(cell: Cell) -> None:
    config = sample_config().model_copy(
        update={
            "experiments": [
                Experiment(name="pilot", fraction_with_memory=0.0, salt="s", starts_at=T0 - timedelta(days=1))
            ]
        }
    )
    cell.config.put(config)
    h = Harness(cell)
    response = await h.serve.context(AGENT, ask())
    assert (response.path, response.text) == (DeliveryPath.HOLDOUT, "")
    receipt = h.receipts.sent[-1]
    assert receipt.kind is ReceiptKind.HOLDOUT
    assert receipt.experiment_group == "pilot:control"
    assert receipt.manifest_hash is not None
    assert receipt.item_ids


async def test_a_profile_keeps_the_group_it_first_got_whatever_a_union_does(cell: Cell) -> None:
    # The draw goes by the oldest handle, which a union can change; the receipt writer records
    # the first group of each profile, and the read keeps it.
    config = sample_config().model_copy(
        update={
            "experiments": [
                Experiment(name="pilot", fraction_with_memory=0.0, salt="s", starts_at=T0 - timedelta(days=1))
            ]
        }
    )
    cell.config.put(config)
    h = Harness(cell)
    async with cell.uow(SPACE_ID) as uow:
        await uow.serve.project_assignment(
            experiment="pilot", profile_id=PROFILE, group="memory", assigned_at=T0 - timedelta(days=1)
        )
        await uow.serve.project_assignment(
            experiment="pilot", profile_id=PROFILE, group="control", assigned_at=T0
        )  # a later receipt never moves it
    response = await h.serve.context(AGENT, ask())
    assert response.path is not DeliveryPath.HOLDOUT
    assert h.receipts.sent[-1].experiment_group == "pilot:memory"


async def test_the_receipt_writer_records_the_first_group_of_each_profile(cell: Cell) -> None:
    def read(group: str) -> ReceiptDraft:
        return ReceiptDraft(
            receipt_id=uid(0x9000 + len(group)),
            space_id=SPACE_ID,
            producer_id="read-1",
            producer_seq=1,
            kind=ReceiptKind.HOLDOUT,
            surface=ReceiptSurface.PACK,
            audience=Audience.CUSTOMER_AGENT,
            read_at=T0,
            profile_id=PROFILE,
            experiment_group=group,
        )

    async with cell.uow(SPACE_ID) as uow:
        await _project(uow, [read("pilot:control"), read("pilot:memory"), read("pilot:shadow")])
        assert await uow.serve.get_assignments(PROFILE) == {"pilot": "control"}


async def test_an_overdue_company_promise_takes_the_customer_out_of_the_control_group(cell: Cell) -> None:
    config = sample_config().model_copy(
        update={
            "experiments": [
                Experiment(name="pilot", fraction_with_memory=0.0, salt="s", starts_at=T0 - timedelta(days=1))
            ]
        }
    )
    cell.config.put(config)
    h = Harness(cell)
    h.world.replace_profile(
        replace(
            marina(), open_items=(open_item(1, "Visita técnica", due_days=-1, status=OpenItemStatus.OVERDUE),)
        )
    )
    response = await h.serve.context(AGENT, ask())
    assert response.path is DeliveryPath.T2
    assert "Visita técnica" in (response.text or "")
    # Out of both groups: the read is billed and never measured as a control observation.
    assert h.receipts.sent[-1].experiment_group is None


async def test_a_privacy_change_refuses_older_packs(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    assert (await h.serve.context(AGENT, ask())).path is DeliveryPath.T0
    h.world.feed.append(
        {
            "id": uid(0xF1),
            "kind": "unmerge",
            "profile_ids": [PROFILE],
            "handle_ids": [],
            "privacy_version": 2,
            "created_at": T0,
        }
    )
    cell.clock.advance(seconds=5)
    # The replica already shows the new version: the read compiles a fresh pack from it. The feed is two
    # polls late here, so the read waits for it (a poll the loop keeps up with runs in the background).
    h.world.replace_profile(replace(marina(privacy_version=2)))
    response = await h.serve.context(AGENT, ask())
    assert response.path is DeliveryPath.T2


async def test_a_privacy_change_reaches_the_reads_after_the_poll_they_start(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    assert (await h.serve.context(AGENT, ask())).path is DeliveryPath.T0
    h.world.feed.append(
        {
            "id": uid(0xF1),
            "kind": "unmerge",
            "profile_ids": [PROFILE],
            "handle_ids": [],
            "privacy_version": 2,
            "created_at": T0,
        }
    )
    cell.clock.advance(seconds=3)
    h.world.replace_profile(replace(marina(privacy_version=2)))
    # A due poll runs in the background: the read that starts it goes on with the guard of the last one,
    # within the poll's period, as the reads behind a poll in flight always did.
    await h.serve.context(AGENT, ask())
    await h.serve.drain()
    assert (await h.serve.context(AGENT, ask())).path is DeliveryPath.T2


async def test_a_replica_behind_the_privacy_feed_returns_empty_and_degraded(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    await h.add_turn(PHONE, minutes_ago=5, text="oi")
    h.world.feed.append(
        {
            "id": uid(0xF2),
            "kind": "erasure",
            "profile_ids": [PROFILE],
            "handle_ids": [PHONE],
            "privacy_version": 5,
            "created_at": T0,
        }
    )
    cell.clock.advance(seconds=3)
    response = await h.serve.context(AGENT, ask())
    assert (response.path, response.text, response.live, response.degraded) == (
        DeliveryPath.T4,
        None,
        [],
        True,
    )


async def test_live_turns_come_from_other_conversations_after_the_pack(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    await h.add_turn(
        PHONE, minutes_ago=10, text="liguei e ninguém atendeu", conversation_id="voice-1", event=1
    )
    await h.add_turn(WA, minutes_ago=5, text="mesma conversa", conversation_id="c1", event=2)
    await h.add_turn(
        WA, minutes_ago=4, text="nota interna", conversation_id="wa-7", visibility="internal", event=3
    )
    await h.add_turn(PHONE, minutes_ago=300, text="antes do pack", conversation_id="old", event=4)
    response = await h.serve.context(AGENT, ask(conversation_id="c1"))
    assert [t.text for t in response.live] == ["liguei e ninguém atendeu"]
    assert response.live_complete
    # The internal note is left out like an internal item of the pack, and counted with it.
    [live] = [r for r in h.receipts.sent if r.surface is ReceiptSurface.LIVE]
    assert (len(live.item_ids), live.withheld, live.policy_version) == (1, 1, "p1")


async def test_a_busy_number_gets_only_its_newest_live_turns(cell: Cell) -> None:
    # The throughput incident of 24/09: every read decrypted and screened every turn of the hot layer; a
    # test number with hundreds of them starved the read process. The newest turns are what `live` is for.
    h = Harness(cell)
    await h.compile()
    for n in range(LIVE_READ + 30):
        await h.add_turn(PHONE, minutes_ago=0, text=f"turno {n}", conversation_id=f"other-{n}", event=100 + n)
    # add_turn stamps every turn at the same minute: the newest are the highest event numbers here.
    response = await h.serve.context(AGENT, ask(conversation_id="c1"))
    assert len(response.live) == LIVE_TURNS
    assert not response.live_complete  # older turns were left unread, and the answer says so


def starter_space() -> SpaceConfig:
    """The starter policy, a care agent, and a billing agent allowed to prove everything (V4)."""
    base = sample_config()
    sources = [
        s.model_copy(update={"verification_ceiling": Verification.V4}) if s.source_id == BILLING_SOURCE else s
        for s in base.sources
    ]
    care = SourceConfig(
        source_id=CARE_SOURCE,
        name="care",
        channel="voice",
        purposes=frozenset({"care"}),
        audience=Audience.CUSTOMER_AGENT,
        verification_ceiling=Verification.V3,
    )
    return base.model_copy(update={"policy": STARTER_POLICY, "sources": [*sources, care]})


async def test_live_turns_need_the_level_history_needs_and_count_when_withheld(cell: Cell) -> None:
    # A call that proved nothing gets no line of the customer's other channels, only
    # how many it missed; once its conversation proves a level, the lines come.
    world = default_world()
    world.proven = Verification.V0
    h = Harness(cell, world)
    await h.compile()
    before = await h.serve.context(AGENT, ask(verification=Verification.V2))
    await h.add_turn(WA, minutes_ago=4, text="mudei o endereço de entrega", conversation_id="wa-7")
    blind = await h.serve.context(AGENT, ask(verification=Verification.V2))
    assert (blind.verification.effective, blind.live) == (Verification.V0, [])
    assert blind.withheld == before.withheld + 1
    live = h.receipts.sent[-1]
    assert (live.surface, live.item_ids, live.withheld) == (ReceiptSurface.LIVE, (), 1)

    h.world.proven = Verification.V2
    seen = await h.serve.context(AGENT, ask(verification=Verification.V2))
    assert seen.verification.effective is Verification.V2
    assert [t.text for t in seen.live] == ["mudei o endereço de entrega"]


async def test_no_line_of_a_live_conversation_about_health_reaches_the_billing_agent(cell: Cell) -> None:
    # A line that touches health carries the category, and so does the rest of its
    # conversation; the billing agent never reads them, whatever it proves, and the care agent reads
    # them at V3. The other conversation is ordinary history for both.
    cell.config.put(starter_space())
    world = default_world()
    world.proven = Verification.V4
    h = Harness(cell, world)
    await h.compile()
    await h.add_turn(
        WA, minutes_ago=9, text="Já saiu o resultado do exame de sangue?", conversation_id="wa-exam", event=1
    )
    await h.add_turn(WA, minutes_ago=8, text="E quando fica pronto?", conversation_id="wa-exam", event=2)
    await h.add_turn(PHONE, minutes_ago=7, text="A fatura veio em dobro", conversation_id="call-9", event=3)

    config = cell.config.space(SPACE_ID)
    billing = await h.serve.context(
        principal(BILLING_SOURCE, config),
        ask(verification=Verification.V4, conversation_id=None, task_id="t-1"),
    )
    assert billing.verification.effective is Verification.V4
    assert [t.text for t in billing.live] == ["A fatura veio em dobro"]
    assert h.receipts.sent[-1].withheld == 2

    care = await h.serve.context(principal(CARE_SOURCE, config), ask(verification=Verification.V3))
    assert [t.text for t in care.live] == [
        "Já saiu o resultado do exame de sangue?",
        "E quando fica pronto?",
        "A fatura veio em dobro",
    ]
    support = await h.serve.context(principal(VOICE_SOURCE, config), ask(verification=Verification.V3))
    assert [t.text for t in support.live] == ["A fatura veio em dobro"]


async def test_a_masking_rule_masks_live_lines_as_it_masks_items(cell: Cell) -> None:
    config = sample_config()
    policy = config.policy.model_copy(
        update={
            "rules": [
                *config.policy.rules,
                PolicyRule(
                    effect="mask", when=PolicyCondition(category="history", audience=Audience.INTERNAL_AGENT)
                ),
            ]
        }
    )
    cell.config.put(config.model_copy(update={"policy": policy}))
    h = Harness(cell)
    await h.compile()
    await h.add_turn(PHONE, minutes_ago=3, text="quero cancelar o pedido", conversation_id="call-2")
    billing = await h.serve.context(
        principal(BILLING_SOURCE, cell.config.space(SPACE_ID)),
        ask(conversation_id=None, task_id="t-2"),
    )
    assert [t.text for t in billing.live] == ["(mascarado)"]
    agent = await h.serve.context(AGENT, ask())
    assert [t.text for t in agent.live] == ["quero cancelar o pedido"]


async def test_erased_handles_lose_their_older_live_turns_and_a_degraded_hot_layer_is_declared(
    cell: Cell,
) -> None:
    h = Harness(cell)
    await h.compile()
    await h.add_turn(WA, minutes_ago=10, text="antes do apagamento", event=1)
    await h.add_turn(PHONE, minutes_ago=9, text="outro número", event=2)
    h.world.feed.append(
        {
            "id": uid(0xF3),
            "kind": "erasure",
            "profile_ids": [],
            "handle_ids": [WA],
            "privacy_version": 1,
            "created_at": T0,
        }
    )
    h.world.degraded.add("hot_layer")
    response = await h.serve.context(AGENT, ask())
    assert [t.text for t in response.live] == ["outro número"]
    assert response.live_complete is False


async def test_when_compilation_fails_the_hot_layer_answers_alone(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(cell)

    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(h.compiler, "compile_on_demand", broken)
    empty = await h.serve.context(AGENT, ask())
    assert (empty.path, empty.degraded) == (DeliveryPath.T4, True)
    await h.add_turn(PHONE, minutes_ago=3, text="estou esperando")
    hot_only = await h.serve.context(AGENT, ask())
    assert (hot_only.path, hot_only.text, hot_only.degraded) == (DeliveryPath.T3, None, True)
    assert [t.text for t in hot_only.live] == ["estou esperando"]


async def test_receipts_the_queue_does_not_confirm_are_committed_before_the_answer(cell: Cell) -> None:
    # Past the 20 ms budget the receipt goes to the outbox in the database, not to memory, so a
    # crash of the read process right after the answer loses nothing.
    h = Harness(cell)
    h.receipts.hang = True
    await h.serve.context(AGENT, ask())
    assert h.receipts.sent == []
    assert len(h.buffer) == 0
    [spooled] = h.outbox(TaskName.RECORD_RECEIPTS)
    [draft] = spooled.payload["drafts"]
    assert (draft["kind"], draft["surface"], draft["producer_seq"]) == ("read", "pack", 1)
    h.receipts.hang = False
    await h.serve.context(AGENT, ask())
    assert [r.producer_seq for r in h.receipts.sent] == [2]
    await h.serve.heartbeat(final=True)
    assert h.receipts.marks[-1].last_seq == 2
    assert h.receipts.marks[-1].final

    # The writer task chains it like any receipt, and a second delivery chains nothing twice.
    audit = AuditService(cell.uow, NoSource(), cell.objects, cell.clock)
    await audit.record_receipts(spooled)
    await audit.record_receipts(spooled)
    async with cell.uow(SPACE_ID) as uow:
        [stored] = await uow.audit.list_receipts(SPACE_ID, ReceiptQuery(limit=10))
    assert (stored.draft.producer_seq, stored.seq) == (1, 1)


async def test_the_answer_waits_for_the_receipt_while_the_pin_is_written(cell: Cell) -> None:
    # No answer before the queue confirms. The pin, the cache's note of the bytes the conversation
    # reads, is written while the confirmation is awaited: the bytes still leave only after it.
    # The receipt timeout is stretched so the wait below is bound only by `confirm`, an asyncio.Event,
    # never by the real 20 ms budget racing the test process under load (a wall-clock flake, not a bug).
    h = Harness(cell, receipt_timeout_s=3600)
    await h.compile()
    pin = pin_key(0, SPACE_ID, PROFILE, "wa-now", "chat", WHATSAPP_SOURCE)
    confirm = asyncio.Event()

    async def slow_emit(drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        await confirm.wait()
        h.receipts.sent.extend(drafts)
        return True

    h.receipts.emit = slow_emit  # type: ignore[method-assign]
    read = asyncio.create_task(h.serve.context(AGENT, ask()))
    for _ in range(20):
        await asyncio.sleep(0)
    assert not read.done()
    assert pin in cell.cache.values
    assert h.receipts.sent == []
    confirm.set()
    assert (await read).path is DeliveryPath.T0
    assert len(h.receipts.sent) == 1


async def test_a_full_buffer_stops_sensitive_content_and_an_exhausted_one_stops_reads(cell: Cell) -> None:
    # Memory holds receipts only when neither the queue nor the database takes them.
    h = Harness(cell, capacity=1)
    await h.compile()
    h.receipts.confirm = False
    h.spool.down = True
    assert (await h.serve.context(AGENT, ask())).path is DeliveryPath.T0
    assert h.buffer.full
    # The stored packs may carry sensitive items; a pack compiled without them is served instead.
    assert (await h.serve.context(AGENT, ask())).path is DeliveryPath.T2
    assert h.receipts.sent == []
    assert h.buffer.exhausted
    with pytest.raises(Unavailable):
        await h.serve.context(AGENT, ask())
    h.receipts.confirm = True
    assert await h.serve.flush_receipts()
    assert (await h.serve.context(AGENT, ask())).path is DeliveryPath.T0


async def test_about_needs_an_active_link_and_brings_the_account_block(cell: Cell) -> None:
    org_handle = uid(0xE100)
    h = Harness(cell)
    h.world.handles.append(
        handle_record(org_handle, HandleType.SYSTEM_ID, "acme-1", scope="crm", kind=SubjectKind.ACCOUNT)
    )
    h.world.snapshots.append(
        MemorySnapshot(
            profile=profile(pid=uid(0xD100), kind=SubjectKind.ACCOUNT, handles=(org_handle,)),
            facts=(fact(50, "sla", "4 horas", "contract", handle=org_handle),),
        )
    )
    acme = Handle(type=HandleType.SYSTEM_ID, value="acme-1", scope="crm", subject_kind=SubjectKind.ACCOUNT)
    # Without the link the hot read fails soft: the person's own pack, flagged, the same for an organization
    # that exists and one that does not.
    alone = await h.serve.context(AGENT, ask())
    unlinked = await h.serve.context(AGENT, ask(about=acme))
    unknown = await h.serve.context(
        AGENT, ask(about=Handle(type=HandleType.SYSTEM_ID, value="nope", scope="crm"))
    )
    assert (unlinked.about_unlinked, unknown.about_unlinked, alone.about_unlinked) == (True, True, False)
    assert unlinked.text == unknown.text == alone.text
    assert "[Conta]" not in (unlinked.text or "")
    h.world.links.append(
        LinkRecord(uid(0x800), PHONE, org_handle, "buyer", True, T0 - timedelta(days=9), None)
    )
    response = await h.serve.context(AGENT, ask(about=acme, verification=Verification.V1))
    assert "[Conta]" in (response.text or "")
    assert not response.about_unlinked
    assert response.path is DeliveryPath.T2
    # The skeleton pins what is always small; the turn that asks for the contract gets it in its slots.
    asked = await h.serve.context(AGENT, ask(about=acme, verification=Verification.V1, query="Qual o sla?"))
    assert "sla: 4 horas" in (asked.text or "") + (asked.slots or "")
    assert h.receipts.sent[-1].about_handle_id == org_handle


async def test_an_object_read_centres_on_the_object(cell: Cell) -> None:
    h = Harness(cell)
    response = await h.serve.context(
        AGENT, ContextRequest(object=ObjectRef(type="invoice", namespace="erp", id="0823"), view="chat")
    )
    assert response.path is DeliveryPath.T2
    assert h.receipts.sent[-1].object_id == uid(0x700)


async def test_coverage_lists_the_sources_that_feed_the_memory_and_the_silent_ones(cell: Cell) -> None:
    h = Harness(cell)
    now = cell.clock.now()
    removed = uid(0xE777)
    h.world.last_event_at = {
        WHATSAPP_SOURCE: now - timedelta(minutes=5),
        VOICE_SOURCE: now - timedelta(hours=3),
        removed: now,
    }
    response = await h.serve.context(AGENT, ask())
    coverage = {c.source_id: (c.status, c.last_event_at) for c in response.coverage}
    assert coverage == {
        str(WHATSAPP_SOURCE): ("ok", now - timedelta(minutes=5)),
        str(VOICE_SOURCE): ("silent", now - timedelta(hours=3)),
    }
    # A space's coverage is read once and shared by the reads of the next half minute.
    h.world.last_event_at = {}
    assert (await h.serve.context(AGENT, ask())).coverage == response.coverage
    cell.clock.advance(seconds=31)
    # Past the half minute the last answer still serves while one background read renews it.
    assert (await h.serve.context(AGENT, ask())).coverage == response.coverage
    await h.serve.drain()
    assert (await h.serve.context(AGENT, ask())).coverage == []


async def test_a_bound_read_by_object_stays_on_the_bound_customer(cell: Cell) -> None:
    h = Harness(cell)
    invoice = ObjectRef(type="invoice", namespace="erp", id="0823")
    mine = await h.serve.context(AGENT, ContextRequest(object=invoice), bound=SUBJECT)
    assert mine.path is DeliveryPath.T2
    assert h.receipts.sent[-1].object_id == uid(0x700)
    stranger = Handle(type=HandleType.PHONE_E164, value="+55 11 98888-0002")
    with pytest.raises(NotFound):
        await h.serve.context(AGENT, ContextRequest(object=invoice), bound=stranger)


async def test_a_policy_change_invalidates_cached_packs(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    config = sample_config()
    cell.config.put(config.model_copy(update={"policy": config.policy.model_copy(update={"version": "p2"})}))
    assert (await h.serve.context(AGENT, ask())).path is DeliveryPath.T2


async def test_a_competing_source_gets_its_own_pack(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    config = sample_config()
    policy = config.policy.model_copy(update={"competes_with": {str(AGENT.source_id): [str(uid(0xE999))]}})
    cell.config.put(config.model_copy(update={"policy": policy}))
    assert (await h.serve.context(AGENT, ask())).path is DeliveryPath.T2


async def test_target_model_directives_come_with_the_text(cell: Cell) -> None:
    config = sample_config()
    cell.config.put(
        config.model_copy(
            update={"settings": config.settings.model_copy(update={"target_model_floors": {"anthropic": 30}})}
        )
    )
    h = Harness(cell)
    response = await h.serve.context(AGENT, ask(target=TargetModel(provider="anthropic", model="m")))
    assert response.cache is not None
    assert response.cache.cacheable
    assert len(response.cache.breakpoints) == 2


async def test_a_known_target_model_gets_its_floor_without_settings(cell: Cell) -> None:
    h = Harness(cell)
    response = await h.serve.context(AGENT, ask(target=TargetModel(provider="openai", model="gpt-realtime")))
    assert response.cache is not None
    assert response.cache.floor_tokens == 1024
    unknown = await h.serve.context(AGENT, ask(target=TargetModel(provider="acme", model="m")))
    assert unknown.cache is not None
    assert unknown.cache.floor_tokens is None


async def test_the_full_view_is_for_internal_sources_only(cell: Cell) -> None:
    # Everything the policy allows, in 4,000 tokens, never reaches an agent that talks to the
    # customer; an internal source reads it under its own policy and level.
    h = Harness(cell)
    with pytest.raises(Forbidden):
        await h.serve.context(AGENT, ask(view="full"))
    internal = await h.serve.context(
        principal(BILLING_SOURCE),
        ask(view="full", verification=Verification.NO_CUSTOMER, conversation_id=None),
    )
    assert internal.text
    # In a conversation with the customer the internal key talks to the customer.
    with pytest.raises(Forbidden):
        await h.serve.context(
            principal(BILLING_SOURCE), ask(view="full", verification=Verification.NO_CUSTOMER)
        )


async def test_reads_by_profile_id_and_scope_checks(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    voice = principal(VOICE_SOURCE)
    answer = await h.serve.context_answer_for_profile(
        voice, PROFILE, view="voice", verification=Verification.V1
    )
    anonymous = await answer.delivered()
    # Read by profile id there is no channel handle to prove anything: V0 unless the call proved more.
    # V0 is in the eager set, so the read is a cache hit all the same.
    assert anonymous.verification.effective is Verification.V0
    assert anonymous.path is DeliveryPath.T0
    h.world.proven = Verification.V1
    answer = await h.serve.context_answer_for_profile(
        voice, PROFILE, view="voice", verification=Verification.V1, conversation_id="call-1"
    )
    response = await answer.delivered()
    assert response.path is DeliveryPath.T0
    assert response.text is not None
    with pytest.raises(Forbidden):
        await h.serve.context(principal(scopes=frozenset({Scope.TRACK})), ask())


async def test_the_pack_of_a_recent_object_is_ready_before_the_task_agent_asks(cell: Cell) -> None:
    # An internal agent's task view on an invoice that just changed is compiled ahead, like the
    # usual views; before, a read centred on an object always compiled on the read path.
    from niadra.app.compile import object_variants
    from niadra.config.models import TaskView
    from niadra.testing.cell import BILLING_SOURCE

    config = sample_config().model_copy(
        update={
            "views": [
                TaskView(name="task:collections", object_types=["invoice"], purposes=frozenset({"billing"}))
            ]
        }
    )
    cell.config.put(config)
    h = Harness(cell)
    agent = principal(BILLING_SOURCE, config)
    [variant] = object_variants(config, marina().objects, T0)
    assert (variant.view, variant.focus_object_id) == ("task:collections", uid(0x700))
    assert object_variants(config, marina().objects, T0 + timedelta(days=30)) == []  # only recent objects
    await h.compile()
    request = ContextRequest(
        object=ObjectRef(type="invoice", namespace="erp", id="0823"),
        view="task:collections",
        verification=Verification.NO_CUSTOMER,
        task_id="t-1",
    )
    response = await h.serve.context(agent, request)
    assert response.path in (DeliveryPath.T0, DeliveryPath.T1)
    assert h.receipts.sent[-1].object_id == uid(0x700)


def firm(h: Harness) -> tuple[UUID, Handle]:
    """A firm with a fact of its own and a case it owns, as a court system reports it."""
    org_handle = uid(0xE500)
    h.world.handles.append(
        handle_record(org_handle, HandleType.SYSTEM_ID, "firm-1", scope="cases", kind=SubjectKind.ACCOUNT)
    )
    case = BusinessObject(
        uid(0x750),
        ObjectKey("legal_case", "cases", "case-1"),
        {"status": "active"},
        T0 - timedelta(days=3),
        WHATSAPP_SOURCE,
        org_handle,
    )
    h.world.snapshots.append(
        MemorySnapshot(
            profile=profile(pid=uid(0xD500), kind=SubjectKind.ACCOUNT, handles=(org_handle,)),
            facts=(fact(55, "sla", "48 horas", "contract", handle=org_handle),),
            objects=(case,),
        )
    )
    return org_handle, Handle(
        type=HandleType.SYSTEM_ID, value="firm-1", scope="cases", subject_kind=SubjectKind.ACCOUNT
    )


async def test_a_task_read_of_the_firms_case_about_the_firm_is_not_unlinked(cell: Cell) -> None:
    """B20 (03/10/2026): a drafting task reads by the case, the firm's object, and names the firm in `about`
    (the SDK sends no subject with an object). The read already is the firm's memory: `about` names its own
    subject, which needs no link, and the read was flagged unlinked."""
    h = Harness(cell)
    _, the_firm = firm(h)
    case = ObjectRef(type="legal_case", namespace="cases", id="case-1")
    response = await h.serve.context(
        AGENT,
        ContextRequest(
            object=case, about=the_firm, view="chat", verification=Verification.V1, conversation_id="wa-now"
        ),
    )
    assert not response.about_unlinked
    assert "sla: 48 horas" in (response.text or "")


async def test_an_object_of_a_linked_person_brings_the_firms_block(cell: Cell) -> None:
    """The object belongs to a person (Marina's invoice), read with no handle: her link to the firm counts as
    it does for a read through her handle."""
    h = Harness(cell)
    org_handle, the_firm = firm(h)
    invoice = ObjectRef(type="invoice", namespace="erp", id="0823")
    alone = await h.serve.context(
        AGENT,
        ContextRequest(
            object=invoice,
            about=the_firm,
            view="chat",
            verification=Verification.V1,
            conversation_id="wa-now",
        ),
    )
    assert alone.about_unlinked
    h.world.links.append(
        LinkRecord(uid(0x850), PHONE, org_handle, "lawyer", True, T0 - timedelta(days=9), None)
    )
    linked = await h.serve.context(
        AGENT,
        ContextRequest(
            object=invoice,
            about=the_firm,
            view="chat",
            verification=Verification.V1,
            conversation_id="wa-now",
        ),
    )
    assert not linked.about_unlinked
    assert "[Conta]" in (linked.text or "")
