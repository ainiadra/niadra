"""When a space's database next needs the relay or the scheduler (06, 6.3).

The loops process reads it in one statement and sleeps until then, so a quiet space's database is not
polled and holds no connection between passes. What a transaction schedules for later, or an outbox row
the publisher could not send, rings the doorbell (`ports.infra.Doorbell`) and wakes the loop sooner.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

# The writer publishes right after commit; the relay only takes rows it left behind (5.5).
STRAGGLER_AGE = timedelta(seconds=2)


@dataclass(frozen=True, slots=True)
class PipelineDue:
    outbox_oldest: datetime | None
    scheduled_due: datetime | None
    schedule_due: datetime | None
    intake_pending: int = 0
    intake_oldest: datetime | None = None

    def relay_due(self, now: datetime, straggler_age: timedelta) -> bool:
        """An outbox row the publisher left behind, or a delayed task whose time came."""
        stale = self.outbox_oldest is not None and self.outbox_oldest <= now - straggler_age
        return stale or (self.scheduled_due is not None and self.scheduled_due <= now)

    def schedules_due(self, now: datetime) -> bool:
        return self.schedule_due is not None and self.schedule_due <= now

    def next_at(self, straggler_age: timedelta) -> datetime | None:
        """The earliest time something known today becomes due; None when nothing waits."""
        times = [
            t
            for t in (
                self.outbox_oldest + straggler_age if self.outbox_oldest is not None else None,
                self.scheduled_due,
                self.schedule_due,
            )
            if t is not None
        ]
        return min(times, default=None)

    def outbox_lag_s(self, now: datetime) -> float:
        return 0.0 if self.outbox_oldest is None else max(0.0, (now - self.outbox_oldest).total_seconds())

    def intake_lag_s(self, now: datetime) -> float:
        return 0.0 if self.intake_oldest is None else max(0.0, (now - self.intake_oldest).total_seconds())
