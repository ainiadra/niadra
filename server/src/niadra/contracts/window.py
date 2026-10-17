"""A contact window in the open specs (the suppression-list spec, 6.1): the local hours a suppression holds
in, in an IANA time zone and on some weekdays. Its own module: the coordination contracts and the constraints
block both carry it."""

from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.common import Model
from niadra.domain.coordinate.window import InvalidWindow, Window

ClockTime = Annotated[str, StringConstraints(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]


class ContactWindow(Model):
    """The local hours a suppression applies in (the suppression-list spec, 6.1): from `from` up to `to`, in
    the IANA zone `tz`, on the ISO weekdays `days` (1 is Monday; null for every day). A window whose `to` is
    not after its `from` crosses midnight and belongs to the day it starts on."""

    from_: ClockTime = Field(alias="from")
    to: ClockTime
    tz: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    days: list[Annotated[int, Field(ge=1, le=7)]] | None = Field(default=None, min_length=1, max_length=7)

    @model_validator(mode="after")
    def _a_window(self) -> Self:
        try:
            self.window()
        except InvalidWindow as error:
            raise ValueError(str(error)) from None
        return self

    def window(self) -> Window:
        return Window.parse(self.from_, self.to, self.tz, self.days)

    @classmethod
    def of(cls, window: Window) -> "ContactWindow":
        return cls.model_validate(window.to_json())
