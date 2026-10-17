"""Each context read's receipt keeps the server time of the read in its detail: a read is
auditable by its own receipt, with no column added."""

from niadra.app.read_state import ReadState
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.domain.vocabulary import DeliveryPath, HandleType, ReceiptSurface, Verification
from niadra.testing.cell import Cell, principal
from tests.unit.serve.harness import SUBJECT, Harness

AGENT = principal()


def ask(subject: Handle = SUBJECT) -> ContextRequest:
    return ContextRequest(subject=subject, verification=Verification.V1, conversation_id="wa-now")


async def test_the_read_receipt_says_how_long_the_server_took(cell: Cell) -> None:
    h = Harness(cell)
    response = await h.serve.context(AGENT, ask())
    assert response.path is DeliveryPath.T2
    [pack] = [r for r in h.receipts.sent if r.surface is ReceiptSurface.PACK]
    server_ms = float(pack.detail["server_ms"])
    assert 0 < server_ms <= sum(response.timing.values()) + 1


async def test_a_first_contact_read_says_it_too(cell: Cell) -> None:
    h = Harness(cell)
    stranger = Handle(type=HandleType.PHONE_E164, value="+5511888887777")
    response = await h.serve.context(AGENT, ask(stranger))
    assert response.path is DeliveryPath.T4
    [receipt] = h.receipts.sent
    assert float(receipt.detail["server_ms"]) > 0


async def test_a_read_resolved_from_the_read_state_and_served_from_the_cache_is_warm(cell: Cell) -> None:
    """The statement counter is scoped to warm reads: the first read of a conversation
    resolves its customer on the database, the next ones from the read state."""
    h = Harness(cell, read_state=ReadState(synchronous=True))
    await h.compile()
    first = await h.serve.context_answer(AGENT, ask())
    again = await h.serve.context_answer(AGENT, ask())
    assert (first.response.path, first.warm) == (DeliveryPath.T0, False)
    assert (again.response.path, again.warm) == (DeliveryPath.T0, True)
    unknown = await h.serve.context_answer(
        AGENT, ask(Handle(type=HandleType.PHONE_E164, value="+5511888887777"))
    )
    assert unknown.warm is False
