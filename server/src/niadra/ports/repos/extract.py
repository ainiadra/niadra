"""Extraction runs: versions, model, tokens, cost, validation outcome (5.7); daily LLM spend (5.9)."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.extract.result import ExtractionRun, RunStatus
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope


class ExtractionRunRepository(Protocol):
    """Owned by the `extract` module. `memory` reads the run it reconciles and `measure` reads the
    agent blocks (19.3); nobody else touches these tables (4.3)."""

    async def save_run(self, run: ExtractionRun) -> None:
        """Upsert by (session, versions): tokens and cost add up across attempts, the rest is replaced."""
        ...

    async def get_run(self, session_id: UUID, versions_key: str) -> ExtractionRun | None: ...

    async def mark_applied(
        self, session_id: UUID, versions_key: str, profile_id: UUID | None, work: int
    ) -> None:
        """Memory applied the run to `profile_id` (None: to nothing), and `work` is the profile's `compile`
        request that covers it (0: none to wait for). Saving the run again clears both."""
        ...

    async def latest_run(self, session_id: UUID) -> ExtractionRun | None:
        """The newest run of a session under any versions, with its result decrypted."""
        ...

    async def run_keys(self, session_ids: Sequence[UUID]) -> dict[UUID, list[tuple[str, RunStatus]]]:
        """The versions and status of every run of the sessions, without results."""
        ...

    async def runs_page(self, after: tuple[UUID, str] | None, limit: int) -> list[ExtractionRun]:
        """Runs that keep a result, by (session, versions), with the result decrypted: the withholding
        pass (`app/withhold.py`)."""
        ...

    async def rewrite_result(self, run: ExtractionRun) -> None:
        """The run's result as given, sealed again; tokens, cost and status stay as they are."""
        ...

    async def extractor_state(self) -> tuple[int | None, datetime | None]:
        """The fingerprint of the extractor the space last saw, and when a change of it was last seen."""
        ...

    async def set_extractor_state(self, fingerprint: int, changed_at: datetime | None) -> None:
        """Keeps the fingerprint; `changed_at` is kept only when given."""
        ...

    async def spent(self, day: date, purpose: str) -> int:
        """Micro-dollars spent on LLM calls for `purpose` on `day` (UTC)."""
        ...

    async def add_spend(self, day: date, purpose: str, cost_micros: int, *, calls: int = 1) -> None:
        """Adds to the day's spend (negative to give a reservation back) and counts `calls` calls."""
        ...

    async def reserve_spend(self, day: date, purpose: str, cost_micros: int, cap_micros: int) -> bool:
        """Adds `cost_micros` to the day's spend only while the spend is below `cap_micros`, atomically
        against other reservations; False when the cap was already reached."""
        ...

    async def mark_budget_notice(self, day: date, purpose: str) -> bool:
        """True the first time it is called for the day and purpose: the budget notice goes out once."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4): clears the results of runs of `scope.session_ids`, which hold the
        customer's words; the run row stays with its tokens and cost, which usage and spend count."""
        ...
