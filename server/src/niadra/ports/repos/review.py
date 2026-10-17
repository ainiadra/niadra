"""Review samples and verdicts (22.2)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.review.sampling import ReviewSampleRecord


class ReviewRepository(Protocol):
    """Owned by the `review` module; no other module reads or writes these tables (4.3)."""

    async def has_sample(self, session_id: UUID) -> bool: ...

    async def add_sample(self, sample: ReviewSampleRecord) -> None: ...

    async def count_since(self, at: datetime) -> int:
        """Samples created since `at`; the daily cap reads it."""
        ...

    async def get_sample(self, sample_id: UUID) -> ReviewSampleRecord | None: ...

    async def drop_sample(self, sample_id: UUID) -> None:
        """Deletes a sample nobody judged, as if its session had not been sampled."""
        ...

    async def pending(self, limit: int) -> list[ReviewSampleRecord]:
        """Samples without a verdict yet, oldest first."""
        ...

    async def reviewed_since(self, at: datetime, limit: int) -> list[ReviewSampleRecord]:
        """Samples with verdicts reviewed since `at`, most recent first: the second-review candidates."""
        ...

    async def add_verdicts(
        self, sample_id: UUID, verdicts: Sequence[dict[str, Any]], reviewer_id: UUID, at: datetime
    ) -> None:
        """Appends: a second reviewer's verdicts sit beside the first; inter-reviewer agreement reads them."""
        ...

    async def verdicts_since(self, at: datetime) -> list[dict[str, Any]]:
        """Every verdict of samples reviewed since `at`, flattened."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4): deletes at most `limit` rows of the target, idempotent."""
        ...

    async def purge_before(self, before: datetime) -> int:
        """Retention (22.5): samples created before `before` go, with their verdicts."""
        ...
