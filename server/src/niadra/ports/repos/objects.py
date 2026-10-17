"""Business objects, their events and participants, and agent actions (18)."""

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.memory.vectors import ItemVector, PendingVector
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.records import AgentAction, BusinessObject, ObjectKey
from niadra.domain.state.observed import Holding
from niadra.domain.state.typed import UNTYPED, ScanMark, TimerMark, Typed
from niadra.domain.vocabulary import ActionStatus


class ObjectRepository(Protocol):
    """Owned by the `objects` module."""

    async def get_object(self, key: ObjectKey) -> BusinessObject | None: ...

    async def get_objects(self, keys: Sequence[ObjectKey]) -> list[BusinessObject]:
        """The objects of these keys that exist, in one statement."""
        ...

    async def matching(
        self, searches: Sequence[tuple[str, str, str]], *, limit: int
    ) -> dict[tuple[str, str, str], list[ObjectKey]]:
        """Per (type, field, value), up to `limit` objects of the type whose field held the value when last
        written, newest first, in one statement: a reverse relation's related objects, by the keyed digest
        each write keeps (`Typed.matched`)."""
        ...

    async def objects_for(
        self, handle_ids: Sequence[UUID], *, limit: int = 50, before: tuple[datetime, UUID] | None = None
    ) -> list[BusinessObject]:
        """Objects owned by or involving the handles, newest state first; `before` is the (`as_of`, id)
        of the last one read."""
        ...

    async def subject_records(
        self, handle_ids: Sequence[UUID], *, types: Collection[str], keys: Collection[tuple[str, str]]
    ) -> list[BusinessObject]:
        """The objects these handles own that are a subject's own record: of one of `types`, or keyed by
        one of `keys` (namespace, external id), in one statement."""
        ...

    async def objects_involving(
        self, handle_ids: Sequence[UUID], *, per_handle: int
    ) -> list[tuple[BusinessObject, tuple[tuple[UUID, str], ...]]]:
        """Each handle's `per_handle` latest objects, as owner or participant, newest state first, each with
        the (handle, role) of the handles it was found through (`owner` for the one that owns it), in one
        statement however many objects a handle has."""
        ...

    async def actions_for(
        self,
        handle_ids: Sequence[UUID],
        *,
        since: datetime | None = None,
        limit: int = 100,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[AgentAction]:
        """Newest first; `before` is the (`occurred_at`, id) of the last one read."""
        ...

    async def operation_counts(self, *, since: datetime, limit: int) -> list[tuple[str, int]]:
        """How many actions of the space recorded each operation, as written, since `since`: the most used
        first, at most `limit` names. Names and counts only."""
        ...

    async def operation_results(self, operations: Sequence[str], *, since: datetime, limit: int) -> list[str]:
        """The result texts of the newest actions recorded under any of `operations` since `since`, at most
        `limit`, the ones without a text left out."""
        ...

    async def object_event_ids(self, object_id: UUID, *, limit: int = 100) -> list[UUID]: ...

    async def get_object_by_id(self, object_id: UUID) -> BusinessObject | None: ...

    async def get_objects_by_id(self, object_ids: Sequence[UUID]) -> list[BusinessObject]:
        """The objects of these ids that exist, in one statement."""
        ...

    async def changed_since(self, revision: int, *, limit: int) -> list[BusinessObject]:
        """The objects whose last change came after `revision`, oldest change first."""
        ...

    async def last_revision(self) -> int:
        """The newest revision the space's database gave out: lower than one read before when the database
        was emptied or restored."""
        ...

    async def types_keyed_by(
        self, namespaces: Sequence[str], types: Sequence[str], ids: Sequence[str]
    ) -> dict[str, list[str]]:
        """Per id, the types among `types` of the space's objects in `namespaces` whose key is that id: what
        a field naming another object holds."""
        ...

    async def add_object(self, obj: BusinessObject) -> BusinessObject:
        """Inserts the object unless its (type, namespace, id) exists; returns the stored one."""
        ...

    async def set_state(
        self,
        object_id: UUID,
        *,
        state: dict[str, Any],
        fields: Holding,
        as_of: datetime,
        source_id: UUID,
        state_version: str | None,
        record_ref: str | None,
        typed: Typed = UNTYPED,
        derived_status: str | None = None,
        inputs_hash: bytes | None = None,
    ) -> int:
        """`fields` are the observations by source, `state` what they resolve to, `typed` the lifecycle,
        values' versions and timers; returns the row's new change sequence. The new state drops the object's
        vector, which described the old one."""
        ...

    async def expire_dependents(
        self, changed: Mapping[UUID, Sequence[str]], at: datetime
    ) -> list[BusinessObject]:
        """In one statement (24.3.7): every derived object that names, of an input object in `changed`, one of
        its inputs there (as its type declares them, `lead.city`) turns `expired_by_input`, with the names of
        those inputs. An input object is a customer's or a shared one. Returns them as stored."""
        ...

    async def mark_timers(self, marks: Mapping[UUID, Mapping[str, TimerMark]]) -> list[BusinessObject]:
        """Sets the named timers' marks on each object, in one statement; returns the objects as stored."""
        ...

    async def mark_scans(self, marks: Mapping[UUID, Mapping[str, ScanMark]]) -> list[BusinessObject]:
        """Sets the named content fields' scan marks on each object, in one statement."""
        ...

    async def set_owner(self, object_id: UUID, handle_id: UUID) -> int | None:
        """Pins an object that has no owner yet to `handle_id` and returns the row's new change sequence; an
        owned object keeps its owner (L6), and None comes back."""
        ...

    async def add_object_event(self, object_id: UUID, event_id: UUID, occurred_at: datetime) -> bool:
        """False when the event was already applied to the object (the task is idempotent per event)."""
        ...

    async def add_participants(self, object_id: UUID, participants: Sequence[tuple[UUID, str]]) -> None:
        """(handle_id, role) pairs; existing pairs are kept."""
        ...

    async def participants_of(
        self, object_ids: Sequence[UUID], *, per_object: int
    ) -> dict[UUID, list[tuple[UUID, str]]]:
        """Each object's (handle_id, role) pairs, at most `per_object` handles of each, in one statement: the
        owner first, then the handles with a role of their own (a lawyer, a buyer), then the bare
        participants; each object's pairs come in that order."""
        ...

    async def participant_count(self, object_id: UUID) -> int:
        """How many handles take part in the object, its owner aside: asked only for an object whose
        participants pass a read's cap, to say how many were left out."""
        ...

    async def add_action(self, action: AgentAction) -> None: ...

    async def action_for_event(self, event_id: UUID) -> AgentAction | None: ...

    async def actions_on(
        self,
        object_id: UUID,
        operation: str,
        *,
        since: datetime,
        until: datetime,
        statuses: Sequence[ActionStatus] | None = None,
    ) -> list[AgentAction]:
        """Actions on one object and canonical operation inside a time window, oldest first."""
        ...

    async def actions_of_object(self, object_id: UUID, *, limit: int = 100) -> list[AgentAction]:
        """Newest first; the object's timeline."""
        ...

    async def is_confirming(self, event_id: UUID) -> bool:
        """Whether a system event already confirms some action (one event confirms one action)."""
        ...

    async def set_action_status(
        self, action_id: UUID, status: ActionStatus, *, confirmed_by_event_id: UUID | None = None
    ) -> None: ...

    async def declared_before(self, cutoff: datetime, *, limit: int = 500) -> list[AgentAction]:
        """Declared actions on an object older than `cutoff`: the ones whose window ran out. An action on an
        object of `COORDINATION_NAMESPACE` waits for no system event, so it is never one of them."""
        ...

    async def divergent_actions_for(
        self, handle_ids: Sequence[UUID], *, since: datetime
    ) -> list[tuple[AgentAction, UUID]]:
        """Divergent actions on objects owned by the handles, with the object's origin handle."""
        ...

    async def vectors_of(self, object_ids: Sequence[UUID], action_ids: Sequence[UUID]) -> list[ItemVector]:
        """Stored vectors of these objects and actions, for history search (16.2)."""
        ...

    async def unembedded(
        self, *, model: str | None, handle_ids: Sequence[UUID] | None = None, limit: int
    ) -> list[PendingVector]:
        """Objects and actions without a vector or, with `model`, with one of another encoder: those of
        the handles, as `objects_for` and `actions_for` pick them, or the whole space's. Objects come
        fenced, since a new state rewrites their row."""
        ...

    async def set_vector(self, pending: PendingVector, vector: Sequence[float], model: str) -> bool:
        """Stores the vector sealed; False when the row is gone or changed since `pending` was read."""
        ...

    async def expire_retained(
        self,
        type_: str,
        before: datetime,
        *,
        limit: int,
        keep_objects: Collection[UUID],
        keep_handles: Collection[UUID],
    ) -> list[UUID]:
        """Customers' objects of the type that last changed before `before` (their type's retention passed,
        24.5.1), up to `limit`, with their participants, actions and event links; the objects in
        `keep_objects` and the ones owned by `keep_handles` (under a legal hold) stay. Returns the ids that
        went."""
        ...

    async def objects_page(self, after: UUID | None, limit: int) -> list[BusinessObject]:
        """The space's objects by id after `after`."""
        ...

    async def rewrite_state(self, obj: BusinessObject, unmatched: Sequence[tuple[str, str]]) -> int:
        """The object's state, observations and typed state in place, its stamps and dates kept, and the
        reverse relations no longer finding it by the (field, value) pairs in `unmatched`: an erasure
        redacts someone named in it. Returns the new change sequence."""
        ...

    async def actions_by_ids(self, action_ids: Sequence[UUID]) -> list[AgentAction]: ...

    async def actions_page(self, after: UUID | None, limit: int) -> list[AgentAction]:
        """The space's actions by id after `after`."""
        ...

    async def rewrite_result(self, action: AgentAction) -> None:
        """An action's result text in place: an erasure redacts someone named in it."""
        ...

    async def erase_unowned(self, keys: Sequence[ObjectKey], *, limit: int) -> int:
        """Deletes up to `limit` objects of these keys that have no owner, with their participants, actions
        and event links: what an observation brought back while the owner's erasure ran."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4). With `scope.event_ids`: actions and object events of those events.
        Otherwise: objects the target's handles own (with their events, participants and actions), the
        handles' participations and the actions that close one of their open items."""
        ...

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        """`ExportSource` (22.3) for `business_objects`, `object_events` and `agent_actions`: decrypted
        rows by `change_seq`."""
        ...
