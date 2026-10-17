"""End to end: every write that creates or changes a navigable item stores its vector, so a search
over the customer's history encodes only its question (16.2)."""

from typing import Any

from niadra.adapters.memory.fakes import DeterministicModels, ScriptedLlm
from niadra.app.compile import compile_settings, load_snapshot
from niadra.app.memory import unembedded
from niadra.contracts.common import Handle
from niadra.contracts.context import SearchRequest
from niadra.domain.compile.templates import Formatter
from niadra.domain.serve.history import vector_text
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_closing import PHONE, _at, billing_credit, contest, extraction, items


class RecordingModels(DeterministicModels):
    def __init__(self) -> None:
        self.encoded: list[str] = []

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        self.encoded.extend(texts)
        return await super().embed(texts)

    async def embed_query(self, text):  # type: ignore[no-untyped-def]
        self.encoded.append(text)
        return await super().embed_query(text)


def credited(flow: Flow) -> dict[str, Any]:
    return {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": "erp-0823-credited",
        "channel": "erp",
        "canonical_type": "invoice.credited",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "object_refs": [{"type": "invoice", "namespace": "erp", "id": "0823"}],
        "speaker": {"role": "system"},
        "fields": {"amount": "189.90", "status": "credited"},
        "occurred_at": _at(flow.cell.clock),
    }


async def test_after_the_writes_settle_a_search_encodes_only_its_question(cell: Cell) -> None:
    models = RecordingModels()
    cell.models = models
    flow = Flow.build(cell, ScriptedLlm(extraction("invoice.credited")))
    await contest(flow)
    flow.cell.clock.advance(minutes=4)
    await flow.send(BILLING_SOURCE, billing_credit(flow))
    flow.cell.clock.advance(minutes=30)
    await flow.send(BILLING_SOURCE, credited(flow))
    await flow.drain()

    [item] = await items(flow)
    async with cell.uow(SPACE_ID) as uow:
        profile = await uow.identity.profile_of(item.origin_handle_id)
        assert profile is not None
        snapshot = await load_snapshot(uow, profile)
        waiting = await unembedded(uow, None, 100, profile)
        stored = {
            v.doc_id: v
            for v in [
                *await uow.memory.vectors_for(profile.handle_ids),
                *await uow.objects.vectors_of(
                    [o.object_id for o in snapshot.objects], [a.action_id for a in snapshot.actions]
                ),
            ]
        }
    [episode], [invoice], [credit] = snapshot.episodes, snapshot.objects, snapshot.actions
    assert waiting == []
    assert {
        f"episode:{episode.episode_id}",
        f"object:{invoice.object_id}",
        f"action:{credit.action_id}",
    } <= set(stored)
    # The invoice's vector describes its state after the ERP's event, not the empty one it had first.
    settings = compile_settings(cell.config.space(SPACE_ID))
    fmt = Formatter(settings.timezone, settings.locale)
    assert invoice.state["status"] == "credited"
    assert stored[f"object:{invoice.object_id}"].vector == models._embed(vector_text(invoice, fmt))

    models.encoded.clear()
    # The billing agent searches the customer's history for its task, as in 18.7.
    request = SearchRequest(
        subject=Handle(type=HandleType.PHONE_E164, value=PHONE),
        query="crédito da cobrança em dobro",
        verification=Verification.NO_CUSTOMER,
        task_id="billing-0823",
    )
    found = await flow.serve.search(principal(BILLING_SOURCE, cell.config.space(SPACE_ID)), request)
    assert found.items
    assert found.degraded is None
    assert models.encoded == ["crédito da cobrança em dobro"]
