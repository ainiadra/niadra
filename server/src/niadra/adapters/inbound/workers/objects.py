"""Task handlers of the `objects` module."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from uuid import UUID

from niadra.app.shared_objects import SharedObserved
from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    async def apply_object_event(task: Task) -> None:
        written = await c.objects.apply_object_event(task)
        # Once committed: the copies the read path serves typed objects from (24.3), the reasons to fetch
        # what was written again (24.3.5), and the shared objects it relates to in the working set (24.2.12).
        await c.state.publish(task.space_id, written)
        await c.refresh.on_written(task.space_id, written)
        await c.shared.enter_related(task.space_id, written)

    async def apply_observations(task: Task) -> None:
        opened = await c.observations.open(task)
        # What a turn showed of a type this process's configuration did not know yet (turned on moments ago)
        # is applied once it does; past the wait, the type is not declared: a data issue for the company.
        subject, shared, still = c.observations.resolve(task.space_id, opened.unresolved)
        ready, later = c.observations.known(task.space_id, opened.enter)
        opened = replace(
            opened, subject=[*opened.subject, *subject], shared=[*opened.shared, *shared], enter=ready
        )
        ended = await c.observations.defer(task.space_id, still, c.clock.now(), later)
        # Past the wait, an internal agent's or a system's tool of a type nobody declared is kept for the
        # inference; a customer-facing agent's stays a data issue.
        kept, dropped = c.observations.kept_undeclared(task.space_id, ended)
        opened = replace(opened, subject=[*opened.subject, *kept])
        if dropped:
            await c.type_drift.undeclared(task.space_id, dropped)
        # The shared objects a turn presented, engaged or watched enter the working set before what the
        # same turn observed of them is written.
        by_reason: dict[str, list[str]] = {}
        for ref, reason, _ in opened.enter:
            by_reason.setdefault(reason, []).append(ref)
        for reason, refs in sorted(by_reason.items()):
            await c.shared.enter_working_set(task.space_id, refs, reason)
        await c.shared.watch(task.space_id, opened.watches)
        await c.shared.observe(task.space_id, [SharedObserved(key, report) for key, report in opened.shared])
        subject = await c.observations.unfenced(task.space_id, opened.subject)
        written = await c.objects.apply_observations(task.space_id, subject)
        await c.state.publish(task.space_id, written)
        await c.refresh.on_written(task.space_id, written)
        await c.shared.enter_related(task.space_id, written)

    async def apply_snapshot(task: Task) -> None:
        await c.shared.apply_snapshot(task)

    async def fire_timers(task: Task) -> None:
        await c.state.publish(task.space_id, await c.objects.fire_timers(task.space_id))
        await c.shared.fire_timers(task.space_id)

    async def observe_config(task: Task) -> None:
        await c.observed_config.observe(task)

    return {
        TaskName.APPLY_OBJECT_EVENT: apply_object_event,
        TaskName.APPLY_OBSERVATIONS: apply_observations,
        TaskName.FIRE_TIMERS: fire_timers,
        TaskName.APPLY_SNAPSHOT: apply_snapshot,
        TaskName.OBSERVE_CONFIG: observe_config,
    }


async def scan_content(c: Any, space_id: UUID) -> int:
    """A per-space loop step: the full scan of the content waiting for it (24.3.10), then the copies."""
    written = await c.objects.scan_pending(space_id)
    await c.state.publish(space_id, written)
    return len(written)


async def plan_shared(c: Any, space_id: UUID) -> int:
    """A per-space loop step: the typed pass of the shared objects, then the copies of the
    customers' objects it expired and their reasons to be fetched again."""
    expired = await c.shared.plan_typed(space_id)
    await c.state.publish(space_id, expired)
    await c.refresh.on_written(space_id, expired)
    return len(expired)


async def retention(c: Any, space_id: UUID) -> int:
    """A per-space loop step (24.5.1): the working states and customers' objects past their retention leave,
    except what a legal hold holds, which the holds port says."""
    held = await c.legal_holds.held(space_id)
    return int(await c.agent_state.sweep(space_id, held)) + int(
        await c.objects.expire_retained(space_id, held)
    )


async def leave_working_set(c: Any, space_id: UUID) -> int:
    """A per-space loop step: shared objects without a reference leave the working set, unless held."""
    declared = c.config.space(space_id).object_types.types
    if not any(t.ownership == "shared" and t.working_set is not None for t in declared):
        return 0
    return int(await c.shared.leave(space_id, await c.legal_holds.held(space_id)))
