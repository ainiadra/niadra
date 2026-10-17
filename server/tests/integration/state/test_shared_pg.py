"""Shared objects on the cache store and PostgreSQL (24.3.12; 8.6): a burst of 10,000 pushes in one second is
decided with no database statement and consolidated in one; the hot copy's script and the consolidator's
statement keep the same per-field rule, whatever the order the pushes arrive in."""

import os
import random
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from niadra.adapters.memory.fakes import FixedClock, InMemoryObjectStore, StaticConfig
from niadra.adapters.memory.shared import InMemorySharedObjects
from niadra.adapters.outbound.redis import connect
from niadra.adapters.outbound.redis.shared import RedisSharedObjects
from niadra.app.observations import ObservationsConsumer
from niadra.app.shared_objects import SharedObjectsService
from niadra.config.models import FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import ObjectPushRequest
from niadra.domain.state.shared import SharedKey, shared_object_id
from niadra.domain.vocabulary import Feature, Scope
from niadra.testing.cell import BILLING_SOURCE, principal, sample_config
from tests.integration.conftest import CloningUnitOfWork

REDIS = os.environ.get("NIADRA_TEST_REDIS", "")
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
ITEM = {
    "type": "item_variant",
    "ownership": "shared",
    "mirror_of": {"system": "shop"},
    "working_set": {"enter_on": ["presented"], "leave_after": "30d"},
    "fields": {"price_sale": {"type": "money"}, "available": {"type": "bool"}},
    "sources": {"platform": {"kind": "push", "precedence": 1}},
}

pytestmark = pytest.mark.skipif(not REDIS, reason="needs NIADRA_TEST_REDIS")


@contextmanager
def counting() -> Iterator[list[str]]:
    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", count)


@pytest.fixture
async def service(pg_uow: CloningUnitOfWork) -> AsyncIterator[tuple[SharedObjectsService, Any]]:
    space = uuid4()
    await pg_uow.ensure(space)
    config = StaticConfig(
        sample_config(space).model_copy(
            update={
                "object_types": ObjectTypeRegistry.model_validate({"types": [ITEM]}),
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
            }
        )
    )
    redis = connect(REDIS)
    observations = ObservationsConsumer(pg_uow, pg_uow._keys, config)  # type: ignore[arg-type]
    shared = SharedObjectsService(
        pg_uow,  # type: ignore[arg-type]
        RedisSharedObjects(redis),
        pg_uow._keys,
        FixedClock(NOW),
        config,
        observations,
        InMemoryObjectStore(),
    )
    caller = principal(BILLING_SOURCE, config.space(space), frozenset({Scope.STATE_PUSH}))
    try:
        yield shared, caller
    finally:
        await redis.aclose()


def item(ref: str, version: int, fields: dict[str, Any]) -> dict[str, Any]:
    _, namespace, external = ref.split(":", 2)
    return {
        "ref": {"type": "item_variant", "namespace": namespace, "id": external},
        "version": version,
        "fields": fields,
        "provenance": {"source": "live", "source_observed_at": NOW.isoformat()},
    }


async def test_ten_thousand_pushes_in_one_second_become_one_statement(
    service: tuple[SharedObjectsService, Any],
) -> None:
    shared, caller = service
    space = caller.space.space_id
    refs = [f"item_variant:loja:v-{n}" for n in range(500)]
    await shared.enter_working_set(space, refs, "presented")
    await shared.consolidate(space)  # the first step finds the store never loaded and loads the working set
    # The process's key vault opens the space's data key once; every push after it seals in process.
    await shared.push(
        caller, ObjectPushRequest.model_validate({"objects": [item(refs[0], 0, {"available": True})]})
    )
    with counting() as pushing:
        for version in range(1, 21):
            request = [item(ref, version, {"price_sale": 100.0 + version}) for ref in refs]
            for start in range(0, len(request), 1000):
                page = ObjectPushRequest.model_validate({"objects": request[start : start + 1000]})
                answer = await shared.push(caller, page)
                assert answer.applied == len(page.objects)
    assert pushing == []  # 10,000 pushes, decided on the hot copy alone
    with counting() as consolidating:
        assert await shared.consolidate(space) == 500
    writes = [s for s in consolidating if "shared_objects" in s]
    assert len(writes) == 1, writes
    [copy] = (await shared.copies(space, [SharedKey("item_variant", "loja", "v-9")]))[0]
    assert copy is not None
    assert copy.fields["price_sale"][f"src:{BILLING_SOURCE}"].value == 120.0


async def test_the_script_and_the_statement_keep_the_same_rule_in_any_order(
    service: tuple[SharedObjectsService, Any],
) -> None:
    """Pushes in shuffled order and grouping: the database ends where the hot copy and an in-memory copy of
    the rule do."""
    shared, caller = service
    space = caller.space.space_id
    refs = [f"item_variant:loja:r-{n}" for n in range(5)]
    await shared.enter_working_set(space, refs, "presented")
    reference = InMemorySharedObjects()
    await reference.enter(
        space, [(ref, shared_object_id(space, SharedKey(*ref.split(":"))), {}, 60, None, 0) for ref in refs]
    )
    rng = random.Random(7)
    pushes = [
        (
            rng.choice(refs),
            rng.randint(1, 30),
            rng.choice([{"price_sale": 1.0}, {"available": True}, {"price_sale": 2.0, "available": False}]),
        )
        for _ in range(200)
    ]
    for ref, version, fields in pushes:
        await shared.push(caller, ObjectPushRequest.model_validate({"objects": [item(ref, version, fields)]}))
        await reference.push(space, [(ref, version, {f"{k}|src": b"x" for k in fields})])
        if rng.random() < 0.1:
            await shared.consolidate(space)
    await shared.consolidate(space)
    expected = {
        ref: {slot.split("|")[0]: version for slot, (version, _) in held[1].items()}
        for ref, held in zip(refs, await reference.load(space, refs), strict=True)
        if held is not None
    }
    async with shared._uow(space) as uow:  # type: ignore[attr-defined]
        rows = await uow.typed.shared_by_id([shared_object_id(space, SharedKey(*r.split(":"))) for r in refs])
    stored = {
        f"item_variant:{row.key.namespace}:{row.key.external_id}": {
            field: by[f"src:{BILLING_SOURCE}"]["version"] for field, by in row.meta.items()
        }
        for row in rows
    }
    assert stored == expected
