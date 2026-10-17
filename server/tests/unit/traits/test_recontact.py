"""The cross-channel recontact pattern (13, 6.4): the channel a customer comes back on within 72 hours."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from niadra.domain.traits.recontact import recontact_pattern

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


@dataclass(frozen=True)
class C:
    channel: str
    started_at: datetime


def ago(days: float, channel: str) -> C:
    return C(channel, NOW - timedelta(days=days))


def test_two_calls_within_72_hours_of_a_message_make_voice_the_recontact_pattern() -> None:
    history = [ago(60, "whatsapp"), ago(59, "voice"), ago(20, "whatsapp"), ago(18.5, "voice")]
    pattern = recontact_pattern(history, NOW)
    assert pattern is not None
    assert (pattern.channel, pattern.returns) == ("voice", 2)


def test_one_return_a_return_past_72_hours_or_the_same_channel_is_no_pattern() -> None:
    assert recontact_pattern([ago(60, "whatsapp"), ago(59, "voice")], NOW) is None
    assert (
        recontact_pattern([ago(60, "whatsapp"), ago(55, "voice"), ago(20, "whatsapp"), ago(16, "voice")], NOW)
        is None
    )
    assert (
        recontact_pattern([ago(9, "voice"), ago(8, "voice"), ago(3, "voice"), ago(2, "voice")], NOW) is None
    )
    assert recontact_pattern([], NOW) is None


def test_the_lookback_and_the_moment_bound_the_history() -> None:
    old = [ago(400, "whatsapp"), ago(399, "voice"), ago(300, "whatsapp"), ago(299, "voice")]
    assert recontact_pattern(old, NOW) is None
    future = [ago(-5, "whatsapp"), ago(-4, "voice"), ago(-3, "whatsapp"), ago(-2, "voice")]
    assert recontact_pattern(future, NOW) is None


def test_the_most_frequent_return_channel_wins_and_the_order_of_input_does_not_matter() -> None:
    history = [
        ago(90, "whatsapp"), ago(89, "voice"), ago(70, "email"), ago(69, "voice"),
        ago(40, "voice"), ago(39, "whatsapp"), ago(10, "email"), ago(9, "voice"),
    ]  # fmt: skip
    assert (pattern := recontact_pattern(history, NOW)) is not None
    assert pattern.channel == "voice"
    assert recontact_pattern(list(reversed(history)), NOW) == pattern
