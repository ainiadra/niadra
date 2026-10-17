"""What the read process keeps of one customer between reads, and the channels that rank over it (11,
3.3).

Opening a customer's sealed rows costs far more than ranking them, so what the channels read of a profile's
navigable rows is kept here once per generation: the vectors in half precision, the settled values by type
and by key, the system events as temporal anchors. The policy is not applied here; the entry holds every
row and each read filters by what its reader may see, as the compiled pack is rechecked (L8).

Ranking stays exact inside one customer (no approximate index, 16.2). The cosine runs on the standard
library (`math.sumprod` over a half-precision view), a few milliseconds for a few hundred rows.
"""

import math
import struct
from array import array
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.memory.vectors import ItemVector
from niadra.domain.serve.history import HistoryDoc
from niadra.domain.serve.relative_time import SystemMark

_DEADLINE_EVERY = 32
"""Rows between two looks at the clock while the cosine runs: a look costs about as much as a row."""

# Bytes the process holds beyond the raw data, measured with tracemalloc on CPython 3.13 (the unit test
# `test_the_estimate_follows_what_python_allocates` keeps them honest).
VECTOR_OVERHEAD = 100
"""A row's slot in the vector map, its id and its inverse norm, on top of its half-precision bytes."""
VALUE_OVERHEAD = 700
MARK_OVERHEAD = 200
ENTRY_OVERHEAD = 2_000


class ChannelStatus(StrEnum):
    RAN = "ran"
    SKIPPED = "skipped"
    """The channel was on and did not answer in time or at all: the slots go out without it."""
    OFF = "off"
    EMPTY = "empty"
    """Nothing to rank: no rows with a vector of the question's encoder, or an empty question."""


@dataclass(frozen=True, slots=True)
class ChannelResult:
    """What one retrieval channel returned for a turn: scores by row id, best first by `ranking()`.
    `reason` says why a channel was skipped (`deadline`, `unavailable`, `busy`, `cold`); the manifest
    records the status and the reason, never the question."""

    channel: str
    status: ChannelStatus
    scores: Mapping[str, float] = field(default_factory=dict)
    reason: str | None = None


@dataclass(frozen=True, slots=True, eq=False)
class ProfileEntry:
    profile_id: UUID
    generation: int
    handles: frozenset[UUID]
    privacy_version: int
    vector_model: str | None
    dim: int
    vectors: bytes
    """Half precision, one row of `dim` values per entry of `vector_rows`."""
    vector_rows: Mapping[str, int]
    inverse_norms: array[float]
    values: tuple[SettledValue, ...]
    """The customer's settled values, newest first, as `values_for` reads them."""
    values_by_type: Mapping[ValueType, tuple[int, ...]]
    values_by_key: Mapping[str, tuple[int, ...]]
    """By `value_key`: `45778-204` and `45778204` land on the same values."""
    marks: tuple[SystemMark, ...]
    """The customer's system events as temporal anchors ("after the inspection"), oldest first: each
    event of the hot window, and each object's last event, which may be older than the window."""
    nbytes: int
    with_vectors: bool = True
    """False for an entry loaded without the rows and their vectors: only the semantic channel reads them,
    and a process without it never loads them."""


def _halves(data: bytes) -> "memoryview[float]":
    # The stubs list no half-precision format for `cast`; CPython reads "e" since 3.3.
    view: memoryview[float] = memoryview(data).cast("e")  # type: ignore[call-overload]
    return view


def build_entry(
    profile_id: UUID,
    generation: int,
    handles: Collection[UUID],
    privacy_version: int,
    docs: Sequence[HistoryDoc],
    vectors: Sequence[ItemVector],
    values: Sequence[SettledValue] = (),
    marks: Sequence[SystemMark] = (),
    *,
    with_vectors: bool = True,
) -> ProfileEntry:
    shown = {d.id for d in docs}
    usable = [v for v in vectors if v.doc_id in shown and v.vector]
    # One encoder per entry: while a backfill moves rows to a new encoder, the rows of the other one rank
    # by their words alone, as in the history search (16.2).
    model = Counter(v.model for v in usable).most_common(1)[0][0] if usable else None
    chosen = sorted((v for v in usable if v.model == model), key=lambda v: v.doc_id)
    dim = len(chosen[0].vector) if chosen else 0
    chosen = [v for v in chosen if len(v.vector) == dim]
    # Native order, which is what `memoryview.cast("e")` reads back.
    halves = struct.pack(f"{len(chosen) * dim}e", *(x for v in chosen for x in v.vector))
    rows = {v.doc_id: i for i, v in enumerate(chosen)}
    view = _halves(halves)
    inverse = array("f")
    for i in range(len(chosen)):
        norm = math.sqrt(math.sumprod(view[i * dim : (i + 1) * dim], view[i * dim : (i + 1) * dim]))
        inverse.append(1.0 / norm if norm else 0.0)

    ordered_values = tuple(sorted(values, key=lambda v: (v.said_at, str(v.value_id)), reverse=True))
    by_type: dict[ValueType, list[int]] = {}
    by_key: dict[str, list[int]] = {}
    for i, value in enumerate(ordered_values):
        by_type.setdefault(value.type, []).append(i)
        by_key.setdefault(value.key, []).append(i)

    ordered_marks = tuple(sorted(set(marks), key=lambda m: (m.at, m.canonical_type, str(m.object_id))))
    nbytes = (
        ENTRY_OVERHEAD
        + len(halves)
        + len(chosen) * VECTOR_OVERHEAD
        + len(ordered_values) * VALUE_OVERHEAD
        + sum(len(v.raw) for v in ordered_values)
        + len(ordered_marks) * MARK_OVERHEAD
    )
    return ProfileEntry(
        profile_id=profile_id,
        generation=generation,
        handles=frozenset(handles),
        privacy_version=privacy_version,
        vector_model=model,
        dim=dim,
        vectors=halves,
        vector_rows=rows,
        inverse_norms=inverse,
        values=ordered_values,
        values_by_type={k: tuple(v) for k, v in by_type.items()},
        values_by_key={k: tuple(v) for k, v in by_key.items()},
        marks=ordered_marks,
        nbytes=nbytes,
        with_vectors=with_vectors,
    )


def cosine_scores(
    entry: ProfileEntry,
    query_vector: Sequence[float],
    allowed: Collection[str] | None = None,
    *,
    expired: Callable[[], bool] | None = None,
) -> dict[str, float] | None:
    """Exact cosine between the question and each row the reader may see, positive scores only. None
    when `expired` says the deadline passed before every row was scored: a half-ranked list would make
    the same question return different rows depending on the machine's load."""
    dim = entry.dim
    if dim == 0 or len(query_vector) != dim:
        return {}
    query_norm = math.sqrt(math.sumprod(query_vector, query_vector))
    if not query_norm:
        return {}
    flat = _halves(entry.vectors)
    scale = 1.0 / query_norm
    scores: dict[str, float] = {}
    for n, (doc_id, row) in enumerate(entry.vector_rows.items()):
        if expired is not None and n % _DEADLINE_EVERY == 0 and expired():
            return None
        if allowed is not None and doc_id not in allowed:
            continue
        score = (
            math.sumprod(flat[row * dim : (row + 1) * dim], query_vector) * entry.inverse_norms[row] * scale
        )
        if score > 0:
            scores[doc_id] = score
    return scores
