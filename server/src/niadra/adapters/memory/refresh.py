"""In-memory refresh wants for unit tests: the semantics of `adapters.outbound.redis.refresh`."""

from collections.abc import Mapping, Sequence
from uuid import UUID


class InMemoryRefreshWants:
    def __init__(self) -> None:
        self.limiters: dict[tuple[UUID, str], str] = {}
        self.queue: dict[UUID, list[bytes]] = {}
        self.pending: dict[tuple[UUID, str], bytes] = {}
        self.answers: dict[UUID, list[bytes]] = {}
        self.workers: set[UUID] = set()

    async def claim(self, space_id: UUID, key: str, ttl_s: int) -> str | None:  # noqa: ARG002
        found = self.limiters.get((space_id, key))
        if found is None:
            self.limiters[(space_id, key)] = "pending"
        return found

    async def settle_claim(self, space_id: UUID, key: str, outcome: str, ttl_s: int) -> None:  # noqa: ARG002
        self.limiters[(space_id, key)] = outcome

    async def enqueue(
        self,
        space_id: UUID,
        requests: Sequence[bytes],
        pending: Mapping[str, bytes],
        ttl_s: int,  # noqa: ARG002
    ) -> None:
        self.queue.setdefault(space_id, []).extend(requests)
        for object_id, reservation in pending.items():
            self.pending[(space_id, object_id)] = reservation

    async def take(self, space_id: UUID, limit: int) -> list[bytes]:
        queue = self.queue.setdefault(space_id, [])
        taken, self.queue[space_id] = queue[:limit], queue[limit:]
        return taken

    async def requeue(self, space_id: UUID, requests: Sequence[bytes]) -> None:
        self.queue[space_id] = [*requests, *self.queue.get(space_id, [])]

    async def outstanding(self, space_id: UUID, object_ids: Sequence[str]) -> dict[str, bytes]:
        return {o: self.pending.pop((space_id, o)) for o in object_ids if (space_id, o) in self.pending}

    async def answered(self, space_id: UUID, answers: Sequence[bytes], ttl_s: int) -> None:  # noqa: ARG002
        self.answers.setdefault(space_id, []).extend(answers)

    async def take_answered(self, space_id: UUID, limit: int) -> list[bytes]:
        queue = self.answers.setdefault(space_id, [])
        taken, self.answers[space_id] = queue[:limit], queue[limit:]
        return taken

    async def requeue_answered(self, space_id: UUID, answers: Sequence[bytes]) -> None:
        self.answers[space_id] = [*answers, *self.answers.get(space_id, [])]

    async def worker_seen(self, space_id: UUID, ttl_s: int) -> None:  # noqa: ARG002
        self.workers.add(space_id)

    async def has_worker(self, space_id: UUID) -> bool:
        return space_id in self.workers
