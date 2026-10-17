"""The statistics of experiments and of the tool counterfactual: overlap@k against the
open spec's vectors, a run added up with its limits, CUPED, and the mixture sequential test, whose false
positive rate stays within its nominal rate when the company looks every day."""

import math
import random

import pytest

from niadra.domain.measure.counterfactual import Case, overlap_at_k, summarize
from niadra.domain.measure.stats import (
    Moments,
    compare,
    mixing_tau2,
    msprt,
    needed,
    of_pairs,
    per_person_variance,
    theta,
)
from tests.spec import cases

OVERLAP = cases("counterfactual-overlap", {"description", "a", "b", "k"})


@pytest.mark.parametrize("case", OVERLAP, ids=lambda c: c["id"])
def test_overlap_at_k_matches_the_spec_vectors_both_ways(case: dict[str, object]) -> None:
    a, b, k = case["a"], case["b"], case["k"]
    assert isinstance(a, list)
    assert isinstance(b, list)
    assert isinstance(k, int)
    expected = case["expect"]["overlap"]  # type: ignore[index]
    assert overlap_at_k(a, b, k) == pytest.approx(expected, abs=1e-6)
    assert overlap_at_k(b, a, k) == pytest.approx(expected, abs=1e-6)


def test_a_run_adds_its_cases_up_against_their_noise_and_says_what_holds_it_back() -> None:
    changed = [Case("completed", 10, 0.4, 0.98, engaged=((2, 12), (5, 3))) for _ in range(30)]
    same = [Case("completed", 10, 1.0, 1.0, engaged=((None, 1),)) for _ in range(5)]
    skipped = [Case("no_dry_run"), Case("tool_error")]
    run = summarize([*changed, *same, *skipped], "size")
    assert (run.cases, run.completed, run.skipped) == (37, 35, {"no_dry_run": 1, "tool_error": 1})
    assert (run.below_noise, run.above_noise, run.ties) == (30, 0, 5)
    assert run.overlap == pytest.approx((30 * 0.4 + 5) / 35, abs=1e-6)
    assert run.effect == pytest.approx(run.noise_floor - run.overlap, abs=1e-6)  # type: ignore[operator]
    assert run.p_value is not None
    assert run.p_value < 1e-6
    engaged = run.engaged
    assert (engaged.items, engaged.shown, engaged.kept, engaged.lost, engaged.gained) == (65, 60, 30, 30, 5)
    assert engaged.mean_shift == pytest.approx((30 * 10 + 30 * -2) / 60)
    assert run.limits == ["not_quality", "model_reaction_not_measured", "cases_skipped"]
    hard = summarize([Case("completed", 10, 0.2, 0.7, dry_run=True)], "hard")
    assert hard.limits == [
        "not_quality", "model_reaction_not_measured", "trivial_for_hard", "few_cases", "noisy_tool", "dry_run"
    ]  # fmt: skip
    assert summarize([Case("infrastructure_error")], "exclude").p_value is None


def people(rng: random.Random, n: int, lift: float) -> Moments:
    """`n` people whose history (`x`) says much about their outcome (`y`)."""
    pairs = []
    for _ in range(n):
        x = rng.gauss(100, 30)
        pairs.append((x * (1 + lift) + rng.gauss(0, 10), x))
    return of_pairs(pairs)


def test_cuped_keeps_the_difference_and_shrinks_its_variance() -> None:
    rng = random.Random(3)
    control, arm = people(rng, 2000, 0.0), people(rng, 2000, 0.05)
    plain = compare(control, arm, None)
    adjusted = compare(control, arm, theta([control, arm]))
    assert plain is not None
    assert adjusted is not None
    assert adjusted.difference == pytest.approx(5.0, abs=1.5)
    assert adjusted.variance < plain.variance / 5
    assert adjusted.reduction is not None
    assert adjusted.reduction > 0.8
    assert plain.reduction is None
    assert compare(people(rng, 10, 0.0), arm, None) is None  # too few people to compare


def test_the_interval_is_what_the_test_does_not_reject() -> None:
    test = msprt(0.02, 0.0001, 0.0004, 0.05)
    assert test is not None
    inside = msprt(0.02 - (test.ci_low + 0.001), 0.0001, 0.0004, 0.05)
    edge = msprt(0.02 - test.ci_low, 0.0001, 0.0004, 0.05)
    assert inside is not None
    assert inside.p_value > 0.05
    assert edge is not None
    assert edge.p_value == pytest.approx(0.05, rel=1e-6)
    assert msprt(0.0, 0.0, 0.1, 0.05) is None


def _day(rng: random.Random, n: int, lift: float) -> Moments:
    """One day's new people of one arm, as the cells of (history, converted): 20% converted before, and those
    convert more again."""
    had = rng.binomialvariate(n, 0.2)
    again = rng.binomialvariate(had, 0.18 * (1 + lift))
    new = rng.binomialvariate(n - had, 0.08 * (1 + lift))
    y = again + new
    return Moments(n, y, y, had, had, again)


def _looked_every_day(
    runs: int, lift: float, *, sequential: bool = True, days: int = 30, per_day: int = 100, seed: int = 11
) -> float:
    """The share of experiments some daily look declared decided, at alpha 0.05."""
    rng = random.Random(seed)
    decided = 0
    for _ in range(runs):
        control = arm = Moments()
        best = 1.0
        for _ in range(days):
            control = control + _day(rng, per_day, 0.0)
            arm = arm + _day(rng, per_day, lift)
            coefficient = theta([control, arm])
            estimate = compare(control, arm, coefficient)
            if estimate is None:
                continue
            if not sequential:
                if abs(estimate.difference) / math.sqrt(estimate.variance) > 1.959964:
                    best = 0.0
                continue
            tau2 = mixing_tau2(0.01, per_person_variance(control, arm, coefficient))
            test = msprt(estimate.difference, estimate.variance, tau2, 0.05)
            if test is not None:
                best = min(best, test.p_value)
        decided += best <= 0.05
    return decided / runs


def test_looked_at_every_day_the_sequential_test_keeps_false_positives_within_the_nominal_rate() -> None:
    assert _looked_every_day(2000, 0.0) <= 0.05
    # The fixed-horizon test, looked at every day on the same data, declares a difference that is not there
    # several times as often: what the sequential test is for.
    assert _looked_every_day(500, 0.0, sequential=False) > 0.15


def test_the_sequential_test_finds_a_real_difference() -> None:
    assert _looked_every_day(300, 0.5, seed=5) > 0.8


def test_power_needs_more_people_for_a_smaller_difference_and_more_again_to_look_every_day() -> None:
    rate = 0.1
    big = needed(
        0.2 * rate, rate * (1 - rate), alpha=0.05, power=0.8, control_fraction=0.5, mixing_variance=0.01
    )
    small = needed(
        0.1 * rate, rate * (1 - rate), alpha=0.05, power=0.8, control_fraction=0.5, mixing_variance=0.01
    )
    assert big is not None
    assert small is not None
    # 10% against 12% at 80% power, with the baseline's variance in both arms: about 3,530 people per arm.
    assert big.people == pytest.approx(7_070, rel=0.02)
    assert small.people == pytest.approx(4 * big.people, rel=0.02)
    assert big.people_sequential > big.people
    skewed = needed(
        0.2 * rate, rate * (1 - rate), alpha=0.05, power=0.8, control_fraction=0.1, mixing_variance=0.01
    )
    assert skewed is not None
    assert skewed.people > big.people
    assert needed(0.0, 0.09, alpha=0.05, power=0.8, control_fraction=0.5, mixing_variance=0.01) is None
