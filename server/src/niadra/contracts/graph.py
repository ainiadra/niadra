"""The Console's relationship graph (`GET /v1/graph`): a customer's relations, or the space's most
connected customers and companies, as nodes and edges."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from niadra.contracts.common import Model

NodeKindName = Literal["person", "account", "partner", "handle", "object", "agent", "system"]
EdgeKindName = Literal[
    "identifies",
    "member_of",
    "partner_of",
    "owns",
    "relates_to",
    "acted_on",
    "talked_with",
    "reported",
    "handed_off",
    "suggested_same",
]


class GraphNode(Model):
    id: str = Field(description="Opaque, and stable within this answer only.")
    kind: NodeKindName
    label: str = Field(
        description="A company by its name; a person by the name the reader may see, else by their phone or "
        "e-mail (in full for the roles that see contacts, masked for the others); a customer with neither by "
        "its pseudonym. An identifier masked whatever the reader's role, an object by its type and id, an "
        "agent or a system by its source's name."
    )
    ref: str | None = Field(
        description="The real id the Console links to: the profile, the business object or the source. None "
        "for an identifier and for a handoff target that is not a source."
    )
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="`hop`, the distance from the center (0 for the center; 0 for every node of the "
        "overview), and per kind: a customer's `display_name` and `display_contact` (as `GET /v1/profiles` "
        "sends them, None when not known or not for this reader), `pseudonym`, `handles` and, when its "
        "relations were read, "
        "`objects_total` (how many objects it owns or takes part in; a graph shows its 25 latest); an "
        "identifier's `type`, `channel`, "
        "`weight`, `provisional` and `last_seen`; an object's `type`, `namespace`, `external_id`, `state`, "
        "`state_label`, `masked` and `as_of`; a source's `channel` and `vendor`, or a handoff target's "
        "`target`. The overview adds `links`, `objects`, `interactions` and `score` to each customer.",
    )


class GraphEdge(Model):
    id: str
    from_: str = Field(alias="from", description="The node the relation starts at.")
    to: str
    kind: EdgeKindName
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="`identifies`: `strength` (strong or weak), `weight`, `since`. `member_of` and "
        "`partner_of`: `role`, `since`. `owns`: `role` (owner or participant), "
        "`observations` and `instructions` (what the person said about the object). "
        "`relates_to`: `relation`. `acted_on`: `count`, `last_at`, `operations`. `talked_with`: `count`, "
        "`last_at`, `channels`. `reported`: `count`, `last_at`. `handed_off`: `count`, `status`, `outcome`, "
        "`last_at`, `about` (the customer's node). `suggested_same`: `score`.",
    )


class Graph(Model):
    center: str | None = Field(description="The profile the graph is centered on; None for the overview.")
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = Field(
        description="Whether nodes were left out: past the limit, the farthest first (never the center, its "
        "identifiers or its own companies, partners and look-alikes before anything else), or a customer's "
        "objects past the 25 latest."
    )
    withheld: int = Field(
        description="Nodes left out because the reader's role may not see them: identifiers joined by a "
        "claim nobody confirmed yet, and objects the policy denies."
    )
    generated_at: datetime
