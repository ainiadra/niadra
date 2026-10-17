"""Where the `signals` module keeps things outside the database (24.3.2, 24.5).

- `measure/raw/<space>/<handle>/<day>/<id>.bin`: the raw interactions of one handle, sealed, kept until an
  erasure or the `raw_content` retention the customer sets; under the handle, so an erasure is a prefix
  delete, as it is for raw payloads.
- `measure/raw/<space>/_days/<day>/<handle>/<id>`: an empty marker of each raw object, so the nightly pass
  lists one day instead of every handle's history; the pass deletes a day's markers once it read them.
- `measure/intake/<space>/<id>.bin`: a standalone interaction batch until its worker takes it.
- `measure/daily/<space>/<day>.parquet`: the day's aggregate by pseudonym and field value, sealed, 13 months,
  indexed in `measure_segments`.
- `measure/demand/<space>/<day>.parquet`: the day's unmet demand by pseudonym, tool and combination, sealed,
  13 months, indexed in `measure_segments`.
- `measure/bi/<space>/<upload>.csv`: a BI file the company uploaded for a reconciliation, deleted once read
  (two days by the bucket's rule otherwise).
- `ov:<space>:{<handle>}` in the state store: the session overlay, the interactions not derived yet, so the
  next turn of any agent sees them.
- `sg:<space>:<handle>` in the cache: the handle's sealed rows, so a warm read runs no statement.

Sealed objects are the data-key version (4 bytes, big endian) followed by the ciphertext; the AAD is the
object's key.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

RAW = "measure/raw/"
INTAKE = "measure/intake/"
DAILY = "measure/daily/"
DEMAND = "measure/demand/"
BI = "measure/bi/"


def raw_key(space_id: UUID, handle_id: UUID, day: date, object_id: UUID) -> str:
    return f"{RAW}{space_id}/{handle_id}/{day.isoformat()}/{object_id}.bin"


def raw_prefix(space_id: UUID, handle_id: UUID) -> str:
    return f"{RAW}{space_id}/{handle_id}/"


def day_marker(space_id: UUID, day: date, handle_id: UUID, object_id: UUID) -> str:
    return f"{day_markers(space_id, day)}{handle_id}/{object_id}"


def day_markers(space_id: UUID, day: date, handle_id: UUID | None = None) -> str:
    return f"{RAW}{space_id}/_days/{day.isoformat()}/" + (f"{handle_id}/" if handle_id else "")


def raw_of_marker(space_id: UUID, day: date, marker: str) -> tuple[UUID, str]:
    """The handle and the raw object a day's marker names."""
    handle, _, object_id = marker.removeprefix(day_markers(space_id, day)).partition("/")
    return UUID(handle), raw_key(space_id, UUID(handle), day, UUID(object_id))


def intake_key(space_id: UUID, intake_id: UUID) -> str:
    return f"{INTAKE}{space_id}/{intake_id}.bin"


def intake_body(source_id: UUID, received_at: datetime, items: Sequence[Mapping[str, Any]]) -> bytes:
    """A standalone interaction batch as `apply_interactions` reads it: the items as the batch writes them."""
    body = {"source_id": str(source_id), "received_at": received_at.isoformat(), "items": list(items)}
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()


def daily_key(space_id: UUID, day: date) -> str:
    return f"{DAILY}{space_id}/{day.isoformat()}.parquet"


def bi_key(space_id: UUID, upload_id: UUID) -> str:
    return f"{BI}{space_id}/{upload_id}.csv"


def demand_key(space_id: UUID, day: date) -> str:
    return f"{DEMAND}{space_id}/{day.isoformat()}.parquet"


def priors_key(space_id: UUID) -> str:
    return f"sp:{space_id}"


def object_aad(key: str) -> bytes:
    return f"object:{key}".encode()


def overlay_key(space_id: UUID, handle_id: UUID) -> str:
    # The hash tag is the handle's, as the hot layer's: its folded set lands in the same slot.
    return f"ov:{space_id}:{{{handle_id}}}"


def folded_key(space_id: UUID, handle_id: UUID) -> str:
    return f"ovd:{space_id}:{{{handle_id}}}"


def cache_key(space_id: UUID, handle_id: UUID) -> str:
    return f"sg:{space_id}:{handle_id}"
