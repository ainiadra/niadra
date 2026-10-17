"""In-memory state store and confirmed queue of the agents' working state, for unit tests: the semantics of
`adapters.outbound.redis.agent_state` and `adapters.outbound.rabbitmq.agent_state`, without the servers."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from uuid import UUID

from niadra.domain.state.agent_state import DurableState
from niadra.ports.state import HeldField, HeldState, WriteOutcome, WriteResult


class InMemoryAgentStateStore:
    def __init__(self) -> None:
        self.states: dict[str, HeldState] = {}
        self.index: dict[tuple[UUID, bytes], set[str]] = {}
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("state store unreachable")

    async def load(self, key: str) -> HeldState | None:
        self._check()
        return self.states.get(key)

    async def restore(self, key: str, state: HeldState, ttl_s: int) -> None:  # noqa: ARG002
        self._check()
        self.states.setdefault(key, state)

    async def write(
        self,
        key: str,
        changes: dict[str, HeldField | None],
        *,
        replace: bool,
        if_version: int | None,
        now_ms: int,
        ttl_s: int,  # noqa: ARG002 (nothing expires here)
    ) -> WriteOutcome:
        self._check()
        current = self.states.get(key)
        if current is None:
            return WriteOutcome(WriteResult.MISSING, 0)
        if if_version is not None and if_version != current.version:
            return WriteOutcome(WriteResult.CONFLICT, current.version)
        fields = {} if replace else dict(current.fields)
        for name, change in changes.items():
            if change is None:
                fields.pop(name, None)
            else:
                fields[name] = replace_written(change, now_ms)
        stored = HeldState(current.version + 1, now_ms, fields)
        self.states[key] = stored
        return WriteOutcome(WriteResult.STORED, stored.version, stored)

    async def forget(self, keys: Sequence[str]) -> None:
        self._check()
        for key in keys:
            self.states.pop(key, None)

    async def rewrite(self, key: str, state: HeldState, ttl_s: int) -> bool:  # noqa: ARG002
        self._check()
        current = self.states.get(key)
        if current is None or current.version != state.version:
            return False
        self.states[key] = state
        return True

    async def track(self, space_id: UUID, marks: Sequence[bytes], tag: str, ttl_s: int) -> None:  # noqa: ARG002
        self._check()
        for mark in marks:
            self.index.setdefault((space_id, mark), set()).add(tag)

    async def tracked(self, space_id: UUID, marks: Sequence[bytes]) -> list[str]:
        self._check()
        return sorted({tag for mark in marks for tag in self.index.get((space_id, mark), ())})


def replace_written(field_: HeldField, now_ms: int) -> HeldField:
    return replace(field_, written_ms=now_ms)


@dataclass
class InMemoryDurableBatch:
    states: list[DurableState]
    log: "InMemoryAgentStateLog"

    async def ack(self) -> None:
        self.log.acked += len(self.states)

    async def retry_later(self) -> None:
        self.log.pending[:0] = self.states


@dataclass
class InMemoryAgentStateLog:
    pending: list[DurableState] = field(default_factory=list)
    acked: int = 0
    confirms: bool = True

    async def publish(self, states: Sequence[DurableState], timeout_s: float) -> bool:  # noqa: ARG002
        if not self.confirms:
            return False
        self.pending.extend(states)
        return True

    async def batches(self, max_items: int) -> AsyncIterator[InMemoryDurableBatch]:
        while self.pending:
            taken, self.pending = self.pending[:max_items], self.pending[max_items:]
            yield InMemoryDurableBatch(taken, self)
