"""The reviewable profile (24.5.3; LGPD, art. 20) across the real modules: every inference seen with what it
rests on, corrected into what the subject says, and deleted so the same evidence never infers it again, on
the read, in the derivation and in the subject's package."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from niadra.adapters.memory.signals import InMemorySignalRepository
from niadra.app.inferences import InferencesService
from niadra.app.signals import exported_suppressions
from niadra.config.models import FeatureSettings
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.signals import ConstraintsBlock, Inference, InferenceCorrection
from niadra.contracts.turns import TurnRecord
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.signals.derive import value_token
from niadra.domain.signals.records import SELF, SuppressionRow
from niadra.domain.vocabulary import Feature, HandleType, Verification
from niadra.ports.turns import AppliedTurn
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.flow.harness import Flow

PHONE = "+5511955554444"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
BEGE = ("item_variant.color", "bege")


def said(conversation: str, at: datetime) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-hi",
        "channel": "whatsapp",
        "conversation_id": conversation,
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": "Quero um vestido"},
        "occurred_at": at.isoformat(),
    }


def bought(turn_id: str, conversation: str, at: datetime, color: str = "bege") -> AppliedTurn:
    """A list shown and its first item put in the cart: a session's worth of signal for its color."""
    exposure = str(uuid4())
    ref = f"item_variant:store:{turn_id}"
    record = TurnRecord.model_validate(
        {
            "turn_id": turn_id,
            "conversation_id": conversation,
            "agent": {"name": "stylist"},
            "started_at": at.isoformat(),
            "content_mode": "hash_only",
            "interactions": [
                {
                    "kind": "presented", "exposure_id": exposure, "list_id": "results",
                    "items": [{"pos": 1, "ref": ref, "shown": {"color": color}}],
                    "delivered_at": at.isoformat(),
                },
                {"kind": "engaged", "exposure_id": exposure, "ref": ref, "how": "add_to_cart"},
            ],
        }
    )  # fmt: skip
    return AppliedTurn(WHATSAPP_SOURCE, at, record)


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    """A customer who bought beige in three conversations, nothing derived yet."""
    config = sample_config().model_copy(
        update={"features": FeatureSettings(enabled=frozenset({Feature.SIGNALS}))}
    )
    cell.config.put(config)
    flow = Flow.build(cell)
    now = cell.clock.now()
    for conversation in ("wa-1", "wa-2", "wa-3"):
        assert (await flow.send(WHATSAPP_SOURCE, said(conversation, now))).errors == []
    await flow.drain()
    await apply(flow, *(bought(f"t{i}", f"wa-{i}", now) for i in (1, 2, 3)))
    return flow


async def apply(flow: Flow, *turns: AppliedTurn) -> None:
    async with flow.cell.uow(SPACE_ID) as uow:
        await flow.signals.consume(uow, SPACE_ID, list(turns))


async def profile_id(flow: Flow) -> UUID:
    async with flow.cell.uow(SPACE_ID) as uow:
        record = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert record is not None
        profile = await uow.identity.profile_of(record.handle_id)
    assert profile is not None
    return profile.profile_id


def service(flow: Flow) -> InferencesService:
    return InferencesService(flow.cell.uow, flow.signals)


def governance(flow: Flow) -> Any:
    return principal(WHATSAPP_SOURCE, flow.cell.config.space(SPACE_ID))


async def inferences(flow: Flow) -> list[Inference]:
    return (await service(flow).page(governance(flow), await profile_id(flow), cursor=None, limit=200)).items


async def block(flow: Flow, conversation: str) -> ConstraintsBlock:
    await flow.prove(VOICE_SOURCE, conversation, PHONE)
    request = ContextRequest(
        subject=SUBJECT, conversation_id=conversation, verification=Verification.V1, include=["constraints"]
    )
    response = await flow.serve.context(principal(VOICE_SOURCE, flow.cell.config.space(SPACE_ID)), request)
    assert response.constraints is not None
    return response.constraints


def soft(found: ConstraintsBlock) -> list[tuple[str, Any, str]]:
    return [(s.attr, s.value, s.source) for s in found.soft]


def beige(found: list[Inference]) -> Inference:
    return next(i for i in found if (i.attr, i.value) == BEGE)


async def test_every_inference_is_listed_with_what_it_rests_on_and_named_by_a_keyed_hash(flow: Flow) -> None:
    found = await inferences(flow)
    color = beige(found)
    assert (color.kind, color.polarity, color.for_, color.origin) == (
        "soft_constraint",
        "prefer",
        "self",
        "interactions",
    )
    assert color.evidence == {"exposure": 3.0, "signal": 4.5, "events": 3.0, "sessions": 3, "lift": 0.5062}
    assert color.confidence == 0.375
    assert color.used_for == ["personalization"]
    assert "bege" not in color.key
    assert len(color.key) == 32
    interests = sorted(i.ref for i in found if i.kind == "interest")
    assert interests == [f"item_variant:store:t{i}" for i in (1, 2, 3)]
    # The same listing a page at a time, by key.
    first = await service(flow).page(governance(flow), await profile_id(flow), cursor=None, limit=2)
    rest = await service(flow).page(
        governance(flow), await profile_id(flow), cursor=first.next_cursor, limit=200
    )
    assert [i.key for i in first.items + rest.items] == sorted(i.key for i in found)
    assert rest.next_cursor is None


async def test_a_deleted_inference_is_never_inferred_again_from_the_same_evidence(flow: Flow) -> None:
    assert ("item_variant.color", "bege", "inferred") in soft(await block(flow, "call-1"))
    key = beige(await inferences(flow)).key
    await service(flow).delete(governance(flow), await profile_id(flow), key)

    assert soft(await block(flow, "call-2")) == []
    assert all((i.attr, i.value) != BEGE for i in await inferences(flow))
    # The derivation folds the same pending interactions: the evidence up to the deletion still counts for
    # nothing.
    flow.cell.clock.advance(minutes=6)
    assert await flow.signals.derive_due(SPACE_ID) == 1
    assert soft(await block(flow, "call-3")) == []
    # The tombstone is kept and exported.
    repo = InMemorySignalRepository(flow.cell.uow.store)
    [tombstone] = repo.suppressed.values()
    assert (tombstone.beneficiary_id, tombstone.kind) == (SELF, "soft_constraint")
    # What happens after the deletion can infer it anew.
    now = flow.cell.clock.now()
    for i in (4, 5, 6):
        assert (await flow.send(WHATSAPP_SOURCE, said(f"wa-{i}", now))).errors == []
    await flow.drain()
    await apply(flow, *(bought(f"t{i}", f"wa-{i}", now) for i in (4, 5, 6)))
    assert ("item_variant.color", "bege", "inferred") in soft(await block(flow, "call-4"))


async def test_a_warm_read_honors_a_deletion_without_a_statement(flow: Flow) -> None:
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    await block(flow, "call-1")
    await service(flow).delete(governance(flow), await profile_id(flow), beige(await inferences(flow)).key)
    repo = InMemorySignalRepository(flow.cell.uow.store)
    repo.statements.clear()
    assert soft(await block(flow, "call-2")) == []
    assert repo.statements == []


async def test_a_tombstone_the_derivation_reads_undoes_a_body_written_before_it(flow: Flow) -> None:
    # A deletion that raced a derivation: the tombstone is stored, the body the derivation wrote is not.
    [handle_id] = {r.handle_id for r in InMemorySignalRepository(flow.cell.uow.store).pending.values()}
    key = await flow.signals.inference_key(SPACE_ID, SELF, value_token(*BEGE))
    now = flow.cell.clock.now()
    async with flow.cell.uow(SPACE_ID) as uow:
        await uow.signals.add_suppressions(
            [SuppressionRow(handle_id, SELF, key, "soft_constraint", now, now)]
        )
    flow.cell.clock.advance(minutes=6)
    assert await flow.signals.derive_due(SPACE_ID) == 1
    assert soft(await block(flow, "call-1")) == []


async def test_a_correction_becomes_what_the_subject_says_and_the_inference_goes(flow: Flow) -> None:
    key = beige(await inferences(flow)).key
    corrected = await service(flow).correct(
        governance(flow), await profile_id(flow), key, InferenceCorrection(value="azul")
    )
    assert (corrected.attr, corrected.value) == BEGE
    found = await block(flow, "call-2")
    assert soft(found) == [("item_variant.color", "azul", "stated")]
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    assert soft(await block(flow, "call-3")) == [("item_variant.color", "azul", "stated")]
    # An interest is deleted, not corrected; a key nobody holds is unknown.
    interest = next(i for i in await inferences(flow) if i.kind == "interest")
    with pytest.raises(InvalidInput):
        await service(flow).correct(
            governance(flow), await profile_id(flow), interest.key, InferenceCorrection(value="x")
        )
    with pytest.raises(NotFound):
        await service(flow).delete(governance(flow), await profile_id(flow), "0" * 32)


async def test_the_subjects_package_lists_what_they_deleted(flow: Flow) -> None:
    key = beige(await inferences(flow)).key
    await service(flow).delete(governance(flow), await profile_id(flow), key)
    handles = list(await _handles(flow))
    async with flow.cell.uow(SPACE_ID) as uow:
        exported = await exported_suppressions(uow, handles)
    assert [(e["key"], e["kind"]) for e in exported] == [(key, "soft_constraint")] * len(handles)


async def _handles(flow: Flow) -> tuple[UUID, ...]:
    async with flow.cell.uow(SPACE_ID) as uow:
        profile = await uow.identity.get_profile(await profile_id(flow))
    assert profile is not None
    return profile.handle_ids
