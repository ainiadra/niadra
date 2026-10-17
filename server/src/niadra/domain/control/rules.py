"""Business rules of the control plane: placement, approvals, slugs and canonical documents."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from niadra.domain.common.errors import Conflict, Forbidden, InvalidInput, Unavailable, VerificationNotAllowed
from niadra.domain.control.credentials import is_valid_slug
from niadra.domain.control.records import Cell, CellStatus, ConfigDiff, DiffStatus
from niadra.domain.spaces.directory import PRIMARY_INSTANCE, Tier
from niadra.domain.vocabulary import Audience, Environment, Verification

SANDBOX_SUFFIX = "-sandbox"
RESERVED_SPACE_SLUGS = frozenset({"mtls", "api", "www", "control", "cells", "grafana"})
"""Names the edge serves under `<name>.<region>.api.<domain>` itself (the generic webhook's mTLS address)
or may serve: a space named so would share its address, and `scripts/cell.sh route` would point that name
at a dedicated cell."""


def space_slug(project_slug: str, environment: Environment) -> str:
    """Unique per region: it names the space in the key and in `<space>.<region>.api.niadra.com` (7.0)."""
    slug = project_slug if environment is Environment.PRODUCTION else project_slug + SANDBOX_SUFFIX
    if not is_valid_slug(slug):
        raise InvalidInput("the slug must be 3 to 40 lowercase letters, digits or hyphens")
    if slug in RESERVED_SPACE_SLUGS:
        raise InvalidInput(f"the slug {slug} is reserved")
    return slug


def choose_cell(
    cells: Sequence[Cell], placed: Mapping[str, int], region: str, tenant_id: UUID | None = None
) -> Cell:
    """The active cell of the region with the most free capacity; ties go to the oldest cell. A cell
    dedicated to a tenant (plan Regulado) never takes another tenant's space, and a tenant with
    one in the region gets every new space there: when it is full, the answer is no cell, never a
    shared one."""
    active = [c for c in cells if c.region == region and c.status is CellStatus.ACTIVE]
    own = [c for c in active if tenant_id is not None and c.tenant_id == tenant_id]
    pool = own or [c for c in active if not c.dedicated]
    candidates = [c for c in pool if placed.get(c.cell_id, 0) < c.capacity]
    if not candidates:
        raise Unavailable("no cell with free capacity in the region")
    return max(candidates, key=lambda c: (c.capacity - placed.get(c.cell_id, 0), -c.created_at.timestamp()))


def check_placement(cell: Cell, tenant_id: UUID, region: str) -> None:
    """Whether an operator may place (or move) a space of `tenant_id` in `region` on `cell`."""
    if cell.region != region:
        raise InvalidInput("a space never leaves its region")
    if cell.status is not CellStatus.ACTIVE:
        raise Conflict("the cell does not take new spaces")
    if cell.dedicated and cell.tenant_id != tenant_id:
        raise Conflict("the cell is dedicated to another tenant")


def tier_for(cell: Cell, instance: str = PRIMARY_INSTANCE) -> Tier:
    """T2 on a cell dedicated to the tenant; T1 on an instance of its own in a shared cell; T0 otherwise."""
    if cell.dedicated:
        return Tier.T2
    return Tier.T0 if instance == PRIMARY_INSTANCE else Tier.T1


def check_ceiling(audience: Audience, ceiling: Verification) -> None:
    """The highest level a source may ever state (2.2): V4 (a document or a person checked it) only for a
    human desk or a system of record, `no_customer` only for internal agents, and an automated agent
    that talks to customers tops out at V3, the OTP it can run. A leaked agent key cannot declare V4."""
    if ceiling is Verification.NO_CUSTOMER:
        if audience is not Audience.INTERNAL_AGENT:
            raise VerificationNotAllowed("only internal agents work without the customer")
        return
    top = Verification.V4 if audience in (Audience.HUMAN, Audience.INTERNAL_AGENT) else Verification.V3
    if ceiling.rank > top.rank:
        raise VerificationNotAllowed(f"a {audience.value} source's ceiling is at most {top.value}")


def check_approval(diff: ConfigDiff, approver_id: UUID, other_approvers: int) -> None:
    """Four eyes on every change of a production space (4.7): the author approves their own diff only when
    nobody else in the tenant holds a role that approves that document on that space, so a lone person is
    never locked out. A sandbox counts nobody else (`app.control._other_approvers`)."""
    if diff.status not in (DiffStatus.PENDING, DiffStatus.PROPOSED):
        raise Conflict("the diff was already decided")
    if diff.author_id == approver_id and other_approvers > 0:
        raise Forbidden("the author cannot approve the own diff while another person can")


def check_not_stale(changed_since_base: bool) -> None:
    if changed_since_base:
        raise Conflict("the document changed after the diff was written; write the diff again")


def canonical_json(value: Any) -> bytes:
    """The bytes that get hashed and signed: sorted keys, no whitespace, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
