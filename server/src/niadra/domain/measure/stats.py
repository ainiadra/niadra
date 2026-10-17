"""The statistics of an experiment on an element or an agent: pure.

- **CUPED.** Each person's outcome in the experiment (`y`) is adjusted by the same measure over the days
  before they were assigned (`x`, the covariate): `y - theta * (x - mean x)`, with
  `theta = cov(x, y) / var(x)` pooled over the arms. The difference between arms is unchanged in expectation,
  and its variance shrinks by the share of `y` the history explains.
- **mSPRT.** The mixture sequential probability ratio test on the difference of means, under the normal
  approximation: with the difference `d`, its variance `s2` and a normal mixing distribution of variance
  `tau2` over the true difference,
  `Lambda = sqrt(s2 / (s2 + tau2)) * exp(tau2 * d^2 / (2 * s2 * (s2 + tau2)))`. `1 / Lambda` is a p-value
  valid at any stopping time, so the company may look every day: the running minimum over the looks is the
  always-valid p-value, and the confidence interval is the set of differences it does not reject.
- **Power.** How many people, and so how many days of the space's traffic, a difference of a given relative
  size needs: the fixed-horizon formula, and the smallest sample at which the sequential test rejects with
  the asked power at that look.

Each person is one observation; `Moments` carries the sums a SQL statement adds up, never the people.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import NormalDist

MIN_PEOPLE = 50
"""An arm below this many people is not compared: the normal approximation would not hold."""
_NORMAL = NormalDist()


@dataclass(frozen=True, slots=True)
class Moments:
    """Sums over the people of one arm: of the outcome `y`, of the covariate `x` and of their products."""

    n: int = 0
    sy: float = 0.0
    syy: float = 0.0
    sx: float = 0.0
    sxx: float = 0.0
    sxy: float = 0.0

    def __add__(self, other: "Moments") -> "Moments":
        return Moments(
            self.n + other.n,
            self.sy + other.sy,
            self.syy + other.syy,
            self.sx + other.sx,
            self.sxx + other.sxx,
            self.sxy + other.sxy,
        )

    @property
    def mean_y(self) -> float:
        return self.sy / self.n if self.n else 0.0

    @property
    def mean_x(self) -> float:
        return self.sx / self.n if self.n else 0.0

    @property
    def var_y(self) -> float:
        return _var(self.n, self.sy, self.syy)

    @property
    def var_x(self) -> float:
        return _var(self.n, self.sx, self.sxx)

    @property
    def cov(self) -> float:
        if self.n < 2:
            return 0.0
        return (self.sxy - self.sx * self.sy / self.n) / (self.n - 1)


def _var(n: int, s: float, ss: float) -> float:
    if n < 2:
        return 0.0
    return max(0.0, (ss - s * s / n) / (n - 1))


def of_pairs(pairs: Iterable[tuple[float, float]]) -> Moments:
    """The moments of `(y, x)` pairs, one per person."""
    m = Moments()
    for y, x in pairs:
        m = m + Moments(1, y, y * y, x, x * x, x * y)
    return m


def theta(arms: Iterable[Moments]) -> float | None:
    """The CUPED coefficient, pooled over the arms; None when the history does not vary."""
    pooled = sum(arms, Moments())
    return pooled.cov / pooled.var_x if pooled.var_x > 0 else None


@dataclass(frozen=True, slots=True)
class Estimate:
    control_mean: float
    arm_mean: float
    difference: float
    """The arm's mean minus the control's, adjusted by CUPED when `theta` is set."""
    variance: float
    """Of `difference`."""
    unadjusted_variance: float
    theta: float | None

    @property
    def relative(self) -> float | None:
        return self.difference / self.control_mean if self.control_mean else None

    @property
    def reduction(self) -> float | None:
        """The share of the variance CUPED removed."""
        if self.theta is None or self.unadjusted_variance <= 0:
            return None
        return max(0.0, 1 - self.variance / self.unadjusted_variance)


def compare(control: Moments, arm: Moments, coefficient: float | None) -> Estimate | None:
    """The arm against the control; None while either is too small to compare."""
    if control.n < MIN_PEOPLE or arm.n < MIN_PEOPLE:
        return None
    raw = arm.var_y / arm.n + control.var_y / control.n
    t = coefficient or 0.0
    difference = (arm.mean_y - control.mean_y) - t * (arm.mean_x - control.mean_x)
    variance = _adjusted(arm, t) / arm.n + _adjusted(control, t) / control.n
    return Estimate(control.mean_y, arm.mean_y, difference, variance, raw, coefficient)


def _adjusted(m: Moments, t: float) -> float:
    return max(0.0, m.var_y - 2 * t * m.cov + t * t * m.var_x)


def per_person_variance(control: Moments, arm: Moments, coefficient: float | None) -> float:
    """The pooled variance of one person's adjusted outcome: the scale of the mixing distribution."""
    t = coefficient or 0.0
    return (_adjusted(control, t) * (control.n - 1) + _adjusted(arm, t) * (arm.n - 1)) / max(
        1, control.n + arm.n - 2
    )


@dataclass(frozen=True, slots=True)
class Sequential:
    p_value: float
    """`min(1, 1 / Lambda)` at this look; the report keeps the running minimum over the looks."""
    ci_low: float
    ci_high: float


def msprt(difference: float, variance: float, tau2: float, alpha: float) -> Sequential | None:
    """The mixture SPRT at one look, with its always-valid confidence interval at `alpha`; None without
    variance to test against."""
    if variance <= 0 or tau2 <= 0:
        return None
    total = variance + tau2
    log_lambda = 0.5 * math.log(variance / total) + tau2 * difference * difference / (2 * variance * total)
    p_value = 1.0 if log_lambda <= 0 else math.exp(-log_lambda)
    half = math.sqrt(variance * total / tau2 * (2 * math.log(1 / alpha) + math.log(total / variance)))
    return Sequential(min(1.0, p_value), difference - half, difference + half)


def mixing_tau2(mixing_variance: float, person_variance: float) -> float:
    """The mixing distribution's variance over the true difference: `mixing_variance` is in units of one
    person's variance, so the test expects effects of about `sqrt(mixing_variance)` standard deviations."""
    return mixing_variance * person_variance


# Power.


@dataclass(frozen=True, slots=True)
class Needed:
    people: int
    """In both arms together."""
    people_sequential: int


def needed(
    delta: float,
    variance: float,
    *,
    alpha: float,
    power: float,
    control_fraction: float,
    mixing_variance: float,
) -> Needed | None:
    """The people a true difference `delta` needs, over both arms: at a fixed horizon (two-sided `alpha`),
    and for the sequential test to have rejected with probability `power` by the time they came."""
    if delta == 0 or variance <= 0:
        return None
    share = 1 / control_fraction + 1 / (1 - control_fraction)
    z = _NORMAL.inv_cdf(1 - alpha / 2) + _NORMAL.inv_cdf(power)
    fixed = math.ceil(z * z * variance * share / (delta * delta))
    tau2 = mixing_tau2(mixing_variance, variance)

    def enough(n: int) -> bool:
        s2 = variance * share / n
        total = s2 + tau2
        c = math.sqrt(total / tau2 * (2 * math.log(1 / alpha) + math.log(total / s2)))
        shift = abs(delta) / math.sqrt(s2)
        return (1 - _NORMAL.cdf(c - shift)) + _NORMAL.cdf(-c - shift) >= power

    high = max(fixed, 2)
    while not enough(high):
        high *= 2
        if high > 10**12:
            return Needed(fixed, high)
    low = 1
    while low < high:
        mid = (low + high) // 2
        if enough(mid):
            high = mid
        else:
            low = mid + 1
    return Needed(fixed, high)
