"""Drift of a declared type (24.2.1; object-type spec 3.2 and 8.8): the company's system moved away from what
the type declares, seen by a deriving tool's fingerprint or by the traffic itself.

Pure. By observation, a report drifts when it carries a field the type names nowhere, or a lifecycle state the
type does not declare and the source's vocabulary does not translate. Niadra never refuses what the company's
system did (L24): the value is kept as a plain field, and the drift goes back to the company as a data issue.
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from niadra.domain.state.lifecycle import EXPIRED_WITHOUT_OUTCOME

LIFECYCLE_FIELD = "state"
"""The reported field that carries the lifecycle state: its drift is a state the type does not declare."""


@dataclass(frozen=True, slots=True)
class Declared:
    """What a type declares, as drift reads it."""

    names: frozenset[str]
    """Every field, value, time axis and derived field."""
    states: frozenset[str]
    vocabulary: Mapping[str, str]
    """The reporting source's codes for the type's states."""


def drifted_fields(
    reported: Mapping[str, Any], declared: Declared, bookkeeping: Collection[str]
) -> list[str]:
    """The reported fields the type names nowhere, and `state` when the reported state is not one of the
    type's states once the source's vocabulary translated it; sorted. What a system event says about itself
    (`bookkeeping`) is never drift."""
    found = sorted(
        name
        for name in reported
        if name not in declared.names and name not in bookkeeping and name != LIFECYCLE_FIELD
    )
    state = reported.get(LIFECYCLE_FIELD)
    if isinstance(state, str) and declared.states:
        canonical = declared.vocabulary.get(state, state)
        if canonical != EXPIRED_WITHOUT_OUTCOME and canonical not in declared.states:
            found.append(LIFECYCLE_FIELD)
    return found


class Recently:
    """What this process already reported, so a system that keeps sending the same undeclared field costs one
    statement an hour, not one an event, while its issue stays open. The count of an issue is then the hours
    it was seen in, per process."""

    def __init__(self, every: timedelta = timedelta(hours=1), cap: int = 10_000) -> None:
        self._every = every
        self._cap = cap
        self._seen: dict[tuple[str, ...], datetime] = {}

    def fresh(self, keys: Iterable[tuple[str, ...]], now: datetime) -> list[tuple[str, ...]]:
        """The keys not reported within the last hour, marked as reported now."""
        if len(self._seen) >= self._cap:
            self._seen = {k: at for k, at in self._seen.items() if now - at < self._every}
        found = []
        for key in dict.fromkeys(keys):
            at = self._seen.get(key)
            if at is None or now - at >= self._every:
                self._seen[key] = now
                found.append(key)
        return found

    def forget(self, first: str) -> None:
        """Forgets the keys whose first part is `first`, so each is fresh again: an issue of theirs was
        acknowledged, and its next occurrence opens a new one."""
        self._seen = {k: at for k, at in self._seen.items() if k[0] != first}
