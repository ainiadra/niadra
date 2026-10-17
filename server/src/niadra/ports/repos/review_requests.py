"""Requests to review an automated decision (`review_requests`, 6.7), owned by the privacy module. Reasons
and resolutions arrive sealed; the explanation holds ids and numbers only."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.privacy.reviews import ReviewRequestRecord


class ReviewRequestRepository(Protocol):
    async def add(self, request: ReviewRequestRecord) -> ReviewRequestRecord:
        """The request as stored: an id already there keeps what it held (a retried request)."""
        ...

    async def get(self, request_id: UUID) -> ReviewRequestRecord | None: ...

    async def page(
        self, *, status: str | None, before: tuple[datetime, UUID] | None, limit: int
    ) -> list[ReviewRequestRecord]:
        """Newest first, keyset paged."""
        ...

    async def resolve(
        self, request_id: UUID, *, outcome: str, resolution_ct: bytes | None, at: datetime, by: str
    ) -> ReviewRequestRecord | None:
        """Resolves an open request and returns it; None when it is unknown or resolved already."""
        ...

    async def for_profiles(self, profile_ids: Sequence[UUID]) -> list[ReviewRequestRecord]: ...

    async def purge_resolved(self, before: datetime, *, limit: int) -> int:
        """Requests resolved before `before`: kept that long by a legal obligation, then gone."""
        ...
