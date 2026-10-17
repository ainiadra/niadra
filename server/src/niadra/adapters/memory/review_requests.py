"""In-memory `ReviewRequestRepository` for unit tests."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.privacy.reviews import ReviewRequestRecord


class InMemoryReviewRequestRepository:
    def __init__(self, store: MemoryStore) -> None:
        self.requests: dict[UUID, ReviewRequestRecord] = store.module("review_requests").setdefault(
            "rows", {}
        )

    async def add(self, request: ReviewRequestRecord) -> ReviewRequestRecord:
        return self.requests.setdefault(request.request_id, request)

    async def get(self, request_id: UUID) -> ReviewRequestRecord | None:
        return self.requests.get(request_id)

    async def page(
        self, *, status: str | None, before: tuple[datetime, UUID] | None, limit: int
    ) -> list[ReviewRequestRecord]:
        found = [
            r
            for r in self.requests.values()
            if (status is None or r.status == status)
            and (before is None or (r.created_at, r.request_id) < before)
        ]
        return sorted(found, key=lambda r: (r.created_at, r.request_id), reverse=True)[:limit]

    async def resolve(
        self, request_id: UUID, *, outcome: str, resolution_ct: bytes | None, at: datetime, by: str
    ) -> ReviewRequestRecord | None:
        request = self.requests.get(request_id)
        if request is None or request.status != "open":
            return None
        resolved = replace(
            request,
            status="resolved",
            outcome=outcome,
            resolution_ct=resolution_ct,
            resolved_at=at,
            resolved_by=by,
        )
        self.requests[request_id] = resolved
        return resolved

    async def for_profiles(self, profile_ids: Sequence[UUID]) -> list[ReviewRequestRecord]:
        wanted = set(profile_ids)
        return sorted(
            (r for r in self.requests.values() if r.profile_id in wanted),
            key=lambda r: (r.created_at, r.request_id),
        )

    async def purge_resolved(self, before: datetime, *, limit: int) -> int:
        gone = [
            k
            for k, r in self.requests.items()
            if r.status == "resolved" and r.resolved_at and r.resolved_at < before
        ][:limit]
        for key in gone:
            del self.requests[key]
        return len(gone)
