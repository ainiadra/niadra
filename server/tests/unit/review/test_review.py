"""Sampling rates, the reviewer's queue under the purpose mask, verdicts and agreement (22.2)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from niadra.adapters.memory.measure import InMemoryMeasureRepository
from niadra.adapters.memory.review import InMemoryReviewRepository
from niadra.app.review import ReviewService
from niadra.config.models import ReviewSampling
from niadra.contracts.review import VerdictIn, VerdictsRequest
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.extract.result import (
    EpisodeDraft,
    ExtractionResult,
    ExtractionRun,
    FactCommand,
    FactOp,
    RunStatus,
)
from niadra.domain.measure.model import ContextUseRow
from niadra.domain.records import EventRecord, Fact, ProfileRecord, SessionRecord
from niadra.domain.review.sampling import (
    ReviewSampleRecord,
    SamplingPolicy,
    draw,
    sampling_condition,
    should_sample,
    wants_second_review,
)
from niadra.domain.review.verdicts import TargetKind, agreement, scoped, visible_to
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Environment, EventKind, FactStatus, Sensitivity, Speaker, SubjectKind
from niadra.testing.cell import (
    SPACE_ID,
    TENANT_ID,
    VOICE_SOURCE,
    WHATSAPP_SOURCE,
    Cell,
    PatchedUnitOfWorkFactory,
    sample_config,
)
from tests.unit.measure.fakes import FakeExtract, FakeIdentity, FakeIngest, FakeMemory, FakeSessions

T0 = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
PROFILE = UUID("0192f5a0-0000-7000-8000-0000000000e1")
HANDLE = UUID("0192f5a0-0000-7000-8000-0000000000f1")
POLICY = SamplingPolicy(
    base_rate=0.01, boosted_rate=0.1, boost_on=frozenset({"measure_signal", "sensitive"}), daily_cap=3
)


class TestSamplingRules:
    def test_boosted_conditions_raise_the_rate(self):
        assert sampling_condition({"measure_signal", "new_trait"}, POLICY) == "measure_signal"
        assert sampling_condition({"new_trait"}, POLICY) == "base"
        assert should_sample("measure_signal", 0.05, POLICY, 0)
        assert not should_sample("base", 0.05, POLICY, 0)

    def test_the_daily_cap_stops_sampling(self):
        assert not should_sample("measure_signal", 0.0, POLICY, taken_today=3)

    def test_the_draw_is_stable_and_close_to_the_rate(self):
        session = uuid7()
        assert draw(SPACE_ID, session) == draw(SPACE_ID, session)
        hits = sum(should_sample("base", draw(SPACE_ID, uuid7()), POLICY, 0) for _ in range(20000))
        assert 120 < hits < 290  # 1% of 20000, with room for chance


class TestAgreement:
    def test_system_and_reviewer_agreement(self):
        verdicts = [
            {"target": "fact", "target_id": "f1", "verdict": "correct", "reviewer_id": "a"},
            {"target": "fact", "target_id": "f1", "verdict": "wrong", "reviewer_id": "b"},
            {"target": "fact", "target_id": "f2", "verdict": "missing", "reviewer_id": "a"},
            {"target": "signal", "target_id": "s1", "verdict": "holds", "reviewer_id": "a"},
            {"target": "signal", "target_id": "s1", "verdict": "holds", "reviewer_id": "b"},
        ]
        result = agreement(verdicts)
        assert (result["fact"].agreed, result["fact"].judged) == (1, 3)
        assert (result["signal"].reviewer_pairs, result["signal"].reviewer_pairs_agreeing) == (1, 1)
        assert (result["all"].agreed, result["all"].judged, result["all"].reviewer_pairs) == (3, 5, 2)

    def test_each_signal_of_the_measurement_has_its_own_agreement(self):
        # The number next to "asked again" is the agreement on "asked again", not on all signals.
        verdicts = [
            {"target": "signal", "target_id": "m1:f1:repeated", "verdict": "holds", "reviewer_id": "a"},
            {
                "target": "signal",
                "target_id": "m1:f1:repeated",
                "verdict": "does_not_hold",
                "reviewer_id": "b",
            },
            {"target": "signal", "target_id": "m1:f2:used", "verdict": "holds", "reviewer_id": "a"},
            {"target": "signal", "target_id": "legacy", "verdict": "holds", "reviewer_id": "a"},
        ]
        result = agreement(verdicts)
        assert (result["signal:repeated"].agreed, result["signal:repeated"].judged) == (1, 2)
        assert result["signal:repeated"].reviewer_pairs == 1
        assert (result["signal:used"].agreed, result["signal:used"].judged) == (1, 1)
        assert (result["signal"].judged, result["all"].judged, result["all"].reviewer_pairs) == (4, 4, 1)

    def test_a_vendor_reads_the_agreement_on_its_own_agents_only(self):
        mine, theirs = {"source_id": str(VOICE_SOURCE)}, {"source_id": str(WHATSAPP_SOURCE)}
        legacy: dict[str, str] = {}
        verdicts = [mine, theirs, legacy]
        assert list(scoped(verdicts, None)) == verdicts
        assert list(scoped(verdicts, frozenset({VOICE_SOURCE}))) == [mine]
        assert list(scoped(verdicts, None, WHATSAPP_SOURCE)) == [theirs]

    def test_purpose_mask(self):
        purposes = {"billing": frozenset({"billing"}), "contact": frozenset()}
        assert visible_to("support", "contact", purposes)
        assert not visible_to("support", "billing", purposes)
        assert not visible_to(None, "billing", purposes)


class World:
    def __init__(self, cell: Cell, **sampling: object) -> None:
        config = sample_config()
        billing_only = [
            c.model_copy(update={"purposes": frozenset({"billing"})}) if c.name == "billing" else c
            for c in config.policy.categories
        ]
        policy = config.policy.model_copy(update={"categories": billing_only})
        cell.config.put(
            config.model_copy(update={"review_sampling": ReviewSampling(**sampling), "policy": policy})
        )
        self.cell = cell
        self.sessions = FakeSessions()
        self.extract = FakeExtract()
        self.ingest = FakeIngest()
        self.identity = FakeIdentity(
            {PROFILE: ProfileRecord(PROFILE, SubjectKind.PERSON, "p", 1, 1, (HANDLE,))}
        )
        self.memory = FakeMemory()
        uow = PatchedUnitOfWorkFactory(
            cell.uow,
            sessions=self.sessions,
            extract=self.extract,
            ingest=self.ingest,
            identity=self.identity,
            memory=self.memory,
        )
        self.service = ReviewService(uow, cell.clock, cell.config)

    def session(self, source: UUID = VOICE_SOURCE, *, said: bool = True) -> SessionRecord:
        """A closed session; with `said`, the customer said something in it that became a fact."""
        record = SessionRecord(
            uuid7(),
            "conversation",
            source,
            "voice",
            T0,
            T0 + timedelta(minutes=5),
            conversation_id=f"c-{uuid7()}",
            profile_id=PROFILE,
            closed_at=T0 + timedelta(minutes=5),
        )
        self.sessions.sessions[record.session_id] = record
        if said:
            event = _said(record)
            self.ingest.events.append(event)
            self.memory.facts.append(_fact("preferred_name", "Marina", "contact", event.event_id))
        return record

    async def sample(self, session: SessionRecord, **payload: object) -> None:
        await self.service.sample_review(
            Task(TaskName.SAMPLE_REVIEW, SPACE_ID, {"session_id": str(session.session_id), **payload})
        )

    def repo(self) -> InMemoryReviewRepository:
        return InMemoryReviewRepository(self.cell.uow.store)


def person(*, sources: frozenset[UUID] | None = None) -> PersonPrincipal:
    return PersonPrincipal(
        SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX),
        uuid7(),
        frozenset({"review"}),
        allowed_sources=sources,
    )


async def test_every_session_is_sampled_at_rate_one_and_only_once(cell: Cell):
    world = World(cell, base_rate=0.1, boosted_rate=1.0, boost_on=frozenset({"measure_signal"}))
    session = world.session()
    await world.sample(session, measure_signal=True)
    await world.sample(session, measure_signal=True)
    [sample] = await world.repo().pending(10)
    assert (sample.condition, sample.purpose) == ("measure_signal", "support")


async def test_a_sensitive_category_in_the_extraction_boosts_the_session(cell: Cell):
    world = World(cell, base_rate=0.0, boosted_rate=1.0)
    session = world.session()
    fact = FactCommand(FactOp.ADD, "condition", (uuid7(),), value="x", category="health")
    result = ExtractionResult(EpisodeDraft("s", "support", "health", "resolved"), facts=(fact,))
    world.extract.runs[session.session_id] = ExtractionRun(
        session.session_id, "x1", RunStatus.OK, T0, result=result
    )
    await world.sample(session)
    [sample] = await world.repo().pending(10)
    assert sample.condition == "sensitive"


async def test_the_daily_cap_is_respected(cell: Cell):
    world = World(cell, base_rate=0.0, boosted_rate=1.0, daily_cap=2)
    for _ in range(4):
        await world.sample(world.session(), measure_signal=True)
    assert await world.repo().count_since(T0 - timedelta(days=1)) == 2


async def test_at_rate_zero_nothing_is_sampled(cell: Cell):
    world = World(cell, base_rate=0.0, boosted_rate=0.0)
    await world.sample(world.session(), measure_signal=True)
    assert await world.repo().pending(10) == []


def _said(session: SessionRecord) -> EventRecord:
    return EventRecord(
        uuid7(),
        EventKind.MESSAGE,
        VOICE_SOURCE,
        "voice",
        Speaker.CUSTOMER,
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        (HANDLE,),
        text="...",
        conversation_id=session.conversation_id,
    )


async def test_a_session_that_left_nothing_to_judge_is_not_sampled(cell: Cell):
    world = World(cell, base_rate=0.0, boosted_rate=1.0)
    await world.sample(world.session(said=False), measure_signal=True)
    nobody = world.session(said=False)
    world.sessions.sessions[nobody.session_id] = replace(nobody, profile_id=None)
    await world.sample(nobody, measure_signal=True)
    assert await world.repo().pending(10) == []
    # It did not count against the day's cap either.
    assert await world.repo().count_since(T0 - timedelta(days=1)) == 0


async def test_a_sample_with_nothing_left_to_judge_leaves_the_queue(cell: Cell):
    world = World(cell, base_rate=0.0, boosted_rate=1.0)
    kept, emptied = world.session(), world.session()
    await world.sample(kept, measure_signal=True)
    await world.sample(emptied, measure_signal=True)
    # Sampled before empty sessions were skipped: a session that left nothing, of nobody.
    old = ReviewSampleRecord(uuid7(), world.session(said=False).session_id, "base", "support", T0)
    await world.repo().add_sample(old)
    # The customer's memory of the second conversation went away after it was sampled.
    [gone] = [e.event_id for e in world.ingest.events if e.conversation_id == emptied.conversation_id]
    world.memory.facts = [f for f in world.memory.facts if gone not in f.evidence_event_ids]
    assert len(await world.repo().pending(10)) == 3

    [item] = (await world.service.queue(person())).items

    assert item.session_id == kept.session_id
    assert [s.session_id for s in await world.repo().pending(10)] == [kept.session_id]
    # The session can be sampled again once it has something to show.
    assert not await world.repo().has_sample(emptied.session_id)


async def test_a_vendor_does_not_empty_the_queue_of_sources_it_cannot_see(cell: Cell):
    world = World(cell, base_rate=0.0, boosted_rate=1.0)
    old = ReviewSampleRecord(uuid7(), world.session(said=False).session_id, "base", "support", T0)
    await world.repo().add_sample(old)
    assert (await world.service.queue(person(sources=frozenset({WHATSAPP_SOURCE})))).items == []
    assert await world.repo().pending(10) == [old]


async def queued(world: World) -> SessionRecord:
    session = world.session(said=False)
    event = _said(session)
    world.ingest.events.append(event)
    world.memory.facts = [
        _fact("preferred_name", "Marina", "contact", event.event_id),
        _fact("disputed_invoice", "0823", "billing", event.event_id),
        _fact("condition", "x", "health", event.event_id, Sensitivity.SENSITIVE),
        _fact("rumor", "y", "undeclared_gossip", event.event_id),
        _fact("preferred_name", "Old", "contact", uuid7()),
    ]
    await InMemoryMeasureRepository(world.cell.uow.store).insert_rows(
        [
            ContextUseRow(
                conversation_key=session.conversation_id or "",
                manifest_hash="m1",
                source_id=VOICE_SOURCE,
                measure_version="m1/x",
                session_id=session.session_id,
                profile_id=PROFILE,
                channel="voice",
                view="voice",
                experiment_group="",
                items=({"id": "f1", "kind": "fact", "used": True, "repeated": False, "contradicted": False},),
                questions=0,
                repeated=0,
                used=1,
                contradicted=0,
                late=False,
                measured_at=T0,
            )
        ]
    )
    await world.sample(session, measure_signal=True)
    return session


async def test_the_queue_shows_facts_of_the_session_under_the_purpose_mask(cell: Cell):
    world = World(cell, base_rate=0.0, boosted_rate=1.0)
    session = await queued(world)
    [item] = (await world.service.queue(person())).items
    assert item.session_id == session.session_id
    # The reviewer reads under the space's policy, as the Console does: billing is limited to other
    # purposes, the health condition is sensitive and no rule releases it, and a category the policy
    # never declared is released to nobody.
    assert {f.predicate for f in item.facts} == {"preferred_name"}
    assert item.withheld == 3
    assert [s.target_id for s in item.signals] == ["m1:f1:used"]


async def test_a_fraction_of_judged_samples_goes_to_a_second_reviewer(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
):
    # The queue offered only samples nobody judged, so agreement between reviewers had no pairs.
    monkeypatch.setattr("niadra.domain.review.sampling.SECOND_REVIEW_FRACTION", 1.0)
    world = World(cell, base_rate=0.0, boosted_rate=1.0)
    await queued(world)
    first, second = person(), person()
    [item] = (await world.service.queue(first)).items
    signal = item.signals[0].target_id
    verdict = VerdictsRequest(
        verdicts=[VerdictIn(target=TargetKind.SIGNAL, target_id=signal, verdict="holds")]
    )
    await world.service.record_verdicts(first, item.sample_id, verdict)
    assert (await world.service.queue(first)).items == []  # never back to who judged it
    [again] = (await world.service.queue(second)).items
    assert again.sample_id == item.sample_id
    await world.service.record_verdicts(second, again.sample_id, verdict)
    assert (await world.service.queue(person())).items == []  # two opinions are enough
    report = {t.target: t for t in (await world.service.agreement(first)).targets}
    assert report["signal:used"].reviewer_pairs == 1
    assert report["signal:used"].reviewer_agreement is not None


def test_the_second_review_fraction_is_a_stable_draw_per_sample() -> None:
    reviewer = uuid7()
    samples = [
        ReviewSampleRecord(uuid7(), uuid7(), "base", None, T0, verdicts=({"reviewer_id": str(reviewer)},))
        for _ in range(2000)
    ]
    offered = [s for s in samples if wants_second_review(s, uuid7())]
    assert 0.05 < len(offered) / len(samples) < 0.15
    assert all(wants_second_review(s, uuid7()) for s in offered)
    assert not any(wants_second_review(s, reviewer) for s in samples)


async def test_a_vendor_reviewer_only_sees_its_sources(cell: Cell):
    world = World(cell, base_rate=0.0, boosted_rate=1.0)
    await queued(world)
    assert (await world.service.queue(person(sources=frozenset({WHATSAPP_SOURCE})))).items == []


async def test_verdicts_are_recorded_and_never_touch_memory(cell: Cell):
    world = World(cell, base_rate=0.0, boosted_rate=1.0)
    await queued(world)
    reviewer = person()
    [item] = (await world.service.queue(reviewer)).items
    before = list(world.memory.facts)
    request = VerdictsRequest(
        verdicts=[
            VerdictIn(target=TargetKind.FACT, target_id=str(item.facts[0].fact_id), verdict="wrong"),
            VerdictIn(target=TargetKind.SIGNAL, target_id=item.signals[0].target_id, verdict="holds"),
        ]
    )
    response = await world.service.record_verdicts(reviewer, item.sample_id, request)

    assert response.recorded == 2
    assert world.memory.facts == before
    assert cell.uow.store.module("pipeline").get("outbox", []) == []
    assert (await world.service.queue(reviewer)).items == []
    report = await world.service.agreement(reviewer)
    by_target = {t.target: t for t in report.targets}
    assert (by_target["all"].agreed, by_target["all"].judged) == (1, 2)
    assert (by_target["signal:used"].agreed, by_target["signal:used"].judged) == (1, 1)
    # The verdicts name the sample's source: a vendor of another agent reads none of them.
    assert (await world.service.agreement(person(sources=frozenset({WHATSAPP_SOURCE})))).targets == []
    only_voice = await world.service.agreement(reviewer, source_id=VOICE_SOURCE)
    assert {t.target for t in only_voice.targets} == set(by_target)
    with pytest.raises(NotFound):
        await world.service.agreement(person(sources=frozenset({WHATSAPP_SOURCE})), source_id=VOICE_SOURCE)


async def test_invalid_or_foreign_verdicts_are_refused(cell: Cell):
    world = World(cell, base_rate=0.0, boosted_rate=1.0)
    await queued(world)
    [item] = (await world.service.queue(person())).items
    with pytest.raises(InvalidInput):
        await world.service.record_verdicts(
            person(),
            item.sample_id,
            VerdictsRequest(verdicts=[VerdictIn(target=TargetKind.FACT, target_id="x", verdict="holds")]),
        )
    with pytest.raises(NotFound):
        await world.service.record_verdicts(
            person(sources=frozenset({WHATSAPP_SOURCE})),
            item.sample_id,
            VerdictsRequest(verdicts=[VerdictIn(target=TargetKind.FACT, target_id="x", verdict="correct")]),
        )


def _fact(
    predicate: str, value: str, category: str, evidence: UUID, sensitivity: Sensitivity = Sensitivity.NORMAL
) -> Fact:
    return Fact(
        uuid7(),
        HANDLE,
        predicate,
        value,
        category,
        sensitivity,
        Speaker.CUSTOMER,
        0.9,
        T0,
        T0,
        FactStatus.ACTIVE,
        T0,
        evidence_event_ids=(evidence,),
    )


async def test_what_is_new_boosts_the_session(cell: Cell):
    # A new source, a new extractor and a new pattern raise the rate, not only the measurement.
    from niadra.app.extract import extractor_key
    from niadra.domain.ingest.coverage import SourceDay
    from niadra.domain.records import Trait

    world = World(cell, base_rate=0.0, boosted_rate=1.0)
    now = cell.clock.now()
    first = world.session()
    world.ingest.days[VOICE_SOURCE] = [SourceDay((now - timedelta(days=3)).date(), 5, 5, 0, 0, None)]
    await world.sample(first)
    assert [s.condition for s in await world.repo().pending(10)] == ["new_source"]

    world.ingest.days[VOICE_SOURCE].insert(0, SourceDay((now - timedelta(days=28)).date(), 1, 1, 0, 0, None))
    second = world.session()
    key = extractor_key(cell.config.space(SPACE_ID))
    world.extract.runs[second.session_id] = ExtractionRun(second.session_id, key, RunStatus.OK, T0)
    world.extract.changed_at = now - timedelta(days=2)
    await world.sample(second)
    third = world.session()
    world.extract.runs[third.session_id] = ExtractionRun(third.session_id, "old", RunStatus.OK, T0)
    async with cell.uow(SPACE_ID) as uow:
        trait = Trait(uuid7(), PROFILE, 1, "prefers_whatsapp", "yes", "stable", "r1", now, now, now, 0.8)
        await uow.traits.save_trait(trait, [])
    await world.sample(third)
    conditions = {s.session_id: s.condition for s in await world.repo().pending(10)}
    assert conditions[second.session_id] == "new_extractor"
    assert conditions[third.session_id] == "new_trait"


async def test_the_queue_shows_the_items_promises_and_patterns_of_the_session(cell: Cell):
    # The reviewer judges pending items, promises and patterns too, not only facts and signals.
    from niadra.domain.records import Episode, OpenItem, Trait
    from niadra.domain.traits.evidence import EvidenceRef
    from niadra.domain.vocabulary import OpenItemStatus

    world = World(cell, base_rate=0.0, boosted_rate=1.0)
    session = await queued(world)
    event = world.ingest.events[0].event_id
    episode = Episode(
        uuid7(),
        1,
        HANDLE,
        session.session_id,
        "voice",
        VOICE_SOURCE,
        "s",
        "support",
        "visit",
        "visit",
        "open",
        T0,
        T0,
    )
    world.memory.episodes = [episode]
    origin = {"session_id": str(session.session_id), "event_ids": [str(event)]}
    world.memory.open_items = [
        OpenItem(uuid7(), HANDLE, "Retornar a ligacao amanha", "company", OpenItemStatus.OPEN, T0, origin),
        OpenItem(uuid7(), HANDLE, "Enviar o comprovante", "customer", OpenItemStatus.OPEN, T0, origin),
        OpenItem(
            uuid7(), HANDLE, "De outra conversa", "company", OpenItemStatus.OPEN, T0, {"session_id": "x"}
        ),
    ]
    async with cell.uow(SPACE_ID) as uow:
        here = Trait(uuid7(), PROFILE, 1, "recurring_visit", "yes", "volatile", "r1", T0, T0, T0, 0.8)
        await uow.traits.save_trait(here, [EvidenceRef("episode", episode.episode_id, HANDLE)])
        other = Trait(uuid7(), PROFILE, 1, "night_caller", "yes", "volatile", "r1", T0, T0, T0, 0.8)
        await uow.traits.save_trait(other, [EvidenceRef("episode", uuid7(), HANDLE)])
    [item] = (await world.service.queue(person())).items
    assert [(i.description, i.promise) for i in item.open_items] == [
        ("Retornar a ligacao amanha", True),
        ("Enviar o comprovante", False),
    ]
    assert item.open_items[0].evidence_event_ids == [event]
    [pattern] = item.traits
    assert pattern.name == "recurring_visit"
    assert [(e.kind, e.id) for e in pattern.evidence] == [("episode", str(episode.episode_id))]

    reviewer = person()
    request = VerdictsRequest(
        verdicts=[
            VerdictIn(
                target=TargetKind.OPEN_ITEM, target_id=str(item.open_items[0].item_id), verdict="correct"
            ),
            VerdictIn(target=TargetKind.TRAIT, target_id=str(pattern.trait_id), verdict="does_not_hold"),
        ]
    )
    assert (await world.service.record_verdicts(reviewer, item.sample_id, request)).recorded == 2
    by_target = {t.target: t for t in (await world.service.agreement(reviewer)).targets}
    assert (by_target["open_item"].agreed, by_target["trait"].agreed) == (1, 0)


async def test_every_verdict_runs_as_a_test_against_todays_memory_and_measurement(cell: Cell):
    # A verdict is a case of the space; a new measurement that answers otherwise fails it at once.
    from dataclasses import replace as changed

    world = World(cell, base_rate=0.0, boosted_rate=1.0)
    await queued(world)
    reviewer = person()
    [item] = (await world.service.queue(reviewer)).items
    request = VerdictsRequest(
        verdicts=[
            VerdictIn(target=TargetKind.SIGNAL, target_id="m1:f1:used", verdict="holds"),
            VerdictIn(target=TargetKind.FACT, target_id=str(item.facts[0].fact_id), verdict="wrong"),
        ]
    )
    await world.service.record_verdicts(reviewer, item.sample_id, request)

    cases = await world.service.cases(reviewer)
    tallies = {t.target: (t.cases, t.passing, t.failing) for t in cases.tallies}
    assert tallies == {"signal:used": (1, 1, 0), "fact": (1, 0, 1)}
    [failing] = cases.failing
    assert (failing.target, failing.now, failing.passes) == ("fact", "active", False)

    # New rules measure the same pack again and stop marking the item as used: the case fails now.
    measure = InMemoryMeasureRepository(cell.uow.store)
    [row] = await measure.rows_for_session(item.session_id)
    await measure.delete_rows([row])
    await measure.insert_rows(
        [changed(row, measure_version="m2/x", items=({**row.items[0], "used": False},))]
    )
    tallies = {t.target: (t.passing, t.failing) for t in (await world.service.cases(reviewer)).tallies}
    assert tallies["signal:used"] == (0, 1)
    # A vendor's reviewer runs only the cases of its own agents.
    assert (await world.service.cases(person(sources=frozenset({WHATSAPP_SOURCE})))).tallies == []
