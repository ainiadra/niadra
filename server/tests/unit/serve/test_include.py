"""`include` on the context read (24.2.10): blocks read beside the pack, each with a receipt of its own, only
in a space that turned its feature on."""

from dataclasses import dataclass, field, replace

import pytest

from niadra.app.serve import BlockAsk, BlockGate, BlockRead
from niadra.app.serve_reads import Target
from niadra.config.models import FeatureSettings
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.signals import ConstraintsBlock
from niadra.domain.common.errors import NotFound
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.policy import PolicyContext, Reader
from niadra.domain.vocabulary import Audience, DeliveryPath, Feature, HandleType, ReceiptSurface, Verification
from niadra.testing.cell import SPACE_ID, Cell, principal
from tests.unit.compile.factories import PROFILE, marina
from tests.unit.serve.harness import SUBJECT, Harness

AGENT = principal()
PROFILE_RECORD = marina().profile
BLOCK = ConstraintsBlock(version="cv_0123456789abcdef", text="")


@dataclass
class FakeBlock:
    name: str = "constraints"
    feature: Feature = Feature.SIGNALS
    surface: ReceiptSurface = ReceiptSurface.CONSTRAINTS
    fails: bool = False
    asked: list[tuple[Target | None, BlockAsk]] = field(default_factory=list)

    async def read(self, caller: SourcePrincipal, target: Target | None, ask: BlockAsk) -> BlockRead:
        self.asked.append((target, ask))
        if self.fails:
            raise ConnectionError("state store unreachable")
        return BlockRead(BLOCK, item_ids=("constraint:h1",), detail={"version": BLOCK.version})


def turn_on(cell: Cell, *features: Feature) -> None:
    config = cell.config.space(SPACE_ID)
    cell.config.put(config.model_copy(update={"features": FeatureSettings(enabled=frozenset(features))}))


def ask(**extra: object) -> ContextRequest:
    defaults = {"subject": SUBJECT, "verification": Verification.V1, "conversation_id": "wa-now"}
    return ContextRequest(**(defaults | extra))  # type: ignore[arg-type]


async def test_a_read_without_include_answers_as_before(cell: Cell) -> None:
    h = Harness(cell)
    h.serve.add_block(FakeBlock())
    await h.compile()
    response = await h.serve.context(AGENT, ask())
    assert response.constraints is None
    assert [r.surface for r in h.receipts.sent] == [ReceiptSurface.PACK]
    assert "constraints" not in response.model_dump(exclude_none=True)


async def test_a_block_of_a_feature_the_space_left_off_is_not_found(cell: Cell) -> None:
    h = Harness(cell)
    h.serve.add_block(FakeBlock())
    with pytest.raises(NotFound):
        await h.serve.context(AGENT, ask(include=["constraints"]))
    assert h.receipts.sent == []


async def test_a_block_of_a_feature_this_process_has_not_read_yet_reads_the_copy_first(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(cell)
    h.serve.add_block(FakeBlock())
    await h.compile()

    async def arrived() -> bool:
        turn_on(cell, Feature.SIGNALS)  # the snapshot reached the cell after this process's timed read
        return True

    monkeypatch.setattr(cell.config, "refresh_on_miss", arrived)
    response = await h.serve.context(AGENT, ask(include=["constraints"]))
    assert response.constraints == BLOCK


async def test_an_included_block_answers_in_its_field_with_a_receipt_of_its_surface(cell: Cell) -> None:
    turn_on(cell, Feature.SIGNALS)
    h = Harness(cell)
    block = FakeBlock()
    h.serve.add_block(block)
    await h.compile()
    response = await h.serve.context(AGENT, ask(include=["constraints"]))
    assert response.constraints == BLOCK
    assert not response.degraded
    [(target, asked)] = block.asked
    assert target is not None
    assert target.profile.profile_id == PROFILE
    assert replace(asked, gate=None, carried=frozenset()) == BlockAsk("wa-now", None, "chat")
    # The served pack's lines go with it: an instruction the pack says is not said again in the text.
    assert asked.carried
    assert asked.gate is not None
    pack, constraints = h.receipts.sent
    assert (pack.surface, constraints.surface) == (ReceiptSurface.PACK, ReceiptSurface.CONSTRAINTS)
    assert (constraints.profile_id, constraints.conversation_id) == (PROFILE, "wa-now")
    assert constraints.detail == {"version": BLOCK.version}


async def test_a_first_contact_still_reads_its_blocks(cell: Cell) -> None:
    turn_on(cell, Feature.SIGNALS)
    h = Harness(cell)
    block = FakeBlock()
    h.serve.add_block(block)
    stranger = Handle(type=HandleType.PHONE_E164, value="+5511888887777")
    response = await h.serve.context(AGENT, ask(subject=stranger, include=["constraints"]))
    assert (response.path, response.constraints) == (DeliveryPath.T4, BLOCK)
    assert [target for target, _ in block.asked] == [None]
    assert [r.surface for r in h.receipts.sent] == [ReceiptSurface.PACK, ReceiptSurface.CONSTRAINTS]


async def test_a_block_that_fails_is_left_out_and_the_read_is_degraded(cell: Cell) -> None:
    turn_on(cell, Feature.SIGNALS)
    h = Harness(cell)
    h.serve.add_block(FakeBlock(fails=True))
    await h.compile()
    response = await h.serve.context(AGENT, ask(include=["constraints"]))
    assert response.text
    assert (response.constraints, response.degraded) == (None, True)
    assert [r.surface for r in h.receipts.sent] == [ReceiptSurface.PACK]


def test_a_block_is_added_once_under_its_own_feature(cell: Cell) -> None:
    h = Harness(cell)
    h.serve.add_block(FakeBlock())
    with pytest.raises(ValueError, match="added twice"):
        h.serve.add_block(FakeBlock())
    with pytest.raises(ValueError, match="not an `include` name"):
        h.serve.add_block(FakeBlock(name="state", feature=Feature.SIGNALS))


def test_include_names_each_block_once() -> None:
    with pytest.raises(ValueError, match="once"):
        ask(include=["constraints", "constraints"])


async def test_every_block_gets_the_packs_gate_at_the_level_the_pack_was_read(cell: Cell) -> None:
    turn_on(cell, Feature.SIGNALS)
    h = Harness(cell)
    block = FakeBlock()
    h.serve.add_block(block)
    await h.compile()
    response = await h.serve.context(AGENT, ask(include=["constraints"], verification=Verification.V0))
    [(target, asked)] = block.asked
    assert asked.gate is not None
    assert target is not None
    assert asked.gate.context.verification is response.verification.effective
    assert asked.gate.private == cell.config.space(SPACE_ID).object_types.private_fields()
    # Each item goes by its own attributes: at V0 nothing of the customer's, at V1 an ordinary type.
    assert not asked.gate.admits_about("order", target)
    at_v1 = replace(asked.gate, context=PolicyContext(Verification.V1))
    assert at_v1.admits_about("order", target)
    assert not at_v1.admits_about("health", target)


def test_a_field_its_type_holds_private_never_passes_the_gate(cell: Cell) -> None:
    gate = BlockGate(
        cell.config.space(SPACE_ID).policy,
        Reader(Audience.CUSTOMER_AGENT),
        PolicyContext(Verification.V3),
        {"proposal": frozenset({"health_declaration"})},
    )
    target = Target(PROFILE_RECORD)
    assert gate.admits_field("proposal.plan_code", target)
    assert not gate.admits_field("proposal.health_declaration", target)
