"""Control group and shadow mode (19.7): a stable draw per customer, the same on every channel."""

import hashlib
import hmac
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.records import OpenItem
from niadra.domain.vocabulary import OpenItemStatus


class ExperimentSpec(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def fraction_with_memory(self) -> float: ...
    @property
    def salt(self) -> str: ...
    @property
    def starts_at(self) -> datetime: ...
    @property
    def ends_at(self) -> datetime | None: ...
    @property
    def source_ids(self) -> frozenset[UUID]: ...
    @property
    def shadow_source_ids(self) -> frozenset[UUID]: ...


@dataclass(frozen=True, slots=True)
class Assignment:
    experiment: str
    group: str

    @property
    def holdout(self) -> bool:
        return self.group in ("control", "shadow")

    @property
    def label(self) -> str:
        return f"{self.experiment}:{self.group}"


def draw(salt: str, oldest_handle_id: UUID) -> float:
    """HMAC(salt, oldest handle of the component) mapped to [0, 1)."""
    digest = hmac.new(salt.encode(), str(oldest_handle_id).encode(), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def assign(
    experiments: Iterable[ExperimentSpec],
    *,
    source_id: UUID,
    critical_source: bool,
    oldest_handle_id: UUID,
    now: datetime,
) -> Assignment | None:
    """The first active experiment that covers the source decides; critical sources never join (19.7)."""
    if critical_source:
        return None
    for experiment in sorted(experiments, key=lambda e: e.name):
        if now < experiment.starts_at or (experiment.ends_at is not None and now >= experiment.ends_at):
            continue
        if source_id in experiment.shadow_source_ids:
            return Assignment(experiment.name, "shadow")
        if experiment.source_ids and source_id not in experiment.source_ids:
            continue
        with_memory = draw(experiment.salt, oldest_handle_id) < experiment.fraction_with_memory
        return Assignment(experiment.name, "memory" if with_memory else "control")
    return None


def overdue_company_items(open_items: Iterable[OpenItem]) -> int:
    """Company promises already overdue: any of them takes the customer out of both groups (19.7)."""
    return sum(1 for i in open_items if i.status is OpenItemStatus.OVERDUE and i.owner == "company")
