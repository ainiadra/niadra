"""The write of an agent's working state, as the open spec `agent-state.v0` fixes it: a compare-and-swap names
the version it read and writes the whole state; a merge by key removes a field only with the exact marker."""

from typing import Any

import pytest
from pydantic import ValidationError

from niadra.contracts.state import AgentStateReadRequest, AgentStateWrite

SCOPE = {"kind": "conversation", "id": "c-8812"}


def write(**changes: Any) -> dict[str, Any]:
    return {"scope": SCOPE, "agent": "closing", "mode": "cas", "if_version": 0, "body": {}, **changes}


@pytest.mark.parametrize(
    "body",
    [
        write(body={"offer": {"status": "accepted"}}),
        write(mode="merge_by_key", if_version=None, body={"offer": {"$delete": True}, "step": "docs"}),
        write(mode="merge_by_key", if_version=3, body={"cart": []}),
    ],
)
def test_accepted(body: dict[str, Any]) -> None:
    AgentStateWrite.model_validate(body)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (write(if_version=None), "if_version"),
        (write(if_version=-1), "greater than or equal to 0"),
        (write(mode="merge_by_key", if_version=-1), "greater than or equal to 0"),
        (write(body={"offer": {"$delete": True}}), "leave a field out to remove it"),
        (write(mode="merge_by_key", body={"offer": {"$delete": True, "why": "x"}}), "a removal is exactly"),
        (write(mode="merge_by_key", body={"offer": {"$delete": 1}}), "a removal is exactly"),
        (write(mode="merge_by_key", body={"offer": {"$delete": False}}), "a removal is exactly"),
        (write(scope={"kind": "task"}), "scope.id"),
    ],
)
def test_refused(body: dict[str, Any], reason: str) -> None:
    with pytest.raises(ValidationError) as caught:
        AgentStateWrite.model_validate(body)
    assert reason in str(caught.value).replace("\n  ", ".")


def test_a_read_names_its_scope_by_id() -> None:
    with pytest.raises(ValidationError):
        AgentStateReadRequest.model_validate({"scope": {"kind": "subject"}, "agent": "closing"})
