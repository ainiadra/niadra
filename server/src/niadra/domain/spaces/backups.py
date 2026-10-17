"""Nightly dumps of each database, and the names a restore uses (11).

The managed PostgreSQL only restores a whole instance, so each space database (and the cell database)
is also dumped every night to the cell's bucket, where the objects expire after 35 days. A restore
loads a dump beside the live database and swaps the names; the old one is kept for a week.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

BACKUP_PREFIX = "backups/"
RETIRED_KEEP = timedelta(days=7)
# Digits only: database names stay plain lowercase identifiers.
_STAMP = "%Y%m%d%H%M%S"
_RETIRED = re.compile(r"^(?P<db>s_[0-9a-f]{32})_old_(?P<at>\d{14})$")


def backup_prefix(db_name: str) -> str:
    return f"{BACKUP_PREFIX}{db_name}/"


def dump_key(db_name: str, at: datetime) -> str:
    return f"{backup_prefix(db_name)}{at.astimezone(UTC):%Y%m%dT%H%M}.dump"


def latest_key(db_name: str) -> str:
    return f"{backup_prefix(db_name)}latest.json"


def restore_name(db_name: str, at: datetime) -> str:
    """The database a dump is restored into, beside the live one."""
    return f"{db_name}_r{at.astimezone(UTC):{_STAMP}}"


def retired_name(db_name: str, at: datetime) -> str:
    """What the live database is renamed to when a restore takes its place."""
    return f"{db_name}_old_{at.astimezone(UTC):{_STAMP}}"


def retired_at(name: str) -> datetime | None:
    """When a retired copy was set aside, for the reconciler that drops it after a week."""
    match = _RETIRED.match(name)
    if match is None:
        return None
    return datetime.strptime(match["at"], _STAMP).replace(tzinfo=UTC)


def retired_to_drop(names: list[str], now: datetime) -> list[str]:
    return sorted(n for n in names if (at := retired_at(n)) is not None and now - at >= RETIRED_KEEP)


@dataclass(frozen=True, slots=True)
class DumpInfo:
    """`latest.json` of a database: what the last nightly dump holds."""

    key: str
    db_name: str
    size_bytes: int
    sha256: str
    schema_version: str | None
    taken_at: datetime

    def document(self) -> dict[str, object]:
        return {
            "key": self.key,
            "db_name": self.db_name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "schema_version": self.schema_version,
            "taken_at": self.taken_at.astimezone(UTC).isoformat(),
        }

    @classmethod
    def from_document(cls, doc: dict[str, object]) -> "DumpInfo":
        schema = doc.get("schema_version")
        return cls(
            key=str(doc["key"]),
            db_name=str(doc["db_name"]),
            size_bytes=int(str(doc["size_bytes"])),
            sha256=str(doc["sha256"]),
            schema_version=None if schema is None else str(schema),
            taken_at=datetime.fromisoformat(str(doc["taken_at"])),
        )
