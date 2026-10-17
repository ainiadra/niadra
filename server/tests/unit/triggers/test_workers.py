"""Worker wiring of the operate modules and the 5-minute sweep entry the scheduler calls."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from niadra.adapters.inbound.workers import notify, objects, traits, triggers
from niadra.domain.tasks import TaskName


class Recorder:
    def __init__(self, name: str, calls: list[tuple[str, Any]]) -> None:
        self._name, self._calls = name, calls

    def __getattr__(self, method: str) -> Any:
        async def call(*args: Any) -> dict[str, Any]:
            self._calls.append((f"{self._name}.{method}", args))
            return {}  # what a sweep step wrote, by space: nothing here

        return call


def test_each_module_handles_its_own_tasks() -> None:
    calls: list[tuple[str, Any]] = []
    c = SimpleNamespace(**{m: Recorder(m, calls) for m in ("objects", "traits", "triggers", "notify")})
    wired = {**objects.handlers(c), **traits.handlers(c), **triggers.handlers(c), **notify.handlers(c)}
    assert set(wired) == {
        TaskName.APPLY_OBJECT_EVENT,
        TaskName.APPLY_OBSERVATIONS,
        TaskName.FIRE_TIMERS,
        TaskName.APPLY_SNAPSHOT,
        TaskName.OBSERVE_CONFIG,
        TaskName.DERIVE_TRAITS,
        TaskName.REDERIVE_TRAITS,
        TaskName.TRIGGER,
        TaskName.DELIVER_WEBHOOK,
    }


async def test_the_sweep_runs_every_deadline_job_in_order() -> None:
    calls: list[tuple[str, Any]] = []
    modules = ("objects", "shared", "traits", "memory", "triggers", "notify", "state")
    c = SimpleNamespace(**{m: Recorder(m, calls) for m in modules})
    now = datetime(2026, 9, 22, 18, tzinfo=UTC)
    await triggers.trigger_sweep(c, now)
    assert calls == [
        ("objects.expire_declared", (now,)),
        ("objects.fire_all_timers", (now,)),
        ("shared.fire_all_timers", (now,)),
        ("traits.expire_due", (now,)),
        ("memory.sweep_due", (now,)),
        ("triggers.sweep", (now,)),
        ("notify.purge_dead", (now,)),
    ]
