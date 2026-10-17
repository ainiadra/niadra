"""A task view that asks for the agent's own notes (`include_agent_memory`, 10 C.3) gets up to 200 tokens
of them after its context, for agents whose prompt cannot change; the etag covers them and the read leaves
an agent memory receipt."""

from niadra.app.agent_memory import AgentMemoryService
from niadra.config.models import AgentMemorySettings, TaskView
from niadra.contracts.agent_memory import CreateAgentNoteRequest
from niadra.contracts.common import ObjectRef
from niadra.contracts.context import ContextRequest
from niadra.domain.vocabulary import ReceiptKind, Scope, Verification
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, Cell, principal
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_closing import INVOICE, _at

CREDIT = CreateAgentNoteRequest(
    kind="procedure",
    title="Crédito aparece depois do refresh",
    body="No ERP, post_credit e depois refresh_invoice, nessa ordem.",
    tags=["invoice", "credit"],
)


async def test_a_task_view_carries_the_agents_notes_after_its_context(cell: Cell) -> None:
    config = cell.config.space(SPACE_ID)
    views = [TaskView(name="task:billing", object_types=["invoice"], include_agent_memory=True)]
    cell.config.put(
        config.model_copy(update={"views": views, "agent_memory": AgentMemorySettings(enabled=True)})
    )
    flow = Flow.build(cell)
    invoice = {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": "erp-0823-issued",
        "channel": "erp",
        "canonical_type": "invoice.issued",
        "handles": [{"type": "phone_e164", "value": "+5511955556666"}],
        "object_refs": [INVOICE],
        "speaker": {"role": "system"},
        "fields": {"amount": "379.80", "status": "open"},
        "occurred_at": _at(cell.clock),
    }
    assert (await flow.send(BILLING_SOURCE, invoice)).errors == []
    await flow.drain()
    service = AgentMemoryService(
        cell.uow,
        cell.config,
        cell.clock,
        cell.models,
        RecordingAdminAudit(),
        cell.keys,  # type: ignore[arg-type]
    )
    agent = principal(BILLING_SOURCE, cell.config.space(SPACE_ID))
    writer = principal(
        BILLING_SOURCE,
        cell.config.space(SPACE_ID),
        scopes=frozenset({Scope.AGENT_MEMORY_WRITE, Scope.CONTEXT}),
    )
    await service.create(writer, CREDIT)
    await service.create(
        writer,
        CreateAgentNoteRequest(kind="pitfall", title="Visitas", body="Janela da manhã", tags=["visit"]),
    )
    request = ContextRequest(
        object=ObjectRef.model_validate(INVOICE),
        view="task:billing",
        verification=Verification.NO_CUSTOMER,
        task_id="billing-0823",
    )
    read = await flow.serve.context(agent, request)
    assert read.text is not None
    _, _, tail = read.text.partition("<agent_notes>")
    assert "post_credit e depois refresh_invoice" in tail
    assert "Janela da manhã" not in tail  # tagged for visits, not for invoices
    assert read.etag.count("-") == 1
    again = await flow.serve.context(agent, request.model_copy(update={"known_etag": read.etag}))
    assert again.not_modified
    kinds = [r.kind for r in flow.receipts.sent]
    assert ReceiptKind.AGENT_MEMORY in kinds
    # Another agent without the flag's notes, or a view without the flag, reads the pack alone.
    cell.config.put(
        cell.config.space(SPACE_ID).model_copy(
            update={"views": [TaskView(name="task:billing", object_types=["invoice"])]}
        )
    )
    cell.clock.advance(minutes=2)
    plain = await flow.serve.context(agent, request.model_copy(update={"task_id": "billing-0824"}))
    assert "<agent_notes>" not in (plain.text or "")
    assert "-" not in plain.etag
