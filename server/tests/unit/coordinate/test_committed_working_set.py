"""A commitment made on a shared object brings it into the objects module's working set (24.3.12)."""

from collections.abc import Sequence
from uuid import UUID

from niadra.config.coordination import CoordinationSettings
from tests.unit.coordinate.support import make_world


class RecordingWorkingSet:
    def __init__(self) -> None:
        self.entered: list[tuple[list[str], str]] = []

    async def enter_working_set(self, space_id: UUID, refs: Sequence[str], reason: str) -> int:
        self.entered.append((list(refs), reason))
        return len(refs)


async def test_a_commitment_on_an_object_enters_it_with_reason_committed() -> None:
    settings = CoordinationSettings.model_validate(
        {"commitments": [{"name": "proposal", "compatibility": "best_wins"}]}
    )
    working = RecordingWorkingSet()
    world = await make_world(settings, working_set=working)
    subject = {"type": "phone_e164", "value": "+5511988887777"}
    listing = {"type": "listing", "namespace": "catalog", "id": "apt-204"}
    await world.declare(
        key="c1",
        kind="commitment.made",
        agent="agent:sales",
        subject=subject,
        object=listing,
        detail={"commitment_id": "p1", "type": "proposal", "terms": {"price": "1900"}},
    )
    assert working.entered == [(["listing:catalog:apt-204"], "committed")]
    # On the subject alone, nothing enters; two of a `best_wins` type without a score compare as 0.
    await world.declare(
        key="c2",
        kind="commitment.made",
        agent="agent:sales",
        subject=subject,
        detail={"commitment_id": "p2", "type": "proposal"},
    )
    assert len(working.entered) == 1
