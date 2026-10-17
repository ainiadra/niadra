"""Drift by observation on shared objects (object-type spec 3.2.2): a push, a snapshot or a tool's observation
of a shared object that carries a field its type does not declare, or a state outside the declared ones,
opens the same `drift` issue a customer's object opens. The push is still decided with no statement: the
finding waits in the state store, and the consolidator's next transaction opens it."""

from typing import Any

import orjson
import pytest

from niadra.adapters.memory.shared import InMemorySharedObjects
from niadra.app.data_issues import DataIssuesService
from niadra.app.observations import ObservationsConsumer
from niadra.app.shared_objects import SharedObjectsService, SharedObserved
from niadra.app.type_drift import TypeDriftService
from niadra.config.models import FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import ObjectPushRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal
from niadra.domain.state.observed import Report
from niadra.domain.state.shared import SharedKey
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.issues import DataIssueRow, IssueQuery
from niadra.domain.vocabulary import Feature
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.objects.fakes import World, config_with
from tests.unit.state.test_shared_objects import ITEM, NOW, REF, item, pusher, repo

DRIFTING: dict[str, Any] = {
    **ITEM,
    "mirror_of": {"system": "shop", "derived_by": "introspection", "fingerprint": "sha256:" + "a" * 64,
                  "drift": "alert"},
    "states": ["active", "discontinued"],
}  # fmt: skip


@pytest.fixture
def world(cell: Cell) -> World:
    cell.clock.at = NOW
    cell.config.put(
        config_with().model_copy(
            update={
                "object_types": ObjectTypeRegistry.model_validate({"types": [DRIFTING]}),
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
            }
        )
    )
    return World(cell)


@pytest.fixture
def issues_service(world: World) -> DataIssuesService:
    cell = world.cell
    return DataIssuesService(cell.uow, cell.clock, cell.config, RecordingAdminAudit(), cell.cache)  # type: ignore[arg-type]


@pytest.fixture
def shared(world: World, issues_service: DataIssuesService) -> SharedObjectsService:
    cell = world.cell
    observations = ObservationsConsumer(world.uow, cell.keys, cell.config)
    service = SharedObjectsService(
        world.uow, InMemorySharedObjects(), cell.keys, cell.clock, cell.config, observations, cell.objects
    )
    service.drift = TypeDriftService(world.uow, cell.clock, cell.config, issues_service, cell.cache)
    return service


async def issues(world: World) -> list[DataIssueRow]:
    async with world.cell.uow(SPACE_ID) as uow:
        return await uow.turns.issues(IssueQuery(open=None))


async def pushed(world: World, shared: SharedObjectsService, version: int, fields: dict[str, Any]) -> int:
    request = ObjectPushRequest.model_validate({"objects": [item(version, fields)]})
    return (await shared.push(pusher(world), request)).applied


async def test_a_push_with_an_undeclared_field_and_state_opens_drift_from_the_consolidators_transaction(
    world: World, shared: SharedObjectsService
) -> None:
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    assert (
        await pushed(world, shared, 1, {"price_sale": 199.9, "loyalty_tier": "gold", "state": "paused"}) == 1
    )
    assert await issues(world) == []  # the push itself writes nothing
    assert await shared.consolidate(SPACE_ID) == 1
    found = {i.field: i for i in await issues(world)}
    assert set(found) == {"loyalty_tier", "state"}
    issue = found["loyalty_tier"]
    assert (issue.kind, issue.object_type, issue.source, issue.refs) == (
        "drift",
        "item_variant",
        "platform",
        (REF,),
    )
    # Names and references only: never the value.
    assert "gold" not in repr(found)
    assert "paused" not in repr(found)
    # The consolidation stays one statement for the objects, whatever drift it opens beside it.
    assert repo(world).consolidations == [1]


async def test_the_same_drift_again_is_throttled_and_opens_anew_once_acknowledged(
    world: World, shared: SharedObjectsService, issues_service: DataIssuesService
) -> None:
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    for version in (1, 2, 3):
        await pushed(world, shared, version, {"price_sale": 100.0 + version, "loyalty_tier": "gold"})
        await shared.consolidate(SPACE_ID)
    [first] = await issues(world)
    assert first.occurrences == 1  # one finding an hour per process, while the issue stays open
    person = PersonPrincipal(
        principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID)).space, uuid7(), frozenset()
    )
    await issues_service.acknowledge(person, str(first.issue_id))
    await pushed(world, shared, 4, {"price_sale": 104.0, "loyalty_tier": "gold"})
    await shared.consolidate(SPACE_ID)
    reopened, closed = await issues(world)
    assert (closed.issue_id, reopened.field, reopened.acknowledged_at) == (
        first.issue_id,
        "loyalty_tier",
        None,
    )


async def test_a_snapshot_and_a_tools_observation_open_it_too(
    world: World, shared: SharedObjectsService
) -> None:
    await shared.enter_working_set(SPACE_ID, [REF], "presented")

    async def body() -> Any:
        yield orjson.dumps(item(5, {"price_sale": 150.0, "outlet": True}))

    await shared.take_snapshot(pusher(world), "item_variant", body())
    [task] = world.pipeline.take(TaskName.APPLY_SNAPSHOT)
    await shared.apply_snapshot(Task(task.name, task.space_id, task.payload))
    report = shared_report({"price_sale": 149.0, "badge": "new"})
    await shared.observe(SPACE_ID, [SharedObserved(SharedKey("item_variant", "loja", "v-1"), report)])
    await shared.consolidate(SPACE_ID)
    assert {(i.field, i.source) for i in await issues(world)} == {("outlet", "platform"), ("badge", "search")}


async def test_a_type_that_ignores_drift_or_a_push_out_of_the_set_opens_nothing(
    world: World, shared: SharedObjectsService
) -> None:
    assert await pushed(world, shared, 1, {"loyalty_tier": "gold"}) == 0  # out of the working set
    await shared.consolidate(SPACE_ID)
    assert await issues(world) == []
    ignoring = {**DRIFTING, "mirror_of": {**DRIFTING["mirror_of"], "drift": "ignore"}}
    config = world.cell.config.space(SPACE_ID)
    world.cell.config.put(
        config.model_copy(update={"object_types": ObjectTypeRegistry.model_validate({"types": [ignoring]})})
    )
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    assert await pushed(world, shared, 2, {"loyalty_tier": "gold"}) == 1
    await shared.consolidate(SPACE_ID)
    assert await issues(world) == []


def shared_report(fields: dict[str, Any]) -> Report:
    """What the search tool of a turn showed of the item, as the observations consumer opens it."""
    return Report(
        source="tool:search", fields=fields, valid_at=NOW, observed_at=NOW, received_at=NOW, provenance="live"
    )
