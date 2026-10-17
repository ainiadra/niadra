"""Why a task waits in `scheduled_tasks` before the relay publishes it again (5.5).

The column's CHECK constraint allows exactly these values. A writer outside the list fails its insert,
and the task it meant to delay goes back to the broker until the delivery limit dead-letters it: on
25/09 that lost busy, budget and re-extraction delays. The model builds its constraint from this enum,
the space migrations write the same list, and an integration test compares the enum with the live
constraint of a migrated space database, so a new reason fails CI until a migration allows it.
"""

import hashlib
from datetime import timedelta
from enum import StrEnum
from uuid import UUID


class DelayReason(StrEnum):
    RETRY = "retry"  # a failed attempt, with backoff (worker runtime, webhook deliveries)
    LEASE_BUSY = "lease_busy"  # another worker holds the task's lease
    SPACE_BUSY = "space_busy"  # the worker already runs its share of this space's tasks
    SAFETY_NET = "safety_net"  # the hot layer's repair after an ingest
    LLM_RETRY = "llm_retry"  # the extraction model failed or answered out of contract
    LLM_BUDGET = "llm_budget"  # the space's daily extraction budget is spent
    REEXTRACT = "reextract"  # the pause between pages of a re-extraction pass
    RATE_LIMITED = "rate_limited"  # the tenant's trigger cap
    VALID_UNTIL = "valid_until"  # a fact stops being valid: recompile then
    RECEIPT_WATERMARK = "receipt_watermark"  # the receipts of that moment are not all written yet
    TRANSFER_WINDOW = "transfer_window"  # a handoff is measured once its window closes
    EXPORT_SETTLE = "export_settle"  # an export run waits for the changes it covers to settle
    DELAY = "delay"  # the pause between passes of a long rewrite (masking what was already stored)
    BATCH_WINDOW = "batch_window"  # a closed session waits for the customer's others to close with it
    AGENT_TURN = "agent_turn"  # an attendant's action waits for its own reply before it closes an item


def reason_check(column: str = "reason") -> str:
    """The CHECK expression that allows every reason, for the model and for the tests."""
    return f"{column} IN ({', '.join(repr(r.value) for r in DelayReason)})"


def spread_offset(key: UUID, spread: timedelta) -> timedelta:
    """A fixed share of `spread` for `key`, uniform over [0, spread): tasks that all wait for the same
    moment (a daily budget reopening at 00:00 UTC) come back spread over the window instead of together,
    and a redelivered task lands on the same offset."""
    if spread <= timedelta(0):
        return timedelta(0)
    share = int.from_bytes(hashlib.blake2b(key.bytes, digest_size=8).digest(), "big") / 2**64
    return spread * share
