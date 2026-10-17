"""History navigation (16): search with recurrence, timeline, open, tool kit; same policy and receipts."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from niadra.adapters.memory.fakes import DeterministicModels
from niadra.app.compile import compile_settings
from niadra.config.models import Experiment
from niadra.contracts.common import Handle
from niadra.contracts.context import (
    ContextRequest,
    HistoryFilters,
    OpenedItem,
    SearchRequest,
    TimelineRequest,
)
from niadra.domain.common.errors import Forbidden, NotFound
from niadra.domain.compile import CompileInput, Variant
from niadra.domain.compile.candidates import CandidateBuilder
from niadra.domain.compile.model import ItemKind, MemorySnapshot
from niadra.domain.compile.templates import Formatter
from niadra.domain.memory.vectors import ItemVector
from niadra.domain.records import LinkRecord, ObjectKey
from niadra.domain.serve.history import NAVIGABLE, HistoryDoc, history_docs
from niadra.domain.serve.search import bm25, cosine, ranking, reciprocal_rank_fusion
from niadra.domain.serve.tools import _FILTERS
from niadra.domain.vocabulary import (
    HandleType,
    OpenItemStatus,
    ReceiptKind,
    ReceiptSurface,
    Scope,
    Sensitivity,
    SubjectKind,
    Verification,
)
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, Cell, principal, sample_config
from tests.unit.compile.factories import PHONE, PROFILE, T0, episode, marina, open_item, profile, uid
from tests.unit.serve.harness import SUBJECT, Harness, handle_record

AGENT = principal()


def search(query: str, **extra: object) -> SearchRequest:
    base = {"subject": SUBJECT, "query": query, "verification": Verification.V2, "conversation_id": "c1"}
    return SearchRequest(**(base | extra))  # type: ignore[arg-type]


def proven(cell: Cell) -> Harness:
    """A harness where conversation `c1` proved V2 (an OTP, say)."""
    h = Harness(cell)
    h.world.proven = Verification.V2
    return h


async def test_search_ranks_counts_recurrence_and_leaves_a_receipt(cell: Cell) -> None:
    h = proven(cell)
    response = await h.serve.search(AGENT, search("problema visita"))
    assert response.items
    assert response.items[0].kind == "episode"
    assert response.recurrence is not None
    # Three technical visits in the fixture, all complaints within the 90-day window.
    assert (response.recurrence.category, response.recurrence.occurrences) == ("technical_visit", 3)
    assert response.recurrence.last_outcome == "unresolved"
    assert response.withheld == 1  # the sensitive fact
    assert response.tokens_used <= 800
    assert response.degraded is None
    receipt = h.receipts.sent[-1]
    assert (receipt.kind, receipt.surface) == (ReceiptKind.HISTORY, ReceiptSurface.SEARCH)
    assert receipt.query_hash is not None
    # Receipts carry the memory row's own id, the one "who saw fact F" looks up (5.13).
    assert receipt.item_ids == tuple(i.id for i in response.items)


async def test_the_receipt_keeps_a_keyed_hash_of_the_filters(cell: Cell) -> None:
    # Which filters were asked, provable and chained, never in clear.
    h = proven(cell)
    await h.serve.search(AGENT, search("visita"))
    assert h.receipts.sent[-1].detail == {}
    filters = HistoryFilters(categories=["technical_visit"], channels=["whatsapp"])
    await h.serve.search(AGENT, search("visita", filters=filters))
    first = h.receipts.sent[-1].detail["filters_hash"]
    assert "technical_visit" not in first
    # The same filters, listed in another order of fields, hash the same; other filters do not.
    await h.serve.search(
        AGENT, search("visita", filters=HistoryFilters(channels=["whatsapp"], categories=["technical_visit"]))
    )
    assert h.receipts.sent[-1].detail["filters_hash"] == first
    await h.serve.timeline(
        AGENT,
        TimelineRequest(
            subject=SUBJECT, filters=HistoryFilters(categories=["billing"]), conversation_id="c1"
        ),
    )
    timeline = h.receipts.sent[-1]
    assert timeline.surface is ReceiptSurface.TIMELINE
    assert timeline.detail["filters_hash"] not in (first, "")


async def test_the_same_query_returns_the_same_items(cell: Cell) -> None:
    h = proven(cell)
    first = await h.serve.search(AGENT, search("fatura"))
    second = await h.serve.search(AGENT, search("fatura"))
    assert first == second


async def test_search_applies_the_level_filters_and_budget(cell: Cell) -> None:
    h = proven(cell)
    at_v1 = await h.serve.search(AGENT, search("fatura agosto", verification=Verification.V1))
    assert all("fatura de agosto" not in i.text or i.kind == "action" for i in at_v1.items)
    assert at_v1.withheld == 3
    only_actions = await h.serve.search(
        AGENT, search("fatura", filters=HistoryFilters(item_kinds=["action"]))
    )
    assert {i.kind for i in only_actions.items} == {"action"}
    tight = await h.serve.search(AGENT, search("problema visita fatura", max_tokens=50))
    assert tight.tokens_used <= 50


async def test_voice_sources_get_a_short_default_budget(cell: Cell) -> None:
    h = Harness(cell)
    voice = await h.serve.search(principal(VOICE_SOURCE), search("problema"))
    assert voice.tokens_used <= 300


async def test_a_dead_encoder_degrades_to_text_only(cell: Cell) -> None:
    class DeadModels(DeterministicModels):
        async def embed_query(self, text):  # type: ignore[no-untyped-def]
            raise ConnectionError("encoder down")

    cell.models = DeadModels()
    h = proven(cell)
    response = await h.serve.search(AGENT, search("fatura"))
    assert response.degraded == "text_only"
    assert response.items


async def test_the_timeline_pages_newest_first(cell: Cell) -> None:
    h = proven(cell)
    first = await h.serve.timeline(
        AGENT, TimelineRequest(subject=SUBJECT, limit=2, verification=Verification.V2, conversation_id="c1")
    )
    assert [i.kind for i in first.items] == ["action", "episode"]
    assert first.next_cursor
    second = await h.serve.timeline(
        AGENT,
        TimelineRequest(
            subject=SUBJECT,
            limit=2,
            verification=Verification.V2,
            conversation_id="c1",
            cursor=first.next_cursor,
        ),
    )
    rest = await h.serve.timeline_for_profile(
        AGENT,
        PROFILE,
        cursor=second.next_cursor,
        limit=10,
        verification=Verification.V2,
        conversation_id="c1",
    )
    everything = [*first.items, *second.items, *rest.items]
    assert len(everything) == 5
    assert rest.next_cursor is None
    assert [i.at for i in everything] == sorted((i.at for i in everything), reverse=True)
    assert h.receipts.sent[-1].surface is ReceiptSurface.TIMELINE


async def test_open_shows_an_episode_and_hides_what_the_level_withholds(cell: Cell) -> None:
    h = proven(cell)
    opened = await h.serve.open_item(
        AGENT, str(uid(0x201)), verification=Verification.V1, conversation_id="c1"
    )
    assert opened.kind == "episode"
    assert opened.outcome == "unresolved"
    assert "excerpt" not in opened.model_dump()
    with pytest.raises(NotFound):
        # The billing conversation needs V2; withheld and missing look the same.
        await h.serve.open_item(AGENT, str(uid(0x204)), verification=Verification.V1, conversation_id="c1")
    with pytest.raises(NotFound):
        await h.serve.open_item(AGENT, str(uid(0x999)))
    with pytest.raises(NotFound):
        await h.serve.open_item(
            AGENT,
            str(uid(0x201)),
            verification=Verification.V1,
            conversation_id="c1",
            profile_id=uid(1),
        )
    assert h.receipts.sent[-1].surface is ReceiptSurface.OPEN


async def test_open_an_object_with_its_actions_and_promises(cell: Cell) -> None:
    h = proven(cell)
    opened = await h.serve.open_item(
        AGENT, str(uid(0x700)), verification=Verification.V2, subject=SUBJECT, conversation_id="c1"
    )
    assert opened.kind == "object"
    assert "invoice 0823" in opened.summary


async def test_the_control_group_navigates_nothing(cell: Cell) -> None:
    cell.config.put(
        sample_config().model_copy(
            update={
                "experiments": [
                    Experiment(
                        name="pilot", fraction_with_memory=0.0, salt="s", starts_at=T0 - timedelta(days=1)
                    )
                ]
            }
        )
    )
    h = Harness(cell)
    response = await h.serve.search(AGENT, search("fatura"))
    assert response.items == []
    assert response.recurrence is None
    assert h.receipts.sent[-1].experiment_group == "pilot:control"


async def test_an_overdue_company_promise_takes_the_customer_out_of_the_control_group(cell: Cell) -> None:
    # The same exclusion as context(): the customer navigates as anyone, outside both groups.
    cell.config.put(
        sample_config().model_copy(
            update={
                "experiments": [
                    Experiment(
                        name="pilot", fraction_with_memory=0.0, salt="s", starts_at=T0 - timedelta(days=1)
                    )
                ]
            }
        )
    )
    h = Harness(cell)
    overdue = open_item(1, "Visita técnica", due_days=-1, status=OpenItemStatus.OVERDUE)
    h.world.replace_profile(replace(marina(), open_items=(overdue,)))
    response = await h.serve.search(AGENT, search("visita"))
    assert response.items
    assert h.receipts.sent[-1].experiment_group is None


async def test_tools_are_function_schemas_without_the_customer(cell: Cell) -> None:
    h = Harness(cell)
    tools = h.serve.tools_definitions(AGENT)
    assert [t.function["name"] for t in tools] == [
        "search_customer_history",
        "get_customer_timeline",
        "open_history_item",
    ]
    for tool in tools:
        assert tool.type == "function"
        assert not {"subject", "profile_id", "handle"} & set(tool.function["parameters"]["properties"])
        assert len(tool.function["description"]) < 600  # about 120 tokens (16.5)
    assert "'Histórico' line" in tools[0].function["description"]


async def test_the_search_tool_names_the_history_line_in_the_space_language(cell: Cell) -> None:
    # The description points at the label the agent actually sees in its context.
    config = cell.config.space(SPACE_ID)
    english = config.model_copy(update={"settings": config.settings.model_copy(update={"locale": "en-US"})})
    cell.config.put(english)
    h = Harness(cell)
    [search, *_] = h.serve.tools_definitions(AGENT)
    assert "'History' line" in search.function["description"]
    assert "Histórico" not in search.function["description"]


def test_the_kinds_a_filter_takes_are_the_kinds_navigation_returns() -> None:
    # A system event is a row of its own, beside the kinds the pack's candidates give.
    offered = set(HistoryFilters.model_json_schema()["properties"]["item_kinds"]["items"]["enum"])
    assert offered == {kind.value for kind in NAVIGABLE} | {"system_event"}
    assert set(_FILTERS["properties"]["item_kinds"]["items"]["enum"]) == offered
    with pytest.raises(ValidationError):
        HistoryFilters(item_kinds=["transcript"])  # type: ignore[list-item]
    assert "excerpt" not in OpenedItem.model_json_schema()["properties"]


async def test_navigation_needs_the_search_scope_and_an_enabled_source(cell: Cell) -> None:
    h = Harness(cell)
    with pytest.raises(Forbidden):
        await h.serve.search(principal(scopes=frozenset({Scope.CONTEXT})), search("x"))
    config = sample_config()
    sources = [s.model_copy(update={"navigation_enabled": False}) for s in config.sources]
    cell.config.put(config.model_copy(update={"sources": sources}))
    with pytest.raises(Forbidden):
        h.serve.tools_definitions(AGENT)


class RecordingModels(DeterministicModels):
    """Counts every text the read path sends to the encoder."""

    def __init__(self) -> None:
        self.encoded: list[str] = []

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        self.encoded.extend(texts)
        return await super().embed(texts)

    async def embed_query(self, text):  # type: ignore[no-untyped-def]
        self.encoded.append(text)
        return await super().embed_query(text)


def marina_docs(cell: Cell) -> list[HistoryDoc]:
    """Marina's rows as the navigation shows them, with the same templates as the read path."""
    snapshot = marina()
    settings = compile_settings(cell.config.space(SPACE_ID))
    fmt = Formatter(settings.timezone, settings.locale)
    candidates = CandidateBuilder(
        CompileInput(snapshot, Variant(verification=Verification.V2), settings), fmt
    ).build()
    return history_docs(snapshot, candidates)


def stored(doc: HistoryDoc, vector: list[float], model: str = "hash-64") -> ItemVector:
    kind, _, raw = doc.id.partition(":")
    return ItemVector(ItemKind(kind), UUID(raw), vector, model)


def vectors_of_their_text(docs: list[HistoryDoc]) -> list[ItemVector]:
    """What encoding each row's text while serving used to produce, stored instead."""
    models = DeterministicModels()
    return [stored(d, models._embed(d.text)) for d in docs]


async def test_a_search_encodes_only_its_question(cell: Cell) -> None:
    models = RecordingModels()
    cell.models = models
    h = proven(cell)
    for vectors in ([], vectors_of_their_text(marina_docs(cell))):
        h.world.vectors = vectors
        models.encoded.clear()
        response = await h.serve.search(AGENT, search("problema visita fatura"))
        assert response.items
        assert response.degraded is None
        assert models.encoded == ["problema visita fatura"]


async def test_ranking_is_unchanged_for_items_with_vectors(cell: Cell) -> None:
    h = proven(cell)
    docs = marina_docs(cell)
    h.world.vectors = vectors_of_their_text(docs)
    query = "visita técnica remarcada fatura crédito"
    response = await h.serve.search(AGENT, search(query, max_tokens=4000))
    # Before, the read path encoded every row's text while serving and fused its cosine with BM25; with
    # the same vectors stored, the order is the same. The sensitive fact is withheld at any level.
    assert response.withheld == 1
    visible = [d for d in docs if d.attrs.sensitivity is Sensitivity.NORMAL]
    q = DeterministicModels()._embed(query)
    lexical = bm25(query, {d.id: d.text for d in visible})
    by_meaning = {d.id: s for d in visible if (s := cosine(q, DeterministicModels()._embed(d.text))) > 0}
    expected = ranking(reciprocal_rank_fusion([ranking(lexical), ranking(by_meaning)]))
    assert [i.id for i in response.items] == [i.split(":", 1)[1] for i in expected]


async def test_an_item_without_a_vector_of_the_questions_encoder_ranks_by_its_words(cell: Cell) -> None:
    h = proven(cell)
    [invoice] = [d for d in marina_docs(cell) if d.ref == uid(0x700)]
    query = "boleto em atraso"  # no word in common with the invoice's row
    meaning = DeterministicModels()._embed(query)
    # A vector close to the question stands in for a real encoder that knows a bill is an invoice.
    h.world.vectors = [stored(invoice, meaning)]
    found = await h.serve.search(AGENT, search(query))
    assert str(invoice.ref) in [i.id for i in found.items]
    for vectors in ([], [stored(invoice, meaning, model="an-older-encoder")]):
        h.world.vectors = vectors
        found = await h.serve.search(AGENT, search(query))
        assert str(invoice.ref) not in [i.id for i in found.items]
        assert found.degraded is None


async def test_when_narrows_the_search_to_the_period_the_customer_named(cell: Cell) -> None:
    h = proven(cell)
    july = await h.serve.search(AGENT, search("visita problema", filters=HistoryFilters(when="em julho")))
    assert july.ignored == []
    assert july.window is not None
    assert july.window.since.isoformat() == "2026-07-01T00:00:00-03:00"
    assert july.items
    assert all(i.at.month == 7 for i in july.items)
    # A period that holds nothing returns nothing, never the whole history.
    empty = await h.serve.search(AGENT, search("visita", filters=HistoryFilters(when="last week")))
    assert empty.items == []
    unknown = await h.serve.search(AGENT, search("visita", filters=HistoryFilters(when="quando choveu")))
    assert unknown.ignored == ["when"]
    assert unknown.window is None
    assert unknown.items


async def test_when_is_read_from_the_conversations_last_event(cell: Cell) -> None:
    # A replay a month later reads "yesterday" as the day before the conversation, not before the replay.
    h = proven(cell)
    h.world.conversation_last_event = T0 - timedelta(days=19, hours=12)
    found = await h.serve.search(AGENT, search("visita", filters=HistoryFilters(when="ontem")))
    assert found.window is not None
    assert found.window.since.date().isoformat() == "2026-09-02"
    page = await h.serve.timeline(
        AGENT,
        TimelineRequest(
            subject=SUBJECT,
            verification=Verification.V2,
            conversation_id="c1",
            filters=HistoryFilters(when="ontem"),
        ),
    )
    assert [i.kind for i in page.items] == ["episode"]


async def test_the_timeline_lists_the_kinds_item_kinds_names(cell: Cell) -> None:
    # What happened by default; the open items and facts only when asked, listed without a question.
    h = proven(cell)

    def request(*kinds: str) -> TimelineRequest:
        filters = HistoryFilters(item_kinds=list(kinds))  # type: ignore[arg-type]
        return TimelineRequest(
            subject=SUBJECT, verification=Verification.V2, conversation_id="c1", filters=filters
        )

    default = await h.serve.timeline(
        AGENT, TimelineRequest(subject=SUBJECT, verification=Verification.V2, conversation_id="c1")
    )
    assert {i.kind for i in default.items} <= {"episode", "action", "system_event"}
    items = await h.serve.timeline(AGENT, request("open_item"))
    assert [i.kind for i in items.items] == ["open_item"]
    assert "Visita técnica" in items.items[0].text
    facts = await h.serve.timeline(AGENT, request("fact", "open_item"))
    assert {i.kind for i in facts.items} == {"fact", "open_item"}
    assert [i.at for i in facts.items] == sorted((i.at for i in facts.items), reverse=True)
    # Every fact the reader may see, without a question; the sensitive one stays out, as in search.
    listed = [i.text for i in facts.items if i.kind == "fact"]
    assert len(listed) == 2
    assert not any("gestante" in text for text in listed)
    searched = await h.serve.search(AGENT, search("gestante", filters=HistoryFilters(item_kinds=["fact"])))
    assert (facts.withheld, searched.withheld) == (1, 1)


async def test_a_fact_past_its_declared_validity_leaves_search_and_context(cell: Cell) -> None:
    h = proven(cell)
    snapshot = h.world.snapshots[0]
    ended = replace(snapshot.facts[1], valid_until=T0 - timedelta(hours=1))
    h.world.replace_profile(replace(snapshot, facts=(snapshot.facts[0], ended, snapshot.facts[2])))
    found = await h.serve.search(AGENT, search("fibra 500 mega plano"))
    assert str(ended.fact_id) not in [i.id for i in found.items]
    kept = await h.serve.search(
        AGENT, search("fibra 500 mega plano", filters=HistoryFilters(show_expired=True))
    )
    [item] = [i for i in kept.items if i.id == str(ended.fact_id)]
    assert item.valid_until == ended.valid_until
    context = await h.serve.context(
        AGENT, ContextRequest(subject=SUBJECT, verification=Verification.V2, conversation_id="c2")
    )
    assert context.text is not None
    assert "fibra 500" not in context.text


async def test_open_lists_the_versions_of_an_episode(cell: Cell) -> None:
    h = proven(cell)
    current = h.world.snapshots[0].episodes[0]
    first = replace(current, outcome="pending", summary="cliente abriu reclamação")
    h.world.earlier_episodes = [first]
    h.world.replace_profile(
        replace(
            h.world.snapshots[0], episodes=(replace(current, version=2), *h.world.snapshots[0].episodes[1:])
        )
    )
    opened = await h.serve.open_item(
        AGENT, str(current.episode_id), verification=Verification.V1, conversation_id="c1"
    )
    assert [(v.version, v.what_changed) for v in opened.versions] == [(1, "created"), (2, "summary,outcome")]


async def test_a_system_event_is_a_row_of_search_timeline_and_open(cell: Cell) -> None:
    # An ERP event is its own row; the episode it happened in lists it in its timeline.
    from niadra.domain.records import EventRecord, ObjectKey
    from niadra.domain.vocabulary import EventKind, Speaker
    from niadra.testing.cell import BILLING_SOURCE
    from tests.unit.compile.factories import PHONE

    h = proven(cell)
    during_call = T0 - timedelta(days=0.2) + timedelta(minutes=5)
    credited = EventRecord(
        uid(0x980),
        EventKind.SYSTEM_EVENT,
        BILLING_SOURCE,
        "erp",
        Speaker.SYSTEM,
        during_call,
        during_call,
        (PHONE,),
        canonical_type="invoice.credited",
        fields={"amount": "40.00"},
        object_refs=(ObjectKey("invoice", "erp", "0823"),),
    )
    handoff = replace(credited, event_id=uid(0x981), canonical_type="handoff", object_refs=())
    h.world.system_events += [credited, handoff]
    row = str(uid(0x980))

    found = await h.serve.search(AGENT, search("0823", filters=HistoryFilters(item_kinds=["system_event"])))
    assert row in [i.id for i in found.items]
    [item] = [i for i in found.items if i.id == row]
    assert (item.kind, item.origin_event_id) == ("system_event", str(uid(0x980)))
    assert item.text.startswith("invoice.credited · invoice 0823")
    timeline = await h.serve.timeline(
        AGENT, TimelineRequest(subject=SUBJECT, verification=Verification.V2, conversation_id="c1", limit=100)
    )
    ids = [i.id for i in timeline.items]
    assert row in ids
    assert str(uid(0x981)) not in ids  # a handoff is conversation plumbing
    episode = await h.serve.open_item(
        AGENT, str(uid(0x201)), verification=Verification.V2, conversation_id="c1"
    )
    assert [i.id for i in episode.timeline] == [row]
    invoice = await h.serve.open_item(
        AGENT, str(uid(0x700)), verification=Verification.V2, subject=SUBJECT, conversation_id="c1"
    )
    assert row in [i.id for i in invoice.timeline]
    assert str(uid(0x980)) in h.receipts.sent[-1].item_ids


async def test_with_about_navigation_reaches_what_the_account_block_shows(cell: Cell) -> None:
    # The person's pack shows the organization's memory under [Conta] (21.4): search, the timeline and open
    # reach those same items, under the same link, and only with `about`.
    org_handle = uid(0xE100)
    h = proven(cell)
    h.world.handles.append(
        handle_record(org_handle, HandleType.SYSTEM_ID, "acme-1", scope="crm", kind=SubjectKind.ACCOUNT)
    )
    renewal = episode(60, "contract", days_ago=2, summary="Renovação do contrato da Acme", handle=org_handle)
    addendum = replace(open_item(61, "Enviar o aditivo assinado"), origin_handle_id=org_handle)
    h.world.snapshots.append(
        MemorySnapshot(
            profile=profile(pid=uid(0xD100), kind=SubjectKind.ACCOUNT, handles=(org_handle,)),
            episodes=(renewal,),
            open_items=(addendum,),
        )
    )
    acme = Handle(type=HandleType.SYSTEM_ID, value="acme-1", scope="crm", subject_kind=SubjectKind.ACCOUNT)
    items = HistoryFilters(item_kinds=["open_item"])
    # No link yet: the person's own rows, flagged, as the context read fails soft.
    alone = await h.serve.search(AGENT, search("aditivo assinado", about=acme, filters=items))
    assert alone.about_unlinked
    assert str(addendum.item_id) not in [i.id for i in alone.items]
    h.world.links.append(
        LinkRecord(uid(0x800), PHONE, org_handle, "buyer", True, T0 - timedelta(days=9), None)
    )
    found = await h.serve.search(AGENT, search("aditivo assinado", about=acme, filters=items))
    assert str(addendum.item_id) in [i.id for i in found.items]
    assert not found.about_unlinked
    listed = await h.serve.timeline(
        AGENT,
        TimelineRequest(
            subject=SUBJECT, about=acme, verification=Verification.V2, conversation_id="c1", filters=items
        ),
    )
    assert str(addendum.item_id) in [i.id for i in listed.items]
    conversation = str(renewal.episode_id)
    opened = await h.serve.open_item(
        AGENT, conversation, verification=Verification.V2, conversation_id="c1", subject=SUBJECT, about=acme
    )
    assert opened.summary == "Renovação do contrato da Acme"
    # Without `about`, the person's own kit does not reach the organization's conversation.
    with pytest.raises(NotFound):
        await h.serve.open_item(
            AGENT, conversation, verification=Verification.V2, conversation_id="c1", subject=SUBJECT
        )


async def test_an_open_items_row_says_its_object_and_the_operation_that_closes_it(cell: Cell) -> None:
    # Not personal data: the integrator sees what the extraction expects to close each item.
    h = proven(cell)
    snapshot = h.world.snapshots[0]
    waiting = replace(
        snapshot.open_items[0], object=ObjectKey("visit", "field", "v-77"), expected_operation="reschedule"
    )
    h.world.replace_profile(replace(snapshot, open_items=(waiting,)))
    filters = HistoryFilters(item_kinds=["open_item"])
    request = TimelineRequest(
        subject=SUBJECT, verification=Verification.V2, conversation_id="c1", filters=filters
    )
    [row] = (await h.serve.timeline(AGENT, request)).items
    assert row.object is not None
    assert (row.object.type, row.object.namespace, row.object.id) == ("visit", "field", "v-77")
    assert row.expected_operation == "reschedule"
    [found] = (await h.serve.search(AGENT, search("visita", filters=filters))).items
    assert found.expected_operation == "reschedule"


async def test_item_statuses_lists_the_resolved_items_with_what_closed_them(cell: Cell) -> None:
    # An integrator lists what was done, not only what is pending: every resolved item, with when and what
    # closed it. Without the filter the rows stay what the context shows: open, overdue, kept lately.
    h = proven(cell)
    snapshot = h.world.snapshots[0]
    resolved = OpenItemStatus.RESOLVED
    credit = {
        "kind": "action",
        "action_id": str(uid(0x9A1)),
        "event_id": str(uid(0x9A2)),
        "operation": "credit",
    }
    erp = {"kind": "system_event", "action_id": None, "event_id": str(uid(0x9A3)), "operation": "send"}
    pending = open_item(70, "Ligar com a segunda via da fatura")
    late = open_item(71, "Agendar a visita técnica", due_days=-2, status=OpenItemStatus.OVERDUE)
    kept = open_item(72, "Creditar a fatura 0823", status=resolved)
    kept = replace(
        kept,
        closed_at=T0 - timedelta(days=1),
        closed_by_action_id=uid(0x9A1),
        origin={**kept.origin, "closed_by": credit},
    )
    sent = open_item(73, "Enviar o boleto de setembro", status=resolved)
    sent = replace(sent, closed_at=T0 - timedelta(days=30), origin={**sent.origin, "closed_by": erp})
    h.world.replace_profile(replace(snapshot, open_items=(pending, late, kept, sent)))

    def read(**filters: object) -> TimelineRequest:
        return TimelineRequest(
            subject=SUBJECT,
            verification=Verification.V2,
            conversation_id="c1",
            filters=HistoryFilters.model_validate(filters),
        )

    def ids(*items: object) -> set[str]:
        return {str(i.item_id) for i in items}  # type: ignore[attr-defined]

    default = await h.serve.timeline(AGENT, read(item_kinds=["open_item"]))
    assert {i.id for i in default.items} == ids(pending, late, kept)
    rows = {i.id: i for i in default.items}
    assert rows[str(pending.item_id)].status == "open"
    assert rows[str(late.item_id)].status == "overdue"
    assert rows[str(pending.item_id)].closed_by is None
    row = rows[str(kept.item_id)]
    assert (row.status, row.outcome, row.closed_at) == ("resolved", "resolved", T0 - timedelta(days=1))
    assert row.closed_by is not None
    assert row.closed_by.model_dump(exclude_none=True) == {k: v for k, v in credit.items() if v}

    # Named, the statuses list open items in the timeline beside what happened, every resolved one.
    done = await h.serve.timeline(AGENT, read(item_statuses=["resolved"]))
    listed = {i.id: i for i in done.items if i.kind == "open_item"}
    assert set(listed) == ids(kept, sent)
    assert any(i.kind == "episode" for i in done.items)
    by_erp = listed[str(sent.item_id)].closed_by
    assert by_erp is not None
    assert (by_erp.kind, by_erp.event_id, by_erp.operation) == ("system_event", erp["event_id"], "send")

    still = await h.serve.timeline(AGENT, read(item_kinds=["open_item"], item_statuses=["open", "overdue"]))
    assert {i.id for i in still.items} == ids(pending, late)

    # Search reaches an old resolved item only when asked for resolved ones.
    boleto = search("boleto de setembro", filters=HistoryFilters(item_kinds=["open_item"]))
    assert str(sent.item_id) not in [i.id for i in (await h.serve.search(AGENT, boleto)).items]
    asked = search("boleto de setembro", filters=HistoryFilters(item_statuses=["resolved"]))
    found = [i for i in (await h.serve.search(AGENT, asked)).items if i.kind == "open_item"]
    assert str(sent.item_id) in [i.id for i in found]
    assert all(i.status == "resolved" for i in found)


def test_item_statuses_names_only_the_statuses_an_item_has() -> None:
    with pytest.raises(ValidationError):
        HistoryFilters(item_statuses=["closed"])  # type: ignore[list-item]
    assert HistoryFilters().item_statuses == []
