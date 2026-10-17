"""The shadow run (24.3.4, the coordination spec's level of adoption "shadow"): the space's policy applied to
the messages it already ingested, with nothing enforced, so a company sees what coordination would change
before any agent asks it. A conflict is what a check would have deferred or refused:

- `concurrent_agents`: an outbound message a subject got while another agent had messaged it in the last 24
  hours (a check would have found the other holder);
- `outside_window`: an outbound message on a channel with a service window, sent after the window of the
  customer's last message there closed (only a paid template may go out);
- `quiet_hours`: an outbound message inside the channel's quiet hours (deferred to their end);
- `over_budget`: a contact an agent started beyond the space's tightest contact budget in its window.

A subject is named by the handle its messages came under, so one person reached on two numbers counts twice.
"""

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

CONCURRENT_AGENTS = "concurrent_agents"
OUTSIDE_WINDOW = "outside_window"
QUIET_HOURS = "quiet_hours"
OVER_BUDGET = "over_budget"
KINDS = (CONCURRENT_AGENTS, OUTSIDE_WINDOW, QUIET_HOURS, OVER_BUDGET)
OTHER_AGENT_WITHIN = timedelta(hours=24)
MONTH_DAYS = 30


@dataclass(frozen=True, slots=True)
class Message:
    event_id: UUID
    subject: UUID
    channel: str
    at: datetime
    outbound: bool
    agent: str
    conversation: str | None


def _never_quiet(channel: str, at: datetime) -> datetime | None:  # noqa: ARG001
    return None


@dataclass(frozen=True, slots=True)
class Policy:
    """The parts of the `coordination` document a shadow run applies: each channel's service window in hours
    and the end of its quiet hours at a moment (None outside them), and the tightest contact budget."""

    windows: dict[str, int] = field(default_factory=dict)
    quiet: Callable[[str, datetime], datetime | None] = _never_quiet
    budget: tuple[int, timedelta] | None = None


@dataclass(frozen=True, slots=True)
class Conflict:
    kind: str
    event_id: UUID
    at: datetime


@dataclass(frozen=True, slots=True)
class Report:
    customers: int
    messages: int
    conflicts: dict[str, int]
    per_thousand_customers_a_month: dict[str, float]
    sample: tuple[Conflict, ...]


def run(messages: Sequence[Message], policy: Policy, *, days: int, sample: int) -> Report:
    """Conflicts over `days` of messages, per kind and per 1,000 customers a month, with the first `sample`
    of them for a person to review."""
    ordered = sorted(messages, key=lambda m: (m.at, str(m.event_id)))
    found: list[Conflict] = []
    heard: dict[tuple[UUID, str], datetime] = {}
    last_by_agent: dict[UUID, dict[str, datetime]] = defaultdict(dict)
    starts: dict[UUID, list[datetime]] = defaultdict(list)
    talking: set[str] = set()
    customers: set[UUID] = set()
    for message in ordered:
        if not message.outbound:
            heard[(message.subject, message.channel)] = message.at
            if message.conversation is not None:
                talking.add(message.conversation)
            continue
        customers.add(message.subject)
        others = [
            at for agent, at in last_by_agent[message.subject].items()
            if agent != message.agent and message.at - at < OTHER_AGENT_WITHIN
        ]  # fmt: skip
        if others:
            found.append(Conflict(CONCURRENT_AGENTS, message.event_id, message.at))
        last_by_agent[message.subject][message.agent] = message.at
        hours = policy.windows.get(message.channel)
        last_heard = heard.get((message.subject, message.channel))
        if hours is not None and (last_heard is None or message.at - last_heard >= timedelta(hours=hours)):
            found.append(Conflict(OUTSIDE_WINDOW, message.event_id, message.at))
        if policy.quiet(message.channel, message.at) is not None:
            found.append(Conflict(QUIET_HOURS, message.event_id, message.at))
        started = message.conversation is None or message.conversation not in talking
        if started and policy.budget is not None:
            limit, window = policy.budget
            recent = [at for at in starts[message.subject] if message.at - at < window]
            if len(recent) >= limit:
                found.append(Conflict(OVER_BUDGET, message.event_id, message.at))
            starts[message.subject] = [*recent, message.at]
        if message.conversation is not None:
            talking.add(message.conversation)
    counts = {kind: sum(1 for c in found if c.kind == kind) for kind in KINDS}
    scale = 1000 / max(len(customers), 1) * MONTH_DAYS / max(days, 1)
    return Report(
        customers=len(customers),
        messages=sum(1 for m in ordered if m.outbound),
        conflicts=counts,
        per_thousand_customers_a_month={kind: round(count * scale, 2) for kind, count in counts.items()},
        sample=tuple(found[:sample]),
    )
