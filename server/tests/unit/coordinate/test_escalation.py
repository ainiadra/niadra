"""The coordination routes answer only because a change that loosens a purpose failing closed, the
collection budget or the suppression reasons needs the security role on top of integration (the escalate
hook of the configuration diffs): without it, those routes must not go live."""

from niadra.app.control_config import DOC_TYPES
from niadra.config.coordination import ContactBudget, CoordinationSettings
from niadra.domain.control.records import Role

BEFORE = CoordinationSettings().model_dump(mode="json")


def _with(**changes: object) -> dict[str, object]:
    return {**BEFORE, **changes}


def test_the_coordination_document_escalates_what_fails_closed_to_security() -> None:
    spec = DOC_TYPES["coordination"]
    assert spec.escalate is not None
    assert spec.roles == frozenset({Role.INTEGRATION})
    opened = [
        {**p, "fail": "open", "token": False} if p["name"] == "marketing" else p for p in BEFORE["purposes"]
    ]
    assert spec.escalated(BEFORE, _with(purposes=opened)) == {Role.SECURITY}
    budget = ContactBudget(limit=5, per_hours=24).model_dump(mode="json")
    collection = [{**p, "budget": budget} if p["name"] == "collection" else p for p in BEFORE["purposes"]]
    assert spec.escalated(BEFORE, _with(purposes=collection)) == {Role.SECURITY}
    assert spec.escalated(BEFORE, _with(suppression_reasons=[])) == {Role.SECURITY}
    assert spec.escalated(BEFORE, _with(decision_valid_s=20)) == frozenset()
