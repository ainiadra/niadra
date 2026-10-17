"""What the `turns` module keeps in the database (6.7): the intake of `POST /v1/turns`, the index of the
kept tier, the day's totals, and the builds and tool schemas seen."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

from niadra.domain.turns.storage import KeptBy

INTAKE_AAD = "turn_intake:{intake_id}:body"
MAX_INTAKE_ATTEMPTS = 5
"""An intake that failed this many times is set aside, sealed, never deleted, so it never holds back the rest:
the company hears it as a `row_failed` issue and an admin retries it."""


def intake_aad(intake_id: UUID) -> bytes:
    return INTAKE_AAD.format(intake_id=intake_id).encode()


@dataclass(frozen=True, slots=True)
class NewTurnIntake:
    """One acknowledged `POST /v1/turns`: the frames of the turns stored for the first time, sealed."""

    intake_id: UUID
    space_id: UUID
    received_at: datetime
    source_id: UUID
    turns: int
    body_ct: bytes
    dek_version: int


@dataclass(frozen=True, slots=True)
class TurnIntakeRow:
    intake_id: UUID
    received_at: datetime
    source_id: UUID
    body_ct: bytes
    dek_version: int
    attempts: int = 0
    failed_at: datetime | None = None
    """Set when the intake failed every attempt: kept aside, out of the space's order, until retried."""


@dataclass(frozen=True, slots=True)
class TakenIntake:
    """The oldest intakes of a space, and the conversations an erasure fenced off: their turns are dropped,
    whatever intake brings them."""

    rows: list[TurnIntakeRow]
    fenced: frozenset[bytes] = frozenset()


TOMBSTONE_DAYS = 8
"""How long an erasure's fence holds: past the days a producer retries a turn, so its late copy of a turn of
the erased conversation finds the fence still up and is dropped."""


def conversation_mark(conversation: bytes) -> str:
    return f"conversation:{conversation.hex()}"


def needle_mark(needle: str) -> str:
    return f"needle:{needle}"


@dataclass(frozen=True, slots=True)
class KeptTurn:
    """A row of `turns_kept`: what finds a kept turn and what the viewer lists, never its content."""

    turn_id: str
    conversation: bytes
    source_id: UUID
    agent: str
    kind: str
    started_at: datetime
    fidelity: str
    completeness: str
    content_mode: str
    replayable: bool
    build_hash: bytes | None
    flags: tuple[str, ...]
    kept_by: KeptBy
    kept_at: datetime
    kept_until: datetime | None


@dataclass(frozen=True, slots=True)
class BuildSeen:
    build_hash: bytes
    source_id: UUID
    agent: str
    pins: Mapping[str, Any]
    sdk: str | None
    adapter: str | None
    at: datetime


@dataclass(slots=True)
class DailyTotals:
    """One row of `turn_daily`: what the turns of a day added up to for one source, agent and build."""

    day: date
    source_id: UUID
    agent: str
    build_hash: bytes
    turns: int = 0
    incomplete: int = 0
    tool_calls: int = 0
    model_calls: int = 0
    errors: int = 0
    synthetic: int = 0
    cache_hits: int = 0
    tokens_in: int = 0
    tokens_cached: int = 0
    tokens_out: int = 0
    cost_micros: int = 0
    latency_buckets: list[int] = field(default_factory=list)
    claims: dict[str, int] = field(default_factory=dict)
    constraints_sent: int = 0
    results_checked: int = 0
    violations: int = 0
    """Results that broke a hard constraint the tool did not declare it relaxed; a suspected relaxation
    still counts."""
    unverifiable: int = 0
    relaxed_declared: int = 0
    relaxed_suspected: int = 0
    effects: int = 0
    items_presented: int = 0
    """Items the turn's `presented` interactions showed: the denominator of violations per item shown."""

    @property
    def key(self) -> tuple[date, UUID, str, bytes]:
        return (self.day, self.source_id, self.agent, self.build_hash)


@dataclass(frozen=True, slots=True)
class AppliedTurns:
    """What one group of intakes adds to the database, written in one statement with the intakes' removal."""

    intake_ids: tuple[UUID, ...]
    daily: tuple[DailyTotals, ...] = ()
    builds: tuple[BuildSeen, ...] = ()
    kept: tuple[KeptTurn, ...] = ()
    flagged: tuple[dict[str, Any], ...] = ()
    """The `turn.flagged` bodies of the turns kept by their flags, when an endpoint subscribes: the same
    statement adds the task that sends them."""
    scans: tuple[dict[str, Any], ...] = ()
    """The payloads of the tasks that pass the PII model over the stored records' tool arguments, when the
    space asks for it: the same statement adds them."""
    mentions: tuple[tuple[str, frozenset[bytes]], ...] = ()
    """Each turn object's key with what its record names a person by, keyed-hashed
    (`domain.privacy.mentions`): the same statement adds them to what the object named, a part to its
    turn's."""


@dataclass(frozen=True, slots=True)
class TurnQuery:
    """A search of the kept tier (24.3.5, L20): by conversation, agent, time and flags, newest first."""

    conversation: bytes | None = None
    agent: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    flags: tuple[str, ...] = ()
    before: tuple[datetime, str] | None = None
    """The keyset cursor: the last row of the previous page, as (started_at, turn_id)."""
    limit: int = 50
    sources: tuple[UUID, ...] | None = None
    """The sources a person may see, when their role names some."""


@dataclass(frozen=True, slots=True)
class ScenarioRow:
    """A row of `scenarios`: kept turns and the assertions they must keep passing, as JSON documents."""

    scenario_id: UUID
    name: str
    origin: str
    turn_ids: tuple[str, ...]
    assertions: tuple[dict[str, Any], ...]
    status: str
    version: int
    created_at: datetime
    created_by: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ScenarioChange:
    """What a `PATCH` changes; None keeps the current value."""

    name: str | None = None
    assertions: tuple[dict[str, Any], ...] | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class ScenarioRunRow:
    """A row of `scenario_runs`: the verdict of one report of the company's CI, per scenario in `summary`."""

    run_id: UUID
    scenario_ids: tuple[UUID, ...]
    build: dict[str, Any]
    mode: str
    vary: tuple[str, ...]
    status: str
    verdict: str | None
    summary: dict[str, Any]
    created_at: datetime
    decided_at: datetime | None
