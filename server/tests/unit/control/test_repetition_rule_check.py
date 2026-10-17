"""A repetition-rate warning the sweep could not read is refused when sent (22.1)."""

from typing import Any

import pytest

from niadra.domain.common.errors import InvalidInput
from niadra.domain.common.ids import uuid7
from niadra.domain.control.records import Caller, DiffStatus
from tests.unit.control.conftest import World


def rule(**params: Any) -> dict[str, Any]:
    return {
        "rule_id": "agent_ignores_context",
        "condition": "context_use.repetition_rate",
        "version": "1",
        "endpoint_id": "ops",
        "params": params,
    }


async def submit(world: World, admin: Caller, name: str, document: Any) -> Any:
    return await world.service.submit_diff(admin, world.boot.space.space_id, name, document, "watch")


async def test_a_repetition_rule_with_unusable_parameters_is_refused(world: World) -> None:
    admin = await world.admin()
    endpoint = {
        "endpoint_id": "ops",
        "url": "https://ops.acme.example/hooks",
        "source_id": str(world.boot.keys["billing"].key.source_id),
        "event_types": ["trigger.fired"],
        "secret_refs": [f"spaces/{world.boot.space.space_id}/webhook/ops"],
    }
    diff = await submit(world, admin, "webhook-endpoints", [endpoint])
    assert (await world.service.approve_diff(admin, diff.diff_id)).status is DiffStatus.APPLIED

    for wrong, says in (
        ({"above": 1.2}, "params need above in [0, 1)"),
        ({"window_days": 400}, "window_days from 1 to 90"),
        ({"source_id": str(uuid7())}, "params.source_id names no source of the space"),
    ):
        with pytest.raises(InvalidInput) as refused:
            await submit(world, admin, "trigger-rules", [rule(**wrong)])
        assert says in str(refused.value.detail)

    whatsapp = str(world.boot.keys["whatsapp"].key.source_id)
    diff = await submit(world, admin, "trigger-rules", [rule(above=0.25, window_days=7, source_id=whatsapp)])
    assert (await world.service.approve_diff(admin, diff.diff_id)).status is DiffStatus.APPLIED
