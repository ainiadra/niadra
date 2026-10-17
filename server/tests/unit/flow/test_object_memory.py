"""Memory bound to an object across its participants, the legal firm's two agents: a
lawyer tells the firm's internal assistant what to do about a case; the drafting task on the case gets the
instruction as a hard constraint; the customer-facing agent of the firm's client reads the instruction and the
lawyer's promise on the case's line and never the lawyer's internal remark; what the lawyer said about a field
the court system holds is shown beside the recorded value and opens a divergence; erasing the lawyer takes
what he said, and the receipt counts it."""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.config.models import (
    CategoryRule,
    FeatureSettings,
    PredicateSpec,
    SourceConfig,
    SpaceConfig,
    TaskView,
)
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.common import Handle, ObjectRef
from niadra.contracts.context import ContextRequest
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.common.errors import InvalidInput
from niadra.domain.compile.model import CompiledPack, profile_target
from niadra.domain.records import ObjectKey, OpenItem
from niadra.domain.turns.issues import IssueQuery
from niadra.domain.vocabulary import Audience, Feature, HandleType, OpenItemStatus, Speaker, Verification
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

ASSISTANT = UUID("0192f5a0-0000-7000-8000-00000000c0a1")
DRAFTING = UUID("0192f5a0-0000-7000-8000-00000000c0a2")
NUMBER = "0001234-56.2026.8.26.0100"
CASE = {"type": "lawsuit", "namespace": "court", "id": "case-1"}
FIRM = {"type": "system_id", "value": "CLIENT-1", "scope": "cases", "subject_kind": "account"}
CLIENT = {"type": "phone_e164", "value": "+5511955550001"}
# The firm's own system names the lawyer: staff, so his chats at the assistant are internal.
LAWYER = {"type": "system_id", "value": "lawyer-99", "scope": "firm-users", "subject_kind": "staff"}
TYPES = {
    "types": [
        {
            "type": "lawsuit",
            "ownership": "subject",
            "mirror_of": {"system": "court"},
            "key": {
                "natural": ["case_number"],
                "match": {"case_number": r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}"},
            },
            "fields": {"case_number": {"type": "string"}, "due_date": {"type": "string"}},
        }
    ]
}


def configured(cell: Cell) -> SpaceConfig:
    config = cell.config.space(SPACE_ID)
    sources = [
        *config.sources,
        SourceConfig(
            source_id=ASSISTANT, name="legal-assistant", channel="app", audience=Audience.INTERNAL_AGENT
        ),
        SourceConfig(
            source_id=DRAFTING,
            name="drafting-tool",
            channel="app",
            audience=Audience.INTERNAL_AGENT,
            verification_ceiling=Verification.V3,
        ),
    ]
    extraction = config.extraction.model_copy(
        update={
            "predicates": [
                *config.extraction.predicates,
                PredicateSpec(name="said_due_date", category="object", observes="due_date"),
            ]
        }
    )
    # The firm's tools read its cases without a customer to verify; the client's agent proves who it talks to.
    policy = config.policy.model_copy(
        update={
            "categories": [
                *config.policy.categories,
                CategoryRule(name="lawsuit", min_verification=Verification.V0),
                CategoryRule(name="object", min_verification=Verification.V0),
            ]
        }
    )
    return config.model_copy(
        update={
            "policy": policy,
            "sources": sources,
            "extraction": extraction,
            "object_types": ObjectTypeRegistry.model_validate(TYPES),
            "features": FeatureSettings(enabled=frozenset({Feature.SIGNALS, Feature.STATE})),
            "views": [TaskView(name="task:drafting", object_types=["lawsuit"])],
        }
    )


def lawyers_extraction() -> dict[str, Any]:
    return {
        "episode": {
            "summary": "O advogado orientou sobre o processo",
            "intent": "other",
            "category": "contract",
            "outcome": "resolved",
            "resolution": None,
            "sentiment": None,
        },
        "facts": [
            {"op": "add", "predicate": "observation", "value": "a tese é fraca", "evidence": ["e3"],
             "object": "o1", "certainty": 0.9},
            {"op": "add", "predicate": "said_due_date", "value": "2026-10-02", "evidence": ["e4"],
             "object": "o1", "certainty": 0.9},
        ],
        "open_items": [
            {"description": "Mandar os extratos ao cliente", "kind": "promise", "owner": "customer",
             "evidence": ["e2"], "object": "o1", "due_at": None},
        ],
        "instructions": [
            {"text": "não propor acordo", "strength": "must_not", "attr": None, "evidence": ["e1"],
             "object": "o1"},
        ],
    }  # fmt: skip


def said(n: int, text: str, at: str) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"chat-{n}",
        "channel": "app",
        "conversation_id": "lawyer-chat",
        "handles": [LAWYER],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": text},
        "object_refs": [CASE],
        "occurred_at": at,
    }


def about_extraction() -> dict[str, Any]:
    return {
        "episode": {
            "summary": "O advogado orientou sobre o cliente",
            "intent": "other",
            "category": "contract",
            "outcome": "resolved",
            "resolution": None,
            "sentiment": None,
        },
        "instructions": [
            {"text": "não falar de valores com a esposa", "strength": "must_not", "attr": None,
             "evidence": ["e1"]},
        ],
    }  # fmt: skip


async def scenario(
    cell: Cell,
    before_chat: Callable[[Flow], Awaitable[None]] | None = None,
    *later: dict[str, Any],
    lawyer: dict[str, Any] | None = None,
) -> Flow:
    cell.config.put(configured(cell))
    flow = Flow.build(cell, ScriptedLlm(lawyer or lawyers_extraction(), about_extraction(), *later))
    clock = cell.clock
    opened = {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": "court-1",
        "channel": "erp",
        "canonical_type": "lawsuit.opened",
        "handles": [FIRM],
        "object_refs": [CASE],
        "speaker": {"role": "system"},
        "fields": {"case_number": NUMBER, "due_date": "2026-10-05"},
        "occurred_at": clock.now().isoformat(),
    }
    assert (await flow.send(BILLING_SOURCE, opened)).errors == []
    await flow.drain()
    await flow.identity.create_link(
        SPACE_ID, Handle.model_validate(CLIENT), Handle.model_validate(FIRM), role="contact"
    )
    if before_chat is not None:
        await before_chat(flow)
    lines = [
        f"Neste processo {NUMBER}, não propor acordo.",
        "Vou mandar os extratos ao cliente até sexta.",
        "Entre nós: a tese é fraca.",
        "O cliente disse que o prazo é 02/10.",
    ]
    events = []
    for n, text in enumerate(lines, start=1):
        clock.advance(minutes=1)
        events.append(said(n, text, clock.now().isoformat()))
    clock.advance(minutes=1)
    ended = {"type": "conversation.ended", "idempotency_key": "chat-end", "conversation_id": "lawyer-chat",
             "occurred_at": clock.now().isoformat()}  # fmt: skip
    assert (await flow.send(ASSISTANT, *events, ended)).errors == []
    await flow.drain()
    # Another chat, about the client this time and no case: the event names the lawyer and the client.
    clock.advance(minutes=1)
    about = {
        "type": "event", "idempotency_key": "chat-about-1", "channel": "app",
        "conversation_id": "lawyer-about", "handles": [],
        "subjects": [{"kind": "staff", "role": "lawyer", "handles": [LAWYER]},
                     {"kind": "account", "handles": [FIRM]}],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Com este cliente, não falar de valores com a esposa."},
        "occurred_at": clock.now().isoformat(),
    }  # fmt: skip
    clock.advance(minutes=1)
    done = {"type": "conversation.ended", "idempotency_key": "chat-about-end",
            "conversation_id": "lawyer-about", "occurred_at": clock.now().isoformat()}  # fmt: skip
    assert (await flow.send(ASSISTANT, about, done)).errors == []
    await flow.drain()
    return flow


async def test_the_drafting_task_on_the_case_gets_the_instruction_as_a_hard_constraint(cell: Cell) -> None:
    flow = await scenario(cell)
    caller = principal(DRAFTING, cell.config.space(SPACE_ID))
    request = ContextRequest(
        object=ObjectRef(**CASE),
        view="task:drafting",
        task_id="draft-1",
        verification=Verification.V2,
        include=["constraints"],
    )
    response = await flow.serve.context(caller, request)
    assert response.constraints is not None
    [instruction] = response.constraints.instructions
    assert (instruction.text, instruction.strength, instruction.object) == (
        "não propor acordo", "must_not", "lawsuit:court:case-1"
    )  # fmt: skip
    assert "não propor acordo" in response.constraints.text


async def test_a_drafting_conversation_opened_before_the_lawyer_spoke_gets_his_instruction(
    cell: Cell,
) -> None:
    """The conversation keeps the text pinned at its first read, but the constraints and the state blocks are
    the current memory's: an instruction said after the pin is a hard constraint of its next read."""
    request = ContextRequest(
        object=ObjectRef(**CASE),
        view="task:drafting",
        task_id="draft-early",
        verification=Verification.V2,
        include=["constraints", "state"],
    )

    async def first_read(flow: Flow) -> None:
        early = await flow.serve.context(principal(DRAFTING, cell.config.space(SPACE_ID)), request)
        assert early.constraints is not None
        assert not early.constraints.instructions

    flow = await scenario(cell, before_chat=first_read)
    response = await flow.serve.context(principal(DRAFTING, cell.config.space(SPACE_ID)), request)
    assert response.constraints is not None
    assert [i.text for i in response.constraints.instructions if i.object] == ["não propor acordo"]
    assert response.state is not None
    [case] = response.state.objects
    assert case.memory is not None
    assert [i.text for i in case.memory.instructions] == ["não propor acordo"]


async def test_the_clients_agent_reads_the_instruction_and_the_promise_and_never_the_remark(
    cell: Cell,
) -> None:
    flow = await scenario(cell)
    await flow.prove(WHATSAPP_SOURCE, "wa-client", CLIENT["value"], "V2")
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    request = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-client",
        verification=Verification.V2,
    )
    response = await flow.serve.context(caller, request)
    assert "não propor acordo" in response.text
    assert "Mandar os extratos ao cliente" in response.text
    # Said with no owner nor participant: the handle a promise was noted under is not who made it.
    assert "Mandar os extratos ao cliente (" not in response.text
    assert "tese é fraca" not in response.text
    # Out of focus, the instruction rides on the case's line and is no hard constraint of the client.
    internal = principal(DRAFTING, cell.config.space(SPACE_ID))
    request = ContextRequest(object=ObjectRef(**CASE), view="task:drafting", task_id="draft-2")
    assert "tese é fraca" in (await flow.serve.context(internal, request)).text


async def test_what_the_lawyer_said_at_the_firms_assistant_is_the_firms_word_not_the_clients(
    cell: Cell,
) -> None:
    """The lawyer is the person of the internal assistant's conversation, which the SDK records as its
    customer: his instruction read "(cliente)" on the client's case, and a model took the firm's words for the
    client's (juridico-sandbox, 05/10/2026). His rows are said by the team, his promise is the firm's."""
    flow = await scenario(cell)
    await flow.prove(WHATSAPP_SOURCE, "wa-team", CLIENT["value"], "V2")
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    request = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-team",
        verification=Verification.V2,
        include=["state"],
    )
    response = await flow.serve.context(caller, request)
    assert "não propor acordo (equipe)" in response.text
    assert "não propor acordo (cliente)" not in response.text
    assert response.state is not None
    # Said once: the pack's pinned line says it, the state block's JSON keeps it for a program.
    assert "não propor acordo" not in (response.state.text or "")
    assert [
        i.text for o in response.state.objects if o.memory is not None for i in o.memory.instructions
    ] == ["não propor acordo"]
    async with cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.SYSTEM_ID, LAWYER["scope"], LAWYER["value"])
        assert handle is not None
        [promise] = await uow.memory.open_items_for([handle.handle_id], tuple(OpenItemStatus))
        [said_due] = [
            f for f in await uow.memory.facts_for([handle.handle_id]) if f.predicate == "said_due_date"
        ]
    # The extraction called the lawyer the conversation's customer; the promise is the firm's to keep.
    assert (promise.owner, promise.origin["speaker"]) == ("company", "human_agent")
    assert said_due.speaker is Speaker.HUMAN_AGENT


async def test_a_task_read_in_a_conversation_thread_keeps_the_lawyers_internal_word(cell: Cell) -> None:
    """A task names its thread by a conversation id as well as by a task id (the e2e drafting task does): a
    task is internal, as its key is, so the read keeps what the lawyer said and the customer view never
    applies (e2e, 07/10/2026: the said due date was lost to the customer-facing view)."""
    flow = await scenario(cell)
    internal = principal(DRAFTING, cell.config.space(SPACE_ID))
    request = ContextRequest(
        object=ObjectRef(**CASE),
        view="task:drafting",
        conversation_id="draft-thread",
        include=["state"],
        verification=Verification.V2,
    )
    response = await flow.serve.context(internal, request)
    assert response.state is not None
    [obj] = [o for o in response.state.objects if o.ref.id == "case-1"]
    assert obj.memory is not None
    assert [s.observes for s in obj.memory.said if s.observes == "due_date"] == ["due_date"]


async def test_what_the_lawyer_said_of_the_due_date_stands_beside_the_recorded_one(cell: Cell) -> None:
    flow = await scenario(cell)
    internal = principal(DRAFTING, cell.config.space(SPACE_ID))
    request = ContextRequest(
        object=ObjectRef(**CASE),
        view="task:drafting",
        task_id="draft-3",
        include=["state"],
        verification=Verification.V2,
    )
    response = await flow.serve.context(internal, request)
    assert response.state is not None
    # The recorded value first, with when the system recorded it, as the pack's line says it.
    assert "due date: 2026-10-05 (sistema, 22/09/2026 14:00); dito 2026-10-02" in (response.state.text or "")
    [obj] = [o for o in response.state.objects if o.ref.id == "case-1"]
    assert obj.memory is not None
    [said_due] = [s for s in obj.memory.said if s.observes == "due_date"]
    assert (said_due.text, said_due.recorded, said_due.diverges, said_due.claim_safe) == (
        "2026-10-02", "2026-10-05", True, False
    )  # fmt: skip
    async with cell.uow(SPACE_ID) as uow:
        issues = await uow.turns.issues(IssueQuery(kind="divergence"))
    assert any(i.kind == "divergence" and i.field == "due_date" for i in issues)


async def test_erasing_the_lawyer_takes_what_he_said_and_the_receipt_counts_it(cell: Cell) -> None:
    flow = await scenario(cell)
    # A conversation of the client opened before the erasure: its pinned text carries the lawyer's promise.
    await flow.prove(WHATSAPP_SOURCE, "wa-before", CLIENT["value"], "V2")
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    request_before = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-before",
        verification=Verification.V2,
    )
    assert "Mandar os extratos" in (await flow.serve.context(caller, request_before)).text
    await flow.drain()
    assert "Mandar os extratos" in (await flow.serve.context(caller, request_before)).text
    request = ForgetRequest(target="handle", handle=Handle.model_validate(LAWYER))
    await flow.privacy.forget(SPACE_ID, request, idempotency_key="forget-lawyer", requested_by="user:dpo")
    cell.clock.advance(minutes=1)
    await flow.drain()
    async with cell.uow(SPACE_ID) as uow:
        [erasure] = await uow.privacy.list_erasures(SPACE_ID, status=None, before=None, limit=5)
    # Two observations and the promise, and the instruction his signals held.
    assert erasure.counts["object_bound_rows_erased"] == 4
    assert erasure.counts["objects"] == ["lawsuit:court:case-1"]
    # The pin goes with what the lawyer said: the same conversation reads a pack without it.
    pinned = await flow.serve.context(caller, request_before)
    assert "Mandar os extratos" not in pinned.text
    assert "esposa" not in pinned.text
    await flow.prove(WHATSAPP_SOURCE, "wa-after", CLIENT["value"], "V2")
    request_after = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-after",
        verification=Verification.V2,
    )
    cell.clock.advance(minutes=1)
    after = await flow.serve.context(caller, request_after)
    assert "Mandar os extratos" not in after.text
    assert "esposa" not in after.text
    # The client's own packs, compiled ahead, are compiled again without what the lawyer said.
    firm = principal(DRAFTING, cell.config.space(SPACE_ID))
    own = await flow.serve.context(firm, ContextRequest(subject=Handle.model_validate(FIRM), task_id="firm"))
    assert "esposa" not in own.text
    assert "extratos" not in own.text


async def test_the_lawyers_conversation_is_about_the_case_and_he_takes_part_in_it(cell: Cell) -> None:
    await scenario(cell)
    async with cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.SYSTEM_ID, LAWYER["scope"], LAWYER["value"])
        assert handle is not None
        [episode] = [e for e in await uow.memory.episodes_for([handle.handle_id]) if e.object_refs]
        [case] = await uow.objects.get_objects([ObjectKey("lawsuit", "court", "case-1")])
        parts = await uow.objects.participants_of([case.object_id], per_object=20)
    assert episode.object_refs == (ObjectKey("lawsuit", "court", "case-1"),)
    assert (handle.handle_id, "participant") in parts[case.object_id]


async def test_an_instruction_about_the_client_naming_no_case_reaches_the_clients_agent(cell: Cell) -> None:
    flow = await scenario(cell)
    await flow.prove(WHATSAPP_SOURCE, "wa-wife", CLIENT["value"], "V2")
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    request = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-wife",
        verification=Verification.V2,
        include=["constraints"],
    )
    response = await flow.serve.context(caller, request)
    assert "instrução: não falar de valores com a esposa" in response.text
    assert response.constraints is not None
    [told] = [i for i in response.constraints.instructions if i.object is None]
    assert (told.text, told.strength) == ("não falar de valores com a esposa", "must_not")
    assert told.about is not None
    # The pack beside it already says it: the block keeps the entry for a program and does not say it twice.
    assert "não falar de valores com a esposa" not in response.constraints.text
    # The pack compiled ahead for the link (not at a read) carries it the same way.
    async with cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.PHONE_E164, "", CLIENT["value"])
        client = await uow.identity.profile_of(handle.handle_id) if handle else None
        assert client is not None
        stored = await uow.compile.packs_for(profile_target(client.profile_id))
    ahead = [p for p in (CompiledPack.from_bytes(s.body) for s in stored) if p.account_generation]
    assert ahead
    # The policy lets it go where it lets the client's open items go: a V1 customer-facing pack, not a V0 one.
    carried = {
        p.variant.split("|")[1] for p in ahead if [r.text for _, r in p.account_instructions] == [told.text]
    }
    assert carried == {"V1"}
    assert all(not p.account_instructions for p in ahead if p.variant.split("|")[1] != "V1")
    # Never the lawyer's own: his reads hold no instruction he said about the client.
    lawyer = principal(DRAFTING, cell.config.space(SPACE_ID))
    own = ContextRequest(subject=Handle.model_validate(LAWYER), task_id="own", include=["constraints"])
    mine = await flow.serve.context(lawyer, own)
    assert mine.constraints is not None
    assert not mine.constraints.instructions


async def test_an_accounts_instruction_holds_past_its_twentieth_contact(cell: Cell) -> None:
    """juridico-sandbox, 06/10/2026: the account's instruction about the client left every read once the
    firm had more contacts seen after the lawyer who said it than the 20 an account shows in detail: the
    instructions were read from those 20 only, and nothing said the constraints were missing."""
    flow = await scenario(cell)
    for n in range(21):
        cell.clock.advance(seconds=1)
        phone = Handle(type=HandleType.PHONE_E164, value=f"+55119777{n:05d}")
        await flow.identity.create_link(SPACE_ID, phone, Handle.model_validate(FIRM), role="contact")
    newest = {"type": "phone_e164", "value": "+5511977700020"}
    await flow.drain()
    await flow.prove(WHATSAPP_SOURCE, "wa-late", newest["value"], "V2")
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    request = ContextRequest(
        subject=Handle.model_validate(newest),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-late",
        verification=Verification.V2,
        include=["constraints"],
    )
    response = await flow.serve.context(caller, request)
    assert response.constraints is not None
    assert [i.text for i in response.constraints.instructions if i.object is None] == [
        "não falar de valores com a esposa"
    ]


async def items_of(cell: Cell, handle: dict[str, str]) -> list[OpenItem]:
    async with cell.uow(SPACE_ID) as uow:
        found = await uow.identity.find_handle(
            HandleType(handle["type"]), handle.get("scope", ""), handle["value"]
        )
        assert found is not None
        profile = await uow.identity.profile_of(found.handle_id)
        assert profile is not None
        return await uow.memory.open_items_for(profile.handle_ids, tuple(OpenItemStatus))


async def test_the_clients_agent_telling_the_lawyers_promise_writes_no_second_promise(cell: Cell) -> None:
    """juridico-sandbox, 05/10/2026: the attendant tells the client, in its own words and in every
    conversation, what the lawyer promised on the case, and each telling became a second promise on the case.
    It is the lawyer's promise; a promise of the attendant's own on the case is still written."""
    due = (cell.clock.now() + timedelta(days=4)).isoformat()
    lawyer = lawyers_extraction()
    lawyer["open_items"] = [
        {**lawyer["open_items"][0], "expected_operation": "deliver_document", "due_at": due}
    ]
    told = {
        "episode": {"summary": "O atendente falou dos extratos", "intent": "other", "category": "contract",
                    "outcome": "resolved", "resolution": None, "sentiment": None},
        "open_items": [
            {"description": "Mandaremos os extratos ao cliente", "kind": "promise", "owner": "company",
             "evidence": ["e2"], "object": "o1", "expected_operation": "deliver_document", "due_at": due},
            {"description": "Advogado ligará ao cliente", "kind": "promise", "owner": "company",
             "evidence": ["e2"], "object": "o1", "expected_operation": None, "due_at": due},
        ],
    }  # fmt: skip
    flow = await scenario(cell, None, told, lawyer=lawyer)
    clock = cell.clock
    turns = []
    for n, (role, text) in enumerate(
        [("customer", "Vão me mandar os extratos?"), ("ai_agent", "Sim, mandaremos os extratos até sexta.")],
        start=1,
    ):
        clock.advance(minutes=1)
        turns.append(
            {
                "type": "event", "idempotency_key": f"wa-told-{n}", "channel": "whatsapp",
                "conversation_id": "wa-told", "handles": [CLIENT], "speaker": {"role": role},
                "content": {"type": "text", "text": text}, "object_refs": [CASE],
                "occurred_at": clock.now().isoformat(),
            }
        )  # fmt: skip
    clock.advance(minutes=1)
    ended = {"type": "conversation.ended", "idempotency_key": "wa-told-end", "conversation_id": "wa-told",
             "occurred_at": clock.now().isoformat()}  # fmt: skip
    assert (await flow.send(WHATSAPP_SOURCE, *turns, ended)).errors == []
    await flow.drain()
    assert [i.description for i in await items_of(cell, LAWYER)] == ["Mandar os extratos ao cliente"]
    assert [i.description for i in await items_of(cell, CLIENT)] == ["Advogado ligará ao cliente"]


async def test_the_case_systems_record_closes_a_promise_made_on_the_case_by_staff_linked_to_no_client(
    cell: Cell,
) -> None:
    """Staff are named by the firm's own system, and one who only talks about a case, in
    conversations tagged with it, is linked to no client: the court system's record of the act on the case
    closed only the items of the case's owner and of its linked contacts, never that person's promise on the
    case, which every reader of the case still read as pending. A promise on an object closes by that object's
    record, whoever made it."""
    colleague = {"type": "system_id", "value": "lawyer-77", "scope": "firm-users", "subject_kind": "staff"}
    promise = {
        "episode": {"summary": "A advogada prometeu a procuração", "intent": "other", "category": "contract",
                    "outcome": "resolved", "resolution": None, "sentiment": None},
        "open_items": [
            {"description": "Juntar a procuração atualizada", "kind": "promise", "owner": "customer",
             "evidence": ["e1"], "object": "o1", "expected_operation": "file_power_of_attorney",
             "due_at": None},
        ],
    }  # fmt: skip
    flow = await scenario(cell, None, promise)
    clock = cell.clock
    clock.advance(minutes=1)
    said = {
        "type": "event", "idempotency_key": "colleague-1", "channel": "app",
        "conversation_id": "colleague-chat", "handles": [colleague], "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Vou juntar a procuração atualizada no processo."},
        "object_refs": [CASE], "occurred_at": clock.now().isoformat(),
    }  # fmt: skip
    clock.advance(minutes=1)
    ended = {"type": "conversation.ended", "idempotency_key": "colleague-end",
             "conversation_id": "colleague-chat", "occurred_at": clock.now().isoformat()}  # fmt: skip
    assert (await flow.send(ASSISTANT, said, ended)).errors == []
    await flow.drain()
    [promised] = await items_of(cell, colleague)
    assert promised.status is OpenItemStatus.OPEN
    clock.advance(minutes=5)
    filed = {
        "type": "event", "kind": "system_event", "idempotency_key": "court-poa", "channel": "erp",
        "canonical_type": "lawsuit.power_of_attorney_filed", "handles": [FIRM], "object_refs": [CASE],
        "speaker": {"role": "system"}, "fields": {"operation": "file_power_of_attorney"},
        "occurred_at": clock.now().isoformat(),
    }  # fmt: skip
    assert (await flow.send(BILLING_SOURCE, filed)).errors == []
    await flow.drain()
    [closed] = await items_of(cell, colleague)
    assert closed.status is OpenItemStatus.RESOLVED


async def test_one_promise_said_twice_in_one_run_in_other_words_is_one_item(cell: Cell) -> None:
    """The r5 block held "Enviar cópia da sentença ao cliente" and "Enviar ao cliente a cópia da decisão",
    both deliver_document on the case by 06/10: one promise the extraction wrote twice. Another thing to
    send on the case the same day stays its own."""
    due = (cell.clock.now() + timedelta(days=1)).isoformat()
    lawyer = lawyers_extraction()
    lawyer["open_items"] = [
        {"description": "Enviar ao cliente a cópia da decisão", "kind": "promise", "owner": "customer",
         "evidence": ["e2"], "object": "o1", "expected_operation": "deliver_document", "due_at": due},
        {"description": "Enviar cópia da sentença ao cliente", "kind": "promise", "owner": "customer",
         "evidence": ["e2"], "object": "o1", "expected_operation": "deliver_document", "due_at": due},
        {"description": "Enviar o comprovante de pagamento ao cliente", "kind": "promise",
         "owner": "customer", "evidence": ["e2"], "object": "o1", "expected_operation": "deliver_document",
         "due_at": due},
    ]  # fmt: skip
    await scenario(cell, lawyer=lawyer)
    assert sorted(i.description for i in await items_of(cell, LAWYER)) == [
        "Enviar ao cliente a cópia da decisão",
        "Enviar o comprovante de pagamento ao cliente",
    ]


async def test_the_clients_agents_promise_on_the_case_reaches_the_lawyers_case_line(cell: Cell) -> None:
    """juridico-sandbox, round 3 (05/10/2026): the attendant told the client "o advogado liga amanhã" on the
    case; the promise is the firm's, on the case, and the internal agents working the case read it on the
    case's line, named by the number people write."""
    told = {
        "episode": {"summary": "O atendente prometeu retorno", "intent": "other", "category": "contract",
                    "outcome": "resolved", "resolution": None, "sentiment": None},
        "open_items": [
            {"description": "Advogado ligará ao cliente amanhã", "kind": "promise", "owner": "company",
             "evidence": ["e2"], "object": "o1", "expected_operation": None, "due_at": None},
        ],
    }  # fmt: skip
    flow = await scenario(cell, None, told)
    clock = cell.clock
    turns = []
    for n, (role, text) in enumerate(
        [("customer", f"Alguém do jurídico pode me ligar sobre o processo {NUMBER}?"),
         ("ai_agent", "Sim, o advogado liga amanhã.")],
        start=1,
    ):  # fmt: skip
        clock.advance(minutes=1)
        turns.append(
            {
                "type": "event", "idempotency_key": f"wa-call-{n}", "channel": "whatsapp",
                "conversation_id": "wa-call", "handles": [CLIENT], "speaker": {"role": role},
                "content": {"type": "text", "text": text}, "object_refs": [CASE],
                "occurred_at": clock.now().isoformat(),
            }
        )  # fmt: skip
    clock.advance(minutes=1)
    ended = {"type": "conversation.ended", "idempotency_key": "wa-call-end", "conversation_id": "wa-call",
             "occurred_at": clock.now().isoformat()}  # fmt: skip
    assert (await flow.send(WHATSAPP_SOURCE, *turns, ended)).errors == []
    await flow.drain()
    assert [i.description for i in await items_of(cell, CLIENT)] == ["Advogado ligará ao cliente amanhã"]
    # A read by the case has no customer to verify: the firm releases its promises at V0, as its cases.
    config = cell.config.space(SPACE_ID)
    released = config.policy.model_copy(
        update={
            "version": f"{config.policy.version}-items",
            "categories": [
                *config.policy.categories,
                CategoryRule(name="open_item", min_verification=Verification.V0),
            ],
        }
    )
    cell.config.put(config.model_copy(update={"policy": released}))
    internal = principal(DRAFTING, cell.config.space(SPACE_ID))
    request = ContextRequest(
        object=ObjectRef(**CASE),
        view="task:drafting",
        task_id="draft-call",
        include=["state"],
        verification=Verification.V2,
    )
    response = await flow.serve.context(internal, request)
    assert response.state is not None
    lines = (response.state.text or "").splitlines()
    at = next(n for n, x in enumerate(lines) if x.startswith("- lawsuit court:case-1"))
    line = lines[at]
    assert line.startswith(f"- lawsuit court:case-1 [case_number {NUMBER}]: ")
    # Its items read under it, what is still to be done apart from what was done.
    under = lines[at + 1 : at + 3]
    assert any(
        x.startswith("  - pendências abertas: ") and "Advogado ligará ao cliente amanhã" in x for x in under
    )
    # The number is said beside the ref, once.
    assert line.count(NUMBER) == 1


async def test_the_clients_agent_reads_a_files_bookkeeping_as_a_count_on_its_case_line(cell: Cell) -> None:
    """juridico-sandbox, 05/10/2026: each case carried three or four document lines that said only how the
    firm's system scanned them ("Verificação de segurança clean; Extração do texto extracted"), and drowned
    the case's deadlines for the client's agent. For a customer-facing agent such a child is a count on its
    case's line; an internal agent still reads its line, and the JSON view holds it whole."""
    flow = await scenario(cell)
    config = cell.config.space(SPACE_ID)
    files = {
        "type": "case_file",
        "ownership": "subject",
        "mirror_of": {"system": "court"},
        "key": {"natural": ["file_id"]},
        "fields": {
            "file_id": {"type": "string"},
            "case_id": {"type": "string"},
            "scan": {"type": "string", "label": "Verificação de segurança"},
            "method": {"type": "string", "label": "Método de extração"},
        },
        "relations": {"case": {"type": "lawsuit", "via": "case_id"}},
    }
    types = ObjectTypeRegistry.model_validate({"types": [*TYPES["types"], files]})
    # The firm's tools read its files without a customer to verify, as its cases.
    policy = config.policy.model_copy(
        update={
            "categories": [
                *config.policy.categories,
                CategoryRule(name="case_file", min_verification=Verification.V0),
            ]
        }
    )
    cell.config.put(config.model_copy(update={"object_types": types, "policy": policy}))
    cell.clock.advance(minutes=1)
    added = {
        "type": "event", "kind": "system_event", "idempotency_key": "court-file-1", "channel": "erp",
        "canonical_type": "case_file.added", "handles": [FIRM],
        "object_refs": [{"type": "case_file", "namespace": "court", "id": "file-1"}],
        "speaker": {"role": "system"},
        "fields": {"file_id": "file-1", "case_id": "case-1", "scan": "clean", "method": "native_text"},
        "occurred_at": cell.clock.now().isoformat(),
    }  # fmt: skip
    assert (await flow.send(BILLING_SOURCE, added)).errors == []
    await flow.drain()
    await flow.prove(WHATSAPP_SOURCE, "wa-files", CLIENT["value"], "V2")
    client = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    asked = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-files",
        include=["state"],
        verification=Verification.V2,
    )
    read = await flow.serve.context(client, asked)
    assert read.state is not None
    text = read.state.text or ""
    assert "- case_file" not in text, text
    assert "native_text" not in text, text
    [case_line] = [x for x in text.splitlines() if x.startswith("- lawsuit court:case-1")]
    assert case_line.endswith("case file: 1"), case_line
    assert any(o.ref.id == "file-1" for o in read.state.objects)
    # The firm's own assistant reads the same client in a task: the file keeps its line. In a conversation
    # with the client it would read the client's view, as any agent talking to the client.
    internal = principal(ASSISTANT, cell.config.space(SPACE_ID))
    in_task = asked.model_copy(update={"conversation_id": None, "task_id": "lawyer-files"})
    own = await flow.serve.context(internal, in_task)
    assert own.state is not None
    assert "- case_file court:file-1 (de " in (own.state.text or ""), own.state.text
    with_client = await flow.serve.context(
        internal, asked.model_copy(update={"conversation_id": "lawyer-files"})
    )
    assert with_client.state is not None
    assert "- case_file" not in (with_client.state.text or ""), with_client.state.text


async def test_the_clients_agent_acting_on_a_case_by_its_written_number_acts_on_that_case(cell: Cell) -> None:
    """juridico-sandbox, 06/10/2026: the attendant passed the case number it read on the case's line as the
    object's id ("lawsuit:court:<number>") and every such action came back `unknown_object`, so a notice it
    really gave closed nothing. The number names the client's one case that holds it; an id no object of
    the client holds is still refused."""
    flow = await scenario(cell)
    clock = cell.clock

    def acted(key: str, object_id: str) -> dict[str, Any]:
        clock.advance(minutes=1)
        return {
            "type": "event", "kind": "action", "idempotency_key": key, "channel": "whatsapp",
            "conversation_id": "wa-number", "handles": [CLIENT], "speaker": {"role": "ai_agent"},
            "object_refs": [{"type": "lawsuit", "namespace": "court", "id": object_id}],
            "action": {"operation": "notify_client", "result": "Avisei o cliente da audiência"},
            "occurred_at": clock.now().isoformat(),
        }  # fmt: skip

    result = await flow.send(
        WHATSAPP_SOURCE, acted("wa-n1", NUMBER), acted("wa-n2", "0009999-56.2026.8.26.0100")
    )
    assert [(e.index, e.code) for e in result.errors] == [(1, "unknown_object")]
    await flow.drain()
    async with cell.uow(SPACE_ID) as uow:
        case = await uow.objects.get_object(ObjectKey("lawsuit", "court", "case-1"))
        phantom = await uow.objects.get_object(ObjectKey("lawsuit", "court", NUMBER))
        found = await uow.identity.find_handle(HandleType.PHONE_E164, "", CLIENT["value"])
        assert case is not None
        assert found is not None
        actions = await uow.objects.actions_for(
            [found.handle_id], since=clock.now() - timedelta(hours=1), limit=10
        )
    assert phantom is None
    assert [(a.operation, a.object_id) for a in actions] == [("notify_client", case.object_id)]


async def test_the_firms_word_about_its_client_holds_when_its_own_assistant_declines_to_note_it(
    cell: Cell,
) -> None:
    """juridico-sandbox, 06/10/2026: through the firm's internal assistant, the lawyer wrote what the client's
    agent must respect, and the assistant answered it could not record it. The instructions are the firm's
    (said by the team, holding only with Clara where she is named), and the client's agent reads them."""
    note = (
        "Anote sobre o cliente: o Marcos Lima, gerente de operações, prefere ser contatado por WhatsApp e "
        "só depois das 10h. E se a esposa dele, a Sra. Clara, escrever, não falar de valores dos processos "
        "com ela."
    )
    declined = "Não consigo registrar aqui dados pessoais de contato ou restrições sobre terceiros."
    noted = {
        "episode": {"summary": "A equipe registrou orientações sobre o cliente", "intent": "other",
                    "category": "contract", "outcome": "resolved", "resolution": None, "sentiment": None},
        "instructions": [
            {"text": "Marcos Lima: contatar por WhatsApp, só depois das 10h", "strength": "must",
             "attr": None, "evidence": ["e1"], "only_with": "Marcos Lima", "contact": True},
            {"text": "Não falar de valores dos processos com Clara", "strength": "must_not", "attr": None,
             "evidence": ["e1"], "only_with": "Clara"},
        ],
    }  # fmt: skip
    flow = await scenario(cell, None, noted)
    clock = cell.clock
    turns = []
    for n, (role, text) in enumerate([("customer", note), ("ai_agent", declined)], start=1):
        clock.advance(minutes=1)
        turns.append(
            {
                "type": "event", "idempotency_key": f"chat-note-{n}", "channel": "app",
                "conversation_id": "lawyer-note", "handles": [],
                "subjects": [{"kind": "staff", "role": "lawyer", "handles": [LAWYER]},
                             {"kind": "account", "handles": [FIRM]}],
                "speaker": {"role": role}, "content": {"type": "text", "text": text},
                "occurred_at": clock.now().isoformat(),
            }
        )  # fmt: skip
    clock.advance(minutes=1)
    ended = {"type": "conversation.ended", "idempotency_key": "chat-note-end",
             "conversation_id": "lawyer-note", "occurred_at": clock.now().isoformat()}  # fmt: skip
    assert (await flow.send(ASSISTANT, *turns, ended)).errors == []
    await flow.drain()
    await flow.prove(WHATSAPP_SOURCE, "wa-note", CLIENT["value"], "V2")
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    request = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-note",
        verification=Verification.V2,
        include=["constraints"],
    )
    response = await flow.serve.context(caller, request)
    assert response.constraints is not None
    held = {i.text: (i.only_with, i.contact) for i in response.constraints.instructions if i.object is None}
    assert held["Não falar de valores dos processos com Clara"] == ("Clara", False)
    assert held["Marcos Lima: contatar por WhatsApp, só depois das 10h"] == ("Marcos Lima", True)
    # Said once, in the pack: the restriction holds only if Clara speaks; Marcos's preference says how the
    # team reaches him, never what may be said to him (round 8: Marcos refused his amount 4 of 5 with it as
    # "exigido se quem fala for Marcos Lima").
    assert "instrução se quem fala for Clara: Não falar de valores dos processos com Clara" in response.text
    assert (
        "preferência de contato de Marcos Lima (para quando a equipe procurar essa pessoa; não muda o que se "
        "pode responder a ela): contatar por WhatsApp, só depois das 10h"
    ) in response.text
    assert "se quem fala for Marcos" not in response.text + response.constraints.text
    assert "Clara" not in response.constraints.text


async def test_an_account_wide_restriction_reaches_the_restricted_person_on_every_case_of_the_account(
    cell: Cell,
) -> None:
    """Round 9 (juridico-sandbox, 06/10/2026): "não falar de valores dos processos com Clara" reached Clara's
    reads of one case only, and she got the amount of the client's other case. An instruction about the
    organization holds wherever its people are read, whatever case the read is about."""
    flow = await scenario(cell)
    clock = cell.clock
    other = {"type": "lawsuit", "namespace": "court", "id": "case-2"}
    clock.advance(minutes=1)
    opened = {
        "type": "event", "kind": "system_event", "idempotency_key": "court-2", "channel": "erp",
        "canonical_type": "lawsuit.opened", "handles": [FIRM], "object_refs": [other],
        "speaker": {"role": "system"}, "fields": {"case_number": "0005678-11.2026.8.26.0100"},
        "occurred_at": clock.now().isoformat(),
    }  # fmt: skip
    assert (await flow.send(BILLING_SOURCE, opened)).errors == []
    await flow.drain()
    wife = {"type": "phone_e164", "value": "+5511955550002"}
    await flow.identity.create_link(
        SPACE_ID, Handle.model_validate(wife), Handle.model_validate(FIRM), role="contact"
    )
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    for n, case in enumerate((CASE, other), start=1):
        conversation = f"wa-wife-{n}"
        clock.advance(minutes=1)
        asked = {
            "type": "event", "idempotency_key": f"wa-wife-ask-{n}", "channel": "whatsapp",
            "conversation_id": conversation, "handles": [wife], "object_refs": [case],
            "speaker": {"role": "customer"},
            "content": {"type": "text", "text": "Qual o valor desse processo?"},
            "occurred_at": clock.now().isoformat(),
        }  # fmt: skip
        assert (await flow.send(WHATSAPP_SOURCE, asked)).errors == []
        await flow.drain()
        await flow.prove(WHATSAPP_SOURCE, conversation, wife["value"], "V2")
        request = ContextRequest(
            subject=Handle.model_validate(wife),
            about=Handle.model_validate(FIRM),
            conversation_id=conversation,
            verification=Verification.V2,
            include=["constraints"],
        )
        response = await flow.serve.context(caller, request)
        assert response.constraints is not None
        held = [i.text for i in response.constraints.instructions if i.object is None]
        assert held == ["não falar de valores com a esposa"], case
        assert "não falar de valores com a esposa" in response.text, case


async def test_one_key_serves_the_firms_staff_and_its_client_each_with_their_own_view(cell: Cell) -> None:
    """Zero touch: the firm's assistant is one internal key. With the lawyer, staff named by
    the firm's system, it is internal and his words are the firm's; with the client, named by her phone, the
    same key reads the client's view, never the lawyer's internal remark, and what the client says stays
    hers."""
    client_said = {
        "episode": {"summary": "A cliente perguntou do processo", "intent": "other", "category": "contract",
                    "outcome": "resolved", "resolution": None, "sentiment": None},
        "facts": [
            {"op": "add", "predicate": "observation", "value": "a cliente prefere acordo", "evidence": ["e1"],
             "object": "o1", "certainty": 0.9},
        ],
        "open_items": [
            {"description": "Cliente vai enviar os extratos", "kind": "promise", "owner": "customer",
             "evidence": ["e1"], "object": "o1", "due_at": None},
        ],
    }  # fmt: skip
    flow = await scenario(cell, None, client_said)
    space = cell.config.space(SPACE_ID)
    assistant = principal(ASSISTANT, space)
    # The same key, talking with the client.
    clock = cell.clock
    clock.advance(minutes=1)
    turn = {
        "type": "event", "idempotency_key": "assistant-client-1", "channel": "app",
        "conversation_id": "assistant-client", "handles": [CLIENT], "speaker": {"role": "customer"},
        "content": {"type": "text", "text": f"Sobre o processo {NUMBER}: prefiro acordo, mando os extratos."},
        "object_refs": [CASE], "occurred_at": clock.now().isoformat(),
    }  # fmt: skip
    clock.advance(minutes=1)
    ended = {"type": "conversation.ended", "idempotency_key": "assistant-client-end",
             "conversation_id": "assistant-client", "occurred_at": clock.now().isoformat()}  # fmt: skip
    assert (await flow.send(ASSISTANT, turn, ended)).errors == []
    await flow.drain()
    await flow.prove(ASSISTANT, "assistant-client", CLIENT["value"], "V2")
    with_client = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="assistant-client",
        verification=Verification.V2,
        include=["state"],
    )
    read = await flow.serve.context(assistant, with_client)
    assert "não propor acordo (equipe)" in read.text
    assert "tese é fraca" not in read.text
    assert "tese é fraca" not in (read.state.text if read.state else "")
    # Asked as an internal read, the conversation with the client still reads the client's view.
    asked_internal = with_client.model_copy(update={"verification": Verification.NO_CUSTOMER})
    assert "tese é fraca" not in (await flow.serve.context(assistant, asked_internal)).text
    # What the client said there is hers: said by the customer, a promise of the customer, readable by the
    # client's own agent; the lawyer's chat on the same key stays the firm's.
    async with cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.PHONE_E164, "", CLIENT["value"])
        assert handle is not None
        [promise] = [
            i
            for i in await uow.memory.open_items_for([handle.handle_id], tuple(OpenItemStatus))
            if i.description == "Cliente vai enviar os extratos"
        ]
        [prefers] = [f for f in await uow.memory.facts_for([handle.handle_id]) if "acordo" in str(f.value)]
    assert promise.owner == "customer"
    assert "speaker" not in promise.origin
    assert prefers.speaker is Speaker.CUSTOMER
    await flow.prove(WHATSAPP_SOURCE, "wa-after", CLIENT["value"], "V2")
    clients_agent = principal(WHATSAPP_SOURCE, space)
    seen = await flow.serve.context(
        clients_agent, with_client.model_copy(update={"conversation_id": "wa-after"})
    )
    assert "a cliente prefere acordo" in seen.text


async def test_a_customer_never_becomes_staff_by_claiming_it(cell: Cell) -> None:
    """Staff is named by the company's own system id: a phone that says it is staff is refused when it is
    written, and read as the customer it is, so the firm's internal remark never reaches it."""
    flow = await scenario(cell)
    claiming = {**CLIENT, "subject_kind": "staff"}
    turn = {
        "type": "event", "idempotency_key": "claim-1", "channel": "app", "conversation_id": "claim",
        "handles": [claiming], "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Sou da equipe."}, "occurred_at": cell.clock.now().isoformat(),
    }  # fmt: skip
    sent = await flow.send(ASSISTANT, turn)
    assert sent.errors, "a phone claiming staff must be refused"
    await flow.prove(ASSISTANT, "claim-read", CLIENT["value"], "V2")
    read = ContextRequest(
        subject=Handle.model_validate(claiming),
        about=Handle.model_validate(FIRM),
        conversation_id="claim-read",
        verification=Verification.NO_CUSTOMER,
    )
    try:
        response = await flow.serve.context(principal(ASSISTANT, cell.config.space(SPACE_ID)), read)
    except InvalidInput:
        return
    assert "tese é fraca" not in response.text


async def test_a_session_extracted_again_says_its_instruction_once_in_its_latest_words(cell: Cell) -> None:
    """A conversation nobody ends is extracted once it goes quiet and again when a turn of the same activity
    reopens it: the second run reads the same turn and words the instruction again, keeping its
    reason this time, and the case carried both ("não propor acordo" and "não propor acordo; a diretoria
    decidiu levar a julgamento"). What a session says is its latest run's: the instruction is said once."""
    cell.config.put(configured(cell))
    first = {
        "episode": {"summary": "O advogado orientou sobre o processo", "intent": "other",
                    "category": "contract", "outcome": "resolved", "resolution": None, "sentiment": None},
        "instructions": [{"text": "não propor acordo", "strength": "must_not", "attr": None,
                          "evidence": ["e1"], "object": "o1"}],
    }  # fmt: skip
    again = {
        **first,
        "instructions": [{"text": "não propor acordo; a diretoria decidiu levar a julgamento",
                          "strength": "must_not", "attr": None, "evidence": ["e1"], "object": "o1"}],
    }  # fmt: skip
    flow = Flow.build(cell, ScriptedLlm(first, again))
    clock = cell.clock
    opened = {
        "type": "event", "kind": "system_event", "idempotency_key": "court-1", "channel": "erp",
        "canonical_type": "lawsuit.opened", "handles": [FIRM], "object_refs": [CASE],
        "speaker": {"role": "system"}, "fields": {"case_number": NUMBER},
        "occurred_at": clock.now().isoformat(),
    }  # fmt: skip
    assert (await flow.send(BILLING_SOURCE, opened)).errors == []
    await flow.drain()
    clock.advance(minutes=1)
    said_once = said(
        1,
        "Neste processo, não propor acordo: a diretoria decidiu levar a julgamento.",
        clock.now().isoformat(),
    )
    assert (await flow.send(ASSISTANT, said_once)).errors == []
    clock.advance(minutes=4)
    assert await flow.sessions.sweep_idle(SPACE_ID) == 1
    await flow.drain()
    clock.advance(minutes=5)
    later = said(2, "Obrigado.", clock.now().isoformat())
    assert (await flow.send(ASSISTANT, later)).errors == []
    clock.advance(minutes=7)  # a reopened session waits twice as long before it closes
    assert await flow.sessions.sweep_idle(SPACE_ID) == 1
    await flow.drain()
    caller = principal(DRAFTING, cell.config.space(SPACE_ID))
    request = ContextRequest(
        object=ObjectRef(**CASE), view="task:drafting", task_id="draft-again", verification=Verification.V2,
        include=["constraints"],
    )  # fmt: skip
    response = await flow.serve.context(caller, request)
    assert response.constraints is not None
    assert [i.text for i in response.constraints.instructions if i.object] == [
        "não propor acordo; a diretoria decidiu levar a julgamento"
    ]


async def test_two_instructions_of_one_kind_said_in_one_turn_are_both_kept(cell: Cell) -> None:
    both = {
        "episode": {"summary": "O advogado orientou sobre o processo", "intent": "other",
                    "category": "contract", "outcome": "resolved", "resolution": None, "sentiment": None},
        "instructions": [
            {"text": "não propor acordo", "strength": "must_not", "attr": None, "evidence": ["e1"],
             "object": "o1"},
            {"text": "não aceitar audiência virtual", "strength": "must_not", "attr": None,
             "evidence": ["e1"], "object": "o1"},
        ],
    }  # fmt: skip
    flow = await scenario(cell, lawyer=both)
    caller = principal(DRAFTING, cell.config.space(SPACE_ID))
    request = ContextRequest(
        object=ObjectRef(**CASE), view="task:drafting", task_id="draft-both", verification=Verification.V2,
        include=["constraints"],
    )  # fmt: skip
    response = await flow.serve.context(caller, request)
    assert response.constraints is not None
    assert sorted(i.text for i in response.constraints.instructions if i.object) == [
        "não aceitar audiência virtual",
        "não propor acordo",
    ]


async def open_cases(flow: Flow, cell: Cell, count: int) -> None:
    """`count` cases of the firm opened after case-1, so case-1 is the oldest: 30 cases of 4 objects each is
    120 objects, a law firm's client of a few dozen cases (juridico-zero, 08/10/2026)."""
    for n in range(count):
        cell.clock.advance(seconds=1)
        opened = {
            "type": "event", "kind": "system_event", "idempotency_key": f"court-many-{n}", "channel": "erp",
            "canonical_type": "lawsuit.opened", "handles": [FIRM],
            "object_refs": [{"type": "lawsuit", "namespace": "court", "id": f"case-many-{n}"}],
            "speaker": {"role": "system"},
            "fields": {"case_number": f"{n:07d}-11.2026.8.26.0200", "due_date": "2026-12-01"},
            "occurred_at": cell.clock.now().isoformat(),
        }  # fmt: skip
        assert (await flow.send(BILLING_SOURCE, opened)).errors == []
    await flow.drain()


async def test_the_oldest_case_of_a_client_with_many_keeps_its_instruction_and_promise(cell: Cell) -> None:
    """A compilation gathered the 20 newest objects of a subject (and a snapshot read the 50
    newest), so past a client's fifth case or so the instructions and promises of the older ones were never
    read, and nothing said so."""

    async def many(flow: Flow) -> None:
        await open_cases(flow, cell, 120)

    flow = await scenario(cell, before_chat=many)
    await flow.prove(WHATSAPP_SOURCE, "wa-many", CLIENT["value"], "V2")
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    request = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-many",
        verification=Verification.V2,
        include=["state"],
    )
    response = await flow.serve.context(caller, request)
    assert "não propor acordo" in response.text
    assert response.state is not None
    assert response.state.text is not None
    # The case with memory is on the block first, and the block says how many it leaves out.
    assert response.state.objects[0].ref.id == "case-1"
    assert "Mandar os extratos ao cliente" in response.state.text
    assert "mais 101 objetos deste cliente fora desta lista" in response.state.text
