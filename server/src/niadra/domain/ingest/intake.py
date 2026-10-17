"""The intake log: batches acknowledged before they are stored (5.2).

The acknowledgement waits for one durable write: the batch's idempotency keys claimed and its prepared
events sealed in one `ingest_intake` row, in a single statement. Everything else the write path does with
the database (conversations, identity, sessions, tasks) runs right after, in arrival order, and deletes
the row; the relay's copy of it in the outbox brings the row back to a worker if the process that
acknowledged it died first.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

INTAKE_AAD = "ingest_intake:{intake_id}:body"
MAX_INTAKE_ATTEMPTS = 5
"""A row that failed this many times is set aside, never deleted: it would block every later row of the
space, which is applied only after it. It stays sealed in the intake with `failed_at`, the company hears it as
a `row_failed` data issue, an admin retries it (`POST /v1/intake/failed/retry`), and an erasure or the
retention of the company's events reaches it like any stored row."""


MAX_FAILED_LISTED = 500
"""Rows set aside each module lists at once for the Console: a read bound; every one stays kept, and a retry
with no ids puts all of them back."""
ERASE_PAGE = 50
"""Rows set aside an erasure opens per transaction."""


def intake_aad(intake_id: UUID) -> bytes:
    return INTAKE_AAD.format(intake_id=intake_id).encode()


@dataclass(frozen=True, slots=True)
class NewIntake:
    intake_id: UUID
    space_id: UUID
    received_at: datetime
    source_id: UUID
    claims: tuple[tuple[str, UUID], ...]
    """Each event's idempotency key and the event id it claims."""
    body_ct: bytes
    dek_version: int


@dataclass(frozen=True, slots=True)
class IntakeRow:
    intake_id: UUID
    received_at: datetime
    body_ct: bytes
    dek_version: int
    skip: frozenset[str]
    """Keys an earlier batch already claimed: their events are duplicates and are not stored."""
    attempts: int = 0
    source_id: UUID | None = None
    failed_at: datetime | None = None
    """Set when the row failed every attempt: it is kept aside, out of the space's order, until retried."""


@dataclass(frozen=True, slots=True)
class FailedIntake:
    """A row set aside after failing every attempt (`MAX_INTAKE_ATTEMPTS`), as the Console lists it: what
    kind of batch, from which source, when; never its content."""

    intake_id: UUID
    kind: str
    """`events` for `POST /v1/events` and the webhooks, `turns` for `POST /v1/turns`."""
    source_id: UUID
    received_at: datetime
    failed_at: datetime
    attempts: int
