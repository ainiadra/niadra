"""The reviewed agreement beside the context-use numbers: same sources, the last 90 days."""

from datetime import timedelta

from niadra.app.measure import MeasureService
from niadra.domain.common.ids import uuid7
from niadra.domain.review.sampling import ReviewSampleRecord
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal


async def reviewed(cell: Cell, days_ago: int, source: object, verdict: str) -> None:
    at = cell.clock.now() - timedelta(days=days_ago)
    async with cell.uow(SPACE_ID) as uow:
        sample = ReviewSampleRecord(uuid7(), uuid7(), "measure_signal", "support", at)
        await uow.review.add_sample(sample)
        entry = {
            "target": "signal",
            "target_id": f"m{days_ago}:item:repeated",
            "verdict": verdict,
            "reviewer_id": "r1",
            "source_id": str(source),
            "at": at.isoformat(),
        }
        await uow.review.add_verdicts(sample.sample_id, [entry], uuid7(), at)


async def test_a_long_report_carries_the_verdicts_of_its_last_90_days_on_its_sources(cell: Cell) -> None:
    await reviewed(cell, 10, WHATSAPP_SOURCE, "holds")
    await reviewed(cell, 20, WHATSAPP_SOURCE, "does_not_hold")
    await reviewed(cell, 200, WHATSAPP_SOURCE, "holds")  # inside the period, outside the 90 days
    await reviewed(cell, 15, VOICE_SOURCE, "holds")  # another vendor's agent
    service = MeasureService(cell.uow, cell.clock, cell.config)
    today = cell.clock.now().date()

    report = await service.context_use(principal(WHATSAPP_SOURCE), since=today - timedelta(days=399))
    [repeated] = [r for r in report.review if r.signal == "repeated"]
    assert (repeated.judged, repeated.confirmed) == (2, 1)
    assert repeated.agreement is not None
    assert repeated.agreement.value == 0.5

    week = await service.context_use(principal(WHATSAPP_SOURCE), since=today - timedelta(days=12))
    assert [(r.signal, r.judged) for r in week.review if r.judged] == [("repeated", 1)]


def test_a_signal_is_contract_ready_once_review_agrees_with_90_percent_at_its_lower_bound() -> None:
    # 200 verdicts of which 196 confirm the mark: the Wilson lower bound is above 0.90.
    from niadra.app.measure import _signal_review

    def verdicts(signal: str, holds: int, not_holds: int) -> list[dict[str, object]]:
        return [
            {
                "target": "signal",
                "target_id": f"s{n}:{signal}",
                "reviewer_id": "r1",
                "verdict": "holds" if n < holds else "does_not_hold",
            }
            for n in range(holds + not_holds)
        ]

    review = {r.signal: r for r in _signal_review([*verdicts("used", 196, 4), *verdicts("repeated", 9, 1)])}
    assert review["used"].contract_ready
    assert not review["repeated"].contract_ready  # 90% of ten verdicts is not enough evidence
    assert not review["contradicted"].contract_ready


def test_the_verdicts_calibrate_the_count_they_judge() -> None:
    # 40 marks of "asked again", reviewers confirmed 3 of 4: about 30 hold, within the interval.
    from niadra.app.measure import _signal_review
    from niadra.domain.measure.model import DailyCounters

    verdicts = [
        {"target": "signal", "target_id": f"m:i{n}:repeated", "reviewer_id": "r1", "verdict": v}
        for n, v in enumerate(["holds", "holds", "holds", "does_not_hold"])
    ]
    review = {r.signal: r for r in _signal_review(verdicts, DailyCounters(repeated=40, contradicted=7))}
    repeated = review["repeated"]
    assert repeated.measured == 40
    assert repeated.calibrated is not None
    assert repeated.calibrated.value == 30.0
    assert repeated.calibrated.low < 30.0 < repeated.calibrated.high <= 40.0
    assert (review["contradicted"].measured, review["contradicted"].calibrated) == (7, None)
