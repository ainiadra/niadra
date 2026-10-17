"""The pure parts of the decision (24.4.2): the declared default of each purpose when Niadra cannot decide
(L23), the channel's service window and the quiet hours."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from niadra.domain.coordinate.decide import Cause, channel_view, fallback, quiet_until
from niadra.domain.coordinate.model import Decision, FailDirection

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("inbound", "fails", "effect_key", "expected"),
    [
        (True, FailDirection.CLOSED, True, (Decision.ALLOW, "unchecked")),
        (True, FailDirection.OPEN, False, (Decision.ALLOW, "unchecked")),
        (False, FailDirection.OPEN, False, (Decision.ALLOW, "unchecked")),
        (False, FailDirection.OPEN, True, (Decision.DEFER, "unavailable")),
        (False, FailDirection.CLOSED, False, (Decision.DEFER, "unavailable")),
        (False, FailDirection.CLOSED, True, (Decision.DEFER, "unavailable")),
    ],
)
def test_every_check_declares_which_way_it_fails(
    inbound: bool, fails: FailDirection, effect_key: bool, expected: tuple[Decision, str]
) -> None:
    assert fallback(inbound=inbound, fails=fails, effect_key=effect_key, cause=Cause.UNAVAILABLE) == expected


def test_while_the_store_is_reloaded_a_closed_purpose_waits_as_rebuilding() -> None:
    answer = fallback(inbound=False, fails=FailDirection.CLOSED, effect_key=False, cause=Cause.REBUILDING)
    assert answer == (Decision.DEFER, "rebuilding")


def test_the_service_window_runs_from_the_last_customer_message() -> None:
    last = NOW - timedelta(hours=3)
    inside = channel_view(
        free_form_hours=24, template_outside_window=True, last_inbound=last, quiet_until=None, now=NOW
    )
    assert (inside.free_form_until, inside.template_required) == (last + timedelta(hours=24), False)
    old = NOW - timedelta(hours=25)
    outside = channel_view(
        free_form_hours=24, template_outside_window=True, last_inbound=old, quiet_until=None, now=NOW
    )
    assert (outside.free_form_until, outside.template_required) == (None, True)
    unknown = channel_view(
        free_form_hours=24, template_outside_window=True, last_inbound=None, quiet_until=None, now=NOW
    )
    assert unknown.template_required
    free = channel_view(
        free_form_hours=None, template_outside_window=False, last_inbound=None, quiet_until=None, now=NOW
    )
    assert (free.free_form_until, free.template_required) == (None, False)


@pytest.mark.parametrize(
    ("local", "expected"),
    [
        (datetime(2026, 9, 29, 22, 30), datetime(2026, 9, 30, 8, 0)),
        (datetime(2026, 9, 30, 6, 0), datetime(2026, 9, 30, 8, 0)),
        (datetime(2026, 9, 30, 8, 0), None),
        (datetime(2026, 9, 30, 12, 0), None),
    ],
)
def test_quiet_hours_across_midnight_end_on_the_next_local_morning(
    local: datetime, expected: datetime | None
) -> None:
    now = local.replace(tzinfo=SAO_PAULO)
    found = quiet_until(time(21, 0), time(8, 0), SAO_PAULO, now)
    assert found == (expected.replace(tzinfo=SAO_PAULO) if expected else None)


def test_quiet_hours_within_one_day() -> None:
    now = datetime(2026, 9, 29, 13, 0, tzinfo=SAO_PAULO)
    assert quiet_until(time(12, 0), time(14, 0), SAO_PAULO, now) == datetime(
        2026, 9, 29, 14, 0, tzinfo=SAO_PAULO
    )
    assert quiet_until(time(12, 0), time(14, 0), SAO_PAULO, now + timedelta(hours=2)) is None
