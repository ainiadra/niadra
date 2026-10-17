"""Rows that came through an unconfirmed link stay with that link (5.3), end to end.

The attack: a caller whose own number is known declares, next to it, an e-mail the space has never seen.
The e-mail joins the caller's profile, provisionally. Later the e-mail's real owner writes from it: that
conversation becomes memory anchored on the e-mail, in the caller's profile. Until the link is confirmed,
nothing that came through the e-mail may reach a reader through the caller's number, a read by profile
or object, the live turns, the slots, the history, an export or the Console below the roles that confirm
links; and the e-mail's owner, reading through the e-mail, stays capped at V1 on the rest. A
confirmation shares everything as before; a split takes the rows away with the e-mail.
"""

import io
import zipfile
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.serve import _Ask, _Served
from niadra.app.serve_reads import Target
from niadra.contracts.common import Handle
from niadra.contracts.context import (
    ContextRequest,
    ContextResponse,
    SearchRequest,
    TimelineRequest,
)
from niadra.contracts.privacy import ExportRequest
from niadra.domain.common.errors import NotFound
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.compile import CompiledPack
from niadra.domain.records import ProfileRecord
from niadra.domain.vocabulary import AssertionMethod, DeliveryPath, Environment, HandleType, Verification
from niadra.testing.cell import (
    BILLING_SOURCE,
    SPACE_ID,
    VOICE_SOURCE,
    WHATSAPP_SOURCE,
    Cell,
    principal,
    sample_config,
)
from tests.unit.flow.harness import Flow

CALLER = {"type": "phone_e164", "value": "+5521977774444"}
CALLER_CRM = {"type": "system_id", "value": "77001", "scope": "crm"}
VICTIM_EMAIL = {"type": "email", "value": "bia.costa@acme.com.br"}
SECRETS = ("81220", "Bia", "em dobro")
"""What only the e-mail's owner said: a protocol number, her name (V1), a billing dispute (V2)."""


def extraction(summary: str, *facts: tuple[str, str]) -> dict[str, Any]:
    return {
        "episode": {
            "summary": summary,
            "intent": "complaint",
            "category": "invoice",
            "outcome": "pending",
            "resolution": None,
            "sentiment": -0.2,
        },
        "facts": [
            {
                "op": "add",
                "predicate": predicate,
                "value": value,
                "target": None,
                "evidence": ["e1"],
                "valid_at": None,
                "certainty": 0.9,
                "contradicts": [],
                "about": None,
                "object": None,
            }
            for predicate, value in facts
        ],
        "open_items": [],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


CALLER_MEMORY = extraction(
    "Pediu a segunda via do boleto", ("preferred_name", "Carlos"), ("disputed_invoice", "boleto de julho")
)
VICTIM_MEMORY = extraction(
    "Contestou a fatura de agosto, protocolo 81220",
    ("preferred_name", "Bia"),
    ("disputed_invoice", "fatura de agosto em dobro"),
)


def turn(conversation: str, who: list[dict[str, Any]], text: str, at: str, channel: str) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-1",
        "channel": channel,
        "conversation_id": conversation,
        "handles": who,
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": text},
        "occurred_at": at,
        # The channel attests the sender (an e-mail from the address, a call from the number): V1.
        "verification_hint": "V1",
    }


def ended(conversation: str, at: str) -> dict[str, Any]:
    return {
        "type": "conversation.ended",
        "idempotency_key": f"{conversation}-end",
        "conversation_id": conversation,
        "occurred_at": at,
    }


def now(flow: Flow) -> str:
    return flow.cell.clock.now().isoformat()


async def conversation(
    flow: Flow, source: UUID, name: str, who: list[dict[str, Any]], text: str, channel: str
) -> None:
    at = now(flow)
    assert (await flow.send(source, turn(name, who, text, at, channel), ended(name, at))).errors == []
    await flow.drain()
    flow.cell.clock.advance(hours=1)


async def declare(flow: Flow, method: str = "declared") -> None:
    item = {
        "type": "identify",
        "idempotency_key": f"claim-{method}",
        "handles": [CALLER, VICTIM_EMAIL],
        "method": method,
        "occurred_at": now(flow),
    }
    assert (await flow.send(VOICE_SOURCE, item)).errors == []
    await flow.drain()
    flow.cell.clock.advance(hours=1)


async def victim_writes(flow: Flow) -> None:
    await conversation(
        flow,
        WHATSAPP_SOURCE,
        "email-1",
        [VICTIM_EMAIL],
        "Me chama de Bia. Meu protocolo é 81220 e a fatura de agosto veio em dobro",
        "email",
    )


async def proven_call(flow: Flow, conversation_id: str = "call-1") -> None:
    """The caller phones from their own number and passes an OTP sent to it: V3 on that number."""
    at = now(flow)
    line = turn(conversation_id, [CALLER], "Oi, é sobre a minha fatura", at, "voice")
    verify = {
        "type": "verify",
        "idempotency_key": f"{conversation_id}-otp",
        "method": "otp_sms",
        "level": "V3",
        "conversation_id": conversation_id,
        "handle": CALLER,
        "occurred_at": at,
    }
    assert (await flow.send(VOICE_SOURCE, line, verify)).errors == []


def handle(raw: dict[str, Any]) -> Handle:
    return Handle(type=HandleType(raw["type"]), value=raw["value"], scope=raw.get("scope"))


def voice() -> Any:
    return principal(VOICE_SOURCE)


async def read(
    flow: Flow,
    who: dict[str, Any],
    conversation_id: str,
    level: Verification = Verification.V3,
    **extra: Any,
) -> ContextResponse:
    request = ContextRequest(
        subject=handle(who), view="voice", verification=level, conversation_id=conversation_id, **extra
    )
    return await flow.serve.context(voice(), request)


def shown(response: ContextResponse) -> str:
    """Everything a read hands the agent: the pack, the live turns, the delta and the slots."""
    live = " ".join(t.text for t in response.live)
    return " ".join([response.text or "", live, response.delta or "", response.slots or ""])


async def profile_of(flow: Flow, raw: dict[str, Any]) -> UUID:
    async with flow.cell.uow(SPACE_ID) as uow:
        found = await flow.serve._reads.find_handle(uow, handle(raw))
        assert found is not None
        profile = await uow.identity.profile_of(found.handle_id)
        assert profile is not None
        return profile.profile_id


async def victim_episode(flow: Flow) -> str:
    async with flow.cell.uow(SPACE_ID) as uow:
        found = await flow.serve._reads.find_handle(uow, handle(VICTIM_EMAIL))
        assert found is not None
        [episode] = await uow.memory.episodes_for([found.handle_id])
    return f"episode:{episode.episode_id}"


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    """The caller is a known customer of their own (a WhatsApp conversation and a CRM record)."""
    flow = Flow.build(cell, ScriptedLlm(CALLER_MEMORY, VICTIM_MEMORY))
    await conversation(
        flow,
        WHATSAPP_SOURCE,
        "wa-1",
        [CALLER, CALLER_CRM],
        "Me chama de Carlos, preciso da segunda via do boleto de julho",
        "whatsapp",
    )
    return flow


@pytest.fixture
async def attacked(flow: Flow) -> Flow:
    await declare(flow)
    await victim_writes(flow)
    return flow


async def test_the_callers_number_never_reads_what_came_through_the_declared_e_mail(attacked: Flow) -> None:
    flow = attacked
    await proven_call(flow)
    stolen = await read(flow, CALLER, "call-1", query="qual o protocolo que me passaram por e-mail?")
    # The caller's own number is confirmed: the read is at V3 and carries their own memory in full.
    assert stolen.verification.effective is Verification.V3
    assert "Carlos" in (stolen.text or "")
    assert "boleto de julho" in (stolen.text or "")
    # Nothing that arrived through the e-mail, in the pack, the live turns or anywhere else.
    for secret in SECRETS:
        assert secret not in shown(stolen), secret


async def test_the_live_turns_leave_out_the_e_mails_conversation_before_it_becomes_memory(
    flow: Flow,
) -> None:
    await declare(flow)
    await proven_call(flow)
    at = now(flow)
    # The e-mail arrives while the call goes on; its turn is in the hot layer, not extracted yet.
    line = turn("email-2", [VICTIM_EMAIL], "Meu protocolo é 81220, podem me ligar?", at, "email")
    assert (await flow.send(WHATSAPP_SOURCE, line)).errors == []
    response = await read(flow, CALLER, "call-1")
    assert "81220" not in shown(response)


async def test_history_search_timeline_and_open_leave_the_e_mails_rows_out(attacked: Flow) -> None:
    flow = attacked
    await proven_call(flow)
    caller = voice()
    found = await flow.serve.search(
        caller,
        SearchRequest(
            subject=handle(CALLER),
            query="boleto fatura protocolo",
            verification=Verification.V3,
            conversation_id="call-1",
        ),
    )
    assert found.items
    assert not any(s in item.text for item in found.items for s in SECRETS)
    timeline = await flow.serve.timeline(
        caller,
        TimelineRequest(subject=handle(CALLER), verification=Verification.V3, conversation_id="call-1"),
    )
    assert timeline.items
    assert not any(s in item.text for item in timeline.items for s in SECRETS)
    assert all(item.channel != "email" for item in timeline.items)
    episode = await victim_episode(flow)
    with pytest.raises(NotFound):
        await flow.serve.open_item(caller, episode, verification=Verification.V3, conversation_id="call-1")
    with pytest.raises(NotFound):
        await flow.serve.open_item(
            caller, episode, verification=Verification.V3, conversation_id="call-1", subject=handle(CALLER)
        )


async def test_reads_by_profile_id_honor_the_same_rule(attacked: Flow) -> None:
    flow = attacked
    profile_id = await profile_of(flow, CALLER)
    assert profile_id == await profile_of(flow, VICTIM_EMAIL)  # one profile, as the claim made it
    internal = principal(BILLING_SOURCE)
    # An internal agent reads without a customer: billing is open to it, so today it would get the dispute.
    by_profile = await flow.serve.context(
        internal, ContextRequest(profile_id=profile_id, verification=Verification.NO_CUSTOMER)
    )
    assert "boleto de julho" in (by_profile.text or "")
    for secret in SECRETS:
        assert secret not in shown(by_profile), secret
    timeline = await flow.serve.timeline_for_profile(
        internal, profile_id, verification=Verification.NO_CUSTOMER, conversation_id=None
    )
    assert not any(s in item.text for item in timeline.items for s in SECRETS)
    with pytest.raises(NotFound):
        await flow.serve.open_item(
            internal, await victim_episode(flow), verification=Verification.NO_CUSTOMER, profile_id=profile_id
        )


async def test_the_e_mails_owner_reads_her_rows_and_the_rest_only_at_v1(attacked: Flow) -> None:
    flow = attacked
    at = now(flow)
    await flow.send(WHATSAPP_SOURCE, turn("email-3", [VICTIM_EMAIL], "Oi de novo", at, "email"))
    own = await read(flow, VICTIM_EMAIL, "email-3")
    assert own.verification.effective is Verification.V1
    assert "Bia" in (own.text or "")
    # The caller's rows reach her at V1 at most: the name, never the billing dispute.
    assert "boleto de julho" not in shown(own)


async def test_a_pinned_pack_stays_byte_stable_for_its_reader(attacked: Flow) -> None:
    flow = attacked
    await proven_call(flow)
    first = await read(flow, CALLER, "call-1")
    second = await read(flow, CALLER, "call-1")
    assert (second.text, second.etag) == (first.text, first.etag)
    # The e-mail's owner, in her own conversation, gets packs of her own, never the caller's.
    at = now(flow)
    await flow.send(WHATSAPP_SOURCE, turn("email-3", [VICTIM_EMAIL], "Oi de novo", at, "email"))
    hers = await read(flow, VICTIM_EMAIL, "email-3")
    assert hers.etag != first.etag
    again = await read(flow, CALLER, "call-1")
    assert (again.text, again.etag) == (first.text, first.etag)


async def test_the_pin_made_before_the_rule_is_never_served_to_a_view_that_hides_rows(attacked: Flow) -> None:
    """A pack compiled before this rule, holding the e-mail's rows, is left in the cache: the caller's
    read refuses it and compiles again."""
    flow = attacked
    await proven_call(flow)
    before = await read(flow, CALLER, "call-1")
    profile_id = await profile_of(flow, CALLER)
    async with flow.cell.uow(SPACE_ID) as uow:
        profile = await uow.identity.get_profile(profile_id)
        assert profile is not None
        whole = replace(profile, provisional_handle_ids=())  # as every read saw it before
        config = flow.cell.config.space(SPACE_ID)
        variant = flow.serve._variant(voice(), _ask(), Verification.V3, _target(whole), config)
        old = await flow.compile.compile_on_demand(uow, whole, variant)
    assert "81220" in old.text or "em dobro" in old.text
    await flow.serve._remember(voice(), _ask(conversation_id="call-1"), _target(whole), _served(old), None)
    after = await read(flow, CALLER, "call-1")
    for secret in SECRETS:
        assert secret not in shown(after), secret
    assert after.text == before.text


async def test_a_confirmation_shares_everything_again(attacked: Flow) -> None:
    flow = attacked
    await proven_call(flow)
    assert "Bia" not in shown(await read(flow, CALLER, "call-1"))
    # The CRM says the e-mail is on the caller's record: the link is confirmed.
    confirm = {
        "type": "identify",
        "idempotency_key": "crm-confirm",
        "handles": [VICTIM_EMAIL, CALLER_CRM],
        "method": "system_import",
        "occurred_at": now(flow),
    }
    assert (await flow.send(WHATSAPP_SOURCE, confirm)).errors == []
    await flow.drain()
    shared = await read(flow, CALLER, "call-1")
    assert "Bia" in (shared.text or "")
    assert "81220" in (shared.text or "")  # the e-mail's conversation, the latest, is pinned again


async def test_a_split_takes_the_rows_away_with_the_e_mail(attacked: Flow) -> None:
    flow = attacked
    async with flow.cell.uow(SPACE_ID) as uow:
        [claim] = [
            a
            for a in await uow.identity.list_assertions(None, None, None, 100)
            if a.method is AssertionMethod.DECLARED
        ]
    await flow.identity.retract(SPACE_ID, claim.assertion_id, actor="user:ops", reason="not the same person")
    await flow.drain()
    assert await profile_of(flow, CALLER) != await profile_of(flow, VICTIM_EMAIL)
    await proven_call(flow)
    caller = await read(flow, CALLER, "call-1")
    for secret in SECRETS:
        assert secret not in shown(caller), secret
    at = now(flow)
    await flow.send(WHATSAPP_SOURCE, turn("email-3", [VICTIM_EMAIL], "Oi de novo", at, "email"))
    hers = await read(flow, VICTIM_EMAIL, "email-3", Verification.V1)
    assert hers.verification.reason is None  # her own profile now: nothing unconfirmed to cap
    assert "Bia" in (hers.text or "")
    assert "Carlos" not in shown(hers)


async def test_an_export_for_the_callers_number_leaves_the_e_mails_rows_out(attacked: Flow) -> None:
    flow = attacked
    package = await flow.privacy.export_package(
        SPACE_ID, ExportRequest(handle=handle(CALLER)), requested_by="user:privacy"
    )
    data = await flow.privacy.download_package(SPACE_ID, UUID(package.run_id))
    assert b"Carlos" in _unzipped(data)
    for secret in SECRETS:
        assert secret.encode() not in _unzipped(data), secret


def _person(*roles: str) -> PersonPrincipal:
    config = sample_config()
    space = SpaceRef(config.space_id, config.tenant_id, config.region, Environment(config.environment))
    return PersonPrincipal(space, UUID(int=7), frozenset(roles))


async def test_the_console_shows_unconfirmed_rows_only_to_who_confirms_links(attacked: Flow) -> None:
    flow = attacked
    profile_id = await profile_of(flow, CALLER)
    analyst = await flow.memory.profile_memory(_person("analysis"), profile_id)
    values = [f.value for f in analyst.facts]
    assert "Carlos" in values
    assert "Bia" not in values
    assert not any("81220" in (e.summary or "") for e in analyst.timeline)
    security = await flow.memory.profile_memory(_person("security"), profile_id)
    marked = {f.value: f.unconfirmed_link for f in security.facts}
    assert marked["Bia"] is True
    assert marked["Carlos"] is False
    assert [e.unconfirmed_link for e in security.timeline if "81220" in (e.summary or "")] == [True]


# The serve internals the stale-pin test drives.


def _ask(**extra: Any) -> _Ask:
    return _Ask(**({"view": "voice"} | extra))


def _target(profile: ProfileRecord) -> Target:
    return Target(profile)


def _served(pack: CompiledPack) -> _Served:
    return _Served(pack, DeliveryPath.T2, pack)


def _unzipped(data: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return b"\n".join(archive.read(name) for name in archive.namelist())


async def test_the_slots_and_settled_values_leave_the_e_mails_rows_out(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(CALLER_MEMORY, VICTIM_MEMORY))
    await conversation(
        flow, WHATSAPP_SOURCE, "wa-1", [CALLER, CALLER_CRM], "Me chama de Carlos, boleto de julho", "whatsapp"
    )
    await declare(flow)
    await victim_writes(flow)
    async with flow.cell.uow(SPACE_ID) as uow:
        found = await flow.serve._reads.find_handle(uow, handle(VICTIM_EMAIL))
        assert found is not None
        assert [v.raw for v in await uow.memory.values_for([found.handle_id])] == ["81220"]
    await proven_call(flow)
    question = "qual o protocolo que me passaram por e-mail?"
    for turn_n in range(2):  # the second turn reads the customer's retrieval cache the first one warmed
        stolen = await read(flow, CALLER, "call-1", query=question)
        await flow.serve.retrieval.drain()
        for secret in SECRETS:
            assert secret not in shown(stolen), (turn_n, secret)
    # Her own read, through the e-mail, still has her rows (at V1: her name, not the billing dispute).
    at = now(flow)
    await flow.send(WHATSAPP_SOURCE, turn("email-3", [VICTIM_EMAIL], "Oi de novo", at, "email"))
    hers = await read(flow, VICTIM_EMAIL, "email-3", query=question)
    assert "Bia" in shown(hers)
