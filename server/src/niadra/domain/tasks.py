"""The asynchronous task catalog (4.5) and how each task is routed.

Every task is born as an outbox row inside the transaction that motivated it (L2), then
travels through the broker. Ordering is not a guarantee of the queue: tasks are
commutative, carry the profile's composition version, and the profile lease serializes
anything that writes profile state (5.5).
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from niadra.domain.common.ids import uuid7


class TaskName(StrEnum):
    # Not in the catalog yet: `llm_batch_*` waits for an LLM endpoint with a batch API, and
    # `analytics_sync` for a space whose volume needs its own analytical tables (5.5, 5.21).
    CLOSE_SESSION = "close_session"
    GATE = "gate"
    EXTRACT = "extract"
    RECONCILE = "reconcile"
    APPLY_OBJECT_EVENT = "apply_object_event"
    CLOSE_ITEMS = "close_items"
    EMBED = "embed"
    COMPILE = "compile"
    MEASURE = "measure"
    MEASURE_TRANSFER = "measure_transfer"
    TRIGGER = "trigger"
    DELIVER_WEBHOOK = "deliver_webhook"
    CONSOLIDATE = "consolidate"
    DERIVE_TRAITS = "derive_traits"
    ERASE = "erase"
    EXPORT_RUN = "export_run"
    SAMPLE_REVIEW = "sample_review"
    ASSIST = "assist"
    ROLLUP = "rollup"
    IDENTITY_APPLY = "identity_apply"
    HOT_REPAIR = "hot_repair"
    APPLY_INTAKE = "apply_intake"
    HOT_REBUILD = "hot_rebuild"
    SEED_GEN = "seed_gen"
    ARCHIVE_EVENTS = "archive_events"
    ERASE_COLD = "erase_cold"
    RETENTION_SWEEP = "retention_sweep"
    AUDIT_ANCHOR = "audit_anchor"
    USAGE_ROLLUP = "usage_rollup"
    OBSERVE_CONFIG = "observe_config"
    IDENTITY_SUGGEST = "identity_suggest"
    IMPORT_FILE = "import_file"
    DEDUPE_ROTATE = "dedupe_rotate"
    PARTITION_MAINTENANCE = "partition_maintenance"
    RECORD_RECEIPTS = "record_receipts"
    DISTILL_NOTE = "distill_note"
    MEDIA_TEXT = "media_text"
    REDERIVE_TRAITS = "rederive_traits"
    REEXTRACT = "reextract"
    MASK_EVENTS = "mask_events"
    WITHHOLD_STORED = "withhold_stored"
    # The agent features' tasks (4.5.1).
    APPLY_TURNS = "apply_turns"
    NOTIFY_FLAGGED_TURNS = "notify_flagged_turns"
    NOTIFY_DATA_ISSUE = "notify_data_issue"
    APPEND_NOTIFICATION = "append_notification"
    SCAN_TURN_ARGUMENTS = "scan_turn_arguments"
    APPLY_OBSERVATIONS = "apply_observations"
    FIRE_TIMERS = "fire_timers"
    APPLY_SNAPSHOT = "apply_snapshot"
    OBSERVE_OWNERSHIP = "observe_ownership"
    COORDINATION_SHADOW = "coordination_shadow"
    CONTACT_WINDOW = "contact_window"
    APPLY_INTERACTIONS = "apply_interactions"
    WITHDRAW_SIGNALS = "withdraw_signals"
    PURGE_SIGNALS = "purge_signals"
    ATTRIBUTE_OUTCOMES = "attribute_outcomes"
    RELEASE_HELD = "release_held"


class Lease(StrEnum):
    NONE = "none"
    PROFILE = "profile"
    PROFILES = "profiles"
    SPACE = "space"
    OWN = "own"
    """One run of the task per space at a time, and nothing else waits for it."""


class QueueClass(StrEnum):
    """One broker queue per class; each class has its own worker pool (06, 4.4)."""

    REALTIME = "niadra.realtime"
    IDENTITY = "niadra.identity"
    EXTRACTION = "niadra.extraction"
    MEASURE = "niadra.measure"
    DELIVERY = "niadra.delivery"
    BACKGROUND = "niadra.background"
    VOLUME = "niadra.volume"


@dataclass(frozen=True, slots=True)
class TaskSpec:
    queue: QueueClass
    lease: Lease
    coalesce: bool = False
    max_attempts: int = 3
    urgent: bool = False
    """Takes its busy lease next, ahead of the other tasks waiting for it (`app.lease_claims`): what an agent
    reads right after it happened, never queued behind a burst of the same customer's background work."""
    unowned: bool = False
    """A profile task that also runs for a session of no customer (5.3: only a test or placeholder value, or
    no handle at all). It then holds no lease, since there is no profile state to serialize, and its handler
    writes nothing to memory."""


CATALOG: dict[TaskName, TaskSpec] = {
    # The three session tasks run for a session of no customer too: it closes, and is billed, like any other.
    TaskName.CLOSE_SESSION: TaskSpec(QueueClass.REALTIME, Lease.PROFILE, urgent=True, unowned=True),
    TaskName.GATE: TaskSpec(QueueClass.REALTIME, Lease.PROFILE, unowned=True),
    TaskName.EXTRACT: TaskSpec(QueueClass.EXTRACTION, Lease.NONE, max_attempts=5),
    TaskName.RECONCILE: TaskSpec(QueueClass.REALTIME, Lease.PROFILE, unowned=True),
    TaskName.APPLY_OBJECT_EVENT: TaskSpec(QueueClass.REALTIME, Lease.PROFILE, urgent=True),
    TaskName.CLOSE_ITEMS: TaskSpec(QueueClass.REALTIME, Lease.PROFILE),
    TaskName.EMBED: TaskSpec(QueueClass.REALTIME, Lease.PROFILE),
    # Urgent: the compilation is what makes new memory, an action or a system event readable;
    # behind the embed, trigger and trait tasks that the same change requested, it lost the lease for 5 s at a
    # time, 118 times in three minutes on the region cell of 30/09.
    TaskName.COMPILE: TaskSpec(QueueClass.REALTIME, Lease.PROFILE, coalesce=True, urgent=True),
    TaskName.MEASURE: TaskSpec(QueueClass.MEASURE, Lease.NONE),
    TaskName.MEASURE_TRANSFER: TaskSpec(QueueClass.MEASURE, Lease.NONE),
    TaskName.TRIGGER: TaskSpec(QueueClass.REALTIME, Lease.PROFILE, coalesce=True),
    TaskName.DELIVER_WEBHOOK: TaskSpec(QueueClass.DELIVERY, Lease.NONE, max_attempts=1),
    TaskName.CONSOLIDATE: TaskSpec(QueueClass.BACKGROUND, Lease.PROFILE),
    TaskName.DERIVE_TRAITS: TaskSpec(QueueClass.REALTIME, Lease.PROFILE, coalesce=True),
    TaskName.ERASE: TaskSpec(QueueClass.IDENTITY, Lease.PROFILE, max_attempts=10),
    TaskName.EXPORT_RUN: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    TaskName.SAMPLE_REVIEW: TaskSpec(QueueClass.MEASURE, Lease.NONE),
    TaskName.ASSIST: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    TaskName.DISTILL_NOTE: TaskSpec(QueueClass.BACKGROUND, Lease.NONE),
    TaskName.MEDIA_TEXT: TaskSpec(QueueClass.BACKGROUND, Lease.NONE),
    TaskName.REDERIVE_TRAITS: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    TaskName.REEXTRACT: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    TaskName.MASK_EVENTS: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    TaskName.WITHHOLD_STORED: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    TaskName.ROLLUP: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    TaskName.IDENTITY_APPLY: TaskSpec(QueueClass.IDENTITY, Lease.PROFILES, max_attempts=10),
    TaskName.HOT_REPAIR: TaskSpec(QueueClass.REALTIME, Lease.NONE, max_attempts=5),
    # A batch acknowledged by a process that died before storing it: the relay publishes its outbox copy.
    TaskName.APPLY_INTAKE: TaskSpec(QueueClass.REALTIME, Lease.NONE, max_attempts=10),
    TaskName.HOT_REBUILD: TaskSpec(QueueClass.REALTIME, Lease.NONE),
    TaskName.SEED_GEN: TaskSpec(QueueClass.REALTIME, Lease.NONE),
    TaskName.ARCHIVE_EVENTS: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    TaskName.ERASE_COLD: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    TaskName.RETENTION_SWEEP: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    TaskName.AUDIT_ANCHOR: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    TaskName.USAGE_ROLLUP: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    # What a space's traffic showed of its types, under its declaration; once a minute.
    # Its own lease: a run may call the model to word new names, and the space's lease would keep a system's
    # state push (`apply_observations`) and the due timers waiting behind it.
    TaskName.OBSERVE_CONFIG: TaskSpec(QueueClass.BACKGROUND, Lease.OWN, max_attempts=1),
    TaskName.IDENTITY_SUGGEST: TaskSpec(QueueClass.BACKGROUND, Lease.SPACE),
    TaskName.IMPORT_FILE: TaskSpec(QueueClass.VOLUME, Lease.NONE),
    TaskName.DEDUPE_ROTATE: TaskSpec(QueueClass.BACKGROUND, Lease.NONE),
    TaskName.PARTITION_MAINTENANCE: TaskSpec(QueueClass.BACKGROUND, Lease.NONE),
    # Receipts the queue did not confirm in time, committed to the outbox before the read answered.
    TaskName.RECORD_RECEIPTS: TaskSpec(QueueClass.REALTIME, Lease.NONE, max_attempts=10),
    # Recorded turns acknowledged and not yet added up; the relay publishes the outbox copy if the hand-over
    # to the workers failed.
    TaskName.APPLY_TURNS: TaskSpec(QueueClass.REALTIME, Lease.NONE, max_attempts=10),
    # `turn.flagged` for the turns a group kept by their flags, written by the group's own statement.
    TaskName.NOTIFY_FLAGGED_TURNS: TaskSpec(QueueClass.DELIVERY, Lease.NONE),
    # `data_issue.opened` for an issue just opened, written by the statement that opened it.
    TaskName.NOTIFY_DATA_ISSUE: TaskSpec(QueueClass.DELIVERY, Lease.NONE),
    # An event's copy for the notification feed, numbered after the transaction that announced it committed.
    TaskName.APPEND_NOTIFICATION: TaskSpec(QueueClass.DELIVERY, Lease.NONE),
    # The PII model over stored records' tool arguments, behind the realtime work of the models pod.
    TaskName.SCAN_TURN_ARGUMENTS: TaskSpec(QueueClass.BACKGROUND, Lease.NONE, max_attempts=5),
    # What the tools of a group of applied turns showed of the subjects' objects; one at a time per space, so
    # two groups never write the same object at once.
    TaskName.APPLY_OBSERVATIONS: TaskSpec(QueueClass.REALTIME, Lease.SPACE),
    # A space's due timers, at the due time of one armed within the hour (24.3.8); the sweep takes the rest.
    TaskName.FIRE_TIMERS: TaskSpec(QueueClass.REALTIME, Lease.SPACE),
    # A shared type's snapshot, staged in object storage, applied as pushes a part at a time (24.3.12).
    TaskName.APPLY_SNAPSHOT: TaskSpec(QueueClass.BACKGROUND, Lease.NONE, max_attempts=5),
    # A system event a mapped ownership source sent: who holds its subject now (24.3.4).
    TaskName.OBSERVE_OWNERSHIP: TaskSpec(QueueClass.REALTIME, Lease.NONE, max_attempts=10),
    # A shadow run reads weeks of events a page at a time, off the hot path.
    TaskName.COORDINATION_SHADOW: TaskSpec(QueueClass.BACKGROUND, Lease.NONE, max_attempts=3),
    # The hours a customer said they may be contacted in, as a suppression of every purpose outside them on
    # each of their destinations, retried apart from the extraction that found them (suppression-list, 6.1).
    TaskName.CONTACT_WINDOW: TaskSpec(QueueClass.REALTIME, Lease.NONE, max_attempts=10),
    # A standalone interaction batch the request kept in object storage (24.3.2).
    TaskName.APPLY_INTERACTIONS: TaskSpec(QueueClass.REALTIME, Lease.NONE, max_attempts=5),
    # An objection to personalization, and what an erasure leaves outside the database (24.5).
    TaskName.WITHDRAW_SIGNALS: TaskSpec(QueueClass.IDENTITY, Lease.NONE, max_attempts=10),
    TaskName.PURGE_SIGNALS: TaskSpec(QueueClass.IDENTITY, Lease.NONE, max_attempts=10),
    # Objects that reached an outcome, or passed their deadline without one, attributed whole (24.7).
    TaskName.ATTRIBUTE_OUTCOMES: TaskSpec(QueueClass.MEASURE, Lease.NONE, max_attempts=5),
    # A legal hold released: what erasures left waiting under it goes now (24.5.1).
    TaskName.RELEASE_HELD: TaskSpec(QueueClass.IDENTITY, Lease.NONE, max_attempts=10),
}


@dataclass(frozen=True, slots=True)
class Task:
    """A unit of asynchronous work. `payload` carries pointers, never customer content (L2)."""

    name: TaskName
    space_id: UUID
    payload: dict[str, Any] = field(default_factory=dict)
    profile_id: UUID | None = None
    id: UUID = field(default_factory=uuid7)

    @property
    def spec(self) -> TaskSpec:
        return CATALOG[self.name]

    @property
    def queue(self) -> str:
        return self.spec.queue.value

    def to_message(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name.value,
            "space_id": str(self.space_id),
            "profile_id": str(self.profile_id) if self.profile_id else None,
            "payload": self.payload,
        }

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "Task":
        return cls(
            id=UUID(message["id"]),
            name=TaskName(message["name"]),
            space_id=UUID(message["space_id"]),
            profile_id=UUID(message["profile_id"]) if message.get("profile_id") else None,
            payload=dict(message.get("payload") or {}),
        )
