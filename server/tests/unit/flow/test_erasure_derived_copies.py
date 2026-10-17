"""A person named in what memory derived from someone else's conversation (5.14.0): erasing them redacts them
out of the other person's facts, open items, episode summaries, signals, agent notes, recordings and archived
events, found by the index of what each row and object names; the other person's vectors are made again, and
their compiled packs stop being served. A legal hold on the other person keeps their rows as they are, and the
erasure says so."""

from datetime import UTC
from typing import Any
from uuid import UUID, uuid4

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.holds import LegalHoldsService
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.privacy import ForgetRequest
from niadra.contracts.signals import LegalHoldCreate
from niadra.domain.agent_memory.model import AgentNoteRecord, NoteDraft, NoteKind, NoteOrigin, NoteStatus
from niadra.domain.common.ids import uuid7
from niadra.domain.identity.phones import normalize_phone
from niadra.domain.privacy.erasure import ErasureStatus
from niadra.domain.privacy.files import from_parquet
from niadra.domain.privacy.raw_index import ERASED
from niadra.domain.privacy.retention import month_key
from niadra.domain.signals.body import BODY_VERSION, Body, Stated, decode, encode
from niadra.domain.signals.records import SELF, SignalRow, SignalWrite, body_aad
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.digest import digest
from niadra.domain.turns.storage import turn_at
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_first_read import PHONE, said
from tests.unit.turns.records import turn

CLARA = "+5511987654321"
CLARA_TEXT = "(11) 98765-4321"
EMAIL = "clara@exemplo.com"


def extraction() -> dict[str, Any]:
    return {
        "episode": {
            "summary": f"Pediu para avisar a Clara pelo e-mail {EMAIL}",
            "intent": "request",
            "category": "invoice",
            "outcome": "resolved",
            "resolution": "Aviso combinado",
            "sentiment": 0.1,
        },
        "facts": [
            {
                "op": "add",
                "predicate": "preferred_name",
                "value": f"Rafa, irmão da Clara {CLARA_TEXT}",
                "target": None,
                "evidence": ["e1"],
                "valid_at": None,
                "certainty": 0.9,
                "contradicts": [],
                "about": None,
                "object": None,
            }
        ],
        "open_items": [
            {
                "description": f"Ligar para a Clara no {CLARA_TEXT} sobre a fatura",
                "owner": "company",
                "due_at": None,
                "evidence": ["e1"],
                "object": None,
                "expected_operation": None,
                "about": None,
            }
        ],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


def from_clara(conversation: str, text: str, at: str) -> dict[str, Any]:
    handles = [{"type": "phone_e164", "value": CLARA}, {"type": "email", "value": EMAIL}]
    return {**said(conversation, text, at, channel="whatsapp"), "handles": handles}


async def remember(cell: Cell) -> Flow:
    """Marcos's conversation names Clara; memory derives his fact, item and episode from it."""
    flow = Flow.build(cell, ScriptedLlm(extraction()))
    now = cell.clock.now().isoformat()
    text = f"me chama de Rafa; o telefone da Clara é {CLARA_TEXT} e o e-mail {EMAIL}"
    ended = {"type": "conversation.ended", "idempotency_key": "wa-m-end", "conversation_id": "wa-marcos"}
    await flow.send(
        WHATSAPP_SOURCE, said("wa-marcos", text, now, channel="whatsapp"), {**ended, "occurred_at": now}
    )
    await flow.send(WHATSAPP_SOURCE, from_clara("wa-clara", "oi, aqui é a Clara", now))
    await flow.drain()
    return flow


async def erase(flow: Flow, *handles: Handle) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    for handle in handles:
        accepted = await flow.privacy.forget(
            SPACE_ID,
            ForgetRequest(target="handle", handle=handle),
            idempotency_key=f"forget-{uuid4()}",
            requested_by="user:dpo",
        )
        await flow.drain()
        async with flow.cell.uow(SPACE_ID) as uow:
            request = await uow.privacy.get_erasure(SPACE_ID, UUID(accepted.request_id))
        assert request is not None
        assert request.status is ErasureStatus.COMPLETED
        counts[handle.type] = request.counts
    return counts


CLARA_HANDLES = (Handle(type=HandleType.PHONE_E164, value=CLARA), Handle(type=HandleType.EMAIL, value=EMAIL))


async def marcos(cell: Cell) -> tuple[UUID, UUID]:
    async with cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert handle is not None
        profile = await uow.identity.profile_of(handle.handle_id)
        assert profile is not None
    return handle.handle_id, profile.profile_id


def memory(cell: Cell) -> dict[str, Any]:
    return cell.uow.store.data["memory"]


async def test_a_fact_and_an_item_of_someone_else_that_name_the_person_are_redacted(cell: Cell) -> None:
    flow = await remember(cell)
    facts = [f.value for f in memory(cell)["facts"].values()]
    items = [i.description for i in memory(cell)["items"].values()]
    assert any(CLARA_TEXT in v for v in facts)
    assert any(CLARA_TEXT in d for d in items)

    counts = await erase(flow, *CLARA_HANDLES)

    assert [f.value for f in memory(cell)["facts"].values()] == [f"Rafa, irmão da Clara {ERASED}"]
    assert [i.description for i in memory(cell)["items"].values()] == [
        f"Ligar para a Clara no {ERASED} sobre a fatura"
    ]
    assert counts[HandleType.PHONE_E164]["named_rows_redacted"] >= 2


async def test_an_episode_summary_that_names_the_persons_email_is_redacted(cell: Cell) -> None:
    flow = await remember(cell)

    await erase(flow, *CLARA_HANDLES)

    summaries = {e.summary for e, _ in memory(cell)["episodes"].values()}
    assert summaries == {f"Pediu para avisar a Clara pelo e-mail {ERASED}"}


BRUNO = "+551198765432"
"""Bruno reached the company by a mobile from before the ninth digit: his handle keeps it with the digit."""
BRUNO_TEXT = "+55 11 9876-5432"
"""How Marcos wrote it, and so how his summary does."""


async def served(flow: Flow, conversation: str) -> str:
    """Marcos's next block, in a conversation that proved who he is (his episodes are served at V2)."""
    now = flow.cell.clock.now().isoformat()
    assert (
        await flow.send(WHATSAPP_SOURCE, said(conversation, "oi, sou o Marcos", now, channel="whatsapp"))
    ).errors == []
    await flow.prove(WHATSAPP_SOURCE, conversation, PHONE, "V2")
    await flow.drain()
    request = ContextRequest(
        subject=Handle(type=HandleType.PHONE_E164, value=PHONE),
        view="chat",
        verification=Verification.V2,
        conversation_id=conversation,
    )
    response = await flow.serve.context(principal(WHATSAPP_SOURCE, flow.cell.config.spaces()[0]), request)
    return "\n".join(p for p in (response.text, response.slots) if p)


async def test_a_number_written_in_another_form_of_the_same_line_is_redacted_from_the_served_summary(
    cell: Cell,
) -> None:
    summary = f"Pediu que liguem para o Bruno no {BRUNO_TEXT}"
    scripted = {**extraction(), "episode": {**extraction()["episode"], "summary": summary}}
    flow = Flow.build(cell, ScriptedLlm({**scripted, "facts": [], "open_items": []}))
    now = cell.clock.now().isoformat()
    ended = {"type": "conversation.ended", "idempotency_key": "wa-m-end", "conversation_id": "wa-marcos"}
    text = f"liguem para o Bruno no {BRUNO_TEXT}"
    await flow.send(
        WHATSAPP_SOURCE, said("wa-marcos", text, now, channel="whatsapp"), {**ended, "occurred_at": now}
    )
    bruno = {**said("wa-bruno", "oi, aqui é o Bruno", now, channel="whatsapp")}
    await flow.send(WHATSAPP_SOURCE, {**bruno, "handles": [{"type": "phone_e164", "value": BRUNO}]})
    await flow.drain()
    async with cell.uow(SPACE_ID) as uow:
        kept = await uow.identity.find_handle(HandleType.PHONE_E164, "", normalize_phone(BRUNO))
    assert kept is not None
    assert kept.value[-10:] != "".join(c for c in BRUNO_TEXT if c.isdigit())[-10:], "kept in another form"
    assert BRUNO_TEXT in await served(flow, "wa-marcos-2")

    counts = await erase(flow, Handle(type=HandleType.PHONE_E164, value=BRUNO))

    assert counts[HandleType.PHONE_E164]["named_rows_redacted"] >= 1
    block = await served(flow, "wa-marcos-3")
    assert f"Bruno no {ERASED}" in block
    assert "9876" not in block


async def test_a_redacted_rows_vector_is_made_again_from_the_redacted_text(cell: Cell) -> None:
    flow = await remember(cell)
    vectors = memory(cell)["vectors"]
    named = [k for k in vectors if k[0] in ("facts", "open_items")]
    before = {k: vectors[k][0] for k in named}
    assert len(named) == 2
    assert all(v is not None for v in before.values())

    await erase(flow, CLARA_HANDLES[0])

    after = {k: vectors.get(k, (None,))[0] for k in named}
    assert all(after[k] is not None and after[k] != before[k] for k in named), "embedded again, without her"


async def test_the_other_persons_cached_pack_is_no_longer_served(cell: Cell) -> None:
    flow = await remember(cell)
    _, profile = await marcos(cell)
    async with cell.uow(SPACE_ID) as uow:
        before = (await uow.identity.get_profile(profile)).privacy_version  # type: ignore[union-attr]
    purged: list[UUID] = []
    original = flow.privacy._cache.purge_profile  # type: ignore[union-attr]

    async def purge(space_id: UUID, profile_id: UUID) -> int:
        purged.append(profile_id)
        return await original(space_id, profile_id)

    flow.privacy._cache.purge_profile = purge  # type: ignore[union-attr,method-assign]

    await erase(flow, CLARA_HANDLES[0])

    async with cell.uow(SPACE_ID) as uow:
        after = (await uow.identity.get_profile(profile)).privacy_version  # type: ignore[union-attr]
    assert after > before
    assert profile in purged


async def test_a_legal_hold_on_the_other_person_keeps_their_rows_and_is_reported(cell: Cell) -> None:
    flow = await remember(cell)
    body = LegalHoldCreate(handle=Handle(type=HandleType.PHONE_E164, value=PHONE), reason="processo em curso")
    await LegalHoldsService(cell.uow, cell.keys, cell.clock).create(
        principal(WHATSAPP_SOURCE), body, idempotency_key="hold-marcos", by="user:dpo"
    )

    counts = await erase(flow, CLARA_HANDLES[0])

    assert any(CLARA_TEXT in f.value for f in memory(cell)["facts"].values())
    assert any(CLARA_TEXT in i.description for i in memory(cell)["items"].values())
    assert counts[HandleType.PHONE_E164]["named_held"] >= 2
    assert "named_rows_redacted" not in counts[HandleType.PHONE_E164]


async def test_an_archived_event_of_someone_else_that_names_the_person_is_rewritten(cell: Cell) -> None:
    flow = await remember(cell)
    started = cell.clock.now()
    cell.clock.advance(days=45)
    month = month_key(started.astimezone(UTC).date())
    await flow.privacy.archive_events(Task(TaskName.ARCHIVE_EVENTS, SPACE_ID, {"month": month}))
    assert {r["conversation_id"] for r in await archived(flow)} == {"wa-marcos", "wa-clara"}

    counts = await erase(flow, *CLARA_HANDLES)

    rows = {r["conversation_id"]: r for r in await archived(flow)}
    assert set(rows) == {"wa-marcos"}, "her own archived event goes whole"
    assert (
        rows["wa-marcos"]["text"] == f"me chama de Rafa; o telefone da Clara é {ERASED} e o e-mail {ERASED}"
    )
    assert counts[HandleType.PHONE_E164]["named_archived_redacted"] == 1


async def test_a_turn_recording_of_someone_else_that_names_the_person_is_redacted(cell: Cell) -> None:
    flow = await remember(cell)
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    recorded = turn(
        "0192f5a0-7c3e-7a10-9d2b-5e61a4c0d0aa",
        conversation_id="wa-marcos",
        args={"note": f"ligar para a Clara no {CLARA}"},
        flags=["handoff"],
    )
    assert (await flow.turns.record(caller, [recorded])).accepted == 1
    await flow.drain()
    assert any(CLARA in str(r) for r in await recordings(flow))

    await erase(flow, *CLARA_HANDLES)

    found = await recordings(flow)
    assert len(found) == 2, "the short tier's and the kept tier's copies"
    assert all(CLARA not in str(r) and ERASED in str(r) for r in found)
    for record in found:
        for blob in record["blobs"].values():
            assert (blob["sha256"], blob["size"]) == digest(blob["content"]), "the digest of what it keeps"


async def test_an_instruction_in_someone_elses_signals_that_names_the_person_is_redacted(cell: Cell) -> None:
    flow = await remember(cell)
    handle, _ = await marcos(cell)
    said_at = cell.clock.now()
    stated = Stated(
        key="k1",
        attr="contact",
        op="eq",
        values=[CLARA],
        strength="must",
        scope="object",
        object="ticket:tenant:77",
        source="said",
        said_at=said_at,
        text=f"só falar com a Clara no {CLARA_TEXT}",
    )
    body = Body(at=said_at, stated=[stated])
    sealed, version = await cell.keys.encrypt(SPACE_ID, encode(body), body_aad(handle, SELF))
    row = SignalRow(handle, SELF, sealed, version, BODY_VERSION, len(sealed), said_at, said_at)
    async with cell.uow(SPACE_ID) as uow:
        await uow.signals.store(SignalWrite(handle, said_at, (row,), (), ()))

    await erase(flow, *CLARA_HANDLES)

    async with cell.uow(SPACE_ID) as uow:
        [kept] = await uow.signals.rows_for([handle])
    opened = decode(await cell.keys.decrypt(SPACE_ID, kept.body_ct, body_aad(handle, SELF), kept.dek_version))
    assert opened.stated[0].text == f"só falar com a Clara no {ERASED}"
    assert opened.stated[0].values == [ERASED]


async def test_an_agent_note_that_names_the_person_is_redacted(cell: Cell) -> None:
    flow = await remember(cell)
    draft = NoteDraft(kind=NoteKind.PROCEDURE, title="Contato", body=f"Para a fatura, escrever para {EMAIL}.")
    note = AgentNoteRecord(
        note_id=uuid7(),
        source_id=WHATSAPP_SOURCE,
        draft=draft,
        origin=NoteOrigin.AGENT,
        version=1,
        status=NoteStatus.ACTIVE,
        created_at=cell.clock.now(),
        created_by=f"source:{WHATSAPP_SOURCE}",
        lineage_id=uuid7(),
    )
    async with cell.uow(SPACE_ID) as uow:
        await uow.agent_memory.add_note(note)

    await erase(flow, *CLARA_HANDLES)

    async with cell.uow(SPACE_ID) as uow:
        kept = await uow.agent_memory.get_note(note.note_id)
    assert kept is not None
    assert kept.draft.body == f"Para a fatura, escrever para {ERASED}."


async def archived(flow: Flow) -> list[dict[str, Any]]:
    rows = [
        row
        for key, data in sorted(flow.cell.objects.objects.items())
        if key.startswith(f"archive/events/{SPACE_ID}/") and key.endswith(".parquet")
        for row in from_parquet(data)
    ]
    return [
        {
            "conversation_id": r["conversation_id"],
            "text": (await flow.privacy._sealed_record(SPACE_ID, r)).text,
        }
        for r in rows
    ]


async def recordings(flow: Flow) -> list[dict[str, Any]]:
    found = []
    for key, data in sorted(flow.cell.objects.objects.items()):
        if (at := turn_at(key)) is not None:
            found.append((await flow.turns._open(SPACE_ID, at[1], data))["record"])
    return found
