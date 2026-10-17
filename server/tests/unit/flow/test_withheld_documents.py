"""A document number said in a conversation reaches no memory the cell keeps or serves, even
from a model that copies it into every field: not the episode, the facts, the promises, the settled values,
the stored extraction result, the context pack, the history or the customer's export. The protocol, the
order, the postal code and the amount said in the same conversation are still remembered. The log keeps
the turn as the customer typed it (ingest masking holds cards, codes and passwords, not documents), so the
checks below read every store but the log's."""

import io
import zipfile
from typing import Any
from uuid import UUID

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, SearchRequest
from niadra.contracts.privacy import ExportRequest
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

DOCUMENT = "82661763"
PHONE = "+5511944443333"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
CONVERSATION = "wa-doc"
KEPT = ("45778204", "99120", "01310-100", "171,61")


def fact(predicate: str, value: str) -> dict[str, Any]:
    return {
        "op": "add",
        "predicate": predicate,
        "value": value,
        "target": None,
        "evidence": ["e1"],
        "valid_at": None,
        "certainty": 1.0,
        "contradicts": [],
        "about": None,
        "object": None,
    }


class CarelessLlm(ScriptedLlm):
    """Every leak of the measurement at once: the number alone as a fact, in the summary, the resolution,
    a promise, a profile slot and the values, next to the ordinary values of the same conversation."""

    async def complete_json(self, **kwargs: Any) -> Any:
        answer = {
            "episode": {
                "summary": f"Cliente informou o documento {DOCUMENT}; pedido 99120 veio errado, protocolo "
                "45778204, CEP 01310-100, cobrança de R$ 171,61.",
                "intent": "complaint",
                "category": "invoice",
                "outcome": "pending",
                "resolution": f"Cadastro atualizado com o documento {DOCUMENT}.",
                "sentiment": -0.2,
            },
            "facts": [
                fact("disputed_invoice", DOCUMENT),
                fact("disputed_invoice", "cobrança de R$ 171,61 no pedido 99120"),
                fact("preferred_name", "Rafa"),
            ],
            "open_items": [
                {
                    "description": f"Reenviar a nota com o documento {DOCUMENT}",
                    "owner": "company",
                    "evidence": ["e1"],
                    "due_at": None,
                    "object": None,
                    "expected_operation": None,
                    "about": None,
                }
            ],
            "resolve": [],
            "profile": [],
            "agent_questions": [],
            "agent_claims": [],
            "values": [f"code {DOCUMENT}", "protocol 45778204", "order 99120", "postal_code 01310-100"],
        }
        self.answers = [answer]
        return await super().complete_json(**kwargs)


def turn(n: int, role: str, text: str, at: str) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{CONVERSATION}-{n}",
        "channel": "whatsapp",
        "conversation_id": CONVERSATION,
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": role},
        "content": {"type": "text", "text": text},
        "occurred_at": at,
    }


def carries(blob: str | bytes) -> bool:
    text = blob.decode(errors="replace") if isinstance(blob, bytes) else blob
    return DOCUMENT in text


def memory_stores(cell: Cell) -> str:
    """Every module's store but the log's: the events, the conversations and the raw payloads keep the turn
    as it was typed."""
    data = cell.uow.store.data
    kept = {name: rows for name, rows in data.items() if name != "ingest"}
    return "\n".join([repr(kept), repr(cell.cache.values)])


async def converse(cell: Cell) -> tuple[Flow, str, bytes]:
    flow = Flow.build(cell, CarelessLlm())
    at = cell.clock.now().isoformat()
    sent = await flow.send(
        WHATSAPP_SOURCE,
        turn(1, "customer", f"Oi, sou o Rafa. O número do meu documento é {DOCUMENT}.", at),
        turn(2, "customer", "O pedido 99120 veio errado. O protocolo é 45778204, CEP 01310-100.", at),
        turn(3, "customer", "E me cobraram R$ 171,61.", at),
        turn(4, "ai_agent", "Obrigado, atualizei seu cadastro e abri a correção.", at),
    )
    assert sent.errors == []
    ended = {"type": "conversation.ended", "idempotency_key": "end", "conversation_id": CONVERSATION}
    await flow.send(WHATSAPP_SOURCE, {**ended, "occurred_at": at})
    await flow.drain()
    cell.clock.advance(minutes=5)

    call = {**turn(9, "customer", "Oi, é sobre a cobrança", cell.clock.now().isoformat()), "channel": "voice"}
    call["conversation_id"] = "call-2"
    verify = {
        "type": "verify",
        "idempotency_key": "call-2-otp",
        "method": "otp_sms",
        "level": "V3",
        "conversation_id": "call-2",
        "handle": {"type": "phone_e164", "value": PHONE},
        "occurred_at": cell.clock.now().isoformat(),
    }
    assert (await flow.send(VOICE_SOURCE, call, verify)).errors == []
    caller = principal(VOICE_SOURCE, cell.config.spaces()[0])
    pack = await flow.serve.context(
        caller,
        ContextRequest(
            subject=SUBJECT,
            conversation_id="call-2",
            query="qual o meu documento?",
            verification=Verification.V3,
        ),
    )
    found = await flow.serve.search(
        caller,
        SearchRequest(
            subject=SUBJECT,
            query="documento pedido protocolo",
            verification=Verification.V3,
            conversation_id="call-2",
        ),
    )
    opened = [
        (await flow.serve.open_item(caller, i.id, verification=Verification.V3, conversation_id="call-2"))
        for i in found.items
        if i.kind == "episode"
    ]
    assert opened
    await flow.drain()
    served = "\n".join(
        [pack.model_dump_json(), found.model_dump_json(), *(o.model_dump_json() for o in opened)]
    )
    space_id = cell.config.spaces()[0].space_id
    package = await flow.privacy.export_package(
        space_id, ExportRequest(handle=SUBJECT), requested_by="user:privacy"
    )
    exported = await flow.privacy.download_package(space_id, UUID(package.run_id))
    return flow, served, exported


def memory_files(package: bytes) -> dict[str, bytes]:
    """The export's files but the log's own (the events and the conversations)."""
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        return {
            name: archive.read(name)
            for name in archive.namelist()
            if "event" not in name and "conversation" not in name
        }


async def test_a_document_number_reaches_no_memory_the_cell_keeps_or_serves(cell: Cell) -> None:
    _, served, exported = await converse(cell)

    assert carries(repr(cell.uow.store.data["ingest"]))  # the control: the log has it to find
    assert not carries(memory_stores(cell))
    assert not carries(served)
    files = memory_files(exported)
    assert files
    for name, body in files.items():
        assert not carries(body), name
    stored = memory_stores(cell)
    for value in KEPT:
        assert value in stored, value
    assert "[retido:documento]" in stored
    assert "Rafa" in stored
