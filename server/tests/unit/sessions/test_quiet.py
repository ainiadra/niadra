"""When a session nobody ended closes and becomes memory: once quiet, while the channel's inactivity still
decides what is one activity."""

from datetime import timedelta

from niadra.domain.sessions.rules import QUIET_MINUTES, inactivity, quiet


def test_a_session_nobody_ended_closes_once_quiet_and_its_activity_still_spans_the_channels_inactivity() -> (
    None
):
    minutes = {"whatsapp": 20}
    assert quiet(minutes, kind="conversation", channel="whatsapp") == timedelta(minutes=QUIET_MINUTES)
    assert quiet({}, kind="conversation", channel="app") == timedelta(minutes=QUIET_MINUTES)
    assert inactivity(minutes, kind="conversation", channel="whatsapp") == timedelta(minutes=20)


def test_a_channel_set_quieter_than_the_default_keeps_its_own() -> None:
    assert quiet({"voice": 1}, kind="conversation", channel="voice") == timedelta(minutes=1)


def test_each_reopen_doubles_the_quiet_up_to_the_channels_inactivity() -> None:
    waits = [quiet({"whatsapp": 20}, kind="conversation", channel="whatsapp", reopened=n) for n in range(5)]
    assert waits == [timedelta(minutes=m) for m in (QUIET_MINUTES, 6, 12, 20, 20)]
    assert quiet({}, kind="task", channel="erp", reopened=50) == timedelta(minutes=30)
