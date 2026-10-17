"""Clock-skew correction of `occurred_at` against `received_at` (5.2)."""

from datetime import datetime, timedelta

SKEW_TOLERANCE = timedelta(minutes=5)


def correct_occurred_at(
    occurred_at: datetime, received_at: datetime, tolerance: timedelta = SKEW_TOLERANCE
) -> tuple[datetime, bool]:
    """Returns the time to store and whether it was corrected.

    Only timestamps ahead of arrival are corrected: late delivery is legitimate (WhatsApp resync,
    the batch API up to 24 h, post-call analysis), but an event cannot happen after we received it.
    """
    if occurred_at - received_at > tolerance:
        return received_at, True
    return occurred_at, False
