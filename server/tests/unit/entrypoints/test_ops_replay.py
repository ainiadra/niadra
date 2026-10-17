"""`python -m niadra.entrypoints.ops queue-replay`: the operator's way to resume what exhausted its
attempts, like a failed erasure (the replay route does the same for one space from the Console)."""

from typing import Any
from uuid import uuid4

import pytest

from niadra.entrypoints import ops


def test_queue_replay_takes_a_space_a_task_and_a_date(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake(settings: Any, space: Any, task: Any, since: Any, limit: int) -> int:
        seen.update(space=space, task=task, since=since, limit=limit)
        return 0

    monkeypatch.setattr(ops, "queue_replay", fake)
    monkeypatch.setattr(
        ops.Settings, "from_env", classmethod(lambda cls: type("S", (), {"log_level": "INFO"})())
    )
    space = uuid4()
    with pytest.raises(SystemExit) as done:
        ops.main(
            ["queue-replay", "--space", str(space), "--task", "erase", "--since", "2026-09-20T00:00:00+00:00"]
        )
    assert done.value.code == 0
    assert (seen["space"], seen["task"], seen["limit"]) == (space, "erase", 1000)
    assert seen["since"].isoformat() == "2026-09-20T00:00:00+00:00"
    with pytest.raises(SystemExit):
        ops.main(["queue-replay", "--task", "not-a-task"])
