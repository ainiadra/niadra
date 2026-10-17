"""Freshness is policy, computed when a field is read and never stored (L19, L26)."""

from enum import StrEnum

from niadra.domain.state.expr import DAY_MS, MINUTE_MS, SECOND_MS


class FreshnessClass(StrEnum):
    VOLATILE = "volatile"
    PRICE = "price"
    SEMI = "semi"
    STABLE = "stable"
    NONE = "none"


DEFAULT_MAX_AGE_MS: dict[FreshnessClass, int | None] = {
    FreshnessClass.VOLATILE: 30 * SECOND_MS,
    FreshnessClass.PRICE: 60 * SECOND_MS,
    FreshnessClass.SEMI: 60 * MINUTE_MS,
    FreshnessClass.STABLE: 7 * DAY_MS,
    FreshnessClass.NONE: None,
}
"""How old a field of each class may be before a read calls it stale, when its declaration names no
`max_age`; a field of class `none` never goes stale."""
