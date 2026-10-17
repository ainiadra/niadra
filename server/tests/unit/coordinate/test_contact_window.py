"""The window a person's hours give, said in words, kept in the state store's value, and weighed by purpose
and channel (the suppression-list spec, 6.1)."""

from datetime import UTC, datetime

import pytest

from niadra.domain.coordinate.window import (
    InvalidWindow,
    Window,
    WindowedSuppression,
    window_words,
    windows_until,
)

SP = "America/Sao_Paulo"


@pytest.mark.parametrize(
    ("not_before", "not_after", "window"),
    [
        ("09:00", None, ("00:00", "09:00")),
        (None, "20:00", ("20:00", "00:00")),
        ("12:00", "18:00", ("18:00", "12:00")),
    ],
)
def test_the_hours_said_forbid_what_falls_outside_them(
    not_before: str | None, not_after: str | None, window: tuple[str, str]
) -> None:
    said = Window.said(not_before, not_after, SP).to_json()
    assert (said["from"], said["to"], said["tz"], said["days"]) == (*window, SP, None)


@pytest.mark.parametrize(("not_before", "not_after"), [(None, None), ("18:00", "12:00"), ("9h", None)])
def test_hours_that_make_no_window_are_refused(not_before: str | None, not_after: str | None) -> None:
    with pytest.raises(InvalidWindow):
        Window.said(not_before, not_after, SP)


def test_a_window_is_said_in_the_reads_language() -> None:
    assert window_words(Window.said("09:00", None, SP), "voice", "pt") == (
        "ligação: não antes das 09:00, horário de America/Sao_Paulo"
    )
    assert window_words(Window.said("12:00", "18:00", SP), "whatsapp", "pt") == (
        "WhatsApp: só entre 12:00 e 18:00, horário de America/Sao_Paulo"
    )
    assert window_words(Window.said(None, "20:00", SP, [6, 7]), None, "en") == (
        "contact: not after 20:00 (Saturday, Sunday), America/Sao_Paulo time"
    )
    assert window_words(Window.parse("12:00", "14:00", SP), "email", "pt") == (
        "e-mail: não entre 12:00 e 14:00, horário de America/Sao_Paulo"
    )


def test_the_state_stores_value_keeps_the_window_whole() -> None:
    window = Window.said("08:30", "19:45", SP, [1, 2, 3, 4, 5])
    assert Window.decode(window.encode()) == window


def test_a_window_of_any_purpose_holds_for_each_purpose_and_only_on_its_channel() -> None:
    seven = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
    calls = WindowedSuppression("any", "voice", Window.said("09:00", None, SP))
    nine = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    assert windows_until([calls], "marketing", "voice", seven) == nine
    assert windows_until([calls], "service", "voice", seven) == nine
    assert windows_until([calls], "service", "whatsapp", seven) is None
    marketing = WindowedSuppression("marketing", None, Window.said("10:00", None, SP))
    assert windows_until([calls, marketing], "marketing", "whatsapp", seven) == datetime(
        2026, 10, 10, 13, 0, tzinfo=UTC
    )
    assert windows_until([marketing], "service", "whatsapp", seven) is None
