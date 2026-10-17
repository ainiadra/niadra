"""In-memory `ReviewRepository` for unit tests."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.review.sampling import ReviewSampleRecord, erased_by


class InMemoryReviewRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("review")
        self._samples: dict[UUID, ReviewSampleRecord] = self._s.setdefault("samples", {})

    async def has_sample(self, session_id: UUID) -> bool:
        return any(s.session_id == session_id for s in self._samples.values())

    async def add_sample(self, sample: ReviewSampleRecord) -> None:
        if not await self.has_sample(sample.session_id):
            self._samples[sample.sample_id] = sample

    async def count_since(self, at: datetime) -> int:
        return sum(1 for s in self._samples.values() if s.created_at >= at)

    async def get_sample(self, sample_id: UUID) -> ReviewSampleRecord | None:
        return self._samples.get(sample_id)

    async def drop_sample(self, sample_id: UUID) -> None:
        self._samples.pop(sample_id, None)

    async def pending(self, limit: int) -> list[ReviewSampleRecord]:
        waiting = sorted(
            (s for s in self._samples.values() if s.reviewed_at is None),
            key=lambda s: (s.created_at, s.sample_id),
        )
        return waiting[:limit]

    async def reviewed_since(self, at: datetime, limit: int) -> list[ReviewSampleRecord]:
        done = [s for s in self._samples.values() if s.reviewed_at is not None and s.reviewed_at >= at]
        return sorted(done, key=lambda s: (s.reviewed_at, s.sample_id), reverse=True)[:limit]

    async def add_verdicts(
        self, sample_id: UUID, verdicts: Sequence[dict[str, Any]], reviewer_id: UUID, at: datetime
    ) -> None:
        sample = self._samples[sample_id]
        self._samples[sample_id] = replace(
            sample, verdicts=(*sample.verdicts, *verdicts), reviewer_id=reviewer_id, reviewed_at=at
        )

    async def verdicts_since(self, at: datetime) -> list[dict[str, Any]]:
        return [
            dict(v)
            for s in sorted(self._samples.values(), key=lambda s: s.sample_id)
            if s.reviewed_at is not None and s.reviewed_at >= at
            for v in s.verdicts
        ]

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        doomed = sorted(s.sample_id for s in self._samples.values() if erased_by(s, scope))[:limit]
        for sample_id in doomed:
            del self._samples[sample_id]
        return [ErasedRow("review_samples", {"sample_id": str(sample_id)}) for sample_id in doomed]

    async def purge_before(self, before: datetime) -> int:
        doomed = [s.sample_id for s in self._samples.values() if s.created_at < before]
        for sample_id in doomed:
            del self._samples[sample_id]
        return len(doomed)
