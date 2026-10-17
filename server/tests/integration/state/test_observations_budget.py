"""What tools showed, on PostgreSQL (24.3.5, 24.6): the observations of a group of 50 turns cost the group one
statement, and the task they leave writes the subject's objects, sealed, for the next state read."""

from typing import Any
from uuid import uuid4

from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from niadra.adapters.memory.fakes import InMemoryCache, InMemoryObjectStore, StaticConfig
from niadra.adapters.outbound.postgres.turns import PgTurnIntakeLog
from niadra.app.objects import ObjectsService
from niadra.app.observations import ObservationsConsumer
from niadra.app.state import StateService
from niadra.app.turns import APPLY_GROUP, TurnService
from niadra.config.models import FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.config.recording import RecordingSettings
from niadra.contracts.state import StateReadRequest
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Feature
from niadra.ports.turns import TurnConsumers
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import CellStack, CloningUnitOfWork
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.turns.records import turn

QUOTE = {
    "type": "health_quote",
    "ownership": "subject",
    "mirror_of": {"system": "operator_api"},
    "fields": {"price_full": {"type": "money", "claim": {"allowed": True, "class": "money"}}},
    "sources": {"quote": {"kind": "tool_observation", "precedence": 1}},
    "purposes": {"claim": {"prohibitions": ["affirm_price"]}},
}


class Parked:
    async def publish(self, tasks: Any) -> None:
        return None


async def test_the_observations_of_50_turns_cost_the_group_one_statement(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    config = StaticConfig(
        sample_config(space).model_copy(
            update={
                "features": FeatureSettings(enabled=frozenset({Feature.TURNS, Feature.STATE})),
                "recording": RecordingSettings(sample_rate=0),
                "object_types": ObjectTypeRegistry.model_validate({"types": [QUOTE]}),
            }
        )
    )
    consumer = ObservationsConsumer(pg_uow, pg_uow._keys, config)  # type: ignore[arg-type]
    turns = TurnService(
        pg_uow,  # type: ignore[arg-type]
        PgTurnIntakeLog(pg_stack.registry),
        pg_uow._keys,
        InMemoryObjectStore(),
        pg_stack.clock,
        config,
        RecordingAdminAudit(),  # type: ignore[arg-type]
        consumers=TurnConsumers(observations=consumer),
        workers=Parked(),  # type: ignore[arg-type]
    )
    caller = principal(WHATSAPP_SOURCE, config.space(space))
    for i in range(APPLY_GROUP):
        await turns.record(caller, [turn(f"t-{i}")])

    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(" ".join(statement.split()[:3]))

    event.listen(Engine, "before_cursor_execute", count)
    try:
        assert await turns._apply_next(space, APPLY_GROUP)
    finally:
        event.remove(Engine, "before_cursor_execute", count)
    # The turns module's three, and the one outbox row of the whole group's observations.
    assert len(statements) == 4, statements
    assert [s for s in statements if s.startswith("INSERT INTO outbox")] == ["INSERT INTO outbox"]

    async with pg_uow(space) as uow:
        payload = (
            await uow.session.execute(text("SELECT payload FROM outbox WHERE task = 'apply_observations'"))
        ).scalar_one()
    assert "511.06" not in str(payload)
    task = Task(TaskName.APPLY_OBSERVATIONS, space, payload)
    objects = ObjectsService(pg_uow, pg_stack.clock, config)  # type: ignore[arg-type]
    written = await objects.apply_observations(space, (await consumer.open(task)).subject)
    assert [o.key.external_id for o in written] == ["q-77"]
    async with pg_uow(space) as uow:
        sealed = (await uow.session.execute(text("SELECT state_ct FROM business_objects"))).scalar_one()
    assert b"511.06" not in sealed

    state = StateService(pg_uow, InMemoryCache(), pg_uow._keys, pg_stack.clock, config)  # type: ignore[arg-type]
    await state.publish(space, written)
    ref = {"type": "health_quote", "namespace": "operator", "id": "q-77"}
    answer = await state.read(caller, StateReadRequest.model_validate({"refs": [ref], "purpose": "claim"}))
    [quote] = answer.response.objects
    price = quote.fields["price_full"]
    assert (price.v, price.src, price.logic) == (511.06, "quote", "yes")
