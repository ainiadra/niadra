"""What the agents claimed and what their tools honored, on the measurement read (24.3.3, 24.6.9): the claim
verdicts the SDK recorded in turn records, by category and verdict, and the constraints sent, verifiable,
violated and relaxed, per source and agent; only for a space with the feature on."""

from datetime import date
from typing import Any

from niadra.config.measurement import MeasurementSettings, MemoryOffCost
from niadra.config.models import FeatureSettings
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.vocabulary import Environment, Feature
from niadra.testing.cell import SPACE_ID, TENANT_ID, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.flow.harness import Flow

ANALYST = PersonPrincipal(
    SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX), uuid7(), frozenset({"analyst"})
)


def price(verdict: str, action: str) -> dict[str, Any]:
    return {
        "category": "price",
        "class": "money",
        "nature": "model" if verdict == "unsupported" else "computed",
        "role": "price_full",
        "span": [10, 19],
        "value": {"amount": "511.06", "unit": "BRL"},
        "verdict": verdict,
        "action": action,
    }


def record(turn_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "turn_id": turn_id,
        "conversation_id": "wa-1",
        "agent": {"name": "closing"},
        "started_at": "2026-09-22T17:00:00+00:00",
        "content_mode": "hash_only",
        **extra,
    }


def search(call_id: str, **applied: Any) -> dict[str, Any]:
    return {
        "call_id": call_id,
        "kind": "tool",
        "name": "search",
        "applied": {"constraints": "cv_0123456789abcdef", **applied},
    }


async def rates(cell: Cell, *features: Feature, baseline: float | None = None) -> Any:
    measurement = MeasurementSettings(
        memory_off_costs=[MemoryOffCost(agent="closing", cost_per_turn_usd=baseline)] if baseline else []
    )
    cell.config.put(
        sample_config().model_copy(
            update={
                "features": FeatureSettings(enabled=frozenset({Feature.TURNS, *features})),
                "measurement": measurement,
            }
        )
    )
    flow = Flow.build(cell)
    model = {"call_id": "m1", "kind": "model", "tokens": {"in": 12_000, "cached": 11_000, "out": 90}}
    turns = [
        record("t1", claims=[price("unsupported", "warn")], calls=[model], cost={"usd": 0.03}),
        record("t2", claims=[price("matched", "none")], calls=[model], cost={"usd": 0.04}),
        record(
            "t3",
            calls=[
                search("k1", hard_sent=["h1"], results_checked=10, violations=1, unverifiable=2),
                search(
                    "k2",
                    hard_sent=["h1", "h2"],
                    results_checked=5,
                    violations=3,
                    unverifiable=0,
                    relaxed="declared",
                ),
            ],
            interactions=[
                {
                    "kind": "presented",
                    "exposure_id": "0192f5a0-0000-7000-8000-000000000301",
                    "list_id": "results",
                    "items": [{"pos": p, "ref": f"item_variant:store:{p}"} for p in range(1, 5)],
                    "delivered_at": "2026-09-22T17:00:00+00:00",
                }
            ],
        ),
    ]
    result = await flow.turns.record(principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID)), turns)
    assert result.errors == []
    await flow.drain()
    day = date(2026, 9, 22)
    return await flow.measure.context_use(ANALYST, since=day, until=day)


async def test_claims_are_counted_by_category_and_verdict(cell: Cell) -> None:
    report = await rates(cell, Feature.CLAIMS)
    [closing] = report.claims
    assert (closing.agent, closing.turns, closing.claims, closing.evidenced) == ("closing", 3, 2, 1)
    assert closing.categories == {"price": {"matched": 1, "unsupported": 1}}
    assert report.constraints is None
    assert "constraints" not in report.model_dump(mode="json")


async def test_constraints_are_sent_verifiable_and_violated_never_only_sent(cell: Cell) -> None:
    report = await rates(cell, Feature.SIGNALS)
    [closing] = report.constraints
    assert (closing.sent, closing.checked, closing.verifiable, closing.unverifiable) == (3, 15, 13, 2)
    # The call whose tool declared its relaxation is not a violation; it is counted as a relaxation.
    assert (closing.violated, closing.relaxed_declared, closing.items_presented) == (1, 1, 4)
    assert closing.violations_per_1000_items == 250.0
    assert report.claims is None


async def test_a_space_without_the_features_reads_as_before(cell: Cell) -> None:
    report = await rates(cell)
    dumped = report.model_dump(mode="json")
    assert "claims" not in dumped
    assert "constraints" not in dumped


async def test_cost_per_turn_is_read_against_the_cost_with_memory_off(cell: Cell) -> None:
    report = await rates(cell, baseline=0.036)
    [closing] = report.cost
    assert (closing.agent, closing.turns, closing.cost_per_turn_usd) == ("closing", 3, round(0.07 / 3, 6))
    assert (closing.model_calls_per_turn, closing.tool_calls_per_turn) == (0.667, 0.667)
    assert (closing.tokens_in_per_turn, closing.tokens_cached_per_turn) == (8000.0, 7333.333)
    assert closing.memory_off_cost_per_turn_usd == 0.036
    assert closing.difference_usd == round(0.07 / 3 - 0.036, 6)
    unmeasured = (await rates(Cell())).cost
    assert unmeasured is not None
    assert (unmeasured[0].memory_off_cost_per_turn_usd, unmeasured[0].difference_usd) == (None, None)
