"""A proposed source policy may only be stricter than the current one (22.4)."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CategoryAccess:
    min_verification: int
    sensitivity: int
    purposes: frozenset[str]
    """Empty means every purpose may read the category."""


def loosened(
    current: Mapping[str, CategoryAccess],
    proposed: Mapping[str, CategoryAccess],
    current_denies: frozenset[str],
    proposed_denies: frozenset[str],
) -> list[str]:
    """Names of what the proposal would open up; empty when it is at least as strict."""
    problems = [f"deny_rule_removed:{rule}" for rule in sorted(current_denies - proposed_denies)]
    for name, now in sorted(current.items()):
        after = proposed.get(name)
        if after is None:
            problems.append(f"category_removed:{name}")
            continue
        if after.min_verification < now.min_verification:
            problems.append(f"lower_verification:{name}")
        if after.sensitivity < now.sensitivity:
            problems.append(f"lower_sensitivity:{name}")
        if now.purposes and (not after.purposes or not after.purposes <= now.purposes):
            problems.append(f"wider_purposes:{name}")
    return problems
