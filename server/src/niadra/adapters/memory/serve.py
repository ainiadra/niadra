"""In-memory `ServeRepository` for unit tests."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.serve.records import AgentWatermark, ConversationPin


class InMemoryServeRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("serve")
        self._pins: dict[tuple[str, str], ConversationPin] = self._s.setdefault("pins", {})
        self._marks: dict[tuple[UUID, UUID, str], AgentWatermark] = self._s.setdefault("watermarks", {})
        self._groups: dict[tuple[str, UUID], tuple[str, datetime, str]] = self._s.setdefault(
            "assignments", {}
        )

    async def invalidations_since(self, since: datetime) -> list[dict[str, Any]]:  # noqa: ARG002
        # No triggers here: the in-memory unit of work tells its listeners at commit instead.
        return []

    async def purge_invalidations(self, before: datetime) -> int:  # noqa: ARG002
        return 0

    async def get_pin(self, conversation_id: str, view: str) -> ConversationPin | None:
        return self._pins.get((conversation_id, view))

    async def get_watermark(
        self, source_id: UUID, profile_id: UUID, thread: str = ""
    ) -> AgentWatermark | None:
        return self._marks.get((source_id, profile_id, thread))

    async def latest_watermark(self, source_id: UUID, profile_id: UUID) -> AgentWatermark | None:
        marks = [m for (s, p, _), m in self._marks.items() if s == source_id and p == profile_id]
        return max(marks, key=lambda m: (m.generation, m.seen_at), default=None)

    async def project_pins(self, pins: Sequence[ConversationPin]) -> None:
        for pin in pins:
            key = (pin.conversation_id, pin.view)
            current = self._pins.get(key)
            if current is not None and (current.generation, current.pinned_at) > (
                pin.generation,
                pin.pinned_at,
            ):
                continue
            expires_at = max(pin.expires_at, current.expires_at) if current is not None else pin.expires_at
            self._pins[key] = ConversationPin(
                pin.conversation_id,
                pin.view,
                pin.pack_key,
                pin.pack_hash,
                pin.generation,
                pin.pinned_at,
                expires_at,
            )

    async def project_watermarks(self, marks: Sequence[AgentWatermark]) -> None:
        for mark in marks:
            key = (mark.source_id, mark.profile_id, mark.thread)
            current = self._marks.get(key)
            if current is None or (current.generation, current.seen_at) <= (mark.generation, mark.seen_at):
                self._marks[key] = mark

    async def get_assignments(self, profile_id: UUID) -> dict[str, str]:
        return {experiment: found[0] for (experiment, p), found in self._groups.items() if p == profile_id}

    async def project_assignment(
        self,
        *,
        experiment: str,
        profile_id: UUID,
        group: str,
        assigned_at: datetime,
        kind: str = "memory",
    ) -> None:
        self._groups.setdefault((experiment, profile_id), (group, assigned_at, kind))

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids:
            return []
        prefix = f"{scope.profile_id}/" if scope.profile_id else None
        conversation = scope.conversation_id if scope.kind is TargetKind.CONVERSATION else None
        pins = sorted(
            k
            for k, pin in self._pins.items()
            if (prefix and pin.pack_key.startswith(prefix)) or (conversation and k[0] == conversation)
        )
        erased = []
        for key in pins[:limit]:
            del self._pins[key]
            erased.append(ErasedRow("conversation_pins", {"conversation_id": key[0], "view": key[1]}))
        if scope.kind is not TargetKind.CONVERSATION and scope.profile_id is not None:
            marks = sorted((k for k in self._marks if k[1] == scope.profile_id), key=str)
            for source_id, profile_id, thread in marks[: max(limit - len(erased), 0)]:
                del self._marks[(source_id, profile_id, thread)]
                keys = {"source_id": str(source_id), "profile_id": str(profile_id), "thread": thread}
                erased.append(ErasedRow("agent_watermarks", keys))
            for experiment, profile_id in [k for k in self._groups if k[1] == scope.profile_id]:
                del self._groups[(experiment, profile_id)]
                erased.append(
                    ErasedRow(
                        "experiment_assignments", {"experiment": experiment, "profile_id": str(profile_id)}
                    )
                )
        return erased
