"""A card given in a conversation is nowhere the cell stores or serves (WP-A): not in events, episodes,
facts, values, receipts, the hot layer, the extraction prompt, the context pack or the history, while the
protocol said in the same turn is still remembered."""

import re
from dataclasses import dataclass
from typing import Any

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.config.models import IngestMaskingSettings
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, SearchRequest
from niadra.contracts.events import BatchResponse
from niadra.contracts.ingest import IngestStatus, IngestStatusRequest
from niadra.domain.ingest.hot import hot_aad, unpack_envelope
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_first_read import remembered

PAN = "4111111111111111"
PHONE = "+5511933332222"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
CONVERSATION = "wa-card"


class EchoLlm(ScriptedLlm):
    """A careless model: the episode repeats the transcript it was given, so anything the prompt carried
    would end up in memory."""

    async def complete_json(self, **kwargs: Any) -> Any:
        lines = [line for line in kwargs["user"].splitlines() if line.startswith("[e")]
        answer = remembered()
        answer["episode"]["summary"] = " ".join(lines)[:900]
        answer["episode"]["resolution"] = "Pagamento registrado"
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


def carries_pan(blob: str) -> bool:
    """The card number in any of the ways it can be written: plain, or with spaces, dots or hyphens."""
    return PAN in re.sub(r"[\s.\-]", "", blob)


async def hot_layer(flow: Flow) -> str:
    docs = []
    for (space_id, _), turns in flow.cell.hot.turns.items():
        for t in turns.values():
            version, ct = unpack_envelope(t.envelope_ct)
            docs.append(
                (await flow.cell.keys.decrypt(space_id, ct, hot_aad(space_id, t.event_id), version)).decode()
            )
    return "\n".join(docs)


@dataclass
class Seen:
    sent: BatchResponse
    status: IngestStatus
    live: str
    served: str
    stored: str
    prompt: str


async def converse(cell: Cell) -> Seen:
    """A customer pays by card over WhatsApp, then calls; the agent on the call reads the pack, searches the
    history and opens the episode. Returns everything the cell stored and served on the way."""
    flow = Flow.build(cell, EchoLlm())
    at = cell.clock.now().isoformat()
    sent = await flow.send(
        WHATSAPP_SOURCE,
        turn(1, "ai_agent", "Para pagar, qual o número do cartão?", at),
        turn(2, "customer", PAN, at),
        turn(3, "customer", "cvv 123 e a senha do app: hunter2. O protocolo é 45778204", at),
    )
    assert sent.errors == []
    status = await flow.extract.status(principal(), IngestStatusRequest(conversation_id=CONVERSATION))
    live = await hot_layer(flow)
    ended = {"type": "conversation.ended", "idempotency_key": "end", "conversation_id": CONVERSATION}
    await flow.send(WHATSAPP_SOURCE, {**ended, "occurred_at": at})
    await flow.drain()
    cell.clock.advance(minutes=5)

    call = {
        **turn(9, "customer", "Oi, é sobre o pagamento", cell.clock.now().isoformat()),
        "channel": "voice",
    }
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
            query="qual cartão eu usei?",
            verification=Verification.V3,
        ),
    )
    found = await flow.serve.search(
        caller,
        SearchRequest(
            subject=SUBJECT,
            query="cartão protocolo pagamento",
            verification=Verification.V3,
            conversation_id="call-2",
        ),
    )
    episodes = [i.id for i in found.items if i.kind == "episode"]
    assert episodes
    opened = await flow.serve.open_item(
        caller, episodes[0], verification=Verification.V3, conversation_id="call-2"
    )
    await flow.drain()
    return Seen(
        sent=sent,
        status=status,
        live=live + await hot_layer(flow),
        served="\n".join([pack.model_dump_json(), found.model_dump_json(), opened.model_dump_json()]),
        # Events, episodes, facts, values, receipts, measurements, packs, raw payloads.
        stored="\n".join([repr(cell.uow.store.data), repr(cell.cache.values), repr(cell.objects.objects)]),
        prompt=flow.llm.calls[0]["user"],
    )


async def test_a_card_given_in_a_conversation_is_nowhere_after_it(cell: Cell) -> None:
    seen = await converse(cell)

    assert seen.sent.masked == {"card": 1, "cvv": 1, "password": 1}
    assert seen.status.masked == {"card": 1, "cvv": 1, "password": 1}
    assert "[retido:cartão ****1111]" in seen.live
    for blob in (seen.live, seen.served, seen.stored, seen.prompt):
        assert not carries_pan(blob)
        assert "hunter2" not in blob
    # The model only ever saw the marker; the protocol from the same turn is still a value.
    assert "[retido:cartão ****1111]" in seen.prompt
    assert "45778204" in repr(cell.uow.store.data["memory"]["values"])


async def test_without_the_masks_the_same_conversation_leaves_the_card_behind(cell: Cell) -> None:
    """The control: the checks above find a card when there is one to find."""
    config = cell.config.spaces()[0]
    cell.config.put(config.model_copy(update={"ingest_masking": IngestMaskingSettings(types=[])}))

    seen = await converse(cell)

    assert seen.sent.masked == {}
    assert carries_pan(seen.stored)
    assert carries_pan(seen.live)
    assert "hunter2" in seen.stored
