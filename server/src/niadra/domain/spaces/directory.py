"""The space directory: which database holds each space, and whether it can serve.

The control plane owns the list of spaces; the cell's directory (`space_databases`) mirrors it with
where each database lives and its state. The provisioner moves a row `creating -> migrating -> ready`;
the setup runner moves it `ready -> migrating -> ready` (or `failed`) on every release; a restore
passes through `maintenance`; a deletion ends in `deleted`, and the row stays as the record.
"""

import dataclasses
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID


class DbStatus(StrEnum):
    CREATING = "creating"
    MIGRATING = "migrating"
    READY = "ready"
    MAINTENANCE = "maintenance"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class Tier(StrEnum):
    """T0 shares the cell's instance; T1 and T2 have their own."""

    T0 = "t0"
    T1 = "t1"
    T2 = "t2"


PRIMARY_INSTANCE = "primary"
DB_PREFIX = "s_"
# Memory tables are hash-partitioned per database. A space on the shared instance gets one partition per
# table: eight made a database of about 500 relations, and every backend that touched them cached them all
# (20, 8.2, B6). A space with an instance of its own gets 32. Spaces created before keep their eight.
HASH_MODULUS = {Tier.T0: 1, Tier.T1: 32, Tier.T2: 32}
# A migration applies one transactional revision at a time, so a database that is migrating, or whose
# migration failed, sits at a whole revision and keeps serving.
SERVING = frozenset({DbStatus.READY, DbStatus.MIGRATING, DbStatus.FAILED})
RETRY_AFTER_S = 5


@dataclass(frozen=True, slots=True)
class SpaceDatabase:
    """One row of the directory."""

    space_id: UUID
    tenant_id: UUID
    environment: str
    db_name: str
    status: DbStatus
    hash_modulus: int
    instance: str = PRIMARY_INSTANCE
    tier: Tier = Tier.T0
    schema_version: str | None = None
    status_detail: str | None = None
    failures: int = 0
    retry_at: datetime | None = None
    created_at: datetime | None = None
    ready_at: datetime | None = None
    deleted_at: datetime | None = None

    @property
    def serving(self) -> bool:
        """Requests and tasks may use the database: it exists, is stamped and has a schema."""
        return self.schema_version is not None and self.status in SERVING

    @property
    def unfinished(self) -> bool:
        """A creation the provisioner has not finished: the database was never ready. It may serve
        already, when a step after the partitions failed; the provisioner retries it until it is ready."""
        return self.ready_at is None

    @property
    def gone(self) -> bool:
        return self.status in (DbStatus.DELETING, DbStatus.DELETED)

    def moved(self, status: DbStatus, **changes: object) -> "SpaceDatabase":
        return dataclasses.replace(self, status=status, **changes)  # type: ignore[arg-type]


def db_name_for(space_id: UUID) -> str:
    """`s_` and the 32 hex digits of the space id: a valid identifier that never needs quoting."""
    return f"{DB_PREFIX}{space_id.hex}"


def space_of(db_name: str) -> UUID | None:
    """The space of a database name, when it is one of ours and not a restore or a retired copy."""
    if not db_name.startswith(DB_PREFIX) or len(db_name) != len(DB_PREFIX) + 32:
        return None
    try:
        return UUID(hex=db_name[len(DB_PREFIX) :])
    except ValueError:
        return None


def new_entry(
    space_id: UUID,
    tenant_id: UUID,
    environment: str,
    now: datetime,
    tier: Tier = Tier.T0,
    instance: str = PRIMARY_INSTANCE,
) -> SpaceDatabase:
    """The directory row of a new space database, on the instance and with the tier its placement names
    (the control plane's snapshot; T2 on a cell dedicated to the tenant)."""
    return SpaceDatabase(
        space_id=space_id,
        tenant_id=tenant_id,
        environment=environment,
        db_name=db_name_for(space_id),
        status=DbStatus.CREATING,
        hash_modulus=HASH_MODULUS[tier],
        instance=instance,
        tier=tier,
        created_at=now,
    )


# Provisioning retries: 30 s, 1 min, 5 min, then every 15 min; an alert fires after three failures.
RETRY_BACKOFF = (timedelta(seconds=30), timedelta(minutes=1), timedelta(minutes=5))
RETRY_CAP = timedelta(minutes=15)


def next_retry(failures: int, now: datetime) -> datetime:
    """When a creation that failed `failures` times in a row may run again."""
    wait = RETRY_BACKOFF[failures - 1] if 0 < failures <= len(RETRY_BACKOFF) else RETRY_CAP
    return now + wait


def detail(error: BaseException) -> str:
    """What the directory keeps of a failure: the type and the start of the message, never data."""
    return f"{type(error).__name__}: {error}"[:500]


def waves(rows: Iterable[SpaceDatabase]) -> list[list[SpaceDatabase]]:
    """The order of a fleet migration: sandboxes, then small production spaces, then the large ones.
    A bad migration shows up where it costs least; each wave finishes before the next starts."""
    sandbox, small, large = [], [], []
    for row in sorted(rows, key=lambda r: r.db_name):
        if row.environment == "sandbox":
            sandbox.append(row)
        elif row.tier is Tier.T0:
            small.append(row)
        else:
            large.append(row)
    return [wave for wave in (sandbox, small, large) if wave]


@dataclass(frozen=True, slots=True)
class WantedSpace:
    """A space of the configuration snapshot, as far as its database is concerned."""

    space_id: UUID
    tenant_id: UUID
    environment: str
    tier: Tier = Tier.T0
    instance: str = PRIMARY_INSTANCE


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """What the reconciler does in one pass; computed from the snapshot, the directory and the server."""

    create: list[WantedSpace]
    retry: list[SpaceDatabase]
    delete: list[SpaceDatabase]
    drift: list[UUID]
    orphans: list[str]


def reconcile(
    wanted: Iterable[WantedSpace],
    removed: Mapping[UUID, datetime],
    rows: Iterable[SpaceDatabase],
    databases: Iterable[str],
    now: datetime,
) -> Reconciliation:
    """The rules of 8.1. A space missing from the snapshot and not listed as removed is drift and is
    left alone: a truncated or stale snapshot must never delete a database."""
    by_space = {row.space_id: row for row in rows}
    wanted_by_space = {w.space_id: w for w in wanted}
    create = [w for w in wanted_by_space.values() if w.space_id not in by_space and w.space_id not in removed]
    # A creation that never finished (it was never ready); a failed migration of a live database is the
    # setup runner's, which retries it on the next release.
    retry = [
        row
        for row in by_space.values()
        if row.status in (DbStatus.CREATING, DbStatus.MIGRATING, DbStatus.FAILED)
        and row.unfinished
        and row.space_id in wanted_by_space
        and (row.retry_at is None or row.retry_at <= now)
    ]
    delete = [
        row for row in by_space.values() if row.space_id in removed and row.status is not DbStatus.DELETED
    ]
    drift = sorted(
        space_id
        for space_id, row in by_space.items()
        if not row.gone and space_id not in wanted_by_space and space_id not in removed
    )
    known = {row.db_name for row in by_space.values() if row.status is not DbStatus.DELETED}
    orphans = sorted(name for name in databases if space_of(name) is not None and name not in known)
    return Reconciliation(sorted(create, key=lambda w: str(w.space_id)), retry, delete, drift, orphans)
