"""Daily rollups and analytical queries for insights (5.21)."""

from collections.abc import Collection, Sequence
from datetime import date, datetime
from typing import Protocol

from niadra.domain.insights.gaps import GapDay
from niadra.domain.insights.model import (
    AggregateQuery,
    AggregateRow,
    EpisodeText,
    ProfileMatch,
    ProfileRef,
    RollupRow,
    Selection,
)


class InsightsRepository(Protocol):
    """Owned by the `insights` module; it writes `tenant_rollups`, `knowledge_gaps` and `insight_reviews`.
    The reads over episodes, open items, traits, actions, objects and profiles are the named read models
    of 4.3: structured columns only, and text comes back sealed, to be opened by the application after the
    policy check.

    `exclude_categories` drops episodes whose category or normalized category the analyst policy denies.
    """

    async def aggregate(
        self, query: AggregateQuery, exclude_categories: Collection[str]
    ) -> list[AggregateRow]: ...

    async def find_profiles(
        self,
        selection: Selection,
        exclude_categories: Collection[str],
        min_matches: int,
        after: str | None,
        limit: int,
    ) -> list[ProfileMatch]:
        """Profiles with at least `min_matches` rows, ordered by pseudonym, starting after `after`."""
        ...

    async def episode_texts(
        self, selection: Selection, exclude_categories: Collection[str], limit: int, *, newest_first: bool
    ) -> list[EpisodeText]:
        """Current episode versions with a pseudonym; `newest_first=False` samples at random."""
        ...

    async def profile_by_pseudonym(self, pseudonym: str) -> ProfileRef | None: ...

    async def write_rollups(self, day: date, rows: Sequence[RollupRow]) -> None:
        """Replaces the rollups of the day, so a rerun of the nightly task is harmless."""
        ...

    async def latest_rollups(self) -> tuple[date | None, list[RollupRow]]: ...

    async def write_gap_days(self, day: date, rows: Sequence[GapDay]) -> None:
        """Replaces the knowledge gaps of the day (13, 6.5), so a rerun of the pass is harmless."""
        ...

    async def purge_gap_days(self, before: date) -> int: ...

    async def gap_days(self, since: date, until: date) -> list[GapDay]:
        """The gap rows of the days in [since, until)."""
        ...

    async def gaps_day(self) -> int | None:
        """The last day (ordinal) the gaps pass counted (`space_state`)."""
        ...

    async def set_gaps_day(self, day: int) -> None: ...

    async def reviews(self) -> dict[str, tuple[str, str, datetime, str | None]]:
        """Per gap or proposal key, the team's decision, who took it, when, and the note."""
        ...

    async def put_review(
        self, key: str, decision: str, decided_by: str, at: datetime, note: str | None
    ) -> None: ...
