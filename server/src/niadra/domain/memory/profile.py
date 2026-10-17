"""Profile slots (5.8): one row per (origin handle, slot, source), and a survivorship rule on read.

Keeping each source's last value, instead of one merged value, is what lets the "golden record"
change its rule (latest, source priority, most frequent) without re-extracting anything.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from uuid import UUID

from niadra.domain.memory.facts import normalize_value
from niadra.domain.records import ProfileAttr


def merge_attr(existing: ProfileAttr | None, seen: ProfileAttr) -> ProfileAttr | None:
    """The row to write, or `None` when the observation changes nothing (it is older, or a replay)."""
    if existing is None:
        return seen
    if seen.observed_at <= existing.observed_at:
        return None
    if normalize_value(seen.value) == normalize_value(existing.value):
        return replace(existing, observed_at=seen.observed_at, times_seen=existing.times_seen + 1)
    return replace(seen, times_seen=1)


def survive(
    attrs: Sequence[ProfileAttr], survivorship: str, source_rank: Mapping[UUID, int] | None = None
) -> ProfileAttr | None:
    """The slot's value for the whole profile: `latest`, `source_priority` or `most_frequent`."""
    if not attrs:
        return None
    if survivorship == "source_priority" and source_rank:
        worst = len(source_rank)
        return min(attrs, key=lambda a: (source_rank.get(a.source_id, worst), -a.observed_at.timestamp()))
    if survivorship == "most_frequent":
        return max(attrs, key=lambda a: (a.times_seen, a.observed_at))
    return max(attrs, key=lambda a: (a.observed_at, str(a.source_id)))


def surviving(
    attrs: Iterable[ProfileAttr], rules: Mapping[str, tuple[str, Mapping[UUID, int]]]
) -> dict[str, ProfileAttr]:
    """Each slot's value for the whole profile (5.8): the schema's rule when it declares one; otherwise, and
    for `latest`, the most recent observation wins, ties by frequency, then value. A merge leaves one row per
    handle and source, so this is where the merged profile's value is decided."""
    latest: dict[str, ProfileAttr] = {}
    rows: dict[str, list[ProfileAttr]] = {}
    for attr in sorted(attrs, key=lambda a: (a.observed_at, a.times_seen, a.value)):
        latest[attr.slot] = attr
        rows.setdefault(attr.slot, []).append(attr)
    for slot, (rule, ranks) in rules.items():
        if rule != "latest" and slot in rows and (chosen := survive(rows[slot], rule, ranks)):
            latest[slot] = chosen
    return latest
