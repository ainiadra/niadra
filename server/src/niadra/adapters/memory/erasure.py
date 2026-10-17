"""In-memory erasure marks for unit tests: the semantics of `adapters.outbound.redis.erasure`, never
expiring."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from niadra.ports.infra import CacheUnavailable


class InMemoryErasureMarks:
    def __init__(self) -> None:
        self.marks: dict[tuple[UUID, bytes], datetime] = {}
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise CacheUnavailable("state store unreachable")

    async def fence(self, space_id: UUID, marks: Sequence[bytes], at: datetime, ttl_s: int) -> None:  # noqa: ARG002
        self._check()
        for mark in marks:
            held = self.marks.get((space_id, mark))
            if held is None or at >= held:
                self.marks[(space_id, mark)] = at

    async def fenced(self, space_id: UUID, marks: Sequence[bytes]) -> dict[bytes, datetime]:
        self._check()
        return {m: at for m in marks if (at := self.marks.get((space_id, m))) is not None}
