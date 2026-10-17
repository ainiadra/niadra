"""`FeedbackRequest` refuses a correction the memory module could not apply (7.1)."""

from typing import Any

import pytest
from pydantic import ValidationError

from niadra.contracts.events import FeedbackRequest
from niadra.domain.common.ids import uuid7

BASE = {"idempotency_key": "f1", "subject": {"type": "email", "value": "ana@example.com"}}


@pytest.mark.parametrize(
    ("fields", "missing"),
    [
        ({"action": "retract_fact"}, "fact_id"),
        ({"action": "correct_fact", "fact_id": "f"}, "value"),
        ({"action": "correct_fact", "value": "Ana"}, "fact_id"),
        ({"action": "resolve_open_item"}, "open_item_id"),
        ({"action": "conversation_outcome", "value": "resolved"}, "conversation_id"),
        ({"action": "conversation_outcome", "conversation_id": "c-1"}, "value"),
    ],
)
def test_each_action_requires_its_fields(fields: dict[str, Any], missing: str) -> None:
    with pytest.raises(ValidationError, match=missing):
        FeedbackRequest.model_validate({**BASE, **fields})


@pytest.mark.parametrize(
    "fields",
    [
        {"action": "retract_fact", "fact_id": str(uuid7()), "reason": "wrong"},
        {"action": "correct_fact", "fact_id": str(uuid7()), "value": "Ana"},
        {"action": "resolve_open_item", "open_item_id": str(uuid7())},
        {"action": "conversation_outcome", "conversation_id": "c-1", "value": "resolved"},
    ],
)
def test_complete_requests_are_accepted(fields: dict[str, Any]) -> None:
    assert FeedbackRequest.model_validate({**BASE, **fields}).action == fields["action"]
