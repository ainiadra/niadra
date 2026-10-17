"""`PgReviewRepository` against PostgreSQL."""

from datetime import UTC, datetime, timedelta

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.review.sampling import ReviewSampleRecord

T0 = datetime(2026, 9, 22, 10, tzinfo=UTC)


async def test_samples_queue_and_verdicts(pg_uow: PgUnitOfWorkFactory):
    space, session, reviewer_a, reviewer_b = uuid7(), uuid7(), uuid7(), uuid7()
    sample = ReviewSampleRecord(uuid7(), session, "measure_signal", "support", T0)
    later = ReviewSampleRecord(uuid7(), uuid7(), "base", None, T0 + timedelta(minutes=1))
    async with pg_uow(space) as uow:
        await uow.review.add_sample(sample)
        await uow.review.add_sample(later)
        assert await uow.review.has_sample(session)
        assert await uow.review.count_since(T0) == 2
        assert [s.sample_id for s in await uow.review.pending(10)] == [sample.sample_id, later.sample_id]
    first = {"target": "fact", "target_id": "f1", "verdict": "correct", "reviewer_id": str(reviewer_a)}
    second = {"target": "fact", "target_id": "f1", "verdict": "wrong", "reviewer_id": str(reviewer_b)}
    async with pg_uow(space) as uow:
        await uow.review.add_verdicts(sample.sample_id, [first], reviewer_a, T0 + timedelta(hours=1))
        await uow.review.add_verdicts(sample.sample_id, [second], reviewer_b, T0 + timedelta(hours=2))
    async with pg_uow(space) as uow:
        stored = await uow.review.get_sample(sample.sample_id)
        assert stored is not None
        assert (stored.verdicts, stored.reviewer_id) == ((first, second), reviewer_b)
        assert [s.sample_id for s in await uow.review.pending(10)] == [later.sample_id]
        assert await uow.review.verdicts_since(T0) == [first, second]
        # The second-review candidates: judged samples, most recently reviewed first.
        assert [s.sample_id for s in await uow.review.reviewed_since(T0, 10)] == [sample.sample_id]
        assert await uow.review.reviewed_since(T0 + timedelta(hours=3), 10) == []
    async with pg_uow(uuid7()) as other:
        assert not await other.review.has_sample(session)


async def test_a_sample_nobody_judged_is_dropped_and_a_judged_one_stays(pg_uow: PgUnitOfWorkFactory):
    space, reviewer = uuid7(), uuid7()
    empty = ReviewSampleRecord(uuid7(), uuid7(), "base", None, T0)
    judged = ReviewSampleRecord(uuid7(), uuid7(), "base", None, T0 + timedelta(minutes=1))
    async with pg_uow(space) as uow:
        await uow.review.add_sample(empty)
        await uow.review.add_sample(judged)
        verdict = {"target": "fact", "target_id": "f1", "verdict": "correct", "reviewer_id": str(reviewer)}
        await uow.review.add_verdicts(judged.sample_id, [verdict], reviewer, T0 + timedelta(hours=1))
    async with pg_uow(space) as uow:
        await uow.review.drop_sample(empty.sample_id)
        await uow.review.drop_sample(judged.sample_id)
    async with pg_uow(space) as uow:
        assert not await uow.review.has_sample(empty.session_id)
        assert await uow.review.get_sample(judged.sample_id) is not None
