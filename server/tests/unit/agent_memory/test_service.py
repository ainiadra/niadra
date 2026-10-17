"""Agent memory (10, C): refusal of personal data, visibility by owner, vendor and space, the block, search,
versions, limits, proposals, distillation, erasure and export."""

import re
from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.agent_memory import AgentMemoryService
from niadra.config.models import AgentMemorySettings, SourceConfig
from niadra.contracts.agent_memory import (
    AgentMemorySearchRequest,
    AgentNoteVisibility,
    ApproveProposalRequest,
    CreateAgentNoteRequest,
    DistillRequest,
    ProposalStatus,
    UpdateAgentNoteRequest,
)
from niadra.domain.agent_memory.distill import MERGE_SYSTEM, transcript_windows
from niadra.domain.agent_memory.rules import WRITES_PER_HOUR
from niadra.domain.common.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PersonalDataInAgentMemory,
    RateLimited,
    ScopeMissing,
    Unavailable,
)
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.ingest.events import NewEvent
from niadra.domain.records import EventRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Audience, Environment, EventKind, ReceiptSurface, Scope, Speaker
from niadra.ports.infra import PiiSpan
from niadra.testing.cell import (
    BILLING_SOURCE,
    SPACE_ID,
    TENANT_ID,
    VOICE_SOURCE,
    WHATSAPP_SOURCE,
    Cell,
    principal,
    sample_config,
)
from tests.unit.audit.fakes import RecordingAdminAudit

SECOND_A = UUID("0192f5a0-0000-7000-8000-00000000c0a2")
CREDIT = CreateAgentNoteRequest(
    kind="procedure",
    title="Credit shows on the invoice only after a refresh",
    body="In the ERP call post_credit, then refresh_invoice, in that order; the credit is invisible before.",
    tags=["invoice", "credit", "erp"],
)


class NamesModels:
    """The models server with its name detector: marks the synthetic customers' names as persons."""

    def __init__(self, base: object, names: Sequence[str] = ("Marina Souza", "John Carter", "Lucía Pérez")):
        self._base = base
        self._names = names
        self.down = False

    async def pii(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        if self.down:
            raise ConnectionError("models server down")
        found = await self._base.pii(texts)  # type: ignore[attr-defined]
        for spans, text in zip(found, texts, strict=True):
            for name in self._names:
                if (at := text.find(name)) >= 0:
                    spans.append(PiiSpan(at, at + len(name), "person"))
            if (at := text.find("Itaú")) >= 0:
                spans.append(PiiSpan(at, at + 4, "organization"))
        return found


class World:
    def __init__(self, cell: Cell, *, enabled: bool = True, writes: str = "agent") -> None:
        config = sample_config()
        config = config.model_copy(
            update={
                "agent_memory": AgentMemorySettings(enabled=enabled, writes=writes),
                "sources": [
                    *config.sources,
                    SourceConfig(
                        source_id=SECOND_A,
                        name="whatsapp-sales",
                        vendor="A",
                        channel="whatsapp",
                        audience=Audience.CUSTOMER_AGENT,
                    ),
                ],
            }
        )
        cell.config.put(config)
        self.cell = cell
        self.config = config
        self.audit = RecordingAdminAudit()
        self.models = NamesModels(cell.models)
        self.llm = ScriptedLlm()
        self.service = AgentMemoryService(
            cell.uow,
            cell.config,
            cell.clock,
            self.models,
            self.audit,
            cell.keys,
            llm=self.llm,  # type: ignore[arg-type]
        )

    def agent(self, source_id: UUID = WHATSAPP_SOURCE, *scopes: Scope) -> object:
        granted = frozenset(scopes) if scopes else frozenset({Scope.CONTEXT, Scope.AGENT_MEMORY_WRITE})
        return principal(source_id, self.config, scopes=granted)

    def person(self, *roles: str, sources: frozenset[UUID] | None = None) -> PersonPrincipal:
        space = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
        return PersonPrincipal(space, uuid7(), frozenset(roles or {"integration"}), sources)


@pytest.fixture
def world(cell: Cell) -> World:
    return World(cell)


async def test_an_agent_writes_a_note_and_reads_it_back_in_its_block(world: World) -> None:
    agent = world.agent()
    saved = await world.service.create(agent, CREDIT)
    assert saved.note is not None
    assert saved.proposal_id is None
    assert (saved.note.source_id, saved.note.origin, saved.note.version) == (WHATSAPP_SOURCE, "agent", 1)
    block = await world.service.block(agent, max_tokens=300)
    assert block.notes == [saved.note.note_id]
    assert "post_credit" in block.text
    assert block.text.startswith("<agent_notes>")
    assert block.etag == (await world.service.block(agent, max_tokens=300)).etag
    assert world.audit.actions[0] == "agent_memory.note_created"
    reads = [r for r in world.audit.records if r["action"] == "read"]
    assert reads[0]["surface"] is ReceiptSurface.AGENT_BLOCK
    assert reads[0]["item_ids"] == (f"agent_note:{saved.note.note_id}",)


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Call back the customer", "Ring +55 11 99999-0001 after the credit posts"),
        ("Send the invoice copy", "Mail it to marina.souza@example.com and close"),
        ("Check the document", "The customer 123.456.789-09 had the wrong plan"),
        ("Remember Marina Souza", "She prefers WhatsApp for billing questions"),
        ("Enviar segunda via", "A cliente Lucía Pérez pediu por e-mail"),
        ("Reembolso", "Llamar al (11) 98765-4321 antes de acreditar"),
        ("Follow up", "John Carter asked twice about the refund"),
    ],
)
async def test_a_note_with_personal_data_is_refused_not_masked(world: World, title: str, body: str) -> None:
    agent = world.agent()
    with pytest.raises(PersonalDataInAgentMemory) as refused:
        await world.service.create(agent, CreateAgentNoteRequest(kind="pitfall", title=title, body=body))
    # The answer names kinds only, never the value it found.
    assert body.split()[-1] not in str(refused.value.detail)
    page = await world.service.list_notes(
        world.person(), source_id=None, status=None, visibility=None, cursor=None, limit=50
    )
    assert page.items == []


async def test_companies_places_dates_and_codes_are_not_personal(world: World) -> None:
    note = CreateAgentNoteRequest(
        kind="tool_note",
        title="The Itaú boleto API refuses dates without a time zone",
        body="Send due_date with an offset; error 5003 means the zone is missing. Retry at most 3 times.",
        tags=["boleto", "itau"],
    )
    assert (await world.service.create(world.agent(), note)).note is not None


async def test_without_the_detector_the_write_waits(world: World) -> None:
    world.models.down = True
    with pytest.raises(Unavailable):
        await world.service.create(world.agent(), CREDIT)


async def test_visibility_follows_owner_vendor_and_space(world: World) -> None:
    owner = world.agent(WHATSAPP_SOURCE)
    mine = await world.service.create(owner, CREDIT)
    vendor = await world.service.create(
        owner, CREDIT.model_copy(update={"title": "Vendor wide", "visibility": AgentNoteVisibility.VENDOR})
    )
    space = await world.service.create(
        owner, CREDIT.model_copy(update={"title": "Space wide", "visibility": AgentNoteVisibility.SPACE})
    )
    assert mine.note
    assert vendor.note
    assert space.note

    async def seen(source: UUID) -> set[str]:
        block = await world.service.block(world.agent(source, Scope.CONTEXT), max_tokens=2000)
        return {str(i) for i in block.notes}

    assert await seen(WHATSAPP_SOURCE) == {
        str(mine.note.note_id),
        str(vendor.note.note_id),
        str(space.note.note_id),
    }
    # Same vendor, another agent: the vendor and space notes.
    assert await seen(SECOND_A) == {str(vendor.note.note_id), str(space.note.note_id)}
    # Vendor B never reads what vendor A's agents learned; the space-wide note is for everyone.
    assert await seen(VOICE_SOURCE) == {str(space.note.note_id)}
    with pytest.raises(NotFound):
        await world.service.get(world.agent(VOICE_SOURCE, Scope.CONTEXT), mine.note.note_id)


async def test_the_block_puts_matching_tags_first_and_keeps_within_budget(world: World) -> None:
    agent = world.agent()
    for i in range(6):
        await world.service.create(
            agent,
            CreateAgentNoteRequest(
                kind="procedure", title=f"Step list {i}", body="word " * 60, tags=["visit"] if i == 4 else []
            ),
        )
    # Room for one note and the line that says how many the block left out.
    block = await world.service.block(agent, max_tokens=170, tags=["visit"])
    assert len(block.notes) == 1
    assert block.tokens <= 170
    assert "Step list 4" in block.text
    assert block.left_out == 5


async def test_a_space_with_agent_memory_off_answers_empty_and_refuses_agent_writes(cell: Cell) -> None:
    world = World(cell, enabled=False)
    block = await world.service.block(world.agent())
    assert (block.enabled, block.text, block.etag) == (False, "", "am-off")
    with pytest.raises(Forbidden):
        await world.service.create(world.agent(), CREDIT)
    # A person may prepare notes before turning it on.
    person_note = CREDIT.model_copy(update={"source_id": WHATSAPP_SOURCE})
    assert (await world.service.create(world.person(), person_note)).note is not None


async def test_search_finds_by_words_and_tags_with_a_receipt(world: World) -> None:
    agent = world.agent()
    await world.service.create(agent, CREDIT)
    await world.service.create(
        agent,
        CreateAgentNoteRequest(
            kind="pitfall", title="Visits need a window", body="Book a morning window", tags=["visit"]
        ),
    )
    found = await world.service.search(agent, AgentMemorySearchRequest(query="how to post a credit"))
    assert next(n.title for n in found.notes) == CREDIT.title
    tagged = await world.service.search(agent, AgentMemorySearchRequest(query="window", tags=["visit"]))
    assert [n.kind for n in tagged.notes] == ["pitfall"]
    read = [r for r in world.audit.records if r["action"] == "read"][-1]
    assert read["surface"] is ReceiptSurface.AGENT_SEARCH
    assert read["query_hash"]


async def test_an_edit_makes_a_version_and_retires_the_old_one(world: World) -> None:
    agent = world.agent()
    first = (await world.service.create(agent, CREDIT)).note
    assert first is not None
    second = await world.service.update(
        agent, first.note_id, UpdateAgentNoteRequest(body="post_credit, wait 5 s, refresh")
    )
    assert (second.version, second.supersedes_note_id, second.status) == (2, first.note_id, "active")
    versions = await world.service.versions(agent, second.note_id)
    assert [(v.version, v.status) for v in versions] == [(1, "retired"), (2, "active")]
    with pytest.raises(Conflict):
        await world.service.update(agent, first.note_id, UpdateAgentNoteRequest(title="late edit"))
    with pytest.raises(PersonalDataInAgentMemory):
        await world.service.update(agent, second.note_id, UpdateAgentNoteRequest(body="ask Marina Souza"))
    retired = await world.service.retire(agent, second.note_id)
    assert retired.status == "retired"
    assert (await world.service.block(agent)).notes == []


async def test_another_agent_cannot_change_a_note_it_can_read(world: World) -> None:
    shared = (
        await world.service.create(
            world.agent(), CREDIT.model_copy(update={"visibility": AgentNoteVisibility.SPACE})
        )
    ).note
    assert shared is not None
    with pytest.raises(Forbidden):
        await world.service.retire(world.agent(VOICE_SOURCE), shared.note_id)


async def test_writes_need_the_scope_and_keep_to_twenty_an_hour(world: World) -> None:
    with pytest.raises(ScopeMissing):
        await world.service.create(world.agent(WHATSAPP_SOURCE, Scope.CONTEXT), CREDIT)
    agent = world.agent()
    for i in range(20):
        await world.service.create(agent, CREDIT.model_copy(update={"title": f"note {i}"}))
    with pytest.raises(RateLimited):
        await world.service.create(agent, CREDIT)
    world.cell.clock.advance(hours=1, seconds=1)
    assert (await world.service.create(agent, CREDIT)).note is not None


async def test_an_agent_keeps_every_note_and_its_block_says_how_many_it_left_out(world: World) -> None:
    # Memory never cuts. At 500 notes the next one was refused ("retire some first"). Every
    # note is kept now; a block serves what its size holds and says how many it left out, and the search
    # reads them all.
    agent = world.agent()
    made = []
    for n in range(25):
        if n and n % WRITES_PER_HOUR == 0:
            world.cell.clock.advance(hours=1, seconds=1)
        title = f"Passo {n} do crédito"
        made.append((await world.service.create(agent, CREDIT.model_copy(update={"title": title}))).note)
    assert all(note is not None and note.status == "active" for note in made)
    block = await world.service.block(agent, max_tokens=300)
    assert 0 < len(block.notes) < len(made)
    assert block.left_out == len(made) - len(block.notes)
    assert re.search(rf"\+{block.left_out} (notas|notes) ", block.text)
    left = next(n for n in made if n is not None and n.note_id not in block.notes)
    found = await world.service.search(agent, AgentMemorySearchRequest(query=left.title, limit=1))
    assert [n.note_id for n in found.notes] == [left.note_id]


async def test_a_note_a_person_wrote_goes_first_in_the_block(world: World) -> None:
    agent = world.agent()
    await world.service.create(agent, CREDIT.model_copy(update={"title": "Do agente", "tags": ["visit"]}))
    by_person = CREDIT.model_copy(update={"title": "Da equipe", "tags": [], "source_id": WHATSAPP_SOURCE})
    written = (await world.service.create(world.person("integration"), by_person)).note
    assert written is not None
    block = await world.service.block(agent, max_tokens=2000, tags=["visit"])
    assert block.notes[0] == written.note_id
    assert block.left_out == 0


async def test_human_only_spaces_turn_remember_into_a_proposal(cell: Cell) -> None:
    world = World(cell, writes="human_only")
    result = await world.service.create(world.agent(), CREDIT)
    assert result.note is None
    assert result.proposal_id is not None
    page = await world.service.list_proposals(
        world.person(), source_id=None, status=ProposalStatus.PENDING, cursor=None, limit=10
    )
    assert [p.origin for p in page.items] == ["agent"]
    admin = world.person("admin")
    with pytest.raises(PersonalDataInAgentMemory):
        await world.service.approve(
            admin, result.proposal_id, ApproveProposalRequest(body="call Marina Souza")
        )
    note = await world.service.approve(admin, result.proposal_id, ApproveProposalRequest(tags=["credit"]))
    assert (note.origin, note.tags, note.status) == ("agent", ["credit"], "active")
    with pytest.raises(Conflict):
        await world.service.reject(admin, result.proposal_id)


async def _seed_turns(
    world: World, lines: list[tuple[Speaker, str]] | None = None, days_ago: int = 0
) -> None:
    lines = lines or [
        (
            Speaker.CUSTOMER,
            "The credit is not on my invoice, call me at +55 11 99999-0001 or marina@example.com",
        ),
        (Speaker.AI_AGENT, "I ran post_credit and then refresh_invoice; the credit now shows."),
    ]
    now = world.cell.clock.now() - timedelta(days=days_ago)
    async with world.cell.uow(SPACE_ID) as uow:
        await uow.ingest.insert_events(
            [
                NewEvent(
                    SPACE_ID,
                    EventRecord(
                        event_id=uuid7(),
                        kind=EventKind.MESSAGE,
                        source_id=BILLING_SOURCE,
                        channel="whatsapp",
                        speaker=speaker,
                        occurred_at=now - timedelta(minutes=10 - i),
                        received_at=now - timedelta(minutes=10 - i),
                        handle_ids=(),
                        text=text,
                        conversation_id="wa-42",
                    ),
                    f"k{i}",
                )
                for i, (speaker, text) in enumerate(lines)
            ]
        )


async def _tasks(world: World) -> list[Task]:
    outbox: list[Task] = world.cell.uow.store.module("pipeline").setdefault("outbox", [])
    return [t for t in outbox if t.name is TaskName.DISTILL_NOTE]


async def test_distillation_proposes_from_masked_turns_and_a_person_approves(world: World) -> None:
    await _seed_turns(world)
    world.llm.answers.append(
        '{"kind": "procedure", "title": "Post a credit so the invoice shows it", '
        '"body": "Call post_credit, then refresh_invoice.", "tags": ["credit", "Invoice", "bad tag!"]}'
    )
    admin = world.person("admin")
    drafting = await world.service.distill(
        admin, DistillRequest(source_id=BILLING_SOURCE, conversation_id="wa-42")
    )
    assert drafting.status == "drafting"
    [task] = await _tasks(world)
    await world.service.distill_task(task)
    prompt = world.llm.calls[0]["user"]
    assert "+55 11 99999-0001" not in prompt
    assert "marina@example.com" not in prompt
    assert "post_credit" in prompt
    [ready] = (
        await world.service.list_proposals(admin, source_id=BILLING_SOURCE, status=None, cursor=None, limit=5)
    ).items
    assert (ready.status, ready.tags, ready.evidence.conversation_id) == (
        "pending",
        ["credit", "invoice"],
        "wa-42",
    )  # type: ignore[union-attr]
    note = await world.service.approve(admin, ready.proposal_id, ApproveProposalRequest())
    assert (note.origin, note.source_id) == ("distilled", BILLING_SOURCE)


async def test_a_distillation_that_proposes_personal_data_fails(world: World) -> None:
    await _seed_turns(world)
    world.llm.answers.append('{"kind": "pitfall", "title": "Marina Souza", "body": "She calls often."}')
    admin = world.person("admin")
    await world.service.distill(admin, DistillRequest(source_id=BILLING_SOURCE, conversation_id="wa-42"))
    [task] = await _tasks(world)
    await world.service.distill_task(task)
    [failed] = (
        await world.service.list_proposals(admin, source_id=None, status=None, cursor=None, limit=5)
    ).items
    assert (failed.status, failed.problem) == ("failed", "personal_data")


async def test_erasing_an_agent_deletes_every_version_and_leaves_tombstones(world: World) -> None:
    agent = world.agent()
    first = (await world.service.create(agent, CREDIT)).note
    assert first is not None
    await world.service.update(agent, first.note_id, UpdateAgentNoteRequest(title="v2"))
    other = (await world.service.create(world.agent(VOICE_SOURCE), CREDIT)).note
    exported = await world.service.export_source(world.person(), WHATSAPP_SOURCE)
    assert [n.version for n in exported.notes] == [1, 2]
    erased = await world.service.erase_source(world.person("admin"), WHATSAPP_SOURCE)
    assert erased.deleted == 2
    async with world.cell.uow(SPACE_ID) as uow:
        tombstones = await uow.privacy.tombstones_between(SPACE_ID, 0, None)
    assert {t.table for t in tombstones} == {"agent_notes"}
    assert len(tombstones) == 2
    left = await world.service.list_notes(
        world.person(), source_id=None, status=None, visibility=None, cursor=None, limit=10
    )
    assert [n.note_id for n in left.items] == [other.note_id]  # type: ignore[union-attr]
    assert "agent_memory.erase" in world.audit.actions


async def test_vendor_people_see_only_their_sources(world: World) -> None:
    await world.service.create(world.agent(VOICE_SOURCE), CREDIT)
    vendor_person = world.person("vendor", sources=frozenset({WHATSAPP_SOURCE}))
    page = await world.service.list_notes(
        vendor_person, source_id=None, status=None, visibility=None, cursor=None, limit=10
    )
    assert page.items == []
    with pytest.raises(NotFound):
        await world.service.export_source(vendor_person, VOICE_SOURCE)


async def test_an_agent_never_distills_another_sources_conversation(world: World) -> None:
    # A conversation id is not tied to a source: the WhatsApp agent naming the billing agent's conversation
    # would have its turns summarized into a proposal it then reads back. Only the owner's turns count.
    await _seed_turns(world)
    world.llm.answers.append(
        '{"kind": "procedure", "title": "Post a credit", "body": "Call post_credit.", "tags": ["credit"]}'
    )
    agent = world.agent(WHATSAPP_SOURCE)
    await world.service.distill(agent, DistillRequest(source_id=WHATSAPP_SOURCE, conversation_id="wa-42"))
    [task] = await _tasks(world)
    await world.service.distill_task(task)
    assert world.llm.calls == []
    [failed] = (
        await world.service.list_proposals(agent, source_id=None, status=None, cursor=None, limit=5)
    ).items
    assert (failed.status, failed.problem, failed.body) == ("failed", "no_turns", "")


async def test_a_listing_reads_on_until_the_reader_has_a_page(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An agent's listing read 2,000 notes, then filtered what it may see, and ended there in
    # silence. It reads on now until its page is full, and a page cut by the reading says where to go on.
    import niadra.app.agent_memory as module

    monkeypatch.setattr(module, "PAGE_SCAN", 2)
    mine = (await world.service.create(world.agent(), CREDIT.model_copy(update={"title": "mine"}))).note
    for n in range(5):
        await world.service.create(
            world.agent(VOICE_SOURCE), CREDIT.model_copy(update={"title": f"voice {n}"})
        )
    assert mine is not None
    page = await world.service.list_notes(
        world.agent(), source_id=None, status=None, visibility=None, cursor=None, limit=10
    )
    assert [n.note_id for n in page.items] == [mine.note_id]
    assert page.next_cursor is None
    monkeypatch.setattr(module, "PAGE_SCANS", 1)
    cut = await world.service.list_notes(
        world.agent(), source_id=None, status=None, visibility=None, cursor=None, limit=10
    )
    assert cut.items == []
    assert cut.next_cursor is not None


async def test_a_long_conversation_is_distilled_whole_and_its_note_is_kept_whole(world: World) -> None:
    """Memory never cuts: the old distillation read only the last 12,000 characters of a transcript and the
    last 90 days of a conversation, and cut the note's body at 2,000."""
    filler = "The agent checked the ledger entry again and compared the totals line by line. " * 120
    lines = [
        (Speaker.AI_AGENT, "Step one: run open_dispute before anything else, or the refund is refused."),
        *[(Speaker.AI_AGENT, filler) for _ in range(4)],
        (Speaker.AI_AGENT, "Last step: run close_dispute once the refund posts."),
    ]
    await _seed_turns(world, lines, days_ago=120)
    long_body = "Run open_dispute first, then the refund, then close_dispute. " * 60
    note = '{{"kind": "procedure", "title": "Dispute then refund", "body": "{body}", "tags": ["refund"]}}'
    windows = len(transcript_windows([f"ai_agent: {text}" for _, text in lines]))
    assert windows > 1
    world.llm.answers += [note.format(body=f"part {i}") for i in range(windows)]
    world.llm.answers.append(note.format(body=long_body.strip()))
    admin = world.person("admin")
    await world.service.distill(admin, DistillRequest(source_id=BILLING_SOURCE, conversation_id="wa-42"))
    [task] = await _tasks(world)
    await world.service.distill_task(task)

    read = "\n".join(c["user"] for c in world.llm.calls[:windows])
    assert "open_dispute before anything else" in read
    assert "close_dispute once the refund posts" in read
    assert world.llm.calls[-1]["system"] == MERGE_SYSTEM
    [ready] = (
        await world.service.list_proposals(admin, source_id=BILLING_SOURCE, status=None, cursor=None, limit=5)
    ).items
    assert ready.body == long_body.strip()
    assert len(ready.body) > 2000
