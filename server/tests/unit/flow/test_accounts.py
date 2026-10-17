"""Accounts and contacts from the batch API: an event that names a person and an organization
links them, and a link that starts later is not active yet."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.adapters.memory.identity import InMemoryIdentityRepository
from niadra.app.compile import account_contacts, load_snapshot
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, TimelineRequest
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.common.errors import NotFound
from niadra.domain.compile.model import CompiledPack, profile_target
from niadra.domain.identity.handles import HandleKey, normalize_handle
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import AssertionMethod, DeliveryPath, HandleType, SubjectKind, Verification
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

PHONE = {"type": "phone_e164", "value": "+5511933332222"}
COMPANY = {"type": "system_id", "value": "T-77", "scope": "erp", "subject_kind": "account"}


async def test_an_event_naming_a_person_and_an_account_links_them(cell: Cell) -> None:
    flow = Flow.build(cell)
    now = cell.clock.now().isoformat()
    event = {
        "type": "event",
        "idempotency_key": "e1",
        "channel": "whatsapp",
        "conversation_id": "wa-1",
        "handles": [],
        "subjects": [
            {"kind": "account", "role": "carrier", "handles": [COMPANY]},
            {"kind": "person", "role": "driver", "handles": [PHONE]},
        ],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Sou o motorista da transportadora"},
        "occurred_at": now,
    }
    assert (await flow.send(WHATSAPP_SOURCE, event)).errors == []
    repo = InMemoryIdentityRepository(cell.uow.store)
    [person] = await repo.find_handles([_key(PHONE)])
    [account] = await repo.find_handles([_key(COMPANY)])
    [link] = await repo.active_links([person.handle_id])
    assert (link.org_handle_id, link.role) == (account.handle_id, "contact")
    # Linked, never merged: two profiles.
    assert await repo.profile_of(person.handle_id) != await repo.profile_of(account.handle_id)

    # Sending it again adds no second link.
    again = {**event, "idempotency_key": "e2"}
    assert (await flow.send(WHATSAPP_SOURCE, again)).errors == []
    assert len(await repo.active_links([person.handle_id])) == 1


async def test_a_link_that_starts_later_is_not_active_yet(cell: Cell) -> None:
    flow = Flow.build(cell)
    space = cell.config.spaces()[0].space_id
    later = cell.clock.now() + timedelta(days=3650)
    await flow.identity.create_link(
        space,
        Handle(type=HandleType.PHONE_E164, value="+5511922221111"),
        Handle(type=HandleType.SYSTEM_ID, value="T-88", scope="erp", subject_kind=SubjectKind.ACCOUNT),
        role="buyer",
        valid_from=later,
    )
    repo = InMemoryIdentityRepository(cell.uow.store)
    [person] = await repo.find_handles([_key({"type": "phone_e164", "value": "+5511922221111"})])
    assert await repo.active_links([person.handle_id]) == []


def _key(handle: dict[str, str]) -> HandleKey:
    kind = SubjectKind(handle["subject_kind"]) if "subject_kind" in handle else None
    return normalize_handle(HandleType(handle["type"]), handle["value"], handle.get("scope", ""), kind).key


def _said(conversation: str, handles: list[dict[str, str]], at: str, **extra: object) -> dict[str, object]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-1",
        "channel": "whatsapp",
        "conversation_id": conversation,
        "handles": handles,
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Somos a transportadora, queremos rever o contrato"},
        "occurred_at": at,
        **extra,
    }


def _remembered(value: str) -> dict[str, object]:
    return {
        "episode": {
            "summary": "Pediu revisão do contrato",
            "intent": "request",
            "category": "contract",
            "outcome": "pending",
            "resolution": None,
            "sentiment": 0.0,
        },
        "facts": [
            {
                "op": "add",
                "predicate": "preferred_name",
                "value": value,
                "target": None,
                "evidence": ["e1"],
                "valid_at": None,
                "certainty": 0.9,
                "contradicts": [],
                "about": None,
                "object": None,
            }
        ],
        "open_items": [],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


async def test_forgetting_an_account_erases_its_memory_and_links_and_keeps_the_person(cell: Cell) -> None:
    # The account's facts, episodes and objects go, and so do its links (deleted, not ended); the
    # person keeps what came from their own conversations. What came from an event that also named the
    # account goes with it: erasure follows the lineage of the event.
    answers = (_remembered("Transportadora T-77"), _remembered("Ana"), _remembered("Motorista da T-77"))
    flow = Flow.build(cell, ScriptedLlm(*answers))
    now = cell.clock.now().isoformat()

    async def talk(event: dict[str, object]) -> None:
        name = str(event["conversation_id"])
        end = {"type": "conversation.ended", "idempotency_key": f"{name}-end", "conversation_id": name}
        assert (await flow.send(WHATSAPP_SOURCE, event, {**end, "occurred_at": now})).errors == []
        await flow.drain()

    await talk(_said("b2b-1", [COMPANY], now))
    await talk(_said("wa-1", [PHONE], now))
    both = [
        {"kind": "account", "role": "carrier", "handles": [COMPANY]},
        {"kind": "person", "role": "driver", "handles": [PHONE]},
    ]
    await talk(_said("wa-2", [], now, subjects=both))
    invoice = {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": "erp-1",
        "channel": "erp",
        "canonical_type": "invoice.issued",
        "handles": [COMPANY],
        "object_refs": [{"type": "invoice", "namespace": "erp", "id": "0901"}],
        "speaker": {"role": "system"},
        "fields": {"amount": "990.00", "status": "open"},
        "occurred_at": now,
    }
    assert (await flow.send(BILLING_SOURCE, invoice)).errors == []
    await flow.drain()
    repo = InMemoryIdentityRepository(cell.uow.store)
    [person] = await repo.find_handles([_key(PHONE)])
    [account] = await repo.find_handles([_key(COMPANY)])
    memory = cell.uow.store.module("memory")

    def facts_of(handle_id: UUID) -> list[str]:
        return sorted(f.value for (origin, _), f in memory["facts"].items() if origin == handle_id)

    assert facts_of(account.handle_id) == ["Transportadora T-77"]
    assert facts_of(person.handle_id) == ["Ana", "Motorista da T-77"]
    assert await repo.active_links([person.handle_id])
    assert cell.uow.store.module("objects").get("objects")

    space = cell.config.spaces()[0].space_id
    request = ForgetRequest(target="handle", handle=Handle.model_validate(COMPANY))
    await flow.privacy.forget(space, request, idempotency_key="forget-account", requested_by="user:dpo")
    await flow.drain()

    assert facts_of(account.handle_id) == []
    assert facts_of(person.handle_id) == ["Ana"]
    assert await repo.active_links([person.handle_id]) == []
    assert not cell.uow.store.module("objects").get("objects")
    origins = {e.origin_handle_id for e, _ in memory.get("episodes", {}).values()}
    assert account.handle_id not in origins
    assert person.handle_id in origins


PAST_THE_OLD_CAP = 12
"""More organizations than the ten a person could hold before."""


async def test_a_person_keeps_every_link_however_many_organizations(cell: Cell) -> None:
    # Memory never cuts. A person who speaks for many companies keeps every link, declared or
    # made by co-occurrence: none is refused, none ends to make room.
    flow = Flow.build(cell)
    space = cell.config.spaces()[0].space_id
    person = Handle(type=HandleType.PHONE_E164, value="+5511911112222")

    def company(n: int) -> Handle:
        return Handle(
            type=HandleType.SYSTEM_ID, value=f"T-{n}", scope="erp", subject_kind=SubjectKind.ACCOUNT
        )

    for n in range(PAST_THE_OLD_CAP):
        await flow.identity.create_link(space, person, company(n), role="buyer")
    event = {
        "type": "event",
        "idempotency_key": "cap-1",
        "channel": "whatsapp",
        "conversation_id": "wa-cap",
        "handles": [],
        "subjects": [
            {"kind": "account", "handles": [{"type": "system_id", "value": "T-77", "scope": "erp"}]},
            {"kind": "person", "handles": [{"type": "phone_e164", "value": "+5511911112222"}]},
        ],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Sou da T-77 também"},
        "occurred_at": cell.clock.now().isoformat(),
    }
    assert (await flow.send(WHATSAPP_SOURCE, event)).accepted == 1
    repo = InMemoryIdentityRepository(cell.uow.store)
    [record] = await repo.find_handles([_key({"type": "phone_e164", "value": "+5511911112222"})])
    links = await repo.active_links([record.handle_id])
    orgs = {h.value for h in await repo.get_handles([x.org_handle_id for x in links])}
    assert orgs == {*(f"T-{n}" for n in range(PAST_THE_OLD_CAP)), "T-77"}


async def test_an_assertion_links_a_person_to_any_number_of_organizations(cell: Cell) -> None:
    # The assertion route turns a person and an organization into a link with no cap; the
    # same organization again is the link it already has.
    flow = Flow.build(cell)
    space = cell.config.spaces()[0].space_id
    person = Handle(type=HandleType.PHONE_E164, value="+5511911113333")

    def company(n: int) -> Handle:
        return Handle(
            type=HandleType.SYSTEM_ID, value=f"A-{n}", scope="erp", subject_kind=SubjectKind.ACCOUNT
        )

    linked = set()
    for n in range(PAST_THE_OLD_CAP):
        op = await flow.identity.create_assertion(
            space,
            [person, company(n)],
            method=AssertionMethod.EXPLICIT_IDENTIFY,
            subject_kind=SubjectKind.PERSON,
            actor="user:x",
            reason="contrato",
        )
        assert op.status == "linked"
        linked.add(op.link_id)
    again = await flow.identity.create_assertion(
        space,
        [person, company(0)],
        method=AssertionMethod.EXPLICIT_IDENTIFY,
        subject_kind=SubjectKind.PERSON,
        actor="user:x",
        reason="de novo",
    )
    assert again.status == "linked"
    assert again.link_id in linked
    assert len(linked) == PAST_THE_OLD_CAP


async def test_an_account_carries_what_its_contacts_said_twenty_in_detail(cell: Cell) -> None:
    # The account's memory is read with its contacts' recent episodes, by name and role;
    # 20 of them in detail and how many there are.
    flow = Flow.build(cell, ScriptedLlm(_remembered("Bia")))
    space = cell.config.spaces()[0].space_id
    company = Handle.model_validate(COMPANY)
    for n in range(21):
        buyer = Handle(type=HandleType.EMAIL, value=f"comprador{n}@transportes.com.br")
        await flow.identity.create_link(space, buyer, company, role="buyer")
    cell.clock.advance(minutes=5)
    now = cell.clock.now().isoformat()
    talker = {"type": "email", "value": "comprador7@transportes.com.br"}
    end = {"type": "conversation.ended", "idempotency_key": "bia-end", "conversation_id": "bia"}
    end["occurred_at"] = now
    assert (await flow.send(WHATSAPP_SOURCE, _said("bia", [talker], now), end)).errors == []
    await flow.drain()

    async with cell.uow(space) as uow:
        found = await uow.identity.find_handle(HandleType.SYSTEM_ID, "erp", "T-77")
        assert found is not None
        account = await uow.identity.profile_of(found.handle_id)
        assert account is not None
        snapshot = await load_snapshot(uow, account, cell.clock.now())
    assert snapshot.contacts_total == 21
    assert len(snapshot.contacts) == 20
    assert (snapshot.contacts[0].name, snapshot.contacts[0].role) == ("Bia", "buyer")
    assert [e.summary for e in snapshot.episodes] == ["Pediu revisão do contrato"]


async def test_an_accounts_contacts_are_read_in_one_statement_however_many_there_are(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each contact's profile was read on its own: an account of 500 contacts, 500 statements per compile."""
    flow = Flow.build(cell, ScriptedLlm(_remembered("Bia")))
    space = cell.config.spaces()[0].space_id
    company = Handle.model_validate(COMPANY)
    for n in range(5):
        buyer = Handle(type=HandleType.EMAIL, value=f"comprador{n}@transportes.com.br")
        await flow.identity.create_link(space, buyer, company, role="buyer")
    one = InMemoryIdentityRepository.get_profile

    async def batch(self: InMemoryIdentityRepository, ids: list[UUID]) -> list[object]:
        return [p for i in sorted(set(ids)) if (p := await one(self, i)) is not None]

    async def single(self: InMemoryIdentityRepository, profile_id: UUID) -> object:
        raise AssertionError("a statement per contact")

    async with cell.uow(space) as uow:
        found = await uow.identity.find_handle(HandleType.SYSTEM_ID, "erp", "T-77")
        assert found is not None
        account = await uow.identity.profile_of(found.handle_id)
        assert account is not None
        monkeypatch.setattr(InMemoryIdentityRepository, "get_profiles", batch)
        monkeypatch.setattr(InMemoryIdentityRepository, "get_profile", single)
        contacts = await account_contacts(uow, account)
    assert len(contacts) == 5


async def test_a_contacts_open_item_stays_the_accounts_past_twenty_newer_contacts(cell: Cell) -> None:
    """juridico-sandbox, 06/10/2026: the attendant's promise to a client's contact left the account's
    timeline once more than 20 contacts were seen after him, while the case's line still held it: the
    account read open items from the 20 contacts seen last only."""
    promised = _remembered("Bia")
    promised["open_items"] = [
        {"description": "Enviar a minuta revisada do contrato", "kind": "promise", "owner": "company",
         "evidence": ["e1"], "due_at": None, "object": None, "expected_operation": None}
    ]  # fmt: skip
    flow = Flow.build(cell, ScriptedLlm(promised))
    space = cell.config.spaces()[0].space_id
    company = Handle.model_validate(COMPANY)
    talker = {"type": "email", "value": "bia@transportes.com.br"}
    await flow.identity.create_link(space, Handle.model_validate(talker), company, role="buyer")
    now = cell.clock.now().isoformat()
    end = {
        "type": "conversation.ended",
        "idempotency_key": "bia-end",
        "conversation_id": "bia",
        "occurred_at": now,
    }
    assert (await flow.send(WHATSAPP_SOURCE, _said("bia", [talker], now), end)).errors == []
    await flow.drain()
    for n in range(21):
        cell.clock.advance(minutes=1)
        buyer = Handle(type=HandleType.EMAIL, value=f"comprador{n}@transportes.com.br")
        await flow.identity.create_link(space, buyer, company, role="buyer")
        later = {"type": "event", "idempotency_key": f"seen-{n}", "channel": "whatsapp",
                 "conversation_id": f"seen-{n}", "handles": [{"type": "email", "value": buyer.value}],
                 "speaker": {"role": "system"}, "content": {"type": "text", "text": "ok"},
                 "occurred_at": cell.clock.now().isoformat()}  # fmt: skip
        assert (await flow.send(WHATSAPP_SOURCE, later)).errors == []

    async with cell.uow(space) as uow:
        found = await uow.identity.find_handle(HandleType.SYSTEM_ID, "erp", "T-77")
        assert found is not None
        account = await uow.identity.profile_of(found.handle_id)
        assert account is not None
        snapshot = await load_snapshot(uow, account, cell.clock.now())
    assert snapshot.contacts_total == 22
    assert "Bia" not in [c.name for c in snapshot.contacts]
    assert [i.description for i in snapshot.open_items] == ["Enviar a minuta revisada do contrato"]


async def test_about_holds_for_any_handle_of_the_linked_person(cell: Cell) -> None:
    # The link was made on the e-mail; the call comes from the phone of the same person.
    flow = Flow.build(cell, ScriptedLlm(_remembered("Ana")))
    space = cell.config.spaces()[0].space_id
    email = Handle(type=HandleType.EMAIL, value="ana@transportes.com.br")
    phone = Handle.model_validate(PHONE)
    await flow.identity.create_assertion(
        space, [email, phone], method=AssertionMethod.EXPLICIT_IDENTIFY, subject_kind=SubjectKind.PERSON,
        actor="user:ops", reason=None,
    )  # fmt: skip
    await flow.drain()
    await flow.identity.create_link(space, email, Handle.model_validate(COMPANY), role="buyer")
    request = ContextRequest(
        subject=phone, about=Handle.model_validate(COMPANY), verification=Verification.V1
    )
    answer = await flow.serve.context(principal(WHATSAPP_SOURCE), request)
    assert answer.verification is not None


async def _episodes_of(flow: Flow, handle: dict[str, str]) -> list[str]:
    async with flow.cell.uow(SPACE_ID) as uow:
        key = _key(handle)
        found = await uow.identity.find_handle(key.type, key.scope, key.value)
        assert found is not None
        profile = await uow.identity.profile_of(found.handle_id)
        assert profile is not None
        return [e.summary for e in await uow.memory.episodes_for(profile.handle_ids)]


async def test_a_person_who_logs_in_for_the_company_leaves_the_conversation_on_the_company(
    cell: Cell,
) -> None:
    # A buyer proves their login, then the turns are about the company (its handle): the proof opened the
    # session, and the memory still goes to the company the turns name, not to the person who proved.
    flow = Flow.build(cell, ScriptedLlm(_remembered("Transportadora T-77")))
    now = cell.clock.now().isoformat()
    proof = {"type": "verify", "idempotency_key": "b2b-9-proof", "method": "login", "level": "V2",
             "conversation_id": "b2b-9", "handle": PHONE, "occurred_at": now}  # fmt: skip
    assert (await flow.send(WHATSAPP_SOURCE, proof)).errors == []
    end = {
        "type": "conversation.ended",
        "idempotency_key": "b2b-9-end",
        "conversation_id": "b2b-9",
        "occurred_at": now,
    }
    assert (await flow.send(WHATSAPP_SOURCE, _said("b2b-9", [COMPANY], now), end)).errors == []
    await flow.drain()
    assert await _episodes_of(flow, COMPANY) == ["Pediu revisão do contrato"]
    assert await _episodes_of(flow, PHONE) == []


async def test_a_conversation_that_only_proved_writes_no_episode(cell: Cell) -> None:
    llm = ScriptedLlm()
    flow = Flow.build(cell, llm)
    now = cell.clock.now().isoformat()
    proof = {"type": "verify", "idempotency_key": "only-proof", "method": "login", "level": "V2",
             "conversation_id": "only-proof", "handle": PHONE, "occurred_at": now}  # fmt: skip
    end = {"type": "conversation.ended", "idempotency_key": "only-proof-end", "conversation_id": "only-proof",
           "occurred_at": now}  # fmt: skip
    assert (await flow.send(WHATSAPP_SOURCE, proof, end)).errors == []
    await flow.drain()
    assert llm.calls == []
    assert not flow.cell.uow.store.module("memory").get("episodes")


async def test_what_a_contact_said_compiles_the_companys_pack_again(cell: Cell) -> None:
    # The company's pack renders its contacts' recent conversations: one of them talking must recompile it.
    flow = Flow.build(cell, ScriptedLlm(_remembered("Ana")))
    await flow.identity.create_link(
        SPACE_ID, Handle.model_validate(PHONE), Handle.model_validate(COMPANY), role="buyer"
    )
    async with cell.uow(SPACE_ID) as uow:
        key = _key(COMPANY)
        company = await uow.identity.find_handle(key.type, key.scope, key.value)
        assert company is not None
        account = await uow.identity.profile_of(company.handle_id)
    assert account is not None
    flow.ran.clear()
    now = cell.clock.now().isoformat()
    end = {
        "type": "conversation.ended",
        "idempotency_key": "wa-ana-end",
        "conversation_id": "wa-ana",
        "occurred_at": now,
    }
    assert (await flow.send(WHATSAPP_SOURCE, _said("wa-ana", [PHONE], now), end)).errors == []
    await flow.drain()
    compiled = {t.profile_id for t in flow.ran if t.name is TaskName.COMPILE}
    assert account.profile_id in compiled


async def test_the_company_opens_a_contacts_conversation_its_timeline_shows(cell: Cell) -> None:
    # The company's history lists what its contacts said; the ids it lists open under the company.
    flow = Flow.build(cell, ScriptedLlm(_remembered("Ana")))
    await flow.identity.create_link(
        SPACE_ID, Handle.model_validate(PHONE), Handle.model_validate(COMPANY), role="buyer"
    )
    now = cell.clock.now().isoformat()
    end = {
        "type": "conversation.ended",
        "idempotency_key": "wa-ana-end",
        "conversation_id": "wa-ana",
        "occurred_at": now,
    }
    assert (await flow.send(WHATSAPP_SOURCE, _said("wa-ana", [PHONE], now), end)).errors == []
    proof = {"type": "verify", "idempotency_key": "acme-read", "method": "login", "level": "V2",
             "conversation_id": "acme-read", "handle": COMPANY, "occurred_at": now}  # fmt: skip
    assert (await flow.send(WHATSAPP_SOURCE, proof)).errors == []
    await flow.drain()
    agent = principal(WHATSAPP_SOURCE, cell.config.spaces()[0])
    company = Handle.model_validate(COMPANY)
    read = {"verification": Verification.V2, "conversation_id": "acme-read"}
    page = await flow.serve.timeline(
        agent,
        TimelineRequest.model_validate(
            {"subject": COMPANY, "conversation_id": "acme-read", "verification": "V2"}
        ),
    )
    [listed] = [i.id for i in page.items if i.kind == "episode"]
    opened = await flow.serve.open_item(agent, listed, subject=company, **read)  # type: ignore[arg-type]
    assert (opened.id, opened.summary) == (listed, "Pediu revisão do contrato")
    with pytest.raises(NotFound):
        await flow.serve.open_item(agent, str(uuid4()), subject=company, **read)  # type: ignore[arg-type]


async def test_a_linked_contacts_first_read_about_the_company_is_compiled_before_it(cell: Cell) -> None:
    """P6 (phase 2 of the legal integrator): an `about` pack was compiled at every read, so a new contact's
    first read timed out. The link compiles the person's packs about the company; the company's changes
    compile them again, and a pack of an older company generation is never served as current."""
    flow = Flow.build(cell, ScriptedLlm(_remembered("Ana")))
    phone, company = Handle.model_validate(PHONE), Handle.model_validate(COMPANY)
    link = await flow.identity.create_link(SPACE_ID, phone, company, role="buyer")
    await flow.drain()
    async with cell.uow(SPACE_ID) as uow:
        person = await uow.identity.profile_of(link.person_handle_id)
        account = await uow.identity.profile_of(link.org_handle_id)
        assert person is not None and account is not None  # noqa: PT018
        stored = await uow.compile.packs_for(profile_target(person.profile_id))
    marker = f"|{link.org_handle_id}|buyer|0|"
    assert any(marker in s.pack_key for s in stored)
    read = ContextRequest(subject=phone, about=company, verification=Verification.V1)
    first = await flow.serve.context(principal(WHATSAPP_SOURCE), read)
    assert first.path is not DeliveryPath.T2  # served compiled, not compiled at the read
    # The company changes (a contact talks): its compilation compiles the person's packs about it again.
    flow.ran.clear()
    now = cell.clock.now().isoformat()
    end = {"type": "conversation.ended", "idempotency_key": "wa-ana-end", "conversation_id": "wa-ana"}
    assert (
        await flow.send(WHATSAPP_SOURCE, _said("wa-ana", [PHONE], now), {**end, "occurred_at": now})
    ).errors == []
    await flow.drain()
    compiled = [t for t in flow.ran if t.name is TaskName.COMPILE]
    assert account.profile_id in {t.profile_id for t in compiled}
    assert any(t.profile_id == person.profile_id and t.payload.get("reason") == "account" for t in compiled)
    async with cell.uow(SPACE_ID) as uow:
        company_generation = await uow.compile.current_generation(profile_target(account.profile_id))
        stored = await uow.compile.packs_for(profile_target(person.profile_id))
    about = [CompiledPack.from_bytes(s.body) for s in stored if marker in s.pack_key]
    assert about
    assert all(p.account_generation == company_generation for p in about)
    # The link ends: the person's packs about the company go.
    await flow.identity.end_link(SPACE_ID, link.link_id)
    await flow.drain()
    async with cell.uow(SPACE_ID) as uow:
        stored = await uow.compile.packs_for(profile_target(person.profile_id))
    assert not any(marker in s.pack_key for s in stored)


async def test_a_warm_read_about_the_company_finds_its_link_without_the_database(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read about an organization kept its link like a read by a handle keeps its target: the resolve stage
    was 15 to 73 ms on every such read (03/10/2026). An ended link is gone at the next read."""
    flow = Flow.build(cell, ScriptedLlm(_remembered("Ana")))
    phone, company = Handle.model_validate(PHONE), Handle.model_validate(COMPANY)
    link = await flow.identity.create_link(SPACE_ID, phone, company, role="buyer")
    await flow.drain()
    cell.clock.advance(seconds=11)  # past the seconds a new customer's handles are still joining
    read = ContextRequest(subject=phone, about=company, verification=Verification.V1)
    agent = principal(WHATSAPP_SOURCE)
    assert not (await flow.serve.context(agent, read)).about_unlinked
    looked: list[object] = []
    original = InMemoryIdentityRepository.active_links

    async def counted(self: InMemoryIdentityRepository, handle_ids: object) -> object:
        looked.append(handle_ids)
        return await original(self, handle_ids)  # type: ignore[arg-type]

    monkeypatch.setattr(InMemoryIdentityRepository, "active_links", counted)
    warm = await flow.serve.context(agent, read)
    assert not warm.about_unlinked
    assert looked == []  # the link came from the read state
    await flow.identity.end_link(SPACE_ID, link.link_id)
    assert (await flow.serve.context(agent, read)).about_unlinked
    assert looked  # the ended link dropped what was kept: the read asked the database again


async def test_an_about_pack_the_company_moved_past_is_served_and_compiled_again_never_at_the_read(
    cell: Cell,
) -> None:
    """The stale case of 03/10/2026 (request 26b59a0c...): another contact's conversation compiled the company
    again, its generation moved, and the person's about pack was compiled at two reads while its own
    compilation waited in the queue. The pack a compilation behind is served, and the read asks for the
    compilation (coalesced); only a privacy change of the company refuses it."""
    flow = Flow.build(cell, ScriptedLlm(_remembered("Ana")))
    phone, company = Handle.model_validate(PHONE), Handle.model_validate(COMPANY)
    link = await flow.identity.create_link(SPACE_ID, phone, company, role="buyer")
    await flow.drain()
    async with cell.uow(SPACE_ID) as uow:
        person = await uow.identity.profile_of(link.person_handle_id)
        account = await uow.identity.profile_of(link.org_handle_id)
    assert person is not None
    assert account is not None
    # The company compiles again; the person's compilation it asks for is still in the queue.
    await flow.compile.compile(Task(TaskName.COMPILE, SPACE_ID, profile_id=account.profile_id))
    flow._outbox()[:] = [t for t in flow._outbox() if t.profile_id != person.profile_id]
    read = ContextRequest(subject=phone, about=company, verification=Verification.V1)
    agent = principal(WHATSAPP_SOURCE)
    # The request itself: one compilation of the person, once per window.
    await type(flow.serve)._request_account(flow.serve, SPACE_ID, person.profile_id)
    asked: list[UUID] = []

    async def request(space_id: UUID, profile_id: UUID) -> None:
        asked.append(profile_id)

    flow.serve._request_account = request  # type: ignore[method-assign]
    behind = await flow.serve.context(agent, read)
    assert behind.path is not DeliveryPath.T2
    for _ in range(5):
        await asyncio.sleep(0)  # the read's request runs in the background
    assert asked == [person.profile_id]
    # A privacy change of the company (an erasure, an objection): the pack compiled before it is refused.
    async with cell.uow(SPACE_ID) as uow:
        await uow.identity.bump_privacy_version(account.profile_id)
    refused = await flow.serve.context(agent, read)
    assert refused.path is DeliveryPath.T2
