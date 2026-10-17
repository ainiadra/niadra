"""The `measurement` document (24.7): what counts as an outcome and as its revenue, how an outcome is
attributed and in which windows, and the experiments on an element of the pack, on an agent or on a tool's
ranking. Nothing here is personal data. Each part is off until the space writes it, so a space that wrote
nothing is measured by the defaults; the experiments of the memory itself stay in `SpaceConfig.experiments`.
"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from niadra.config.object_types import Duration

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
AttrName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}(?:\.[a-z0-9_]{1,64}){0,3}$")]
Method = Literal["line", "order", "identity", "assisted_handoff"]
DETERMINISTIC: tuple[Method, ...] = ("line", "order")
MIN_DISTINCT_PEOPLE = 10
"""Unmet demand is reported only for a combination of attributes that at least this many people asked for."""


class _Doc(BaseModel):
    # The same as `config.models.Doc`, which imports this module for its section.
    model_config = ConfigDict(extra="forbid", frozen=True, defer_build=True)


class RevenueDefinition(_Doc):
    """Signed with the company, line by line: without it the number never matches its BI. A line's value is
    its amount before discounts, coupons, shipping and taxes; these settings say which of them it takes in."""

    basis: Literal["gross", "net_of_discount"] = Field(
        default="net_of_discount",
        description="`net_of_discount` takes the line's discount and the coupon off; `gross` takes neither.",
    )
    coupons: Literal["prorated_by_line", "order_level"] = Field(
        default="prorated_by_line",
        description="`prorated_by_line` spreads the order's coupon over its lines by their value; "
        "`order_level` leaves the lines whole and takes it off the order's own value only.",
    )
    shipping: Literal["excluded", "included"] = Field(
        default="excluded",
        description="`included` spreads the order's shipping over its lines by their value.",
    )
    taxes: Literal["excluded", "included"] = Field(
        default="excluded",
        description="`included` adds each line's tax, and the order's own to an order without lines.",
    )
    cancellations: Literal["by_line", "whole_order"] = Field(
        default="by_line",
        description="`by_line`: a line in a failed state (a partial cancellation or return) is worth "
        "nothing; `whole_order`: only the order's own state takes its value.",
    )
    exchanges: Literal["new_item", "same_item"] = Field(
        default="new_item",
        description="A line that names the line it replaced: `new_item` is a line of its own, and the "
        "replaced one follows its own state; `same_item` is the same sale, so the replaced line keeps its "
        "value and the replacing one counts nothing.",
    )
    split_orders: Literal["by_seller", "whole_order"] = Field(
        default="by_seller",
        description="`by_seller`: each seller's part of a split purchase is its own order; `whole_order`: "
        "the parts that name the same purchase count, and reconcile, as one order.",
    )
    currency: Currency = "BRL"


class OutcomeLines(_Doc):
    """Where an outcome object keeps its lines, for attribution by line: a list field whose entries carry the
    line's id, the item, its value and quantity, its own state, and the exposure token the cart line copied
    (the exposure token spec). What a line's size and a return's reason say becomes the subject's size."""

    field: Name = "lines"
    line_id: Name = "line_id"
    item: Name | None = Field(default="ref", description="The item as `type:namespace:id`.")
    value: Name | None = Field(
        default="amount", description="The line's amount before discounts, coupons, shipping and taxes."
    )
    discount: Name | None = Field(default="discount", description="The line's own discount.")
    tax: Name | None = Field(default="tax", description="The line's tax, outside its amount.")
    replaces: Name | None = Field(
        default="replaces", description="The id of the line of the same order it replaced, in an exchange."
    )
    quantity: Name | None = "quantity"
    state: Name | None = Field(default="state", description="The line's own state, when it differs.")
    stamp: Name = Field(default="exposure_token", description="The exposure token the cart line carried.")
    failed_states: list[Name] = Field(
        default_factory=lambda: ["returned", "cancelled"],
        max_length=10,
        description="A line in one of these states carries no value: a partial return or cancellation.",
    )
    size: Name | None = Field(default=None, description="The line's size label.")
    size_attribute: AttrName | None = Field(
        default=None, description="The subject attribute a kept size becomes: `size.pants`."
    )
    return_reason: Name | None = Field(
        default=None,
        description="The line's return reason: `size_small` and `size_large` say it did not fit.",
    )

    @model_validator(mode="after")
    def _size_names_an_attribute(self) -> Self:
        if (self.size is None) != (self.size_attribute is None):
            raise ValueError("`size` and `size_attribute` go together")
        return self


class OutcomeDefinition(_Doc):
    """An object type whose lifecycle outcome the space measures. Which of its states are provisional and
    which final comes from the type's lifecycle; this names the states that count as success, the money they
    carry and where the agent's marks are: the exposure token on each line, on the object, or a handoff."""

    name: Name
    object_type: Name
    success_states: list[Name] = Field(min_length=1, max_length=10)
    revenue_field: Name | None = Field(
        default=None,
        description="The object's amount, for an outcome without lines, before discounts, coupon, shipping "
        "and taxes.",
    )
    discount_field: Name | None = Field(default=None, description="The object's discount, without lines.")
    coupon_field: Name | None = Field(default=None, description="The coupon applied to the order as a whole.")
    shipping_field: Name | None = Field(default=None, description="The order's shipping.")
    tax_field: Name | None = Field(default=None, description="The object's tax, without lines.")
    purchase_field: Name | None = Field(
        default=None, description="The id of the purchase a split order's parts share, for `split_orders`."
    )
    lines: OutcomeLines | None = Field(
        default=None, description="The object's lines, for attribution by line."
    )
    stamp_field: Name | None = Field(
        default=None, description="An exposure token on the object itself, for attribution by order."
    )
    handoff_field: Name | None = Field(
        default=None, description="The id of the handoff that assisted the outcome, for assisted attribution."
    )
    final_after: Duration | None = Field(
        default=None,
        description="The exchange window: a provisional outcome is final this long after it was reached, "
        "unless the object reached a final state first.",
    )


class AttributionSettings(_Doc):
    """Deterministic methods (line, order) and probable ones (identity with the same object in a window, an
    assisted handoff) are reported apart, value by band; weights are never summed as money."""

    methods: list[Method] = Field(default_factory=lambda: list(DETERMINISTIC), min_length=1)
    click_window_days: int = Field(default=7, ge=1, le=90)
    view_window_days: int = Field(default=1, ge=0, le=30)
    handoff_window_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="How long after a handoff an outcome that names it counts as assisted by it.",
    )
    reconcile_tolerance: float = Field(
        default=0.02, gt=0, le=0.2, description="The deviation from the BI's file past which a report warns."
    )


class Arm(_Doc):
    name: Name
    fraction: float = Field(gt=0, le=1)


class SequentialTest(_Doc):
    """A mixture sequential probability ratio test (mSPRT): looked at every day without inflating the false
    positives."""

    alpha: float = Field(default=0.05, gt=0, le=0.2)
    mixing_variance: float = Field(
        default=0.01,
        gt=0,
        le=1,
        description="The spread of the effects the test expects, in units of one person's variance: 0.01 "
        "expects differences of about a tenth of a standard deviation. Any value keeps the false positives "
        "within `alpha`; one close to the true effect decides sooner.",
    )


class ElementExperiment(_Doc):
    """A fixed holdout per person on one element (`element`), on the whole agent (`agent`, negotiated), or the
    interleaving of a tool's two rankings (`interleave`, team draft seeded by the turn)."""

    name: Name
    kind: Literal["element", "agent", "interleave"]
    element: Literal["constraints", "soft", "size", "state_view", "pack_section"] | None = None
    section: Literal["facts", "episodes", "actions", "objects"] | None = Field(
        default=None,
        description="The pack's section, for `pack_section`: the sections whose items a measurement can see.",
    )
    agent: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None
    tool: Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")] | None = None
    arms: list[Arm] = Field(default_factory=list, max_length=5)
    sample: float = Field(default=1.0, gt=0, le=1, description="The share of turns an interleaving runs on.")
    salt: Annotated[str, StringConstraints(min_length=8, max_length=128)]
    starts_at: datetime
    ends_at: datetime | None = None
    source_ids: frozenset[UUID] = frozenset()
    cuped_days: int = Field(
        default=90, ge=0, le=365, description="Days of outcome history as the covariate; 0 turns CUPED off."
    )
    sequential: SequentialTest = Field(default_factory=SequentialTest)

    @model_validator(mode="after")
    def _shape_of_kind(self) -> Self:
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError(f"experiment {self.name} ends before it starts")
        if (self.kind == "element") != (self.element is not None):
            raise ValueError(f"experiment {self.name}: `element` names what an element experiment holds out")
        if (self.element == "pack_section") != (self.section is not None):
            raise ValueError(f"experiment {self.name}: `section` goes with `pack_section`, only")
        if (self.kind == "agent") != (self.agent is not None):
            raise ValueError(f"experiment {self.name}: `agent` names the agent an agent experiment holds out")
        if (self.kind == "interleave") != (self.tool is not None):
            raise ValueError(f"experiment {self.name}: `tool` names the ranking an interleaving compares")
        if self.kind == "interleave":
            if self.arms:
                raise ValueError(f"experiment {self.name}: an interleaving has no arms, both rankings run")
        else:
            names = [arm.name for arm in self.arms]
            if len(names) < 2 or len(set(names)) != len(names) or "control" not in names:
                raise ValueError(f"experiment {self.name}: two arms or more, one of them `control`")
            if abs(sum(arm.fraction for arm in self.arms) - 1) > 1e-9:
                raise ValueError(f"experiment {self.name}: the arms' fractions add up to 1")
        return self


class MemoryOffCost(_Doc):
    """What one agent cost per turn with memory off, as the company measured it: the bar a turn with memory
    must not rise above (it replaces context and round trips, never adds to them)."""

    agent: Annotated[str, StringConstraints(min_length=1, max_length=256)] = Field(
        description="The agent's name, as its turn records name it."
    )
    cost_per_turn_usd: float = Field(ge=0, le=100)
    measured: Annotated[str, StringConstraints(max_length=200)] | None = Field(
        default=None, description="How and when it was measured."
    )


class MeasurementSettings(_Doc):
    revenue: RevenueDefinition | None = Field(
        default=None, description="Absent: outcomes are counted, and no revenue is attributed."
    )
    outcomes: list[OutcomeDefinition] = Field(default_factory=list, max_length=20)
    attribution: AttributionSettings = Field(default_factory=AttributionSettings)
    experiments: list[ElementExperiment] = Field(default_factory=list, max_length=20)
    unmet_demand_min_people: int = Field(default=MIN_DISTINCT_PEOPLE, ge=MIN_DISTINCT_PEOPLE, le=1000)
    memory_off_costs: list[MemoryOffCost] = Field(default_factory=list, max_length=50)
    priors_by_source: bool = Field(
        default=False,
        description="Each night, priors per source too: a read by a source smooths affinity toward what that "
        "source's people did, where enough of them saw the value, and toward the space's elsewhere.",
    )

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        for kind, names in (
            ("outcome", [o.name for o in self.outcomes]),
            ("experiment", [e.name for e in self.experiments]),
        ):
            if len(set(names)) != len(names):
                raise ValueError(f"each {kind} has its own name")
        money = ("revenue_field", "discount_field", "coupon_field", "shipping_field", "tax_field")
        if self.revenue is None and any(getattr(o, f) for o in self.outcomes for f in money):
            raise ValueError("an outcome with revenue needs the signed `revenue` definition")
        return self
