"""Experiments on an element of what a read delivers, or on a whole agent (24.7).

A person is drawn once per experiment, as the memory holdout draws (`serve.holdout.draw`): the experiment's
salt over the oldest handle of their profile, mapped to an arm by the arms' fractions in their declared order.
The arm a profile first got stays after a union of profiles, from `experiment_assignments`, where the
receipt writer projects it with its kind. An interleaving has no arm per person: the SDK draws the teams per
turn, seeded by the turn id, and the server only adds up what engagement credited to each team.

The control arm of an element experiment reads without the element: the constraints block, its soft entries,
the inferred size, the typed state block, or a section of the pack (its own pack, compiled without the
section's items and cached under its own key). The control arm of an agent experiment is held out of memory
for that agent alone, as the memory holdout is for everyone. Critical sources never join a control arm
(19.7).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.serve.holdout import draw

CONTROL = "control"
BLOCK_OF_ELEMENT = {"constraints": "constraints", "state_view": "state"}
"""Elements that are a whole block of a read's `include`: the control arm reads without the block."""
CONSTRAINT_PARTS = frozenset({"soft", "size"})
"""Elements that are a part of the constraints block: the control arm gets the block without them."""
SECTION_KINDS = {"facts": "fact", "episodes": "episode", "actions": "action", "objects": "object"}
"""The sections of the pack an experiment can hold out, by the kind of their items: the trimmable kinds, whose
sensitive items and items the policy releases by name stay whatever the arm."""
ASSIGNED_KINDS = frozenset({"element", "agent"})


class ArmSpec(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def fraction(self) -> float: ...


class ExperimentSpec(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def element(self) -> str | None: ...
    @property
    def section(self) -> str | None: ...
    @property
    def agent(self) -> str | None: ...
    @property
    def arms(self) -> "Iterable[ArmSpec]": ...
    @property
    def salt(self) -> str: ...
    @property
    def starts_at(self) -> datetime: ...
    @property
    def ends_at(self) -> datetime | None: ...
    @property
    def source_ids(self) -> frozenset[UUID]: ...


@dataclass(frozen=True, slots=True)
class Arm:
    """The arm one person reads in, in one experiment."""

    experiment: str
    kind: str
    arm: str
    element: str | None = None
    section: str | None = None

    @property
    def control(self) -> bool:
        return self.arm == CONTROL


def arm_of(salt: str, arms: Iterable[ArmSpec], oldest_handle_id: UUID) -> str:
    """The arm the draw lands in, by the fractions in declared order; the last arm takes what rounding
    leaves at the top."""
    point = draw(salt, oldest_handle_id)
    declared = list(arms)
    cumulative = 0.0
    for arm in declared:
        cumulative += arm.fraction
        if point < cumulative:
            return arm.name
    return declared[-1].name


def assign_arms(
    experiments: Iterable[ExperimentSpec],
    *,
    source_id: UUID,
    source_name: str | None,
    critical_source: bool,
    oldest_handle_id: UUID,
    kept: Mapping[str, str],
    now: datetime,
) -> tuple[Arm, ...]:
    """Every active element and agent experiment that covers this read, with the arm the person reads in:
    the one kept for their profile when the experiment still declares it, else the draw."""
    if critical_source:
        return ()
    out: list[Arm] = []
    for experiment in sorted(experiments, key=lambda e: e.name):
        if experiment.kind not in ASSIGNED_KINDS:
            continue
        if now < experiment.starts_at or (experiment.ends_at is not None and now >= experiment.ends_at):
            continue
        if experiment.source_ids and source_id not in experiment.source_ids:
            continue
        if experiment.kind == "agent" and experiment.agent != source_name:
            continue
        names = {arm.name for arm in experiment.arms}
        held = kept.get(experiment.name)
        arm = held if held in names else arm_of(experiment.salt, experiment.arms, oldest_handle_id)
        out.append(Arm(experiment.name, experiment.kind, arm, experiment.element, experiment.section))
    return tuple(out)


def withheld_elements(arms: Iterable[Arm]) -> frozenset[str]:
    """What the read goes without: the elements whose experiment put the person in the control arm."""
    return frozenset(a.element for a in arms if a.kind == "element" and a.control and a.element)


def held_sections(arms: Iterable[Arm]) -> frozenset[str]:
    """The kinds of items whose section of the pack the person's arms hold out."""
    return frozenset(
        SECTION_KINDS[a.section]
        for a in arms
        if a.kind == "element" and a.control and a.element == "pack_section" and a.section in SECTION_KINDS
    )


def agent_control(arms: Iterable[Arm]) -> Arm | None:
    """The agent experiment that holds this read out of memory, if any."""
    return next((a for a in arms if a.kind == "agent" and a.control), None)


def arms_text(arms: Iterable[Arm]) -> str:
    """The arms as the receipt carries them to its projection: `experiment=kind:arm`, joined by `;`."""
    return ";".join(f"{a.experiment}={a.kind}:{a.arm}" for a in arms)


def arms_of_text(text: str) -> list[tuple[str, str, str]]:
    """`(experiment, kind, arm)` from `arms_text`; what does not parse is left out."""
    out = []
    for part in filter(None, text.split(";")):
        experiment, _, rest = part.partition("=")
        kind, _, arm = rest.partition(":")
        if experiment and kind in ASSIGNED_KINDS and arm:
            out.append((experiment, kind, arm))
    return out
