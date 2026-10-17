"""Retention and visibility by purpose and legal basis, per item (13, 6.6), across the real modules: an item
is tagged in the transaction that writes it with the purposes it was collected for; a read for another
purpose never sees it unless the policy declares that purpose compatible; an objection to one purpose takes
only the rows held for it; retention runs per purpose and an item leaves with its last purpose; and the
subject's access package names every item's purposes and legal bases."""

import io
import json
import zipfile
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.purposes import PurposeService
from niadra.config.models import CategoryRule, Policy, PurposeSettings, PurposeSpec, SourceConfig, SpaceConfig
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.privacy import ExportRequest, ForgetRequest
from niadra.domain.common.errors import InvalidInput
from niadra.domain.privacy.purposes import LegalBasis, PurposeTag
from niadra.domain.vocabulary import Audience, HandleType, Verification
from niadra.testing.cell import WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_first_read import remembered, said

PHONE = "+5511955554444"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
BILLING_CHAT = UUID("0192f5a0-0000-7000-8000-00000000c0b1")
MARKETING = UUID("0192f5a0-0000-7000-8000-00000000c0a1")
COLLECTIONS = UUID("0192f5a0-0000-7000-8000-00000000c0c1")
SURVEY = UUID("0192f5a0-0000-7000-8000-00000000c0d1")


def configured(
    *, contact: tuple[str, ...] = ("billing",), compatible: dict[str, list[str]] | None = None
) -> SpaceConfig:
    """The sample space with an agent per purpose. Nothing but the purposes keeps an item from a reader:
    both categories are open to every purpose at any verification level."""
    base = sample_config()
    reader = {"audience": Audience.CUSTOMER_AGENT, "channel": "whatsapp"}
    sources = [
        *base.sources,
        SourceConfig(source_id=BILLING_CHAT, name="billing-chat", purposes=frozenset({"billing"}), **reader),
        SourceConfig(source_id=MARKETING, name="offers", purposes=frozenset({"marketing"}), **reader),
        SourceConfig(
            source_id=COLLECTIONS, name="collections", purposes=frozenset({"collections"}), **reader
        ),
        # A purpose the legal team never declared tags nothing.
        SourceConfig(source_id=SURVEY, name="survey", purposes=frozenset({"surveys"}), **reader),
    ]
    policy = Policy(
        version="p-purposes",
        categories=[
            CategoryRule(name="contact", min_verification=Verification.V0),
            CategoryRule(name="billing", min_verification=Verification.V0),
        ],
        compatible_purposes=compatible or {},
    )
    purposes = PurposeSettings(
        version="1",
        purposes=[
            PurposeSpec(name="billing", legal_basis=LegalBasis.CONTRACT, retention_days=30),
            PurposeSpec(name="support", legal_basis=LegalBasis.CONTRACT, retention_days=90),
            PurposeSpec(name="marketing", legal_basis=LegalBasis.CONSENT, retention_days=180),
            PurposeSpec(name="collections", legal_basis=LegalBasis.LEGITIMATE_INTEREST),
        ],
        # The fact (category `contact`) by this map; the conversation, which no rule names, by the source
        # that collected it: the billing agent's.
        categories={"contact": list(contact)},
    )
    return base.model_copy(update={"sources": sources, "policy": policy, "purposes": purposes})


async def converse(flow: Flow, conversation: str, source: UUID = BILLING_CHAT) -> None:
    now = flow.cell.clock.now().isoformat()
    ended = {
        "type": "conversation.ended",
        "idempotency_key": f"{conversation}-end",
        "conversation_id": conversation,
    }
    first = said(conversation, "Me chama de Rafa, preciso da segunda via", now, channel="whatsapp")
    assert (await flow.send(source, first, {**ended, "occurred_at": now})).errors == []
    await flow.drain()


async def pack(flow: Flow, source: UUID, conversation: str) -> str:
    request = ContextRequest(
        subject=SUBJECT, view="chat", verification=Verification.V0, conversation_id=conversation
    )
    response = await flow.serve.context(principal(source, flow.cell.config.spaces()[0]), request)
    return response.text or ""


async def profile_id(flow: Flow) -> UUID:
    await pack(flow, BILLING_CHAT, "lookup")
    found = flow.receipts.sent[-1].profile_id
    assert found is not None
    return found


def service(cell: Cell) -> PurposeService:
    return PurposeService(cell.uow, cell.clock, cell.config)


@pytest.fixture
def flow(cell: Cell) -> Flow:
    return Flow.build(cell, ScriptedLlm(*(remembered() for _ in range(4))))


async def test_a_read_for_marketing_never_sees_what_billing_collected(cell: Cell, flow: Flow) -> None:
    cell.config.put(configured())
    await converse(flow, "wa-1")

    billing = await pack(flow, BILLING_CHAT, "b-1")
    assert "Rafa" in billing
    assert "segunda via" in billing
    marketing = await pack(flow, MARKETING, "m-1")
    assert "Rafa" not in marketing
    assert "segunda via" not in marketing

    subject = await profile_id(flow)
    async with cell.uow(cell.config.spaces()[0].space_id) as uow:
        profile = await uow.identity.get_profile(subject)
        assert profile is not None
        facts = await uow.memory.facts_for(profile.handle_ids)
        episodes = await uow.memory.episodes_for(profile.handle_ids)
    # Tagged in the transaction that wrote them, with the basis each purpose had then.
    assert [f.purposes for f in facts] == [(PurposeTag("billing", "contract"),)]
    assert [e.purposes for e in episodes] == [(PurposeTag("billing", "contract"),)]


async def test_a_compatible_purpose_the_policy_declares_reads_it_too(cell: Cell, flow: Flow) -> None:
    cell.config.put(configured(compatible={"billing": ["collections"]}))
    await converse(flow, "wa-1")

    assert "Rafa" in await pack(flow, COLLECTIONS, "c-1")
    # Compatibility runs one way: billing items reach collections, not marketing.
    assert "Rafa" not in await pack(flow, MARKETING, "m-1")


async def test_an_objection_takes_only_the_rows_held_for_that_purpose(cell: Cell, flow: Flow) -> None:
    cell.config.put(configured(contact=("billing", "support")))
    await converse(flow, "wa-1")
    subject = await profile_id(flow)

    done = await service(cell).object(
        cell.config.spaces()[0].space_id,
        ForgetRequest(target="profile", profile_id=subject, purpose="billing"),
        idempotency_key="objection-1",
    )
    assert done.status == "completed"
    # The conversation was held for billing alone and goes; the name stays for support.
    assert done.erased == {"episodes": 1, "purpose_narrowed": 1}
    assert "Rafa" in await pack(flow, WHATSAPP_SOURCE, "s-1")
    billing = await pack(flow, BILLING_CHAT, "b-1")
    assert "Rafa" not in billing
    assert "segunda via" not in billing

    # What the customer says for billing later is not kept either.
    cell.clock.advance(hours=1)
    await converse(flow, "wa-2")
    assert "segunda via" not in await pack(flow, BILLING_CHAT, "b-2")
    async with cell.uow(cell.config.spaces()[0].space_id) as uow:
        profile = await uow.identity.get_profile(subject)
        assert profile is not None
        assert await uow.memory.episodes_for(profile.handle_ids) == []
        objected = await uow.memory.objection_list(profile.handle_ids)
    assert [(purpose, at) for _, purpose, at in objected] == [("billing", done.requested_at)]


async def test_an_objection_names_a_declared_purpose(cell: Cell, flow: Flow) -> None:
    cell.config.put(configured())
    await converse(flow, "wa-1")
    with pytest.raises(InvalidInput):
        await service(cell).object(
            cell.config.spaces()[0].space_id,
            ForgetRequest(target="profile", profile_id=await profile_id(flow), purpose="profiling"),
            idempotency_key="k",
        )


async def test_an_item_with_two_purposes_leaves_with_the_last(cell: Cell, flow: Flow) -> None:
    cell.config.put(configured(contact=("billing", "support")))
    await converse(flow, "wa-1")
    space_id = cell.config.spaces()[0].space_id

    # Billing keeps 30 days, support 90: after 31 the conversation (billing only) goes, the name stays.
    cell.clock.advance(days=31)
    assert await service(cell).expire(space_id) == 1
    await flow.drain()
    support = await pack(flow, WHATSAPP_SOURCE, "s-1")
    assert "Rafa" in support
    assert "segunda via" not in support

    cell.clock.advance(days=60)
    assert await service(cell).expire(space_id) == 1
    await flow.drain()
    assert "Rafa" not in await pack(flow, WHATSAPP_SOURCE, "s-2")


async def test_the_access_package_names_each_items_purposes_and_legal_basis(cell: Cell, flow: Flow) -> None:
    cell.config.put(configured(contact=("billing", "support")))
    await converse(flow, "wa-1")
    subject = await profile_id(flow)
    space_id = cell.config.spaces()[0].space_id
    await service(cell).object(
        space_id, ForgetRequest(target="profile", profile_id=subject, purpose="support"), idempotency_key="k"
    )

    package = await flow.privacy.export_package(
        space_id, ExportRequest(profile_id=subject), requested_by="user:u"
    )
    data = await flow.privacy.download_package(space_id, UUID(package.run_id))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        facts = [json.loads(line) for line in archive.read("facts.jsonl").splitlines()]
        episodes = [json.loads(line) for line in archive.read("episodes.jsonl").splitlines()]
        objections = [json.loads(line) for line in archive.read("purpose_objections.jsonl").splitlines()]
    assert [f["purposes"] for f in facts] == [[{"purpose": "billing", "legal_basis": "contract"}]]
    assert [e["purposes"] for e in episodes] == [[{"purpose": "billing", "legal_basis": "contract"}]]
    assert [o["purpose"] for o in objections] == ["support"]


async def test_rows_written_before_the_purposes_are_tagged_by_the_loop(cell: Cell, flow: Flow) -> None:
    before = configured().model_copy(update={"purposes": PurposeSettings()})
    cell.config.put(before)
    await converse(flow, "wa-1")
    # Without purposes nothing is tagged and every reader sees everything, as before.
    assert "Rafa" in await pack(flow, MARKETING, "m-1")

    cell.config.put(configured())
    space_id = cell.config.spaces()[0].space_id
    assert await service(cell).step(space_id) > 0
    await flow.drain()
    assert "Rafa" not in await pack(flow, MARKETING, "m-2")
    assert "Rafa" in await pack(flow, BILLING_CHAT, "b-1")


async def test_rows_no_rule_named_are_tagged_again_when_the_document_changes(cell: Cell, flow: Flow) -> None:
    # No category, declared source purpose or default names a purpose for the survey's conversation: `{}`.
    cell.config.put(configured(contact=()))
    await converse(flow, "wa-1", source=SURVEY)
    space_id = cell.config.spaces()[0].space_id
    await service(cell).step(space_id)
    assert "Rafa" in await pack(flow, MARKETING, "m-1")

    widened = configured(contact=())
    widened = widened.model_copy(
        update={"purposes": widened.purposes.model_copy(update={"default": ["support"], "version": "2"})}
    )
    cell.config.put(widened)
    while await service(cell).step(space_id):
        pass
    await flow.drain()
    assert "Rafa" not in await pack(flow, MARKETING, "m-2")
    assert "Rafa" in await pack(flow, WHATSAPP_SOURCE, "s-1")
