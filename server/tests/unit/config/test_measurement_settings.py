from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from niadra.config.measurement import MeasurementSettings

START = datetime(2026, 10, 1, tzinfo=UTC)


def _experiment(**change: Any) -> dict[str, Any]:
    experiment: dict[str, Any] = {
        "name": "constraints_holdout",
        "kind": "element",
        "element": "constraints",
        "arms": [{"name": "control", "fraction": 0.1}, {"name": "on", "fraction": 0.9}],
        "salt": "a1b2c3d4e5",
        "starts_at": START,
    }
    experiment.update(change)
    return experiment


def test_nothing_is_measured_differently_by_default() -> None:
    settings = MeasurementSettings()
    assert (settings.revenue, settings.outcomes, settings.experiments) == (None, [], [])
    assert settings.attribution.methods == ["line", "order"]
    assert settings.unmet_demand_min_people == 10


def test_an_experiment_on_an_element_an_agent_and_a_ranking() -> None:
    settings = MeasurementSettings(
        experiments=[
            _experiment(),
            _experiment(name="agent_holdout", kind="agent", element=None, agent="sales"),
            _experiment(
                name="rank", kind="interleave", element=None, tool="search_products", arms=[], sample=0.1
            ),
        ]
    )
    assert [e.kind for e in settings.experiments] == ["element", "agent", "interleave"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"arms": [{"name": "on", "fraction": 1.0}]}, "one of them `control`"),
        ({"arms": [{"name": "control", "fraction": 0.5}, {"name": "on", "fraction": 0.4}]}, "add up to 1"),
        ({"element": None}, "`element` names"),
        ({"element": "pack_section"}, "`section` goes with `pack_section`"),
        ({"kind": "interleave", "element": None, "tool": "search"}, "no arms"),
        ({"ends_at": START}, "ends before it starts"),
    ],
)
def test_an_experiment_that_breaks_a_rule_is_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        MeasurementSettings(experiments=[_experiment(**change)])


def test_revenue_needs_its_signed_definition() -> None:
    outcome = {
        "name": "purchase",
        "object_type": "order",
        "success_states": ["paid"],
        "revenue_field": "total",
    }
    with pytest.raises(ValidationError, match="signed `revenue` definition"):
        MeasurementSettings.model_validate({"outcomes": [outcome]})
    assert MeasurementSettings.model_validate({"outcomes": [outcome], "revenue": {}}).revenue is not None


def test_unmet_demand_never_reports_fewer_than_ten_people() -> None:
    with pytest.raises(ValidationError):
        MeasurementSettings(unmet_demand_min_people=5)
