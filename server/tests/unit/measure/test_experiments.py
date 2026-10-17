"""Experiments on an element and on an agent (24.7): drawn per person like the memory holdout,
kept after a union, recorded with their kind and arm, and applied to what the read delivers."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from niadra.app.audit import _project
from niadra.app.serve import BlockAsk
from niadra.config.measurement import Arm as ArmConfig
from niadra.config.measurement import ElementExperiment, MeasurementSettings
from niadra.config.models import FeatureSettings, SpaceConfig
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.turns import TurnRecord
from niadra.domain.compile.constraints import build
from niadra.domain.measure.experiments import (
    Arm,
    agent_control,
    arm_of,
    arms_of_text,
    arms_text,
    assign_arms,
    held_sections,
    withheld_elements,
)
from niadra.domain.receipts import ReceiptDraft
from niadra.domain.signals.derive import Catalog, Entry, fold
from niadra.domain.vocabulary import (
    Audience,
    DeliveryPath,
    Feature,
    HandleType,
    ReceiptKind,
    ReceiptSurface,
    Verification,
)
from niadra.ports.turns import AppliedTurn
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.flow.harness import Flow

PHONE = "+5511955553333"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
START = datetime(2026, 1, 1, tzinfo=UTC)
NO_BLACK = {
    "kind": "preference",
    "attr": "item_variant.color",
    "op": "not_in",
    "values": ["preto"],
    "strength": "must",
    "scope": "persistent",
}
VECTORS = json.loads(
    (Path(__file__).parents[1] / "signals" / "vectors" / "signal-affinity.v0.json").read_text()
)


def experiment(name: str, **change: Any) -> ElementExperiment:
    fields: dict[str, Any] = {
        "name": name,
        "kind": "element",
        "element": "constraints",
        "arms": [ArmConfig(name="control", fraction=0.5), ArmConfig(name="on", fraction=0.5)],
        "salt": "salt-of-the-experiment",
        "starts_at": START,
    }
    fields.update(change)
    return ElementExperiment(**fields)


# The draw.


def test_the_draw_lands_in_the_arms_by_their_fractions() -> None:
    arms = [ArmConfig(name="control", fraction=0.2), ArmConfig(name="on", fraction=0.8)]
    drawn = [arm_of("salt-a", arms, uuid4()) for _ in range(4000)]
    assert 0.17 < drawn.count("control") / len(drawn) < 0.23
    handle = uuid4()
    assert {arm_of("salt-a", arms, handle) for _ in range(5)} == {arm_of("salt-a", arms, handle)}


def test_an_arm_covers_the_read_its_experiment_names_and_keeps_what_the_profile_got_first() -> None:
    now = START + timedelta(days=1)
    experiments = [
        experiment("constraints_holdout"),
        experiment("size_bonus", element="size", source_ids=frozenset({VOICE_SOURCE})),
        experiment("sales_agent", kind="agent", element=None, agent="voice-agent"),
        experiment("rank", kind="interleave", element=None, tool="search", arms=[]),
        experiment("later", starts_at=now + timedelta(days=1)),
    ]
    handle = uuid4()

    def arms(source: UUID, name: str, kept: dict[str, str] | None = None) -> dict[str, str]:
        found = assign_arms(
            experiments,
            source_id=source,
            source_name=name,
            critical_source=False,
            oldest_handle_id=handle,
            kept=kept or {},
            now=now,
        )
        return {a.experiment: a.arm for a in found}

    # No interleaving (drawn per turn by the SDK), nothing before it starts, a source it does not name.
    assert set(arms(WHATSAPP_SOURCE, "whatsapp-bot")) == {"constraints_holdout"}
    assert set(arms(VOICE_SOURCE, "voice-agent")) == {"constraints_holdout", "size_bonus", "sales_agent"}
    # The arm a profile got first stays; one the experiment no longer declares is drawn again.
    assert arms(VOICE_SOURCE, "voice-agent", {"size_bonus": "control"})["size_bonus"] == "control"
    assert arms(VOICE_SOURCE, "voice-agent", {"size_bonus": "gone"})["size_bonus"] in ("control", "on")
    # Critical sources never join a control arm (19.7).
    none = assign_arms(
        experiments,
        source_id=VOICE_SOURCE,
        source_name="voice-agent",
        critical_source=True,
        oldest_handle_id=handle,
        kept={},
        now=now,
    )
    assert none == ()


def test_the_arms_travel_on_the_receipt_and_say_what_is_held_out() -> None:
    arms = (
        Arm("constraints_holdout", "element", "control", "constraints"),
        Arm("soft_holdout", "element", "on", "soft"),
        Arm("sales_agent", "agent", "control"),
    )
    assert arms_of_text(arms_text(arms)) == [
        ("constraints_holdout", "element", "control"),
        ("soft_holdout", "element", "on"),
        ("sales_agent", "agent", "control"),
    ]
    assert arms_of_text("broken;x=memory:control;") == []
    assert withheld_elements(arms) == {"constraints"}
    assert agent_control(arms) == arms[2]


def test_the_control_arm_reads_the_block_without_the_inferred_soft_entries_or_size() -> None:
    def case(case_id: str) -> tuple[Any, datetime]:
        found = next(c for c in VECTORS["cases"] if c["id"] == case_id)
        now = datetime.fromisoformat(found["now"])
        entries = [
            Entry(f"e{i}", datetime.fromisoformat(e["at"]), e["interaction"], e["session"])
            for i, e in enumerate(found["entries"])
        ]
        return fold(None, entries, now, Catalog(found.get("catalog", {}).get("families", {}))), now

    soft, now = case("evidence.three_sessions_infer_a_soft_preference")
    families = Catalog()
    with_soft = build(soft, for_="self", session=None, now=now, catalog=families)
    without = build(soft, for_="self", session=None, now=now, catalog=families, withheld=frozenset({"soft"}))
    assert [s["source"] for s in with_soft["soft"]] == ["inferred"]
    assert "soft" not in without
    assert without["version"] != with_soft["version"]

    size, now = case("size.an_inferred_size_applies_only_when_asked")
    catalog = Catalog({"item_variant.size_label": "size"})
    asked = build(size, for_="self", session=None, now=now, catalog=catalog)
    held = build(size, for_="self", session=None, now=now, catalog=catalog, withheld=frozenset({"size"}))
    assert [a["source"] for a in asked["attributes"]] == ["inferred"]
    assert "attributes" not in held


def test_only_the_sections_a_measurement_sees_can_be_held_out() -> None:
    with pytest.raises(ValueError, match="section"):
        experiment("history_holdout", element="pack_section", section="history")
    held = experiment("episodes_holdout", element="pack_section", section="episodes")
    arms = assign_arms(
        [held], source_id=WHATSAPP_SOURCE, source_name=None, critical_source=False, oldest_handle_id=uuid4(),
        kept={"episodes_holdout": "control"}, now=START,
    )  # fmt: skip
    assert held_sections(arms) == {"episode"}
    assert held_sections([Arm("episodes_holdout", "element", "on", "pack_section", "episodes")]) == set()


# On the read.


def configured(*experiments: ElementExperiment, measurement: bool = True) -> SpaceConfig:
    features = {Feature.SIGNALS, Feature.MEASUREMENT} if measurement else {Feature.SIGNALS}
    return sample_config().model_copy(
        update={
            "features": FeatureSettings(enabled=frozenset(features)),
            "measurement": MeasurementSettings(experiments=list(experiments)),
        }
    )


async def a_customer(flow: Flow) -> UUID:
    item = {
        "type": "event",
        "idempotency_key": "e1",
        "channel": "whatsapp",
        "conversation_id": "wa-1",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": "Quero um vestido"},
        "occurred_at": flow.cell.clock.now().isoformat(),
    }
    assert (await flow.send(WHATSAPP_SOURCE, item)).errors == []
    await flow.drain()
    async with flow.cell.uow(SPACE_ID) as uow:
        record = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert record is not None
        profile = await uow.identity.profile_of(record.handle_id)
    assert profile is not None
    return profile.profile_id


async def keep(flow: Flow, profile_id: UUID, experiment_name: str, arm: str, kind: str = "element") -> None:
    async with flow.cell.uow(SPACE_ID) as uow:
        await uow.serve.project_assignment(
            experiment=experiment_name,
            profile_id=profile_id,
            group=arm,
            assigned_at=flow.cell.clock.now(),
            kind=kind,
        )


async def keep_again(flow: Flow, profile_id: UUID, experiment_name: str, arm: str) -> None:
    """A test's shortcut past the first-assignment rule: a new arm for the profile, as another space would
    draw it."""
    async with flow.cell.uow(SPACE_ID) as uow:
        store = uow.serve._groups
        store[(experiment_name, profile_id)] = (arm, flow.cell.clock.now(), "element")
    flow.serve.read_state._spaces.pop(SPACE_ID, None)


async def read(flow: Flow, source: UUID = VOICE_SOURCE, conversation: str = "call-1") -> Any:
    await flow.prove(source, conversation, PHONE)
    request = ContextRequest(
        subject=SUBJECT, conversation_id=conversation, verification=Verification.V1, include=["constraints"]
    )
    return await flow.serve.context(principal(source, flow.cell.config.space(SPACE_ID)), request)


async def said(flow: Flow) -> None:
    now = flow.cell.clock.now()
    record = TurnRecord.model_validate(
        {
            "turn_id": "t1",
            "conversation_id": "wa-1",
            "agent": {"name": "stylist"},
            "started_at": now.isoformat(),
            "content_mode": "hash_only",
            "interactions": [NO_BLACK],
        }
    )
    async with flow.cell.uow(SPACE_ID) as uow:
        await flow.signals.consume(uow, SPACE_ID, [AppliedTurn(WHATSAPP_SOURCE, now, record)])


async def test_the_control_arm_of_the_constraints_block_reads_without_it_and_the_arm_is_kept(
    cell: Cell,
) -> None:
    cell.config.put(configured(experiment("constraints_holdout")))
    flow = Flow.build(cell)
    profile_id = await a_customer(flow)
    await said(flow)
    await keep(flow, profile_id, "constraints_holdout", "control")

    held = await read(flow)
    assert held.constraints is None
    assert not held.degraded
    assert held.path is not DeliveryPath.HOLDOUT
    assert held.text
    pack = next(r for r in flow.receipts.sent if r.surface is ReceiptSurface.PACK)
    assert arms_of_text(pack.extra["arms"]) == [("constraints_holdout", "element", "control")]
    assert not [r for r in flow.receipts.sent if r.surface is ReceiptSurface.CONSTRAINTS]

    # The other arm reads the block; and its own route answers the control arm as a first contact.
    await keep_again(flow, profile_id, "constraints_holdout", "on")
    shown = await read(flow, conversation="call-2")
    assert shown.constraints is not None
    assert [h.values for h in shown.constraints.hard] == [["preto"]]


async def test_the_blocks_own_route_follows_the_arm(cell: Cell) -> None:
    cell.config.put(configured(experiment("constraints_holdout")))
    flow = Flow.build(cell)
    profile_id = await a_customer(flow)
    await said(flow)
    await keep(flow, profile_id, "constraints_holdout", "control")

    caller = principal(VOICE_SOURCE, cell.config.space(SPACE_ID))
    block = await flow.serve.read_block(caller, "constraints", SUBJECT, BlockAsk("call-9", None, "chat"))
    assert block.hard == []
    assert block.version.startswith("cv_")
    receipt = flow.receipts.sent[-1]
    assert arms_of_text(receipt.extra["arms"]) == [("constraints_holdout", "element", "control")]


async def test_an_agent_experiment_holds_out_that_agent_alone(cell: Cell) -> None:
    cell.config.put(configured(experiment("voice_holdout", kind="agent", element=None, agent="voice-agent")))
    flow = Flow.build(cell)
    profile_id = await a_customer(flow)
    await keep(flow, profile_id, "voice_holdout", "control", kind="agent")

    voice = await read(flow, VOICE_SOURCE)
    assert (voice.path, voice.text) == (DeliveryPath.HOLDOUT, "")
    holdout = flow.receipts.sent[-1]
    assert holdout.kind is ReceiptKind.HOLDOUT
    assert holdout.experiment_group == "voice_holdout:control"
    assert arms_of_text(holdout.extra["arms"]) == [("voice_holdout", "agent", "control")]
    other = await read(flow, WHATSAPP_SOURCE)
    assert other.path is not DeliveryPath.HOLDOUT
    assert other.text


async def test_element_experiments_wait_for_the_measurement_feature(cell: Cell) -> None:
    cell.config.put(configured(experiment("constraints_holdout"), measurement=False))
    flow = Flow.build(cell)
    profile_id = await a_customer(flow)
    await said(flow)
    await keep(flow, profile_id, "constraints_holdout", "control")
    found = await read(flow)
    assert found.constraints is not None
    pack = next(r for r in flow.receipts.sent if r.surface is ReceiptSurface.PACK)
    assert "arms" not in pack.extra


async def test_the_receipt_writer_keeps_the_first_arm_with_its_kind(cell: Cell) -> None:
    profile_id = uuid4()

    def draft(arms: str, group: str | None = None) -> ReceiptDraft:
        return ReceiptDraft(
            receipt_id=uuid4(),
            space_id=SPACE_ID,
            producer_id="read-1",
            producer_seq=1,
            kind=ReceiptKind.READ,
            surface=ReceiptSurface.PACK,
            audience=Audience.CUSTOMER_AGENT,
            read_at=cell.clock.now(),
            profile_id=profile_id,
            experiment_group=group,
            extra={"arms": arms},
        )

    async with cell.uow(SPACE_ID) as uow:
        await _project(
            uow,
            [
                draft("size_bonus=element:control;voice=agent:control", group="voice:control"),
                draft("size_bonus=element:on"),
            ],
        )
        assert await uow.serve.get_assignments(profile_id) == {"size_bonus": "control", "voice": "control"}
        kinds = {k[0]: v[2] for k, v in uow.serve._groups.items() if k[1] == profile_id}
    assert kinds == {"size_bonus": "element", "voice": "agent"}
