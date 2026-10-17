"""Drift of the declared types (object-type spec 3.2.2 and 8.8): a deriving tool's fingerprint, the traffic
that shows a field or a state the type does not declare, and the types never declared; each goes back to the
company as a data issue, with names and references only, and `type.drift` goes out once per issue."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.state import push_router
from niadra.adapters.inbound.workers.objects import handlers
from niadra.app.data_issues import DataIssuesService
from niadra.app.objects import ObjectsService
from niadra.app.observations import ObservationsConsumer
from niadra.app.state import StateService
from niadra.app.type_drift import TypeDriftService
from niadra.app.typed_state import plain_for_reader
from niadra.config.models import FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import TypeFingerprintRequest
from niadra.domain.common.errors import NoFingerprint, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal
from niadra.domain.policy.fields import reader_of
from niadra.domain.records import BusinessObject
from niadra.domain.state.drift import Declared, Recently, drifted_fields
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.issues import DataIssueRow, IssueQuery
from niadra.domain.vocabulary import EventKind, HandleType, Scope
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, principal
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.objects.fakes import World, at, event, handle
from tests.unit.state.test_http import FakeEdge
from tests.unit.state.test_observations import RECEIVED, applied, observation
from tests.unit.state.test_state_service import QUOTE, QUOTE_API, health_config

FINGERPRINT = "sha256:" + "a" * 64
CHANGED = "sha256:" + "b" * 64
COUNTS = {
    "fields_added": 1,
    "fields_removed": 0,
    "fields_retyped": 0,
    "states_added": 0,
    "states_removed": 0,
    "relations_added": 0,
    "relations_removed": 0,
    "key_changed": False,
}


def introspected(drift: str = "alert", fingerprint: str | None = FINGERPRINT) -> Any:
    """The health quote as if a deriving tool had read it from the company's schema."""
    config = health_config()
    [quote] = config.object_types.types
    mirror = quote.mirror_of.model_copy(
        update={"derived_by": "introspection" if fingerprint else "declaration", "fingerprint": fingerprint}
    )
    mirror = mirror.model_copy(update={"drift": drift})
    registry = ObjectTypeRegistry(types=[quote.model_copy(update={"mirror_of": mirror})])
    return config.model_copy(update={"object_types": registry})


def service(world: World) -> TypeDriftService:
    cell = world.cell
    issues = DataIssuesService(cell.uow, cell.clock, cell.config, RecordingAdminAudit(), cell.cache)  # type: ignore[arg-type]
    return TypeDriftService(world.uow, cell.clock, cell.config, issues, cell.cache)


async def issues(world: World) -> list[DataIssueRow]:
    async with world.cell.uow(SPACE_ID) as uow:
        return await uow.turns.issues(IssueQuery(open=None))


async def announced(world: World) -> list[dict[str, Any]]:
    async with world.cell.uow(SPACE_ID) as uow:
        return [d.body["data"] for d in await uow.notify.list_deliveries() if d.event_type == "type.drift"]


def check(fingerprint: str, **changes: Any) -> TypeFingerprintRequest:
    body: dict[str, Any] = {"type": QUOTE.type, "fingerprint": fingerprint}
    if changes:
        body["changes"] = {**COUNTS, **changes}
    return TypeFingerprintRequest.model_validate(body)


@pytest.fixture
def world(cell: Any) -> World:
    cell.config.put(introspected())
    return World(cell)


async def test_the_same_fingerprint_is_no_drift_and_opens_nothing(world: World) -> None:
    caller = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))
    answer = await service(world).check(caller, check(FINGERPRINT))
    assert (answer.drift, answer.issue_id) == (False, None)
    assert await issues(world) == []


async def test_another_fingerprint_opens_one_drift_issue_counted_and_announced_once(world: World) -> None:
    drift = service(world)
    caller = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))
    first = await drift.check(caller, check(CHANGED, fields=["price_full"], fields_retyped=1))
    again = await drift.check(caller, check(CHANGED, fields=["price_full"], fields_retyped=1))
    assert first.drift
    assert first.issue_id is not None
    assert again.issue_id == first.issue_id
    [issue] = await issues(world)
    assert (issue.kind, issue.object_type, issue.field, issue.occurrences) == (
        "drift",
        QUOTE.type,
        "price_full",
        2,
    )
    [notice] = await announced(world)
    assert notice["issue_id"] == first.issue_id
    assert (notice["type"], notice["field"], notice["seen_by"]) == (QUOTE.type, "price_full", "fingerprint")
    assert notice["changes"]["fields_retyped"] == 1


async def test_a_drift_that_names_several_fields_names_none_on_the_issue(world: World) -> None:
    caller = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))
    await service(world).check(caller, check(CHANGED, fields=["price_full", "price_discounted"]))
    [issue] = await issues(world)
    assert issue.field is None


async def test_a_type_that_ignores_drift_says_so_and_opens_nothing(world: World) -> None:
    world.cell.config.put(introspected(drift="ignore"))
    caller = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))
    answer = await service(world).check(caller, check(CHANGED))
    assert (answer.drift, answer.issue_id) == (True, None)
    assert await issues(world) == []


async def test_an_undeclared_type_is_not_found_and_one_without_a_fingerprint_is_refused(world: World) -> None:
    caller = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))
    with pytest.raises(NotFound):
        await service(world).check(caller, TypeFingerprintRequest(type="nothing_here", fingerprint=CHANGED))
    world.cell.config.put(introspected(fingerprint=None))
    with pytest.raises(NoFingerprint):
        await service(world).check(caller, check(CHANGED))


def test_drift_by_observation_names_undeclared_fields_and_untranslated_states() -> None:
    declared = Declared(frozenset({"price"}), frozenset({"open", "paid"}), {"PG": "paid"})
    reported = {"price": 1, "discount_code": "x", "version": 3, "state": "PG"}
    assert drifted_fields(reported, declared, {"version"}) == ["discount_code"]
    assert drifted_fields({"state": "refunded"}, declared, ()) == ["state"]
    assert drifted_fields({"state": "expired_without_outcome"}, declared, ()) == []
    # A type with no states has no state to drift from.
    assert drifted_fields({"state": "anything"}, Declared(frozenset(), frozenset(), {}), ()) == []


def test_a_finding_is_reported_once_an_hour_per_process_until_its_space_is_forgotten() -> None:
    recently = Recently()
    key = ("space", "drift", "quote", "discount_code", "quote_api")
    other = ("another", "drift", "quote", "discount_code", "quote_api")
    assert recently.fresh([key, key, other], at(0)) == [key, other]
    assert recently.fresh([key], at(59)) == []
    assert recently.fresh([key], at(60)) == [key]
    recently.forget("space")
    assert recently.fresh([key, other], at(61)) == [key, other]
    recently.forget("space")
    assert recently.fresh([key, other], at(62)) == [key]


async def test_a_system_event_with_a_field_the_type_lacks_opens_drift_from_its_own_transaction(
    world: World,
) -> None:
    drift = service(world)
    objects = ObjectsService(world.uow, world.cell.clock, world.cell.config, drift=drift)
    state = StateService(world.uow, world.cell.cache, world.cell.keys, world.cell.clock, world.cell.config)
    owner = world.identity.add_profile(handle(HandleType.SYSTEM_ID))
    for minutes in (1, 2):
        e = event(
            EventKind.SYSTEM_EVENT,
            at(minutes),
            [world.identity.handles[owner.handle_ids[0]]],
            objects=[QUOTE],
            canonical_type="quote.priced",
            fields={"price_full": 511.06 + minutes, "loyalty_tier": "gold"},
            source_id=QUOTE_API,
        )
        world.ingest.add(e)
        written = await objects.apply_object_event(
            Task(TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(e.event_id)})
        )
        await state.publish(SPACE_ID, written)
    [issue] = await issues(world)
    assert (issue.kind, issue.field, issue.source, issue.occurrences) == (
        "drift",
        "loyalty_tier",
        "quote_api",
        1,
    )
    assert issue.refs == (f"{QUOTE.type}:{QUOTE.namespace}:{QUOTE.external_id}",)
    [notice] = await announced(world)
    assert notice["seen_by"] == "observation"
    # Names and references only: the value never travels with the issue or its announcement.
    assert "gold" not in repr(issue)
    assert "gold" not in repr(notice)
    # The value is kept as a plain field: Niadra never refuses what the company's system did.
    async with world.cell.uow(SPACE_ID) as uow:
        stored = await uow.objects.get_object(QUOTE)
    assert stored is not None
    assert stored.state["loyalty_tier"] == "gold"


async def test_the_operation_an_event_confirms_is_bookkeeping_never_drift(world: World) -> None:
    # A tenant's event names the operation it confirms in `fields.operation` (5.8); it is not a field of the
    # object, so it opens no drift issue and a read leaves it out.
    drift = service(world)
    objects = ObjectsService(world.uow, world.cell.clock, world.cell.config, drift=drift)
    state = StateService(world.uow, world.cell.cache, world.cell.keys, world.cell.clock, world.cell.config)
    owner = world.identity.add_profile(handle(HandleType.SYSTEM_ID))
    e = event(
        EventKind.SYSTEM_EVENT,
        at(1),
        [world.identity.handles[owner.handle_ids[0]]],
        objects=[QUOTE],
        canonical_type="quote.repriced",
        fields={"price_full": 512.06, "operation": "reprice"},
        source_id=QUOTE_API,
    )
    world.ingest.add(e)
    written = await objects.apply_object_event(
        Task(TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(e.event_id)})
    )
    await state.publish(SPACE_ID, written)
    assert await issues(world) == []
    caller = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))
    read = (await objects.read_object(caller, QUOTE)).state
    assert "operation" not in read.fields
    assert read.fields["price_full"].v == 512.06


async def test_drift_seen_again_after_its_issue_is_acknowledged_opens_a_new_issue_within_the_hour(
    world: World,
) -> None:
    cell = world.cell
    data_issues = DataIssuesService(cell.uow, cell.clock, cell.config, RecordingAdminAudit(), cell.cache)  # type: ignore[arg-type]
    drift = TypeDriftService(world.uow, cell.clock, cell.config, data_issues, cell.cache)
    objects = ObjectsService(world.uow, cell.clock, cell.config, drift=drift)
    owner = world.identity.add_profile(handle(HandleType.SYSTEM_ID))

    async def observe(minutes: int) -> None:
        cell.clock.at = at(minutes)
        e = event(
            EventKind.SYSTEM_EVENT,
            at(minutes),
            [world.identity.handles[owner.handle_ids[0]]],
            objects=[QUOTE],
            canonical_type="quote.priced",
            fields={"price_full": 511.06 + minutes, "loyalty_tier": "gold"},
            source_id=QUOTE_API,
        )
        world.ingest.add(e)
        await objects.apply_object_event(
            Task(TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(e.event_id)})
        )

    await observe(1)
    await observe(2)
    [first] = await issues(world)
    assert first.occurrences == 1
    person = PersonPrincipal(
        principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID)).space, uuid7(), frozenset()
    )
    await data_issues.acknowledge(person, str(first.issue_id))
    await observe(3)
    reopened, closed = await issues(world)
    assert closed.issue_id == first.issue_id
    assert closed.acknowledged_at is not None
    assert (reopened.field, reopened.acknowledged_at, reopened.occurrences) == ("loyalty_tier", None, 1)
    # While the new issue stays open, the same finding costs no statement again within the hour.
    await observe(4)
    assert [i.occurrences for i in await issues(world)] == [1, 1]


async def test_a_type_never_declared_opens_a_type_undeclared_issue_once_the_wait_ends(world: World) -> None:
    world.cell.config.put(introspected().model_copy(update={"object_types": ObjectTypeRegistry()}))
    drift = service(world)
    consumer = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    async with world.uow(SPACE_ID) as uow:
        await consumer.consume(uow, SPACE_ID, [applied(observation({"price_full": 511.06}))])
    [task] = world.pipeline.take(TaskName.APPLY_OBSERVATIONS)
    world.cell.clock.at = RECEIVED + timedelta(minutes=2)
    container = _Container(world, consumer, drift)
    await handlers(container)[TaskName.APPLY_OBSERVATIONS](task)
    [issue] = await issues(world)
    assert (issue.kind, issue.object_type, issue.source) == (
        "type_undeclared",
        "cotacao_plano_saude",
        "quote",
    )
    assert issue.refs == ("cotacao_plano_saude:operadora:q-77",)


class _Container:
    """What the objects worker's handler reads of the cell."""

    def __init__(self, world: World, consumer: ObservationsConsumer, drift: TypeDriftService) -> None:
        cell = world.cell
        self.clock = cell.clock
        self.observations = consumer
        self.type_drift = drift
        self.objects = ObjectsService(world.uow, cell.clock, cell.config, drift=drift)
        self.state = StateService(world.uow, cell.cache, cell.keys, cell.clock, cell.config)
        self.shared = _NoShared()
        self.refresh = _NoRefresh()


class _NoShared:
    async def enter_working_set(self, *args: Any) -> None:
        return None

    async def watch(self, *args: Any) -> None:
        return None

    async def observe(self, *args: Any) -> None:
        return None

    async def enter_related(self, *args: Any) -> None:
        return None


class _NoRefresh:
    async def on_written(self, *args: Any) -> None:
        return None


async def test_the_route_needs_the_push_scope_and_the_feature_and_answers_404_and_422(world: World) -> None:
    space = world.cell.config.space(SPACE_ID)
    keys = {
        "nia_sk_push": principal(WHATSAPP_SOURCE, space),
        "nia_sk_read": principal(WHATSAPP_SOURCE, space, scopes=frozenset({Scope.CONTEXT})),
    }
    container = SimpleNamespace(edge=FakeEdge(keys), config=world.cell.config, type_drift=service(world))
    app = create_app("ingest", container, [push_router])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://cell.test"
    ) as http:
        push, read = {"authorization": "Bearer nia_sk_push"}, {"authorization": "Bearer nia_sk_read"}
        body = {"type": QUOTE.type, "fingerprint": CHANGED, "changes": {**COUNTS, "fields": ["price_full"]}}
        answer = await http.post("/v1/types/fingerprint", headers=push, json=body)
        assert answer.status_code == 200
        assert answer.json()["drift"] is True
        assert answer.json()["issue_id"]
        assert (await http.post("/v1/types/fingerprint", headers=read, json=body)).status_code == 403
        unknown = await http.post(
            "/v1/types/fingerprint", headers=push, json={**body, "type": "nothing_here"}
        )
        assert unknown.status_code == 404
        # A fingerprint is a digest, never a name or a value.
        loose = await http.post("/v1/types/fingerprint", headers=push, json={**body, "fingerprint": "abc"})
        assert loose.status_code == 422
        world.cell.config.put(introspected(fingerprint=None))
        refused = await http.post("/v1/types/fingerprint", headers=push, json=body)
        assert (refused.status_code, refused.json()["code"]) == (422, "no_fingerprint")
        world.cell.config.put(
            introspected().model_copy(update={"features": FeatureSettings(enabled=frozenset())})
        )
        assert (await http.post("/v1/types/fingerprint", headers=push, json=body)).status_code == 404


async def test_a_new_field_reaches_agents_unless_it_may_be_personal_or_free_text(world: World) -> None:
    # 06/10/2026: a field the product sent that the type did not declare reached no agent until someone
    # declared it in the Console (`held_on`, `title`). Now it is served as it came, and its `drift` issue is
    # a note; a field that may be personal data, or a third party's free text, is held from agents until
    # declared, and its own issue says so, never its value.
    drift = service(world)
    objects = ObjectsService(world.uow, world.cell.clock, world.cell.config, drift=drift)
    state = StateService(world.uow, world.cell.cache, world.cell.keys, world.cell.clock, world.cell.config)
    owner = world.identity.add_profile(handle(HandleType.SYSTEM_ID))
    court_text = "Fica a parte intimada para apresentar contestação no prazo legal, sob pena de revelia. " * 4
    e = event(
        EventKind.SYSTEM_EVENT,
        at(1),
        [world.identity.handles[owner.handle_ids[0]]],
        objects=[QUOTE],
        canonical_type="quote.priced",
        fields={
            "price_full": 511.06,
            "held_on": "2026-10-05",
            "cpf_cliente": "529.982.247-25",
            "court_order": court_text,
        },
        source_id=QUOTE_API,
    )
    world.ingest.add(e)
    written = await objects.apply_object_event(
        Task(TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(e.event_id)})
    )
    await state.publish(SPACE_ID, written)
    found = {(i.kind, i.field) for i in await issues(world)}
    assert found == {("drift", "held_on"), ("field_held", "court_order"), ("field_held", "cpf_cliente")}
    assert all("529.982" not in repr(i) for i in await issues(world))
    caller = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))
    read = (await objects.read_object(caller, QUOTE)).state
    assert read.fields["held_on"].v == "2026-10-05"
    assert "cpf_cliente" not in read.fields
    assert "court_order" not in read.fields
    assert read.withheld["cpf_cliente"] == "access"
    assert read.withheld["court_order"] == "scan"
    # The company's own reads, with no agent reading, see every field as it came.
    async with world.cell.uow(SPACE_ID) as uow:
        stored = await uow.objects.get_object(QUOTE)
    assert stored is not None
    assert stored.state["cpf_cliente"] == "529.982.247-25"


def test_a_plain_read_for_an_agent_holds_what_may_be_personal_among_new_fields() -> None:
    # The pack and `GET /v1/objects/...` read an object's plain state for one agent: a new field is there as
    # it came, one that may be personal data or free text is not, and a declared field is never held by it.
    [quote] = health_config().object_types.types
    declared_field = next(iter(quote.fields))
    obj = BusinessObject(
        object_id=UUID(int=1),
        key=QUOTE,
        state={
            declared_field: "maria@exemplo.com",
            "held_on": "2026-10-05",
            "cpf_cliente": "52998224725",
            "court_order": "Fica a parte intimada. " * 20,
        },
        as_of=datetime(2026, 10, 7, tzinfo=UTC),
        source_id=UUID(int=2),
    )
    plain = plain_for_reader(obj, quote, reader_of("atende-zap", ["support"]), (), {}, datetime.now(UTC))
    assert plain["held_on"] == "2026-10-05"
    assert "cpf_cliente" not in plain
    assert "court_order" not in plain
    assert plain[declared_field] == "maria@exemplo.com"


async def test_an_internal_agents_tool_of_a_type_nobody_declared_is_kept_for_the_inference(
    world: World,
) -> None:
    """Zero touch: what an internal agent's tool showed of a type the space never declared is kept
    once the wait ends, as a system's event of it is, so the inference names the type; a customer-facing
    agent's tool still never makes a type, and its observation stays a data issue."""
    world.cell.config.put(introspected().model_copy(update={"object_types": ObjectTypeRegistry()}))
    drift = service(world)
    consumer = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    internal = replace(applied(observation({"price_full": 511.06})), source_id=BILLING_SOURCE)
    async with world.uow(SPACE_ID) as uow:
        await consumer.consume(uow, SPACE_ID, [internal])
    [task] = world.pipeline.take(TaskName.APPLY_OBSERVATIONS)
    world.cell.clock.at = RECEIVED + timedelta(minutes=2)
    await handlers(_Container(world, consumer, drift))[TaskName.APPLY_OBSERVATIONS](task)
    async with world.cell.uow(SPACE_ID) as uow:
        quote = await uow.objects.get_object(QUOTE)
    assert quote is not None
    assert quote.state["price_full"] == 511.06
    assert await issues(world) == []
