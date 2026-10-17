"""The nightly similarity band in the consolidation: it draws candidate pairs between
free-text facts of one predicate; the pairs the decision model confirms become `CONTRADICTS` edges of origin
`band` and send the conversation to review. It never changes a status, then or when the facts are settled
again, and never pairs a row of an unconfirmed link (5.3)."""

import math
from dataclasses import replace
from datetime import timedelta

import pytest

from niadra.adapters.memory.fakes import DeterministicDecider
from niadra.config.models import PredicateSpec
from niadra.domain.decide import pair
from niadra.domain.extract.result import ExtractionResult
from niadra.domain.memory.contradictions import BandPair
from niadra.domain.memory.facts import Relation, ref
from niadra.domain.memory.vectors import PendingVector
from niadra.domain.records import Fact, ProfileRecord, SessionRecord
from niadra.domain.review.sampling import CONTRADICTION_BAND
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import FactStatus, HandleType, RelationType, Sensitivity, Speaker
from niadra.ports.infra import Label
from niadra.testing.cell import SPACE_ID, Cell, sample_config
from tests.unit.extract.world import Services, World, services
from tests.unit.memory.test_service import EPISODE, add, configure, facts, pipeline, reconcile, setup

MORNING = "prefere ser atendida pela gerente"
APP = "quer resolver tudo pelo aplicativo"
MODEL = "encoder-test"


def at(similarity: float) -> list[float]:
    return [similarity, math.sqrt(1 - similarity**2)]


async def state(s: Services, profile: ProfileRecord, text: str, *, days_ago: int) -> SessionRecord:
    session = s.world.session(
        profile, [(Speaker.CUSTOMER, text)], start=s.cell.clock.now() - timedelta(days=days_ago)
    )
    [said] = s.world.session_events(session)
    await reconcile(s, session, ExtractionResult(episode=EPISODE, facts=(add(text, said, "note"),)))
    return session


async def with_vector(s: Services, fact: Fact, vector: list[float]) -> None:
    async with s.cell.uow(SPACE_ID) as uow:
        assert await uow.memory.set_vector(PendingVector(fact), vector, MODEL)


async def night(s: Services, profile: ProfileRecord) -> None:
    await s.memory.consolidate(Task(TaskName.CONSOLIDATE, SPACE_ID, profile_id=profile.profile_id))


async def relations(s: Services, profile: ProfileRecord) -> list[Relation]:
    async with s.cell.uow(SPACE_ID) as uow:
        return await uow.memory.relations_for(profile.handle_ids)


async def test_a_pair_in_the_band_is_drawn_sampled_and_never_retracts(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    await state(s, profile, MORNING, days_ago=60)
    later = await state(s, profile, APP, days_ago=2)
    found = await facts(s, profile.handle_ids)
    old, new = found[MORNING], found[APP]
    await with_vector(s, old, [1.0, 0.0])
    await with_vector(s, new, at(0.6))
    pipeline(cell).take()

    await night(s, profile)

    assert await relations(s, profile) == [
        Relation(ref(new), ref(old), RelationType.CONTRADICTS, origin="band")
    ]
    after = await facts(s, profile.handle_ids)
    assert {f.status for f in after.values()} == {FactStatus.ACTIVE}
    [sample] = pipeline(cell).take(TaskName.SAMPLE_REVIEW)
    assert sample.payload == {"session_id": str(later.session_id), "conditions": [CONTRADICTION_BAND]}
    # A second night draws nothing new.
    await night(s, profile)
    assert len(await relations(s, profile)) == 1
    assert pipeline(cell).take(TaskName.SAMPLE_REVIEW) == []


async def test_a_band_edge_never_settles_the_older_fact_away(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    await state(s, profile, MORNING, days_ago=60)
    await state(s, profile, APP, days_ago=2)
    found = await facts(s, profile.handle_ids)
    await with_vector(s, found[MORNING], [1.0, 0.0])
    await with_vector(s, found[APP], at(0.6))
    await night(s, profile)
    # The newer value is said again: its group is settled, and a model's `contradicts` would expire the
    # older one by recency. The band's edge takes no part.
    await state(s, profile, APP, days_ago=1)
    after = await facts(s, profile.handle_ids)
    assert after[MORNING].status is FactStatus.ACTIVE
    assert after[APP].status is FactStatus.ACTIVE


async def test_a_single_valued_predicate_and_an_unconfirmed_link_never_enter(cell: Cell) -> None:
    s = setup(cell)
    config = sample_config()
    single = [PredicateSpec(name="note", category="contact", single_valued=True)]
    cell.config.put(
        config.model_copy(update={"extraction": config.extraction.model_copy(update={"predicates": single})})
    )
    profile = s.world.customer()
    await state(s, profile, MORNING, days_ago=60)
    await state(s, profile, APP, days_ago=2)
    found = await facts(s, profile.handle_ids)
    await with_vector(s, found[MORNING], [1.0, 0.0])
    await with_vector(s, found[APP], at(0.6))
    await night(s, profile)
    assert not [r for r in await relations(s, profile) if r.origin == "band"]

    s = setup(cell)
    base = s.world.customer(HandleType.PHONE_E164, HandleType.EMAIL)
    phone, email = base.handle_ids
    claimed = replace(base, handle_ids=(email, phone), provisional_handle_ids=(email,))
    s.world.profiles[claimed.profile_id] = claimed
    await state(s, base, MORNING, days_ago=60)
    # The claimed e-mail's owner writes from it: her row stays with the link.
    await state(s, replace(base, handle_ids=(email,)), APP, days_ago=2)
    found = await facts(s, claimed.handle_ids)
    assert found[APP].origin_handle_id == email
    await with_vector(s, found[MORNING], [1.0, 0.0])
    await with_vector(s, found[APP], at(0.6))
    await night(s, claimed)
    assert await relations(s, claimed) == []


def judged(label: str, probability: float) -> DeterministicDecider:
    return DeterministicDecider({"contradiction": Label(label, probability, "decision-model-1")})


async def drawn_pair(cell: Cell, decider: DeterministicDecider) -> tuple[Services, ProfileRecord]:
    configure(cell)
    s = services(cell, World(now=cell.clock.now()), decider=decider)
    profile = s.world.customer()
    await state(s, profile, MORNING, days_ago=60)
    await state(s, profile, APP, days_ago=2)
    found = await facts(s, profile.handle_ids)
    await with_vector(s, found[MORNING], [1.0, 0.0])
    await with_vector(s, found[APP], at(0.6))
    pipeline(cell).take()
    return s, profile


async def test_a_pair_the_model_calls_compatible_leaves_no_edge_and_is_not_asked_again(cell: Cell) -> None:
    decider = judged("compatible", 0.93)
    s, profile = await drawn_pair(cell, decider)

    await night(s, profile)

    # The pair's two values, after the transaction.
    assert decider.calls == [("contradiction", [pair(MORNING, APP)])]
    assert await relations(s, profile) == []
    assert pipeline(cell).take(TaskName.SAMPLE_REVIEW) == []
    await night(s, profile)
    assert len(decider.calls) == 1


async def test_a_pair_the_model_confirms_becomes_the_band_s_edge(cell: Cell) -> None:
    s, profile = await drawn_pair(cell, judged("compatible", 0.1))
    await night(s, profile)
    assert [(r.type, r.origin) for r in await relations(s, profile)] == [(RelationType.CONTRADICTS, "band")]
    assert len(pipeline(cell).take(TaskName.SAMPLE_REVIEW)) == 1


@pytest.mark.parametrize("answer", [Label("compatible", 0.7, "decision-model-1"), None])
async def test_a_pair_the_model_is_unsure_of_or_did_not_answer_keeps_its_warning(
    cell: Cell, answer: Label | None
) -> None:
    s, profile = await drawn_pair(cell, DeterministicDecider({"contradiction": answer}))
    await night(s, profile)
    assert [r.origin for r in await relations(s, profile)] == ["band"]


async def test_a_sensitive_value_never_reaches_the_model_and_keeps_its_warning(cell: Cell) -> None:
    decider = judged("compatible", 0.93)
    s, profile = await drawn_pair(cell, decider)
    found = await facts(s, profile.handle_ids)
    held = replace(found[APP], sensitivity=Sensitivity.SENSITIVE)
    confirmed = await s.memory._confirm_band(SPACE_ID, [BandPair(held, found[MORNING], 0.6, opposed=False)])
    assert decider.calls == []
    assert [p.newer for p in confirmed] == [held]
