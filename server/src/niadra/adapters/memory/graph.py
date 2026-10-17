"""In-memory `GraphRepository` for unit tests: the same read model over the other in-memory repositories'
stores, as PostgreSQL reads it over their tables."""

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.records import AgentAction, BusinessObject
from niadra.domain.vocabulary import EventKind
from niadra.ports.repos.graph import Activity, Acts, Handoffs, Reports, Talk, Tie


class InMemoryGraphRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._identity = store.module("identity")
        self._sessions = store.module("sessions")
        self._objects = store.module("objects")
        self._ingest = store.module("ingest")
        self._coordinate = store.module("coordinate")

    def _member(self, handle_id: UUID) -> UUID | None:
        found = self._identity.get("membership", {}).get(handle_id)
        return found[0] if found else None

    async def ties(self, handle_ids: Sequence[UUID]) -> list[Tie]:
        wanted, now = set(handle_ids), datetime.now(UTC)
        found: list[Tie] = []

        def tie(kind: str, tie_id: UUID, a: UUID, b: UUID, since: datetime, **extra: Any) -> None:
            if a in wanted or b in wanted:
                found.append(Tie(kind, tie_id, a, b, self._member(a), self._member(b), since, **extra))

        def in_force(valid_from: datetime, valid_to: datetime | None) -> bool:
            return valid_from <= now and (valid_to is None or valid_to > now)

        for link in self._identity.get("links", {}).values():
            if in_force(link.valid_from, link.valid_to):
                tie(
                    "link",
                    link.link_id,
                    link.person_handle_id,
                    link.org_handle_id,
                    link.valid_from,
                    role=link.role,
                )
        for s in self._identity.get("suggestions", {}).values():
            if s.status == "open":
                tie("suggestion", s.suggestion_id, s.handle_a, s.handle_b, s.created_at, score=s.score)
        return sorted(found, key=lambda t: (t.kind, t.tie_id))

    async def activity(
        self,
        *,
        profile_ids: Sequence[UUID] = (),
        object_ids: Sequence[UUID] = (),
        handle_ids: Sequence[UUID] = (),
        subject_hmacs: Sequence[bytes] = (),
        owner_handle_ids: Sequence[UUID] = (),
        customers: Mapping[UUID, Sequence[UUID]] | None = None,
    ) -> Activity:
        found = Activity()
        profiles, objects, handles = set(profile_ids), set(object_ids), set(handle_ids)
        subjects, owners = set(subject_hmacs), set(owner_handle_ids)
        sessions = [s for _, s in self._sessions.get("sessions", {}).values() if s.profile_id in profiles]
        for (source, profile), group in _grouped(sessions, lambda s: (s.source_id, s.profile_id)).items():
            channels = tuple(sorted({s.channel for s in group}))
            found.talks.append(
                Talk(source, profile, len(group), max(s.last_event_at for s in group), channels)
            )

        stored: dict[UUID, BusinessObject] = self._objects.get("objects", {})
        actions: list[AgentAction] = list(self._objects.get("actions", {}).values())
        owner_of = {o.object_id: o.origin_handle_id for o in stored.values() if o.origin_handle_id in owners}
        on_objects = _grouped(
            (a for a in actions if a.object_id in objects), lambda a: (a.source_id, a.object_id)
        )
        found.acts.extend([_acts(source, obj, None, group) for (source, obj), group in on_objects.items()])
        closing = (a for a in actions if a.object_id is None and a.closes_handle_id in handles)
        on_handles = _grouped(closing, lambda a: (a.source_id, a.closes_handle_id))
        found.acts.extend(
            [_acts(source, None, handle, group) for (source, handle), group in on_handles.items()]
        )
        owned = (a for a in actions if a.object_id in owner_of)
        by_owner = _grouped(owned, lambda a: (a.source_id, owner_of[a.object_id]))  # type: ignore[index]
        found.acts.extend(
            [_acts(source, None, handle, group) for (source, handle), group in by_owner.items()]
        )

        events = self._ingest.get("events", {})
        reported = [
            (object_id, events[event_id].record.source_id, at)
            for (object_id, event_id), at in self._objects.get("events", {}).items()
            if event_id in events and events[event_id].record.kind is EventKind.SYSTEM_EVENT
        ]
        for (source, obj), rows in _grouped(
            (r for r in reported if r[0] in objects), lambda r: (r[1], r[0])
        ).items():
            found.reports.append(Reports(source, obj, None, len(rows), max(r[2] for r in rows)))
        for (source, handle), rows in _grouped(
            (r for r in reported if r[0] in owner_of), lambda r: (r[1], owner_of[r[0]])
        ).items():
            found.reports.append(Reports(source, None, handle, len(rows), max(r[2] for r in rows)))
        for handle in owner_of.values():
            if handle is not None:
                found.owned[handle] = found.owned.get(handle, 0) + 1

        participants: set[tuple[UUID, UUID, str]] = self._objects.get("participants", set())
        for profile_id, mine in (customers or {}).items():
            handles_of = set(mine)
            ids = {o.object_id for o in stored.values() if o.origin_handle_id in handles_of}
            ids |= {o for o, h, _ in participants if h in handles_of}
            if ids:
                found.involved[profile_id] = len(ids)

        handed = [h for h in self._coordinate.get("handoffs", {}).values() if h.subject_hmac in subjects]
        for (source, subject, target), group in _grouped(
            handed, lambda h: (h.source_id, h.subject_hmac, h.target)
        ).items():
            latest = max(group, key=lambda h: (h.created_at, h.handoff_id))
            found.handoffs.append(
                Handoffs(
                    source, subject, target, len(group), latest.created_at, latest.status, latest.outcome
                )
            )
        return found


def _grouped[T, K: Hashable](rows: Iterable[T], key: Callable[[T], K]) -> dict[K, list[T]]:
    groups: dict[K, list[T]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return dict(groups)


def _acts(source: UUID, obj: UUID | None, handle: UUID | None, group: list[AgentAction]) -> Acts:
    latest = max(group, key=lambda a: (a.occurred_at, a.action_id))
    operations = tuple(sorted({a.operation for a in group}))
    return Acts(source, obj, handle, len(group), latest.occurred_at, operations, latest)
