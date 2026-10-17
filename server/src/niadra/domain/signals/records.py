"""What the `signals` module keeps in the database (6.7): the sealed compact signals per subject and
beneficiary, the beneficiaries themselves, and the interactions waiting for `derive_signals`. Every body is
sealed by the service before it reaches a repository, so the cache can hold the same ciphertexts."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

PURPOSE = "personalization"
"""The purpose subject signals are kept for (24.5.1): an objection to it erases them and stops their
derivation."""
SELF = UUID(int=0)
"""The beneficiary id of the subject's own signals."""
GIFT = "gift"


def beneficiary_id(space_id: UUID, for_: str) -> UUID:
    """The id of whom an interaction is for: the subject itself, a gift's disposable bucket, or a beneficiary
    by the producer's id. Derived, so no lookup finds it."""
    if for_ == "self":
        return SELF
    digest = bytearray(hashlib.sha256(f"beneficiary:{space_id}:{for_}".encode()).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x80  # RFC 9562 version 8: custom, hash-derived
    digest[8] = (digest[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(digest))


def session_digest(mac: bytes) -> str:
    """A conversation as the signals keep it: the first 12 bytes of its keyed HMAC, in hex."""
    return mac[:12].hex()


SESSION_PURPOSE = "signals_session"
INFERENCE_PURPOSE = "signals_inference"
"""The keyed hash an inference is named by, in a URL and in its tombstone: never its value."""


def relation_of(for_: str) -> str:
    return "gift" if for_ == GIFT else "other"


def inference_input(beneficiary: UUID, token: str) -> bytes:
    """What an inference's keyed hash is taken over: whose body it is in, and its token."""
    return f"{beneficiary}|{token}".encode()


def body_aad(handle_id: UUID, beneficiary: UUID) -> bytes:
    return f"subject_signals:{handle_id}:{beneficiary}:body_ct".encode()


def label_aad(handle_id: UUID, beneficiary: UUID) -> bytes:
    return f"beneficiaries:{handle_id}:{beneficiary}:label_ct".encode()


def pending_aad(entry_key: str) -> bytes:
    return f"signal_intake:{entry_key}:body_ct".encode()


@dataclass(frozen=True, slots=True)
class PendingRow:
    """One interaction waiting to be derived. `handle_id` is None while its conversation has no known
    subject: kept, and checked again (`recheck_due`) until its conversation names one or a link resolves who
    it was; an erasure of the conversation, or of the person it turns out to be, takes it."""

    entry_key: str
    handle_id: UUID | None
    received_at: datetime
    body_ct: bytes
    dek_version: int
    session: str | None = None
    """The keyed digest of the conversation, so a conversation's erasure finds its rows."""
    checked_at: datetime | None = None
    """When the loop last looked for its conversation's subject and found none."""
    named: frozenset[bytes] = frozenset()
    """What it names a person by (`domain.privacy.mentions`), keyed-hashed: indexed as it is written."""


RECHECK_LEAST = timedelta(minutes=1)
RECHECK_MOST = timedelta(days=1)


def recheck_due(row: PendingRow, now: datetime) -> bool:
    """Whether an interaction without a subject is looked at again: after waiting as long as it had waited
    when last checked, a minute at least and a day at most. Often at first, when a conversation usually names
    its subject; daily after that, for as long as it takes."""
    if row.checked_at is None:
        return True
    wait = min(max(row.checked_at - row.received_at, RECHECK_LEAST), RECHECK_MOST)
    return row.checked_at + wait <= now


@dataclass(frozen=True, slots=True)
class SignalRow:
    handle_id: UUID
    beneficiary_id: UUID
    body_ct: bytes
    dek_version: int
    body_version: int
    size_bytes: int
    derived_at: datetime
    last_activity_at: datetime
    opted_out_at: datetime | None = None
    """Set on the subject's own row when they objected to personalization: nothing is derived for them."""


@dataclass(frozen=True, slots=True)
class BeneficiaryRow:
    handle_id: UUID
    beneficiary_id: UUID
    relation: str
    label_ct: bytes | None
    dek_version: int | None
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SuppressionRow:
    """An inference the subject deleted (24.5.3), by its keyed hash: evidence up to `evidence_until` never
    infers it again."""

    handle_id: UUID
    beneficiary_id: UUID
    key_hmac: bytes
    kind: str
    evidence_until: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What one derivation reads, in one statement: the handle's rows, its pending interactions and the
    inferences its subject deleted."""

    rows: tuple[SignalRow, ...]
    pending: tuple[PendingRow, ...]
    suppressed: tuple[SuppressionRow, ...] = ()

    @property
    def opted_out(self) -> bool:
        return any(r.opted_out_at is not None for r in self.rows)


@dataclass(frozen=True, slots=True)
class Segment:
    """One object of measurement data in object storage, as `measure_segments` indexes it."""

    segment_id: UUID
    kind: str
    starts_at: datetime
    object_key: str
    rows: int
    size_bytes: int
    sha256: bytes
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DueSnapshots:
    """What one pass of the derivation reads, in one statement: the subjects whose window closed, each with
    its rows and pending interactions, and whether interactions without a subject wait to be resolved."""

    snapshots: dict[UUID, Snapshot]
    unresolved: bool


@dataclass(frozen=True, slots=True)
class SignalWrite:
    """What one derivation writes, in one statement: the bodies, the beneficiaries seen, and the removal of
    the pending interactions it folded. Nothing is written for a subject who objected meanwhile."""

    handle_id: UUID
    at: datetime
    rows: tuple[SignalRow, ...]
    beneficiaries: tuple[BeneficiaryRow, ...]
    folded: tuple[str, ...]
    raw_key: str | None = None
    """The raw interactions kept for measurement, written before the statement and indexed in it, whether or
    not the subject objected meanwhile (`domain.privacy.raw_index`)."""
    named: tuple[tuple[UUID, frozenset[bytes]], ...] = ()
    """What each body names a person by, keyed-hashed, by beneficiary (`domain.privacy.mentions`): indexed in
    the same statement, added to what the body named before."""
