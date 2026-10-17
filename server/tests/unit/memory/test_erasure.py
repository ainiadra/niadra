"""The evidence rule of the erasure cascade (8.4, L17) and the memory module's part in it."""

from datetime import UTC, datetime
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from niadra.adapters.memory.memory import InMemoryMemoryRepository
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import MemoryRef
from niadra.domain.memory.erasure import held_by_feedback_only, plan_evidence_erasure
from niadra.domain.memory.facts import Relation, normalize_value
from niadra.domain.memory.summaries import TailState
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import Episode, Fact, OpenItem, ProfileAttr
from niadra.domain.vocabulary import FactStatus, OpenItemStatus, RelationType, Sensitivity, Speaker

T0 = datetime(2026, 9, 22, 14, tzinfo=UTC)
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c001")


class TestPlan:
    def test_a_fact_goes_with_its_last_mention(self):
        plan = plan_evidence_erasure({"f1": {"e1"}, "f2": {"e1", "e2"}}, {"e1"}.__contains__, 10)
        assert plan.items == ("f1",)
        assert sorted(plan.evidence) == [("f1", "e1"), ("f2", "e1")]

    def test_a_short_batch_means_nothing_is_left(self):
        erased = {"e1", "e2", "e3"}.__contains__
        plan = plan_evidence_erasure({"f1": {"e1", "e2", "e3"}}, erased, 2)
        # f1 needs four rows: the batch fills up with two of its mentions, and the next takes the rest.
        assert (plan.items, len(plan.evidence)) == ((), 2)
        rest = plan_evidence_erasure({"f1": {"e3"}}, erased, 2)
        assert (rest.items, rest.evidence) == (("f1",), (("f1", "e3"),))

    def test_the_last_mention_never_goes_without_its_fact(self):
        # One row of room and an orphan that needs two: both go, one over the limit, never the mention
        # alone, which would leave a fact nothing leads back to.
        plan = plan_evidence_erasure({"a": {"x"}, "b": {"y"}}, {"x", "y"}.__contains__, 3)
        assert plan.rows == 4
        assert plan.items == ("a", "b")

    @given(
        st.dictionaries(
            st.integers(0, 12), st.sets(st.integers(0, 8), min_size=1, max_size=4), min_size=0, max_size=12
        ),
        st.sets(st.integers(0, 8)),
        st.integers(1, 6),
    )
    def test_batches_converge_to_the_rule(self, evidence: dict[int, set[int]], erased: set[int], limit: int):
        left = {item: set(events) for item, events in evidence.items()}
        gone: set[int] = set()
        while True:
            plan = plan_evidence_erasure(left, erased.__contains__, limit)
            assert plan.rows <= limit + 1
            for item, event in plan.evidence:
                left[item].discard(event)
            for item in plan.items:
                assert not left[item], "an item goes only with its last evidence row"
                del left[item]
                gone.add(item)
            if plan.rows < limit:
                break
        assert gone == {i for i, events in evidence.items() if events and events <= erased}
        assert all(not (events & erased) for events in left.values())


class TestFeedbackIsNeverEvidence:
    """The company's feedback leaves mentions for the lineage; only what the customer said keeps a fact."""

    def test_a_fact_the_company_retracted_goes_with_the_customers_last_event(self):
        feedback = {"r1"}.__contains__
        plan = plan_evidence_erasure(
            {"f1": {"e1", "r1"}, "f2": {"e1", "e2", "r1"}}, {"e1"}.__contains__, 10, lambda e: not feedback(e)
        )
        assert plan.items == ("f1",)
        # f1 takes its feedback mention with it; f2 keeps the customer's other statement and the feedback.
        assert sorted(plan.evidence) == [("f1", "e1"), ("f1", "r1"), ("f2", "e1")]

    def test_an_orphan_that_does_not_fit_leaves_an_erased_row_for_the_next_batch(self):
        supports = {"e1", "e2"}.__contains__
        plan = plan_evidence_erasure({"f1": {"e1", "e2", "r1"}}, supports, 1, supports)
        assert (plan.items, plan.evidence) == ((), (("f1", "e1"),))

    def test_only_rows_whose_every_mention_is_feedback_are_held_by_it(self):
        found = held_by_feedback_only(
            {"fix": {"c1"}, "said": {"c1", "e9"}, "none": set()}, {"c1"}.__contains__
        )
        assert found == ["fix"]

    @given(
        st.dictionaries(
            st.integers(0, 12), st.sets(st.integers(0, 8), min_size=1, max_size=4), min_size=0, max_size=12
        ),
        st.sets(st.integers(0, 8)),
        st.sets(st.integers(0, 8)),
        st.integers(1, 6),
    )
    def test_batches_converge_without_counting_feedback(
        self, evidence: dict[int, set[int]], erased: set[int], feedback: set[int], limit: int
    ):
        left = {item: set(events) for item, events in evidence.items()}
        gone: set[int] = set()
        supports = lambda e: e not in feedback  # noqa: E731
        while True:
            plan = plan_evidence_erasure(left, erased.__contains__, limit, supports)
            for item, event in plan.evidence:
                left[item].discard(event)
            for item in plan.items:
                assert not left[item], "an item goes only with every evidence row it has"
                del left[item]
                gone.add(item)
            if plan.rows < limit:
                break
        said = {i: {e for e in events if supports(e)} for i, events in evidence.items()}
        touched = {i for i, events in evidence.items() if events & erased}
        assert gone == {i for i in touched if said[i] <= erased}
        assert all(not (events & erased) for events in left.values())


def fact(origin: UUID, value: str, conversation: str = "c1") -> Fact:
    return Fact(
        fact_id=uuid7(),
        origin_handle_id=origin,
        predicate=f"p_{value}",
        value=value,
        category="contact",
        sensitivity=Sensitivity.NORMAL,
        speaker=Speaker.CUSTOMER,
        confidence=0.8,
        valid_at=T0,
        created_at=T0,
        status=FactStatus.ACTIVE,
        last_confirmed_at=T0,
        origin_conversation_id=conversation,
    )


def episode(origin: UUID, conversation: str) -> Episode:
    return Episode(
        episode_id=uuid7(),
        version=1,
        origin_handle_id=origin,
        session_id=uuid7(),
        channel="whatsapp",
        source_id=SOURCE,
        summary="Pediu segunda via",
        intent="support",
        category="invoice",
        normalized_category="billing",
        outcome="resolved",
        started_at=T0,
        ended_at=T0,
        conversation_id=conversation,
    )


def item(origin: UUID, events: list[UUID]) -> OpenItem:
    return OpenItem(
        item_id=uuid7(),
        origin_handle_id=origin,
        description="Enviar segunda via",
        owner="company",
        status=OpenItemStatus.OPEN,
        created_at=T0,
        origin={"session_id": str(uuid7()), "event_ids": [str(e) for e in events]},
    )


def scope(kind: TargetKind, handles: tuple[UUID, ...], **fields: object) -> ErasureScope:
    return ErasureScope(uuid7(), kind, handles, fields.pop("profile_id", None), None, T0, **fields)  # type: ignore[arg-type]


class Memory:
    def __init__(self) -> None:
        self.repo = InMemoryMemoryRepository(MemoryStore())
        self.marina, self.other = uuid7(), uuid7()

    async def add_fact(self, f: Fact, events: list[UUID]) -> Fact:
        assert await self.repo.insert_fact(f, normalize_value(f.value))
        await self.repo.add_mentions(MemoryRef(f.origin_handle_id, f.fact_id), events)
        return f


async def erase_all(repo: InMemoryMemoryRepository, target: ErasureScope, limit: int = 2) -> list[str]:
    tables = []
    while True:
        batch = await repo.erase_for(target, limit=limit)
        tables += [row.table for row in batch]
        if len(batch) < limit:
            return tables


async def test_erased_events_take_their_mentions_orphaned_facts_and_items() -> None:
    m = Memory()
    e1, e2 = uuid7(), uuid7()
    only = await m.add_fact(fact(m.marina, "Marina"), [e1])
    kept = await m.add_fact(fact(m.marina, "Rua A"), [e1, e2])
    await m.repo.add_relations(
        [
            Relation(
                MemoryRef(m.marina, only.fact_id), MemoryRef(m.marina, kept.fact_id), RelationType.SUPERSEDES
            )
        ]
    )
    assert await m.repo.insert_open_item(item(m.marina, [e1]))
    other_item = item(m.marina, [e2])
    assert await m.repo.insert_open_item(other_item)
    tables = await erase_all(m.repo, scope(TargetKind.CONVERSATION, (m.marina,), event_ids=(e1,)))
    assert sorted(tables) == ["fact_mentions", "fact_mentions", "facts", "open_items"]
    [left] = await m.repo.facts_for([m.marina])
    assert (left.fact_id, left.evidence_event_ids) == (kept.fact_id, (e2,))
    assert await m.repo.relations_for([m.marina]) == []
    assert [i.item_id for i in await m.repo.open_items_for([m.marina])] == [other_item.item_id]
    assert await m.repo.erase_for(scope(TargetKind.CONVERSATION, (m.marina,), event_ids=(e1,)), limit=5) == []


async def test_feedback_holds_neither_the_fact_it_names_nor_the_correction_it_wrote() -> None:
    m = Memory()
    said_once, said_twice, retraction, correction = uuid7(), uuid7(), uuid7(), uuid7()
    retracted = await m.add_fact(fact(m.marina, "Rafa"), [said_once])
    fixed = await m.add_fact(fact(m.marina, "Rafaela"), [])
    also_said = await m.add_fact(fact(m.marina, "Rua A"), [said_once, said_twice])
    await m.repo.add_mentions(MemoryRef(m.marina, retracted.fact_id), [retraction, correction], feedback=True)
    await m.repo.add_mentions(MemoryRef(m.marina, fixed.fact_id), [correction], feedback=True)
    # The consolidation copies evidence between rows without knowing its kind: the kind follows the event.
    await m.repo.add_mentions(MemoryRef(m.marina, also_said.fact_id), [correction])
    await m.repo.add_relations(
        [
            Relation(
                MemoryRef(m.marina, fixed.fact_id),
                MemoryRef(m.marina, retracted.fact_id),
                RelationType.SUPERSEDES,
            )
        ]
    )
    tables = await erase_all(m.repo, scope(TargetKind.CONVERSATION, (m.marina,), event_ids=(said_once,)))
    assert sorted(tables).count("facts") == 2
    [left] = await m.repo.facts_for([m.marina], tuple(FactStatus))
    assert (left.fact_id, left.evidence_event_ids) == (
        also_said.fact_id,
        tuple(sorted((said_twice, correction))),
    )
    assert await m.repo.relations_for([m.marina]) == []


async def test_a_profile_erasure_takes_everything_anchored_on_its_handles() -> None:
    m = Memory()
    profile = uuid7()
    await m.add_fact(fact(m.marina, "Marina"), [uuid7()])
    kept = await m.add_fact(fact(m.other, "Bia"), [uuid7()])
    await m.repo.add_episode_version(episode(m.marina, "c1"))
    assert await m.repo.insert_open_item(item(m.marina, [uuid7()]))
    await m.repo.put_attr(ProfileAttr(m.marina, "name", "Marina", SOURCE, T0))
    await m.repo.put_tail_summary(profile, "short", TailState("resumo", 1, T0, 1))
    tables = await erase_all(m.repo, scope(TargetKind.PROFILE, (m.marina,), profile_id=profile))
    assert sorted(set(tables)) == [
        "episodes",
        "fact_mentions",
        "facts",
        "open_items",
        "profile_attrs",
        "tail_summaries",
    ]
    assert [f.fact_id for f in await m.repo.facts_for([m.marina, m.other])] == [kept.fact_id]
    assert await m.repo.episodes_for([m.marina]) == []
    assert await m.repo.tail_summary(profile, "short") is None


async def test_a_conversation_erasure_keeps_the_rest_of_the_memory() -> None:
    m = Memory()
    profile = uuid7()
    await m.repo.add_episode_version(episode(m.marina, "c1"))
    kept = episode(m.marina, "c2")
    await m.repo.add_episode_version(kept)
    await m.repo.put_tail_summary(profile, "long", TailState("resumo", 1, T0, 1))
    target = ErasureScope(uuid7(), TargetKind.CONVERSATION, (m.marina,), profile, "c1", T0)
    assert sorted(r.table for r in await m.repo.erase_for(target, limit=10)) == ["episodes", "tail_summaries"]
    assert [e.episode_id for e in await m.repo.episodes_for([m.marina])] == [kept.episode_id]


async def test_export_reads_changes_in_sequence_order_and_forgets_erased_rows() -> None:
    m = Memory()
    first = await m.add_fact(fact(m.marina, "Marina"), [uuid7()])
    await m.repo.update_fact(first)
    rows = await m.repo.export_rows("facts", after_seq=0, upto_seq=10_000, limit=10)
    assert [r["fact_id"] for r in rows] == [first.fact_id]
    assert (rows[0]["value"], "evidence_event_ids" in rows[0]) == ("Marina", False)
    [mention] = await m.repo.export_rows("fact_mentions", after_seq=0, upto_seq=10_000, limit=10)
    # The update moved the fact past its mention: the export sees the row once, at its latest sequence.
    assert mention["change_seq"] < rows[0]["change_seq"]
    assert await m.repo.export_rows("facts", after_seq=rows[0]["change_seq"], upto_seq=10_000, limit=10) == []
    await erase_all(m.repo, scope(TargetKind.HANDLE, (m.marina,)))
    assert await m.repo.export_rows("facts", after_seq=0, upto_seq=10_000, limit=10) == []
