"""`POST /v1/policy/simulate`: what would agent X see of profile Y at level V (5.12)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from niadra.contracts.common import Model, ShortStr
from niadra.contracts.context import View
from niadra.domain.vocabulary import Audience, EventKind, Verification


class SimulateRequest(Model):
    profile_id: UUID
    source_id: UUID | None = Field(
        default=None, description="Simulate as this source; or give audience and purposes."
    )
    audience: Audience | None = None
    purposes: list[ShortStr] = Field(default_factory=list)
    verification: Verification = Verification.V1
    view: View = "chat"


class SimulatedItem(Model):
    id: str
    kind: str
    category: str
    effect: Literal["allow", "mask", "deny"]
    reason: str | None = None
    rule: str | None = None
    delivered: bool = Field(
        description="False when allowed but left out by the budget, or because this source never used "
        "items of its kind and category (`reason` = `unused`)."
    )


class SimulatedLiveTurn(Model):
    """One recent turn of the customer's other conversations, as the `live` block would treat it."""

    at: datetime
    channel: str
    kind: EventKind
    speaker: str
    source_id: UUID
    delivered: bool
    effect: Literal["allow", "mask", "deny"]
    reason: str | None = None
    rule: str | None = None
    text: str | None = Field(
        default=None,
        description="What the reader would get: the turn, or the masked word under a `mask` rule. Never "
        "the text of a withheld turn.",
    )


class SimulatedLive(Model):
    """The `live` block of the same read: recent turns of other conversations, through the same policy
    as the pack's items (5.12)."""

    turns: list[SimulatedLiveTurn] = Field(default_factory=list)
    delivered: int = 0
    withheld: dict[str, int] = Field(default_factory=dict, description="Withheld turns by reason.")
    complete: bool = Field(
        default=True, description="False when the hot layer could not be read whole; the list may be short."
    )


class SimulateResponse(Model):
    policy_version: str
    verification: Verification
    audience: Audience
    items: list[SimulatedItem]
    withheld: dict[str, int]
    text: str = Field(description="The pack the reader would receive.")
    live: SimulatedLive = Field(
        default_factory=SimulatedLive,
        description="The recent turns of the customer's other conversations the reader would get, and the "
        "ones it would not, with the reason and no text.",
    )
