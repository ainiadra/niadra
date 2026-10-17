"""The `coordination` document (24.3.4): defaults that follow L23, the shapes it refuses, the parts of a
change that also need the security role."""

from typing import Any

import pytest
from pydantic import ValidationError

from niadra.app.control_config import DOC_TYPES, validate_document
from niadra.config.coordination import (
    BudgetSlice,
    ContactBudget,
    CoordinationSettings,
    PurposeRule,
    SuppressionReason,
    security_changes,
)
from niadra.domain.common.errors import InvalidInput
from niadra.domain.control.records import Role
from niadra.domain.coordinate.model import FailDirection

# A health plan sales space: two marketing contacts a week over every vendor, one of them for the
# proposal follow-up, spread over the week; a panel observes the human attendant.
HEALTH_PLAN_SALES: dict[str, Any] = {
    "purposes": [
        {"name": "transactional", "fail": "open"},
        {"name": "service", "fail": "open"},
        {
            "name": "marketing",
            "fail": "closed",
            "token": True,
            "budget": {
                "limit": 2,
                "per_hours": 168,
                "slices": [{"intent": "proposal_followup", "limit": 1, "pacing": "spread", "spread_days": 7}],
            },
        },
        {"name": "collection", "fail": "closed", "token": True, "budget": {"limit": 3, "per_hours": 720}},
    ],
    "channels": [
        {
            "name": "whatsapp",
            "free_form_hours": 24,
            "template_outside_window": True,
            "quiet_hours": {"start": "21:00", "end": "08:00"},
        }
    ],
    "ownership": {
        "sources": [
            {
                "name": "service_panel",
                "via": "mapping",
                "max_validity_s": 1800,
                "levels": ["human_active"],
                "source_id": "0192f5a0-0000-7000-8000-00000000c009",
                "rules": [
                    {
                        "canonical_type": "panel.session_opened",
                        "level": "human_active",
                        "holder": "human:panel",
                    },
                    {"canonical_type": "panel.session_closed"},
                ],
            },
            {"name": "middleware", "via": "worker", "max_validity_s": 7200, "levels": ["soft_pause"]},
        ]
    },
    "gateways": [
        {"gateway_id": "wa_gateway", "secret_ref": "vault:wa_gateway_rcpt", "channels": ["whatsapp"]}
    ],
    "commitments": [{"name": "discount", "compatibility": "best_wins", "max_validity_days": 7}],
}


OVERSLICED = {"limit": 1, "per_hours": 24, "slices": [{"intent": "x", "limit": 2}]}


def test_the_defaults_are_the_failure_directions_of_l23() -> None:
    purposes = {p.name: (p.fail, p.token) for p in CoordinationSettings().purposes}
    assert purposes == {
        "transactional": (FailDirection.OPEN, False),
        "service": (FailDirection.OPEN, False),
        "marketing": (FailDirection.CLOSED, True),
        "retention": (FailDirection.CLOSED, True),
        "collection": (FailDirection.CLOSED, True),
    }
    ownership = CoordinationSettings().ownership
    assert ownership.levels[0] == "closed"
    assert ownership.levels[-1] == "agent_active"


def test_a_sector_document_is_accepted_through_the_approved_diff() -> None:
    assert DOC_TYPES["coordination"].roles == frozenset({Role.INTEGRATION})
    stored = validate_document("coordination", HEALTH_PLAN_SALES)
    assert stored["purposes"][2]["budget"]["slices"][0]["spread_days"] == 7
    assert CoordinationSettings.model_validate(stored).ownership.levels[1] == "human_active"


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"purposes": [{"name": "promo", "fail": "open", "token": True}]}, "token fails closed"),
        ({"purposes": [{"name": "a", "fail": "open"}, {"name": "a", "fail": "closed"}]}, "declared twice"),
        ({"purposes": [{"name": "promo", "fail": "closed", "budget": OVERSLICED}]}, "add up to more"),
        ({"purposes": [{"name": "Promo", "fail": "open"}]}, "pattern"),
        (
            {
                "ownership": {
                    "sources": [{"name": "p", "via": "mapping", "max_validity_s": 60, "levels": ["x"]}]
                }
            },
            "x",
        ),
        ({"gateways": [{"gateway_id": "g", "secret_ref": "r", "channels": ["sms"]}]}, "unknown channels"),
        ({"channels": [{"name": "sms", "quiet_hours": {"start": "22:00", "end": "22:00"}}]}, "same time"),
        ({"token_ttl_s": 121}, "less than or equal"),
        ({"effect_key_days": {"delivery": 0}}, "from 1 to 3650"),
    ],
)
def test_what_the_document_refuses(change: dict[str, Any], problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        CoordinationSettings.model_validate(change)
    # Through the diff, the error names the field and never echoes a value.
    with pytest.raises(InvalidInput):
        validate_document("coordination", change)


def test_a_spread_slice_needs_its_days_and_only_a_spread_one_has_them() -> None:
    with pytest.raises(ValueError, match="spread_days"):
        BudgetSlice(intent="x", limit=1, pacing="spread")
    with pytest.raises(ValueError, match="spread_days"):
        BudgetSlice(intent="x", limit=1, spread_days=3)


def test_a_change_that_touches_a_closed_purpose_suppressions_or_collection_also_needs_security() -> None:
    base = CoordinationSettings.model_validate(HEALTH_PLAN_SALES)

    def changed(**update: Any) -> list[str]:
        return security_changes(base, base.model_copy(update=update))

    def purposes(name: str, **update: Any) -> list[PurposeRule]:
        return [p.model_copy(update=update) if p.name == name else p for p in base.purposes]

    budget = ContactBudget(limit=4, per_hours=168)
    assert changed(purposes=purposes("marketing", budget=budget)) == ["purposes.marketing"]
    assert changed(purposes=purposes("transactional", budget=budget)) == []
    assert changed(purposes=purposes("service", fail=FailDirection.CLOSED)) == ["purposes.service"]
    assert changed(purposes=[p for p in base.purposes if p.name != "marketing"]) == ["purposes.marketing"]
    reasons = [*base.suppression_reasons, SuppressionReason(name="complaint")]
    assert changed(suppression_reasons=reasons) == ["suppression_reasons"]
    assert changed(channels=[]) == []
    # The collection budget needs security even where a space made collection fail open.
    opened = purposes("collection", fail=FailDirection.OPEN, token=False)
    open_collection = base.model_copy(update={"purposes": opened})
    looser = open_collection.model_copy(
        update={
            "purposes": [
                p.model_copy(update={"budget": budget}) if p.name == "collection" else p
                for p in open_collection.purposes
            ]
        }
    )
    assert security_changes(open_collection, looser) == ["purposes.collection"]
