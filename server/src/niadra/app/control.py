"""Use cases of the control plane (5.16, 7.4).

Every change that alters what a cell must enforce (an applied diff, a source, a key, a placement)
publishes a new signed snapshot of that cell in the same transaction, so a committed change is always
visible to the next pull (4.7).
"""

import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import orjson

from niadra.app.control_config import (
    DOC_TYPES,
    DocType,
    build_space_config,
    current_document,
    doc_type,
    refuse_cell_problems,
    refuse_new_problems,
    revoked_owners,
    validate_document,
)
from niadra.config.models import (
    LLM_BUDGET_METRIC,
    SnapshotManifest,
    SpaceConfig,
    SpaceLimits,
)
from niadra.domain.common.errors import (
    Conflict,
    Forbidden,
    InvalidInput,
    NotFound,
    Unauthenticated,
    Unavailable,
)
from niadra.domain.common.ids import uuid7
from niadra.domain.control.credentials import (
    NO_PASSWORD,
    check_password,
    format_key,
    has_password,
    hash_password,
    invitation_token_hash,
    is_valid_slug,
    matching_hash,
    new_key_id,
    new_recovery_codes,
    new_secret,
    new_totp_secret,
    new_url_token,
    normalize_recovery_code,
    recovery_code_hash,
    totp_step,
)
from niadra.domain.control.preferences import check_preference
from niadra.domain.control.records import (
    AccessChange,
    AccessChangeKind,
    ApiKey,
    Caller,
    Cell,
    CellStatus,
    ConfigDiff,
    ConfigVersion,
    DeletionReceipt,
    DiffStatus,
    Invitation,
    KeyStatus,
    MoveStatus,
    PlacementMove,
    Project,
    Quota,
    RecoveryCode,
    Revocation,
    RevocationKind,
    Role,
    RoleBinding,
    SecondFactorReset,
    SecretRef,
    SigningKey,
    Snapshot,
    Source,
    Space,
    Tenant,
    UsageAggregate,
    User,
)
from niadra.domain.control.rules import (
    canonical_json,
    check_approval,
    check_ceiling,
    check_not_stale,
    check_placement,
    choose_cell,
    sha256_hex,
    space_slug,
    tier_for,
)
from niadra.domain.control.sso import email_domain
from niadra.domain.control.usage import StatementLine, month_range, statement
from niadra.domain.edge.keys import fast_hash
from niadra.domain.edge.keys import slow_hash as key_slow_hash
from niadra.domain.spaces.deletion import RECEIPT_FIELDS, receipt_hash
from niadra.domain.spaces.directory import PRIMARY_INSTANCE
from niadra.domain.vocabulary import Audience, Environment, Scope, Verification
from niadra.ports.control import ControlUnitOfWork, ControlUnitOfWorkFactory, Signer
from niadra.ports.infra import Clock
from niadra.ports.proposals import OBSERVED_AUTHOR

MOVED_NOTICE = timedelta(days=30)
OBSERVABLE = frozenset({"object-types", "schemas"})
"""The documents a cell infers from a space's traffic and records as observed."""
OBSERVED_MAX_BYTES = 256 * 1024
"""How long the old cell keeps answering 421 for a space that moved away."""

_KMS_ARN = re.compile(r"^arn:aws[a-z-]*:kms:(?P<region>[a-z0-9-]+):\d{12}:key/[0-9a-f-]{36}$")
# A database instance's name, as the cell knows it (NIADRA_DB_INSTANCES) and the snapshot carries it.
_INSTANCE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_TOKEN_HASH_BYTES = 32

AGENT_SCOPES = frozenset(
    {
        Scope.TRACK,
        Scope.ACT,
        Scope.CONTEXT,
        Scope.SEARCH,
        Scope.IDENTIFY,
        Scope.COORDINATE,
        Scope.REPLAY,
        Scope.AGENT_MEMORY,
        Scope.AGENT_MEMORY_WRITE,
        Scope.AGENT_STATE,
    }
)
"""Every scope an agent uses (zero touch): a key made with no scopes works for every agent call.
Admin scopes (`admin`, `analytics`, `identity:link`) and a system's `state:push` are asked for by name."""

DEFAULT_SCOPES: dict[Audience, frozenset[Scope]] = {
    Audience.CUSTOMER_AGENT: AGENT_SCOPES,
    Audience.INTERNAL_AGENT: AGENT_SCOPES,
    Audience.HUMAN: frozenset({Scope.CONTEXT, Scope.SEARCH}),
    Audience.ANALYST: frozenset({Scope.ANALYTICS}),
}
# A password check for an unknown e-mail still pays the scrypt cost, so timing does not reveal accounts.
_DUMMY_HASH = hash_password("niadra-dummy-password")


@dataclass(frozen=True, slots=True)
class ControlSettings:
    issuer: str = "niadra-control"
    audience: str = "niadra-cell"
    token_ttl_s: int = 900
    rotation_grace_s: int = 24 * 3600
    revocation_page: int = 500
    poll_interval_s: float = 1.0
    min_password_length: int = 12
    invitation_ttl_s: int = 7 * 24 * 3600


@dataclass(frozen=True, slots=True)
class SourceSpec:
    name: str
    audience: Audience
    vendor: str | None = None
    channel: str | None = None
    purposes: frozenset[str] = frozenset()
    verification_ceiling: Verification = Verification.V2
    confirms_before_acting: bool = False
    navigation_enabled: bool = True
    trusted_action_ops: frozenset[str] = frozenset()
    critical: bool = False


@dataclass(frozen=True, slots=True)
class BindingSpec:
    role: Role
    space_id: UUID | None = None
    source_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class IssuedKey:
    key: ApiKey
    plaintext: str
    """Shown once and never stored."""


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    expires_in: int
    space_id: UUID
    recovery_codes: list[str] | None = None
    """Shown once, when this login confirmed the authenticator."""
    recovery_codes_left: int | None = None
    """When this login spent a recovery code."""


@dataclass(frozen=True, slots=True)
class SecondFactorStatus:
    confirmed_at: datetime | None
    recovery_codes_left: int


@dataclass(frozen=True, slots=True)
class NewAuthenticator:
    secret: str
    account: str
    """The name the authenticator app shows: the person's e-mail."""


@dataclass(frozen=True, slots=True)
class IssuedInvitation:
    invitation: Invitation
    token: str
    """Shown once, to the admin who asked; only a keyed hash is stored."""


@dataclass(frozen=True, slots=True)
class NewUser:
    user: User
    bindings: list[RoleBinding]
    totp_secret: str | None
    """Shown once for enrollment."""
    invitation: IssuedInvitation | None = None
    """For a person created without a password who does not sign in through the tenant's SSO."""
    sso: bool = False
    """The person's e-mail domain belongs to the tenant's SSO connection."""


@dataclass(frozen=True, slots=True)
class PersonView:
    """A person of the tenant as the people screen shows them."""

    user: User
    bindings: list[RoleBinding]
    recovery_codes_left: int
    invitation: Invitation | None
    """The link to choose a password that is still open, if any."""
    sso: bool
    """The person's e-mail domain belongs to the tenant's SSO connection."""


@dataclass(frozen=True, slots=True)
class InvitationPreview:
    email: str
    tenant_name: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Me:
    user: User
    bindings: list[RoleBinding]
    spaces: list[Space]
    """The spaces the person can open: every space of the tenant for a tenant-wide binding."""
    sso: bool = False
    """The person's e-mail domain belongs to the tenant's SSO connection."""


@dataclass(frozen=True, slots=True)
class ProjectView:
    project: Project
    spaces: list[Space]


@dataclass(frozen=True, slots=True)
class SignedFeed:
    """`body` has the shape of the cell's `RevocationFeed`; `signature` covers its canonical JSON."""

    cell_id: str
    body: dict[str, Any]
    kid: str
    signature: bytes


@dataclass(frozen=True, slots=True)
class UsageReport:
    aggregates: list[UsageAggregate]
    quotas: list[Quota]


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    tenant: Tenant
    project: Project
    space: Space
    admin: NewUser
    password: str
    keys: dict[str, IssuedKey] = field(default_factory=dict)


class ControlService:
    def __init__(
        self,
        uow: ControlUnitOfWorkFactory,
        signer: Signer,
        clock: Clock,
        pepper: bytes,
        settings: ControlSettings | None = None,
    ) -> None:
        if len(pepper) < 32:
            raise ValueError("the key pepper must have at least 32 bytes")
        self._uow = uow
        self._signer = signer
        self._clock = clock
        self._pepper = pepper
        self._settings = settings or ControlSettings()

    # Operators: tenants and cells

    async def create_tenant(self, name: str, slug: str, *, mfa_required: bool = True) -> Tenant:
        if not is_valid_slug(slug):
            raise InvalidInput("invalid tenant slug")
        tenant = Tenant(uuid7(), name, slug, self._clock.now(), mfa_required)
        async with self._uow() as uow:
            await uow.directory.add_tenant(tenant)
        return tenant

    async def register_cell(
        self,
        cell_id: str,
        region: str,
        address: str,
        capacity: int,
        *,
        tenant_id: UUID | None = None,
        token_sha256: bytes | None = None,
    ) -> Cell:
        """A new cell. With `tenant_id`, a cell dedicated to that tenant (plan Regulado), which
        must come with the SHA-256 of a credential of its own: the one the shared cells use never acts
        for it, and its own acts for nothing else."""
        if capacity < 1:
            raise InvalidInput("capacity must be positive")
        if tenant_id is not None and token_sha256 is None:
            raise InvalidInput("a dedicated cell needs a credential of its own")
        if token_sha256 is not None and len(token_sha256) != _TOKEN_HASH_BYTES:
            raise InvalidInput("the credential hash must be a SHA-256")
        cell = Cell(
            cell_id,
            region,
            address,
            capacity,
            CellStatus.ACTIVE,
            self._clock.now(),
            tenant_id=tenant_id,
            token_hash=token_sha256,
        )
        async with self._uow() as uow:
            if await uow.directory.get_cell(cell_id):
                raise Conflict("the cell already exists")
            if tenant_id is not None and await uow.directory.get_tenant(tenant_id) is None:
                raise NotFound("unknown tenant")
            await uow.directory.add_cell(cell)
            await self.publish(uow, cell_id)
        return cell

    async def tenant_by_slug(self, slug: str) -> Tenant:
        async with self._uow() as uow:
            tenant = await uow.directory.tenant_by_slug(slug)
        if tenant is None:
            raise NotFound("unknown tenant")
        return tenant

    async def rekey_cell(self, cell_id: str, token_sha256: bytes) -> None:
        """A new credential of the cell's own; the one before stops at once."""
        if len(token_sha256) != _TOKEN_HASH_BYTES:
            raise InvalidInput("the credential hash must be a SHA-256")
        async with self._uow() as uow:
            if await uow.directory.get_cell(cell_id, lock=True) is None:
                raise NotFound("unknown cell")
            await uow.directory.set_cell_token(cell_id, token_sha256)

    async def cell_credential(self, cell_id: str) -> bytes | None:
        """The SHA-256 of the cell's own credential; None for a cell that uses the shared one (or none)."""
        async with self._uow() as uow:
            cell = await uow.directory.get_cell(cell_id)
        return cell.token_hash if cell else None

    async def close_cell(self, cell_id: str) -> Cell:
        """A cell that takes no more spaces, before its machines go (scripts/cell.sh down). Refused while
        it still serves a space: its data would go with it."""
        async with self._uow() as uow:
            cell = await uow.directory.get_cell(cell_id, lock=True)
            if cell is None:
                raise NotFound("unknown cell")
            if await uow.directory.spaces_in_cell(cell_id):
                raise Conflict("the cell still serves spaces")
            await uow.directory.set_cell_status(cell_id, CellStatus.CLOSED)
        return replace(cell, status=CellStatus.CLOSED)

    async def routes(self, cell_id: str) -> list[Space]:
        """The live spaces a cell serves: their addresses, `<space>.<region>.api.<domain>`, must reach it
        (scripts/cell.sh route writes them for a dedicated cell)."""
        async with self._uow() as uow:
            if await uow.directory.get_cell(cell_id) is None:
                raise NotFound("unknown cell")
            return await uow.directory.spaces_in_cell(cell_id)

    async def place_project(
        self,
        tenant_slug: str,
        name: str,
        slug: str,
        region: str,
        cell_id: str,
        instance: str = PRIMARY_INSTANCE,
    ) -> ProjectView:
        """An operator's placement under contract (control_cli place-project): a new project whose sandbox
        and production spaces live on the named cell, and on the named database instance of it. On a cell
        dedicated to the tenant they are tier T2; on an instance of their own in a shared cell, T1."""
        if not _INSTANCE.match(instance):
            raise InvalidInput("invalid instance name")
        async with self._uow() as uow:
            tenant = await uow.directory.tenant_by_slug(tenant_slug)
            cell = await uow.directory.get_cell(cell_id, lock=True)
            if tenant is None or cell is None:
                raise NotFound("unknown tenant or cell")
            check_placement(cell, tenant.tenant_id, region)
            view = await self._add_project(
                uow, tenant.tenant_id, name, slug, region, cell=cell, instance=instance
            )
            await self.publish(uow, cell.cell_id)
        return view

    async def list_cells(self) -> list[Cell]:
        async with self._uow() as uow:
            return await uow.directory.list_cells()

    async def set_space_key(self, space_id: UUID, kms_key_arn: str | None) -> Space:
        """An operator names the customer's KMS key of a space, under contract (10, D.1). The key must be in
        the space's region; the cell generates the space's next data keys under it and refuses to fall
        back to its own key when the customer's is not usable."""
        if kms_key_arn is not None:
            match = _KMS_ARN.match(kms_key_arn)
            if match is None:
                raise InvalidInput("kms_key_arn must be a KMS key ARN")
        async with self._uow() as uow:
            space = await uow.directory.get_space(space_id, lock=True)
            if space is None or space.deleted_at is not None:
                raise NotFound("unknown space")
            if kms_key_arn is not None and match is not None and match.group("region") != space.region:
                raise InvalidInput("the key must be in the space's region")
            await uow.directory.set_space_key(space_id, kms_key_arn)
            await self.publish(uow, space.cell_id)
        return replace(space, kms_key_arn=kms_key_arn)

    async def set_quota(self, tenant_id: UUID, metric: str, limit: int, period: str = "month") -> Quota:
        quota = Quota(tenant_id, metric, limit, period)
        async with self._uow() as uow:
            await uow.directory.set_quota(quota)
            if metric.startswith(f"{LLM_BUDGET_METRIC}:"):
                # The contract's spend ceiling reaches the cells with the next snapshot, now.
                cells = {s.cell_id for s in await uow.directory.spaces_of_tenant(tenant_id)}
                for cell_id in sorted(cells):
                    await self.publish(uow, cell_id)
        return quota

    async def ensure_signing_key(self) -> None:
        """Records the public half of the current key so every replica publishes it in the JWKS."""
        key = SigningKey(
            self._signer.kid, self._signer.algorithm, self._signer.public_jwks()[0], self._clock.now()
        )
        async with self._uow() as uow:
            await uow.access.save_signing_key(key)

    async def bootstrap(
        self,
        *,
        tenant_name: str,
        tenant_slug: str,
        email: str,
        region: str,
        cell_id: str,
        cell_address: str,
        password: str,
        with_totp: bool = True,
        cell_capacity: int = 500,
        sources: Sequence[SourceSpec] = (),
        project_name: str | None = None,
        project_slug: str | None = None,
    ) -> BootstrapResult:
        """The founder's first day: tenant, project, sandbox and production spaces, sources, the first
        admin and one sandbox key per source, in one transaction. The project takes the tenant's name
        and slug unless it has its own (the company "Acme", the project "Support")."""
        self._check_password(password)
        now = self._clock.now()
        async with self._uow() as uow:
            if not await uow.directory.get_cell(cell_id):
                await uow.directory.add_cell(
                    Cell(cell_id, region, cell_address, cell_capacity, CellStatus.ACTIVE, now)
                )
            tenant = Tenant(uuid7(), tenant_name, tenant_slug, now, mfa_required=with_totp)
            await uow.directory.add_tenant(tenant)
            view = await self._add_project(
                uow, tenant.tenant_id, project_name or tenant_name, project_slug or tenant_slug, region
            )
            sandbox = next(s for s in view.spaces if s.environment is Environment.SANDBOX)
            admin = await self._add_user(
                uow, tenant.tenant_id, email, password, [BindingSpec(Role.ADMIN)], with_totp=with_totp
            )
            keys: dict[str, IssuedKey] = {}
            for spec in sources:
                source = await self._add_source(uow, sandbox, spec)
                keys[spec.name] = await self._issue(uow, sandbox, source, DEFAULT_SCOPES.get(spec.audience))
            await self.publish(uow, sandbox.cell_id)
        return BootstrapResult(tenant, view.project, sandbox, admin, password, keys)

    async def add_admin_beside(self, colleague_email: str, email: str, password: str) -> NewUser:
        """An operator's command (control_cli add-admin): one more admin in a colleague's tenant. The
        second factor starts pending, so the person enrolls it in the Console at the first login."""
        self._check_password(password)
        async with self._uow() as uow:
            colleague = await uow.access.user_by_email(_normalize_email(colleague_email))
            if colleague is None:
                raise NotFound("unknown user")
            return await self._add_user(
                uow, colleague.tenant_id, email, password, [BindingSpec(Role.ADMIN)], with_totp=True
            )

    # Projects, spaces and placement

    async def create_project(self, caller: Caller, name: str, slug: str, region: str) -> ProjectView:
        _require(caller, Role.ADMIN)
        async with self._uow() as uow:
            view = await self._add_project(uow, caller.tenant_id, name, slug, region)
            for cell_id in {s.cell_id for s in view.spaces}:
                await self.publish(uow, cell_id)
        return view

    async def projects(self, caller: Caller) -> list[ProjectView]:
        async with self._uow() as uow:
            spaces = await uow.directory.spaces_of_tenant(caller.tenant_id)
            return [
                ProjectView(p, [s for s in spaces if s.project_id == p.project_id])
                for p in await uow.directory.projects_of(caller.tenant_id)
            ]

    async def get_tenant(self, caller: Caller, tenant_id: UUID) -> Tenant:
        if tenant_id != caller.tenant_id:
            raise NotFound("unknown tenant")
        async with self._uow() as uow:
            tenant = await uow.directory.get_tenant(tenant_id)
        if tenant is None:
            raise NotFound("unknown tenant")
        return tenant

    async def get_space(self, caller: Caller, space_id: UUID) -> Space:
        async with self._uow() as uow:
            return await _space_of(uow, caller, space_id)

    async def placement(self, caller: Caller, space_id: UUID) -> tuple[Space, PlacementMove | None]:
        async with self._uow() as uow:
            space = await _space_of(uow, caller, space_id)
            return space, await uow.directory.open_move(space_id)

    async def request_move(self, space_id: UUID, to_cell: str, reason: str) -> PlacementMove:
        """Starts a cell change: the cells copy and dual-write; `complete_move` flips the map (5.16)."""
        async with self._uow() as uow:
            space = await uow.directory.get_space(space_id, lock=True)
            cell = await uow.directory.get_cell(to_cell)
            if space is None or space.deleted_at is not None or cell is None:
                raise NotFound("unknown space or cell")
            if cell.region != space.region:
                raise InvalidInput("a space never leaves its region")
            if cell.status is not CellStatus.ACTIVE or cell.cell_id == space.cell_id:
                raise Conflict("the target cell cannot receive the space")
            if cell.dedicated and cell.tenant_id != space.tenant_id:
                raise Conflict("the cell is dedicated to another tenant")
            if await uow.directory.open_move(space_id):
                raise Conflict("the space is already moving")
            move = PlacementMove(
                uuid7(), space_id, space.cell_id, to_cell, MoveStatus.COPYING, reason, self._clock.now()
            )
            await uow.directory.add_move(move)
        return move

    async def complete_move(self, space_id: UUID) -> PlacementMove:
        async with self._uow() as uow:
            move = await uow.directory.open_move(space_id)
            if move is None:
                raise NotFound("the space is not moving")
            now = self._clock.now()
            await uow.directory.complete_move(move.move_id, now)
            await uow.directory.set_space_cell(space_id, move.to_cell)
            await self.publish(uow, move.from_cell)
            await self.publish(uow, move.to_cell)
        return replace(move, status=MoveStatus.COMPLETED, completed_at=now)

    # Deletion (9)

    async def delete_space(self, caller: Caller, space_id: UUID) -> tuple[Space, DeletionReceipt | None]:
        """Deletes a space for good. Its keys stop at once (revoked, like a source cut); the next snapshot
        lists it as removed, and its cell drops the database, destroys the data keys, deletes objects,
        dumps and cache keys, and sends back a receipt. Asking again returns the same state."""
        _require(caller, Role.ADMIN)
        async with self._uow() as uow:
            space = await uow.directory.get_space(space_id, lock=True)
            if space is None or space.tenant_id != caller.tenant_id:
                raise NotFound("unknown space")
            if space.deleted_at is not None:
                return space, (await uow.directory.deletion_receipts([space_id])).get(space_id)
            now = self._clock.now()
            await uow.directory.mark_space_deleted(space_id, now)
            for source in await uow.access.sources_of(space_id):
                for key in await uow.access.keys_of_source(source.source_id):
                    if key.status is KeyStatus.ACTIVE:
                        await uow.access.revoke_key(key.key_id, now)
                        await uow.access.add_revocation(
                            space_id, RevocationKind.KEY, source.source_id, key.key_id, "space deleted", now
                        )
            await self.publish(uow, space.cell_id)
        return replace(space, deleted_at=now), None

    async def deletion(self, caller: Caller, space_id: UUID) -> tuple[Space, DeletionReceipt | None]:
        """A deleted space and its receipt; None while the cell is still deleting."""
        _require(caller, Role.ADMIN, Role.SECURITY)
        async with self._uow() as uow:
            space = await uow.directory.get_space(space_id)
            if space is None or space.tenant_id != caller.tenant_id or space.deleted_at is None:
                raise NotFound("no deleted space with this id")
            return space, (await uow.directory.deletion_receipts([space_id])).get(space_id)

    async def store_deletion_receipt(
        self, cell_id: str, space_id: UUID, receipt: dict[str, Any], digest_hex: str
    ) -> DeletionReceipt:
        """From the cell that deleted the space. Idempotent: the same receipt again is accepted; a
        different one for the same space is a conflict."""
        try:
            digest = bytes.fromhex(digest_hex)
        except ValueError:
            raise InvalidInput("receipt_hash is not hexadecimal") from None
        if receipt_hash(receipt) != digest:
            raise InvalidInput("the receipt does not match its hash")
        if missing := sorted(RECEIPT_FIELDS - receipt.keys()):
            raise InvalidInput(f"the receipt lacks {', '.join(missing)}")
        if receipt["space_id"] != str(space_id) or receipt["cell_id"] != cell_id:
            raise InvalidInput("the receipt names another space or cell")
        async with self._uow() as uow:
            space = await uow.directory.get_space(space_id, lock=True)
            if space is None or space.cell_id != cell_id:
                raise NotFound("unknown space")
            if space.deleted_at is None:
                raise Conflict("the space is not deleted")
            stored = (await uow.directory.deletion_receipts([space_id])).get(space_id)
            if stored is not None:
                if stored.receipt_hash != digest:
                    raise Conflict("another receipt is stored for the space")
                return stored
            record = DeletionReceipt(space_id, cell_id, receipt, digest, self._clock.now())
            await uow.directory.add_deletion_receipt(record)
            # With the receipt in, the space can leave the snapshot's `removed` list.
            await self.publish(uow, cell_id)
        return record

    # Sources and keys

    async def create_source(self, caller: Caller, space_id: UUID, spec: SourceSpec) -> Source:
        _require(caller, Role.ADMIN)
        async with self._uow() as uow:
            space = await _space_of(uow, caller, space_id)
            source = await self._add_source(uow, space, spec)
            await self.publish(uow, space.cell_id)
        return source

    async def list_sources(self, caller: Caller, space_id: UUID) -> list[Source]:
        async with self._uow() as uow:
            await _space_of(uow, caller, space_id)
            sources = await uow.access.sources_of(space_id)
        return [s for s in sources if caller.source_ids is None or s.source_id in caller.source_ids]

    async def issue_key(
        self, caller: Caller, source_id: UUID, scopes: frozenset[Scope] | None = None
    ) -> IssuedKey:
        _require(caller, Role.ADMIN)
        async with self._uow() as uow:
            space, source = await self._source_of(uow, caller, source_id)
            issued = await self._issue(uow, space, source, scopes)
            await self.publish(uow, space.cell_id)
        return issued

    async def list_keys(self, caller: Caller, source_id: UUID) -> list[ApiKey]:
        _require(caller, Role.ADMIN, Role.SECURITY)
        async with self._uow() as uow:
            await self._source_of(uow, caller, source_id)
            return await uow.access.keys_of_source(source_id)

    async def rotate_key(
        self, caller: Caller, source_id: UUID, key_id: str, grace_s: int | None = None
    ) -> IssuedKey:
        """A new key with the same scopes; the old one keeps working for the grace period, then expires."""
        _require(caller, Role.ADMIN)
        grace = self._settings.rotation_grace_s if grace_s is None else grace_s
        async with self._uow() as uow:
            space, source = await self._source_of(uow, caller, source_id)
            old = await _active_key(uow, source_id, key_id)
            issued = await self._issue(uow, space, source, old.scopes)
            now = self._clock.now()
            if grace > 0:
                await uow.access.expire_key(key_id, now + timedelta(seconds=grace))
            else:
                await uow.access.revoke_key(key_id, now)
                await uow.access.add_revocation(
                    space.space_id, RevocationKind.KEY, source_id, key_id, "rotated", now
                )
            await self.publish(uow, space.cell_id)
        return issued

    async def revoke_key(self, caller: Caller, source_id: UUID, key_id: str, reason: str) -> Revocation:
        _require(caller, Role.ADMIN, Role.SECURITY)
        async with self._uow() as uow:
            space, _ = await self._source_of(uow, caller, source_id)
            await _active_key(uow, source_id, key_id)
            now = self._clock.now()
            await uow.access.revoke_key(key_id, now)
            revocation = await uow.access.add_revocation(
                space.space_id, RevocationKind.KEY, source_id, key_id, reason, now
            )
            await self.publish(uow, space.cell_id)
        return revocation

    async def revoke_source(self, caller: Caller, source_id: UUID, reason: str) -> Revocation:
        """Cuts a vendor at once: every key of the source stops working (4.7, 17.1)."""
        _require(caller, Role.ADMIN, Role.SECURITY)
        async with self._uow() as uow:
            space, source = await self._source_of(uow, caller, source_id)
            if source.revoked_at is not None:
                raise Conflict("the source is already revoked")
            now = self._clock.now()
            await uow.access.revoke_source(source_id, now)
            # One key entry per cut key as well: the edge revokes keys, the source entry is the audit trail.
            for key in await uow.access.keys_of_source(source_id):
                if key.status is KeyStatus.ACTIVE:
                    await uow.access.revoke_key(key.key_id, now)
                    await uow.access.add_revocation(
                        space.space_id, RevocationKind.KEY, source_id, key.key_id, reason, now
                    )
            revocation = await uow.access.add_revocation(
                space.space_id, RevocationKind.SOURCE, source_id, None, reason, now
            )
            await self.publish(uow, space.cell_id)
        return revocation

    # People, roles and login

    async def create_user(
        self,
        caller: Caller,
        email: str,
        password: str | None,
        bindings: Sequence[BindingSpec],
        *,
        with_totp: bool = True,
    ) -> NewUser:
        """A person with a password chosen by the admin, or, without one, an invitation: a single-use link
        where the person chooses it and then sets up the authenticator at the first sign-in (5.22). A person
        whose domain belongs to the tenant's SSO gets no invitation: they sign in through the provider."""
        _require(caller, Role.ADMIN)
        if password is not None:
            self._check_password(password)
        async with self._uow() as uow:
            await self._check_bindings(uow, caller.tenant_id, bindings)
            if password is not None:
                created = await self._add_user(
                    uow, caller.tenant_id, email, password, bindings, with_totp=with_totp
                )
            else:
                created = await self._add_user(uow, caller.tenant_id, email, None, bindings, with_totp=False)
            if await _covered_by_sso(uow, created.user):
                created = replace(created, sso=True)
            elif password is None:
                created = replace(created, invitation=await self._invite(uow, caller, created.user))
            await self._record(
                uow,
                caller.tenant_id,
                AccessChangeKind.INVITED,
                created.user.user_id,
                caller.user_id,
                None,
                {"roles": _roles_detail(created.bindings), "password": password is not None},
            )
        return created

    async def list_users(self, caller: Caller) -> list[PersonView]:
        _require(caller, Role.ADMIN)
        async with self._uow() as uow:
            users = await uow.access.users_of(caller.tenant_id)
            ids = [u.user_id for u in users]
            codes = await uow.access.unused_recovery_codes(ids)
            invitations = await uow.access.open_invitations(ids, self._clock.now())
            connection = await uow.sso.connection_of(caller.tenant_id)
            domains = set(connection.domains) if connection and connection.enabled else set()
            return [
                PersonView(
                    u,
                    await uow.access.bindings_of(u.user_id),
                    codes.get(u.user_id, 0),
                    invitations.get(u.user_id),
                    email_domain(u.email) in domains,
                )
                for u in users
            ]

    async def person(self, caller: Caller, user_id: UUID) -> PersonView:
        _require(caller, Role.ADMIN)
        async with self._uow() as uow:
            user = await _tenant_person(uow, caller, user_id)
            codes = await uow.access.unused_recovery_codes([user_id])
            invitations = await uow.access.open_invitations([user_id], self._clock.now())
            return PersonView(
                user,
                await uow.access.bindings_of(user_id),
                codes.get(user_id, 0),
                invitations.get(user_id),
                await _covered_by_sso(uow, user),
            )

    async def set_roles(
        self, caller: Caller, user_id: UUID, bindings: Sequence[BindingSpec], reason: str | None = None
    ) -> list[RoleBinding]:
        _require(caller, Role.ADMIN)
        why = _reason(reason) if reason else None
        async with self._uow() as uow:
            user = await _tenant_person(uow, caller, user_id)
            await self._check_bindings(uow, caller.tenant_id, bindings)
            before = await uow.access.bindings_of(user_id)
            rows = [_binding(user, b) for b in bindings]
            await uow.access.set_bindings(user_id, rows)
            if await uow.access.count_admins(caller.tenant_id) == 0:
                raise Conflict("the tenant must keep at least one admin")
            await self._record(
                uow,
                caller.tenant_id,
                AccessChangeKind.ROLES_CHANGED,
                user_id,
                caller.user_id,
                why,
                {"before": _roles_detail(before), "after": _roles_detail(rows)},
            )
        return rows

    async def deactivate_user(self, caller: Caller, user_id: UUID, reason: str) -> User:
        """The person can no longer sign in or renew a session; the control plane refuses their token at
        once, and a cell accepts it until it expires (15 minutes at most). Open invitations end too."""
        _require(caller, Role.ADMIN)
        why = _reason(reason)
        if user_id == caller.user_id:
            raise Conflict("a person cannot deactivate themselves")
        async with self._uow() as uow:
            user = await _tenant_person(uow, caller, user_id)
            if not user.active:
                raise Conflict("the person is already deactivated")
            now = self._clock.now()
            await uow.access.set_active(user_id, False, now)
            if await uow.access.count_admins(caller.tenant_id) == 0:
                raise Conflict("the tenant must keep at least one admin")
            await uow.access.close_invitations(user_id, now)
            await self._record(
                uow, caller.tenant_id, AccessChangeKind.DEACTIVATED, user_id, caller.user_id, why
            )
        return replace(user, active=False, deactivated_at=now)

    async def reactivate_user(self, caller: Caller, user_id: UUID, reason: str) -> User:
        _require(caller, Role.ADMIN)
        why = _reason(reason)
        async with self._uow() as uow:
            user = await _tenant_person(uow, caller, user_id)
            if user.active:
                raise Conflict("the person is already active")
            await uow.access.set_active(user_id, True, None)
            await self._record(
                uow, caller.tenant_id, AccessChangeKind.REACTIVATED, user_id, caller.user_id, why
            )
        return replace(user, active=True, deactivated_at=None)

    async def reissue_invitation(self, caller: Caller, user_id: UUID) -> IssuedInvitation:
        """A new single-use link to choose a password, for a person who never used the first one or who
        forgot the password. Earlier links stop working."""
        _require(caller, Role.ADMIN)
        async with self._uow() as uow:
            user = await _tenant_person(uow, caller, user_id)
            if not user.active:
                raise Conflict("the person is deactivated")
            issued = await self._invite(uow, caller, user)
            await self._record(
                uow,
                caller.tenant_id,
                AccessChangeKind.INVITED,
                user_id,
                caller.user_id,
                None,
                {"again": True},
            )
        return issued

    async def preview_invitation(self, token: str) -> InvitationPreview:
        async with self._uow() as uow:
            invitation, user = await self._open_invitation(uow, token)
            tenant = await uow.directory.get_tenant(user.tenant_id)
            assert tenant is not None
        return InvitationPreview(user.email, tenant.name, invitation.expires_at)

    async def accept_invitation(self, token: str, password: str) -> str:
        """Sets the person's password and spends the link. The authenticator is set up at the first sign-in,
        as for everyone. Returns the e-mail, for the sign-in that follows."""
        self._check_password(password)
        async with self._uow() as uow:
            _, user = await self._open_invitation(uow, token)
            now = self._clock.now()
            await uow.access.set_password(user.user_id, await asyncio.to_thread(hash_password, password))
            await uow.access.close_invitations(user.user_id, now)
            await self._record(
                uow, user.tenant_id, AccessChangeKind.INVITATION_ACCEPTED, user.user_id, user.user_id, None
            )
        return user.email

    async def access_history(
        self, caller: Caller, user_id: UUID | None, limit: int = 50
    ) -> list[AccessChange]:
        _require(caller, Role.ADMIN)
        async with self._uow() as uow:
            if user_id is not None:
                await _tenant_person(uow, caller, user_id)
            return await uow.access.access_changes(caller.tenant_id, user_id, min(limit, 200))

    async def login(
        self,
        email: str,
        password: str,
        totp: str | None,
        space_id: UUID | None,
        *,
        recovery_code: str | None = None,
    ) -> IssuedToken:
        """A recovery code stands in for the TOTP code and is spent. The login that confirms an
        authenticator answers with the person's first recovery codes (5.22)."""
        async with self._uow() as uow:
            user = await uow.access.user_by_email(_normalize_email(email))
            ok = await asyncio.to_thread(
                check_password, password, user.password_hash if user else _DUMMY_HASH
            )
            if user is None or not ok or not user.active:
                raise Unauthenticated("invalid credentials")
            if await self._sso_only(uow, user):
                raise Unauthenticated("single sign-on required")
            tenant = await uow.directory.get_tenant(user.tenant_id)
            assert tenant is not None
            required = user.totp_secret is not None or tenant.mfa_required
            return await self.issue_session(uow, user, space_id, required, totp, recovery_code)

    async def issue_session(
        self,
        uow: ControlUnitOfWork,
        user: User,
        space_id: UUID | None,
        second_factor_required: bool,
        totp: str | None,
        recovery_code: str | None,
    ) -> IssuedToken:
        """The token of a person whose first factor passed (a password or the tenant's SSO), after the
        second factor when it is required. Shared by the password login and the SSO exchange."""
        if totp and recovery_code:
            raise InvalidInput("send the authenticator code or a recovery code, not both")
        now = self._clock.now()
        codes: list[str] | None = None
        left: int | None = None
        if second_factor_required:
            confirmed = user.totp_secret is not None and user.totp_confirmed_at is not None
            if recovery_code:
                left = await self._spend_recovery_code(uow, user, recovery_code, now)
            else:
                if not totp and not confirmed:
                    # The Console answers with the enrollment (POST /v1/auth/second-factor).
                    raise Unauthenticated("second factor enrollment required")
                if user.totp_secret is None or not await self._accept_totp(
                    uow, user.user_id, user.totp_secret, totp
                ):
                    raise Unauthenticated("invalid second factor")
                if not confirmed:
                    await uow.access.set_second_factor(user.user_id, user.totp_secret, now)
                    codes = await self._new_recovery_codes(uow, user.user_id, now)
        token = await self._token(uow, user, space_id)
        await uow.access.touch_login(user.user_id, now)
        return replace(token, recovery_codes=codes, recovery_codes_left=left)

    async def enrollment_secret(self, uow: ControlUnitOfWork, user: User) -> str:
        """The TOTP secret of a person who has not confirmed one yet, created when missing; 409 once one is
        confirmed. Shared by the password enrollment and the SSO one."""
        if user.totp_secret is not None and user.totp_confirmed_at is not None:
            raise Conflict("the second factor is already set up")
        secret = user.totp_secret or new_totp_secret()
        if user.totp_secret is None:
            await uow.access.set_second_factor(user.user_id, secret, None)
        return secret

    async def second_factor_setup(self, email: str, password: str) -> str:
        """The TOTP secret for a person who has not confirmed one yet (created when missing), after
        the password. The first valid code at login confirms it; after that this answers 409."""
        async with self._uow() as uow:
            user = await uow.access.user_by_email(_normalize_email(email))
            ok = await asyncio.to_thread(
                check_password, password, user.password_hash if user else _DUMMY_HASH
            )
            if user is None or not ok or not user.active:
                raise Unauthenticated("invalid credentials")
            return await self.enrollment_secret(uow, user)

    async def second_factor_status(self, caller: Caller) -> SecondFactorStatus:
        async with self._uow() as uow:
            user = await _person(uow, caller)
            codes = await uow.access.recovery_codes_of(user.user_id)
        return SecondFactorStatus(user.totp_confirmed_at, sum(1 for c in codes if c.used_at is None))

    async def regenerate_recovery_codes(
        self, caller: Caller, password: str | None, totp: str | None = None
    ) -> list[str]:
        """Ten new codes, after the password again (the current authenticator code for a person who signs in
        through SSO and has no password); the earlier ones stop working."""
        async with self._uow() as uow:
            user = await self._person_with_password(uow, caller, password, totp)
            if user.totp_secret is None or user.totp_confirmed_at is None:
                raise Conflict("the second factor is not set up")
            return await self._new_recovery_codes(uow, user.user_id, self._clock.now())

    async def start_second_factor_change(
        self, caller: Caller, password: str | None, totp: str | None = None
    ) -> NewAuthenticator:
        """A new TOTP secret, after the password again, kept pending beside the current one, which keeps
        working until `confirm_second_factor_change` takes a first code of the new one. Starting over
        replaces a pending secret that was never confirmed."""
        async with self._uow() as uow:
            user = await self._person_with_password(uow, caller, password, totp)
            secret = new_totp_secret()
            await uow.access.set_pending_second_factor(user.user_id, secret)
        return NewAuthenticator(secret, user.email)

    async def confirm_second_factor_change(self, caller: Caller, totp: str) -> list[str]:
        """Switches to the pending secret on its first valid code, with ten new recovery codes."""
        async with self._uow() as uow:
            user = await _person(uow, caller)
            if user.totp_pending_secret is None:
                raise Conflict("no authenticator change was started")
            now = self._clock.now()
            # 403 and not 401: the session is valid, and the Console ends the session on a 401.
            if not await self._accept_totp(uow, user.user_id, user.totp_pending_secret, totp):
                raise Forbidden("invalid second factor")
            await uow.access.set_second_factor(user.user_id, user.totp_pending_secret, now)
            return await self._new_recovery_codes(uow, user.user_id, now)

    async def reset_second_factor(self, caller: Caller, user_id: UUID, reason: str) -> SecondFactorReset:
        """For a person who lost the authenticator and the recovery codes: the secret, its confirmation
        and every code go, so a tenant that requires the second factor asks for a new enrollment at the
        next login. The reset is kept with who did it and why."""
        _require(caller, Role.ADMIN)
        why = _reason(reason)
        async with self._uow() as uow:
            user = await uow.access.get_user(user_id)
            if user is None or user.tenant_id != caller.tenant_id:
                raise NotFound("unknown user")
            reset = SecondFactorReset(
                uuid7(), caller.tenant_id, user_id, caller.user_id, why, self._clock.now()
            )
            await uow.access.set_second_factor(user_id, None, None)
            await uow.access.replace_recovery_codes(user_id, [])
            await uow.access.add_second_factor_reset(reset)
            await self._record(
                uow, caller.tenant_id, AccessChangeKind.SECOND_FACTOR_RESET, user_id, caller.user_id, why
            )
        return reset

    async def preferences(self, caller: Caller) -> dict[str, Any]:
        async with self._uow() as uow:
            return await uow.access.preferences_of((await _person(uow, caller)).user_id)

    async def set_preference(self, caller: Caller, key: str, value: Any) -> dict[str, Any]:
        """One key of the person's own Console preferences, replaced whole; the others stay."""
        async with self._uow() as uow:
            user = await _person(uow, caller)
            kept = await uow.access.preferences_of(user.user_id)
            check_preference(key, value, frozenset(kept))
            await uow.access.set_preference(user.user_id, key, value)
        return {**kept, key: value}

    async def me(self, caller: Caller) -> Me:
        async with self._uow() as uow:
            user = await _person(uow, caller)
            bindings = await uow.access.bindings_of(user.user_id)
            spaces = await uow.directory.spaces_of_tenant(user.tenant_id)
            sso = await _covered_by_sso(uow, user)
        return Me(
            user,
            bindings,
            [s for s in spaces if any(b.space_id in (None, s.space_id) for b in bindings)],
            sso,
        )

    @staticmethod
    def config_types() -> dict[str, DocType]:
        """The document types a diff may change, the same table `config_document` validates against."""
        return dict(DOC_TYPES)

    async def reissue(self, caller: Caller, space_id: UUID | None = None) -> IssuedToken:
        """Silent renewal and space switch (5.22): roles are read again, so a removed role disappears."""
        async with self._uow() as uow:
            user = await _person(uow, caller)
            return await self._token(uow, user, space_id or caller.space_id)

    def authenticate(self, token: str) -> Caller:
        claims = self._signer.decode_jwt(token, self._settings.audience, self._settings.issuer)
        if not isinstance(claims.get("exp"), int) or claims["exp"] <= self._clock.now().timestamp():
            raise Unauthenticated("expired token")
        try:
            return Caller(
                user_id=UUID(claims["sub"]),
                tenant_id=UUID(claims["tenant"]),
                space_id=UUID(claims["space"]),
                roles=frozenset(Role(r) for r in claims["roles"]),
                source_ids=None
                if claims.get("sources") is None
                else frozenset(UUID(s) for s in claims["sources"]),
            )
        except (KeyError, ValueError, TypeError):
            raise Unauthenticated("malformed token") from None

    async def authenticate_active(self, token: str) -> Caller:
        """`authenticate`, plus the person must still be active: a deactivation cuts the control plane at
        once, without waiting for the token to expire."""
        caller = self.authenticate(token)
        async with self._uow() as uow:
            user = await uow.access.get_user(caller.user_id)
        if user is None or not user.active or user.tenant_id != caller.tenant_id:
            raise Unauthenticated("the person is no longer active")
        return caller

    async def jwks(self) -> dict[str, Any]:
        async with self._uow() as uow:
            stored = await uow.access.signing_keys()
        keys = {k["kid"]: k for k in self._signer.public_jwks()}
        for key in stored:
            keys.setdefault(key.kid, key.public_jwk)
        return {"keys": list(keys.values())}

    # Versioned configuration

    async def config_document(self, caller: Caller, space_id: UUID, name: str) -> tuple[int, Any]:
        doc_type(name)
        async with self._uow() as uow:
            space = await _space_of(uow, caller, space_id)
            latest = await uow.config.latest_documents(space_id)
        if name in latest:
            return latest[name].version, latest[name].document
        return 0, current_document(build_space_config(space, [], {}), name)

    async def submit_diff(
        self, caller: Caller, space_id: UUID, name: str, document: Any, reason: str
    ) -> ConfigDiff:
        _require(caller, *doc_type(name).roles)
        normalized = validate_document(name, document)
        async with self._uow() as uow:
            space = await _space_of(uow, caller, space_id)
            await self._assemble(uow, space, {name: normalized})
            _require_each(caller, await _escalation(uow, space, name, normalized))
            diff = ConfigDiff(
                diff_id=uuid7(),
                space_id=space_id,
                doc_type=name,
                document=normalized,
                reason=_reason(reason),
                status=DiffStatus.PENDING,
                base_version=await uow.config.current_version(space_id),
                created_at=self._clock.now(),
                author_id=caller.user_id,
            )
            await uow.config.add_diff(diff)
        return diff

    async def propose_diff(
        self, cell_id: str, space_id: UUID, name: str, document: Any, reason: str
    ) -> ConfigDiff:
        """A diff from the configuration assistant of a cell (22.4); it waits for a person to approve it."""
        normalized = validate_document(name, document)
        async with self._uow() as uow:
            space = await uow.directory.get_space(space_id)
            if space is None or space.cell_id != cell_id:
                raise Forbidden("the space is not served by this cell")
            diff = ConfigDiff(
                diff_id=uuid7(),
                space_id=space_id,
                doc_type=name,
                document=normalized,
                reason=_reason(reason),
                status=DiffStatus.PROPOSED,
                base_version=await uow.config.current_version(space_id),
                created_at=self._clock.now(),
                author_label=f"assistant@{cell_id}",
            )
            await uow.config.add_diff(diff)
        return diff

    async def record_observed(
        self, cell_id: str, space_id: UUID, name: str, document: Any, reason: str
    ) -> ConfigDiff:
        """What a cell inferred of one document from the space's traffic: recorded as applied
        by `OBSERVED_AUTHOR` so the Console tells it from a declaration, with no version of its own. It
        changes no declared document: the cell merges what it inferred under the declaration it serves, and
        a person's declaration always wins. Nothing can approve, reject or roll it back."""
        if name not in OBSERVABLE:
            raise InvalidInput(f"{name!r} is not a document the cell infers")
        if len(orjson.dumps(document)) > OBSERVED_MAX_BYTES:
            raise InvalidInput("the observed document is too large")
        async with self._uow() as uow:
            space = await uow.directory.get_space(space_id)
            if space is None or space.cell_id != cell_id:
                raise Forbidden("the space is not served by this cell")
            now = self._clock.now()
            diff = ConfigDiff(
                diff_id=uuid7(),
                space_id=space_id,
                doc_type=name,
                document=document,
                reason=_reason(reason),
                status=DiffStatus.APPLIED,
                base_version=await uow.config.current_version(space_id),
                created_at=now,
                author_label=OBSERVED_AUTHOR,
                decided_at=now,
            )
            await uow.config.add_diff(diff)
        return diff

    async def list_diffs(
        self, caller: Caller, space_id: UUID, status: DiffStatus | None = None
    ) -> list[ConfigDiff]:
        async with self._uow() as uow:
            await _space_of(uow, caller, space_id)
            return await uow.config.diffs_of(space_id, status)

    async def diff_people(self, diffs: Sequence[ConfigDiff]) -> dict[UUID, str]:
        """The e-mail of each person who proposed or decided these diffs. Whoever reads a diff reads who
        stands behind it: the four eyes mean nothing to a role that sees only an id."""
        wanted = sorted({p for d in diffs for p in (d.author_id, d.decided_by) if p is not None}, key=str)
        if not wanted:
            return {}
        async with self._uow() as uow:
            users = [await uow.access.get_user(user_id) for user_id in wanted]
        return {u.user_id: u.email for u in users if u is not None}

    async def approve_diff(self, caller: Caller, diff_id: UUID) -> ConfigDiff:
        async with self._uow() as uow:
            diff = await self._diff_of(uow, caller, diff_id)
            _require(caller, *doc_type(diff.doc_type).roles)
            space = await uow.directory.get_space(diff.space_id, lock=True)
            assert space is not None
            escalated = await _escalation(uow, space, diff.doc_type, diff.document)
            _require_each(caller, escalated)
            roles = frozenset({Role.ADMIN, *doc_type(diff.doc_type).roles})
            others = await _other_approvers(uow, caller.tenant_id, space, roles, diff.author_id, escalated)
            check_approval(diff, caller.user_id, others)
            check_not_stale(await uow.config.changed_since(space.space_id, diff.doc_type, diff.base_version))
            return await self._apply(uow, space, diff, caller.user_id)

    async def reject_diff(self, caller: Caller, diff_id: UUID) -> ConfigDiff:
        async with self._uow() as uow:
            diff = await self._diff_of(uow, caller, diff_id)
            _require(caller, *doc_type(diff.doc_type).roles)
            if diff.status not in (DiffStatus.PENDING, DiffStatus.PROPOSED):
                raise Conflict("the diff was already decided")
            rejected = replace(
                diff, status=DiffStatus.REJECTED, decided_by=caller.user_id, decided_at=self._clock.now()
            )
            await uow.config.update_diff(rejected)
        return rejected

    async def rollback_diff(self, caller: Caller, diff_id: UUID, reason: str) -> ConfigDiff:
        """Proposes the document as it was before the diff (4.7), as a diff of its own: it passes through
        the same four eyes as any change and waits pending while another person can approve it;
        with nobody else, or in a sandbox, it applies at once. It refuses when a later diff changed the same
        document."""
        async with self._uow() as uow:
            diff = await self._diff_of(uow, caller, diff_id)
            _require(caller, *doc_type(diff.doc_type).roles)
            if diff.status is not DiffStatus.APPLIED or diff.applied_version is None:
                raise Conflict("only an applied diff can be rolled back")
            space = await uow.directory.get_space(diff.space_id, lock=True)
            assert space is not None
            if await uow.config.changed_since(space.space_id, diff.doc_type, diff.applied_version):
                raise Conflict("a later diff changed the same document; roll that one back first")
            previous = await uow.config.version_before(space.space_id, diff.doc_type, diff.applied_version)
            document = (
                previous.document
                if previous
                else current_document(build_space_config(space, [], {}), diff.doc_type)
            )
            escalated = await _escalation(uow, space, diff.doc_type, document)
            _require_each(caller, escalated)
            rollback = ConfigDiff(
                diff_id=uuid7(),
                space_id=space.space_id,
                doc_type=diff.doc_type,
                document=document,
                reason=_reason(reason),
                status=DiffStatus.PENDING,
                base_version=await uow.config.current_version(space.space_id),
                created_at=self._clock.now(),
                author_id=caller.user_id,
                rollback_of=diff.diff_id,
            )
            await uow.config.add_diff(rollback)
            roles = frozenset({Role.ADMIN, *doc_type(diff.doc_type).roles})
            others = await _other_approvers(uow, caller.tenant_id, space, roles, caller.user_id, escalated)
            if others:
                return rollback
            return await self._apply(uow, space, rollback, caller.user_id)

    async def history(
        self, caller: Caller, space_id: UUID, name: str | None, limit: int = 50
    ) -> list[ConfigVersion]:
        if name is not None:
            doc_type(name)
        async with self._uow() as uow:
            await _space_of(uow, caller, space_id)
            return await uow.config.history(space_id, name, min(limit, 200))

    async def put_secret_ref(
        self, caller: Caller, space_id: UUID, ref: str, kind: str, version: int
    ) -> SecretRef:
        """Only metadata: the secret itself was written by the Console straight into the cell vault (4.7)."""
        _require(caller, Role.ADMIN, Role.INTEGRATION, Role.SECURITY)
        async with self._uow() as uow:
            await _space_of(uow, caller, space_id)
            secret = SecretRef(space_id, ref, kind, version, caller.user_id, self._clock.now())
            await uow.config.put_secret_ref(secret)
        return secret

    async def secret_refs(self, caller: Caller, space_id: UUID) -> list[SecretRef]:
        async with self._uow() as uow:
            await _space_of(uow, caller, space_id)
            return await uow.config.secret_refs(space_id)

    # Distribution to cells

    async def snapshot(self, cell_id: str) -> Snapshot:
        async with self._uow() as uow:
            if await uow.directory.get_cell(cell_id) is None:
                raise NotFound("unknown cell")
            snapshot = await uow.config.latest_snapshot(cell_id)
            if snapshot is None:
                snapshot = await self.publish(uow, cell_id)
        return snapshot

    async def wait_snapshot(self, cell_id: str, after: int, wait_s: float) -> Snapshot | None:
        """Long poll: returns as soon as a version newer than `after` exists, or None at the deadline."""
        polls = max(1, int(wait_s / self._settings.poll_interval_s) + 1)
        for attempt in range(polls):
            snapshot = await self.snapshot(cell_id)
            if snapshot.version > after:
                return snapshot
            if attempt + 1 < polls:
                await asyncio.sleep(self._settings.poll_interval_s)
        return None

    async def revocations(self, cell_id: str, after: int) -> SignedFeed:
        async with self._uow() as uow:
            if await uow.directory.get_cell(cell_id) is None:
                raise NotFound("unknown cell")
            # Deleted spaces too: their keys' revocations must still reach the cell.
            spaces = [s.space_id for s in await uow.directory.spaces_in_cell(cell_id, include_deleted=True)]
            items = await uow.access.revocations_after(after, spaces, self._settings.revocation_page)
        body = {
            "cell_id": cell_id,
            "seq": items[-1].seq if items else after,
            "keys": [
                {"space_id": str(r.space_id), "key_id": r.key_id}
                for r in items
                if r.kind is RevocationKind.KEY
            ],
            "sources": [str(r.source_id) for r in items if r.kind is RevocationKind.SOURCE],
        }
        return SignedFeed(cell_id, body, self._signer.kid, self._signer.sign(canonical_json(body)))

    async def access_feed(self, cell_id: str, after: datetime | None) -> SignedFeed:
        """Changes to people's access and to SSO of the tenants this cell serves, signed like the revocation
        feed: the cell writes each into the receipt chain of the tenant's spaces. Ids only; who
        asked and why stay here. Without `after`, the feed starts now."""
        async with self._uow() as uow:
            if await uow.directory.get_cell(cell_id) is None:
                raise NotFound("unknown cell")
            tenants = sorted({s.tenant_id for s in await uow.directory.spaces_in_cell(cell_id)})
            items = (
                []
                if after is None
                else await uow.access.access_changes_since(tenants, after, self._settings.revocation_page)
            )
        until = items[-1].at if items else (after or self._clock.now())
        body = {
            "cell_id": cell_id,
            "after": until.isoformat(),
            "changes": [
                {
                    "change_id": str(c.change_id),
                    "tenant_id": str(c.tenant_id),
                    "kind": c.kind.value,
                    "at": c.at.isoformat(),
                    "user_id": str(c.user_id) if c.user_id else None,
                    "actor_id": str(c.actor_id) if c.actor_id else None,
                }
                for c in items
            ],
        }
        return SignedFeed(cell_id, body, self._signer.kid, self._signer.sign(canonical_json(body)))

    async def push_usage(self, cell_id: str, aggregates: Sequence[UsageAggregate]) -> int:
        async with self._uow() as uow:
            served = {s.space_id for s in await uow.directory.spaces_in_cell(cell_id, include_deleted=True)}
            if any(a.space_id not in served for a in aggregates):
                raise Forbidden("usage for a space this cell does not serve")
            now = self._clock.now()
            await uow.usage.upsert([replace(a, cell_id=cell_id, reported_at=now) for a in aggregates])
        return len(aggregates)

    async def usage(self, caller: Caller, since: datetime, until: datetime) -> UsageReport:
        _require(caller, Role.ANALYSIS, Role.SECURITY)
        async with self._uow() as uow:
            covers = await _role_covers(uow, caller, Role.ANALYSIS, Role.SECURITY)
            spaces = [s.space_id for s in await uow.directory.spaces_of_tenant(caller.tenant_id) if covers(s)]
            return UsageReport(
                await uow.usage.usage(spaces, since, until), await uow.directory.quotas_of(caller.tenant_id)
            )

    async def usage_statement(self, caller: Caller | None, month: str) -> list[StatementLine]:
        """One UTC month of usage per space, to invoice from: the spaces of the caller's tenant
        that the caller's role covers, or every tenant for the operator's command line (no caller).
        Deleted spaces are listed for the months they existed in."""
        if caller is not None:
            _require(caller, Role.ANALYSIS, Role.SECURITY)
        start, end = month_range(month)
        async with self._uow() as uow:
            covers = await _role_covers(uow, caller, Role.ANALYSIS, Role.SECURITY) if caller else None
            spaces = [
                s
                for cell in await uow.directory.list_cells()
                for s in await uow.directory.spaces_in_cell(cell.cell_id, include_deleted=True)
                if (covers is None or covers(s))
                and s.created_at < end
                and (s.deleted_at is None or s.deleted_at >= start)
            ]
            aggregates = await uow.usage.usage([s.space_id for s in spaces], start, end)
        return statement(month, spaces, aggregates)

    # Internals

    async def _add_project(
        self,
        uow: ControlUnitOfWork,
        tenant_id: UUID,
        name: str,
        slug: str,
        region: str,
        *,
        cell: Cell | None = None,
        instance: str = PRIMARY_INSTANCE,
    ) -> ProjectView:
        """Without `cell`, each space goes where `choose_cell` says: a cell dedicated to the tenant when it
        has one in the region, the shared cells otherwise."""
        now = self._clock.now()
        project = Project(uuid7(), tenant_id, name, slug, region, now)
        await uow.directory.add_project(project)
        cells = await uow.directory.list_cells()
        placed = await uow.directory.placed_counts()
        spaces = []
        for environment in (Environment.SANDBOX, Environment.PRODUCTION):
            target = cell or choose_cell(cells, placed, region, tenant_id)
            if placed.get(target.cell_id, 0) >= target.capacity:
                raise Unavailable("the cell has no free capacity")
            placed[target.cell_id] = placed.get(target.cell_id, 0) + 1
            space = Space(
                uuid7(),
                tenant_id,
                project.project_id,
                environment,
                space_slug(slug, environment),
                region,
                target.cell_id,
                now,
                tier=tier_for(target, instance),
                instance=instance,
            )
            await uow.directory.add_space(space)
            spaces.append(space)
        return ProjectView(project, spaces)

    async def _add_source(self, uow: ControlUnitOfWork, space: Space, spec: SourceSpec) -> Source:
        check_ceiling(spec.audience, spec.verification_ceiling)
        source = Source(
            source_id=uuid7(),
            space_id=space.space_id,
            name=spec.name,
            audience=spec.audience,
            created_at=self._clock.now(),
            vendor=spec.vendor,
            channel=spec.channel,
            purposes=spec.purposes,
            verification_ceiling=spec.verification_ceiling,
            confirms_before_acting=spec.confirms_before_acting,
            navigation_enabled=spec.navigation_enabled,
            trusted_action_ops=spec.trusted_action_ops,
            critical=spec.critical,
        )
        await uow.access.add_source(source)
        return source

    async def _issue(
        self, uow: ControlUnitOfWork, space: Space, source: Source, scopes: frozenset[Scope] | None
    ) -> IssuedKey:
        if source.revoked_at is not None:
            raise Conflict("the source is revoked")
        granted = scopes or DEFAULT_SCOPES.get(source.audience, frozenset({Scope.TRACK, Scope.CONTEXT}))
        if Scope.ANALYTICS in granted and source.audience is not Audience.ANALYST:
            raise Forbidden("the analytics scope is only for analyst sources (5.21)")
        if Scope.IDENTITY_LINK in granted and source.audience is Audience.CUSTOMER_AGENT:
            # A link opens an organization's memory to a person: a customer's agent never makes one.
            raise Forbidden("the identity:link scope is not for customer-facing sources")
        key_id, secret = new_key_id(), new_secret()
        plaintext = format_key(space.environment, space.region, space.slug, key_id, secret)
        key = ApiKey(
            key_id=key_id,
            space_id=space.space_id,
            source_id=source.source_id,
            scopes=granted,
            fast_hash=fast_hash(self._pepper, key_id, secret),
            slow_hash=await asyncio.to_thread(key_slow_hash, key_id, secret),
            status=KeyStatus.ACTIVE,
            version=1,
            created_at=self._clock.now(),
        )
        await uow.access.add_key(key)
        return IssuedKey(key, plaintext)

    async def _add_user(
        self,
        uow: ControlUnitOfWork,
        tenant_id: UUID,
        email: str,
        password: str | None,
        bindings: Sequence[BindingSpec],
        *,
        with_totp: bool,
    ) -> NewUser:
        totp = new_totp_secret() if with_totp else None
        user = User(
            user_id=uuid7(),
            tenant_id=tenant_id,
            email=_normalize_email(email),
            password_hash=NO_PASSWORD
            if password is None
            else await asyncio.to_thread(hash_password, password),
            created_at=self._clock.now(),
            totp_secret=totp,
        )
        await uow.access.add_user(user)
        rows = [_binding(user, b) for b in bindings]
        await uow.access.set_bindings(user.user_id, rows)
        return NewUser(user, rows, totp)

    async def _invite(self, uow: ControlUnitOfWork, caller: Caller, user: User) -> IssuedInvitation:
        now = self._clock.now()
        await uow.access.close_invitations(user.user_id, now)
        token = new_url_token()
        invitation = Invitation(
            uuid7(),
            user.tenant_id,
            user.user_id,
            invitation_token_hash(self._pepper, token),
            caller.user_id,
            now,
            now + timedelta(seconds=self._settings.invitation_ttl_s),
        )
        await uow.access.add_invitation(invitation)
        return IssuedInvitation(invitation, token)

    async def _open_invitation(self, uow: ControlUnitOfWork, token: str) -> tuple[Invitation, User]:
        """The invitation and its person, or 401 alike for an unknown, used or expired link."""
        invitation = await uow.access.invitation_by_token(invitation_token_hash(self._pepper, token))
        user = await uow.access.get_user(invitation.user_id) if invitation else None
        if (
            invitation is None
            or user is None
            or invitation.used_at is not None
            or invitation.expires_at <= self._clock.now()
            or not user.active
        ):
            raise Unauthenticated("the invitation is invalid or expired")
        return invitation, user

    async def _record(
        self,
        uow: ControlUnitOfWork,
        tenant_id: UUID,
        kind: AccessChangeKind,
        user_id: UUID | None,
        actor_id: UUID | None,
        reason: str | None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        await uow.access.add_access_change(
            AccessChange(uuid7(), tenant_id, kind, self._clock.now(), user_id, actor_id, reason, detail or {})
        )

    async def _sso_only(self, uow: ControlUnitOfWork, user: User) -> bool:
        """A password sign-in the tenant's enforced SSO refuses: everyone of its domains but the tenant-wide
        admins, who keep the password as the way in when the identity provider fails."""
        connection = await uow.sso.connection_for_domain(email_domain(user.email))
        if connection is None or connection.tenant_id != user.tenant_id:
            return False
        if not (connection.enabled and connection.enforced):
            return False
        bindings = await uow.access.bindings_of(user.user_id)
        return not any(b.role is Role.ADMIN and b.space_id is None for b in bindings)

    async def _new_recovery_codes(self, uow: ControlUnitOfWork, user_id: UUID, now: datetime) -> list[str]:
        """Ten codes in place of every earlier one. Only their hashes are stored."""
        codes = new_recovery_codes()
        await uow.access.replace_recovery_codes(
            user_id, [RecoveryCode(user_id, recovery_code_hash(self._pepper, user_id, c), now) for c in codes]
        )
        return codes

    async def _spend_recovery_code(
        self, uow: ControlUnitOfWork, user: User, typed: str, now: datetime
    ) -> int:
        """Marks the code used and returns how many unused ones remain; a used or unknown code is refused."""
        unused = [c.code_hash for c in await uow.access.recovery_codes_of(user.user_id) if c.used_at is None]
        code = normalize_recovery_code(typed)
        found = matching_hash(recovery_code_hash(self._pepper, user.user_id, code), unused) if code else None
        if found is None or not await uow.access.use_recovery_code(user.user_id, found, now):
            raise Unauthenticated("invalid second factor")
        return len(unused) - 1

    async def _person_with_password(
        self, uow: ControlUnitOfWork, caller: Caller, password: str | None, totp: str | None = None
    ) -> User:
        """The signed-in person, after the password once more: a stolen session alone must not change the
        second factor. A person the tenant's SSO created has no password and shows the current authenticator
        code instead. A wrong answer is 403, since the Console ends the session on a 401."""
        user = await _person(uow, caller)
        if has_password(user.password_hash):
            if password is None or not await asyncio.to_thread(check_password, password, user.password_hash):
                raise Forbidden("invalid password")
            return user
        confirmed = user.totp_secret is not None and user.totp_confirmed_at is not None
        if not confirmed:
            # Nothing to show beyond the session: the provider's own MFA is the second factor.
            raise Conflict("a person without a password manages the second factor after setting one up")
        assert user.totp_secret is not None
        if not await self._accept_totp(uow, user.user_id, user.totp_secret, totp):
            raise Forbidden("invalid second factor")
        return user

    async def _accept_totp(
        self, uow: ControlUnitOfWork, user_id: UUID, secret: str, code: str | None
    ) -> bool:
        """A valid authenticator code that was not used before: its 30-second step must come after the
        last one accepted for the person, and becomes the last one. A code read over a shoulder or kept by
        a proxy never signs in again, not even within its own minute (RFC 6238, 5.2)."""
        if not code:
            return False
        step = totp_step(secret, code, self._clock.now().timestamp())
        return step is not None and await uow.access.use_totp_step(user_id, step)

    async def _check_bindings(
        self, uow: ControlUnitOfWork, tenant_id: UUID, bindings: Sequence[BindingSpec]
    ) -> None:
        if not bindings:
            raise InvalidInput("a person needs at least one role")
        spaces = {s.space_id for s in await uow.directory.spaces_of_tenant(tenant_id)}
        for b in bindings:
            if b.space_id is not None and b.space_id not in spaces:
                raise NotFound("unknown space")
            if (b.role is Role.VENDOR) != bool(b.source_ids):
                raise InvalidInput("the vendor role, and only it, names its sources")

    async def _token(self, uow: ControlUnitOfWork, user: User, space_id: UUID | None) -> IssuedToken:
        spaces = await uow.directory.spaces_of_tenant(user.tenant_id)
        now = self._clock.now()
        bindings = await uow.access.bindings_of(user.user_id)
        space = _pick_space(spaces, bindings, space_id)
        applicable = [b for b in bindings if b.space_id in (None, space.space_id)]
        roles = sorted({b.role.value for b in applicable})
        vendor_only = all(b.role is Role.VENDOR for b in applicable)
        sources = sorted({str(s) for b in applicable for s in b.source_ids}) if vendor_only else None
        ttl = self._settings.token_ttl_s
        claims = {
            "iss": self._settings.issuer,
            "aud": self._settings.audience,
            "sub": str(user.user_id),
            # The cell holds no directory of people: it names a person on their receipts from this.
            "email": user.email,
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + ttl,
            "jti": uuid7().hex,
            "tenant": str(user.tenant_id),
            "space": str(space.space_id),
            "region": space.region,
            "env": space.environment.value,
            "roles": roles,
            "sources": sources,
        }
        return IssuedToken(self._signer.encode_jwt(claims), ttl, space.space_id)

    async def _source_of(
        self, uow: ControlUnitOfWork, caller: Caller, source_id: UUID
    ) -> tuple[Space, Source]:
        source = await uow.access.get_source(source_id)
        if source is None:
            raise NotFound("unknown source")
        return await _space_of(uow, caller, source.space_id), source

    async def _diff_of(self, uow: ControlUnitOfWork, caller: Caller, diff_id: UUID) -> ConfigDiff:
        diff = await uow.config.get_diff(diff_id)
        if diff is None:
            raise NotFound("unknown diff")
        await _space_of(uow, caller, diff.space_id)
        return diff

    async def _assemble(self, uow: ControlUnitOfWork, space: Space, overrides: dict[str, Any]) -> SpaceConfig:
        latest = await uow.config.latest_documents(space.space_id)
        current = {name: v.document for name, v in latest.items()}
        sources = await uow.access.sources_of(space.space_id)
        revoked = {s.source_id for s in sources if s.revoked_at is not None}
        for name, document in overrides.items():
            if added := revoked_owners(name, document, revoked) - revoked_owners(
                name, current.get(name), revoked
            ):
                raise InvalidInput(f"{name}: {', '.join(sorted(added))} belongs to a revoked source")
        config = build_space_config(space, sources, current | overrides)
        if overrides:
            # A diff is checked when it is sent and again when it is approved, against the documents of now.
            try:
                before: SpaceConfig | None = build_space_config(space, sources, current)
            except InvalidInput:
                before = None
            for changed in overrides:
                refuse_new_problems(changed, before, config)
            refuse_cell_problems(before, config)
        return config

    async def _apply(
        self, uow: ControlUnitOfWork, space: Space, diff: ConfigDiff, approver: UUID
    ) -> ConfigDiff:
        await self._assemble(uow, space, {diff.doc_type: diff.document})
        now = self._clock.now()
        version = await uow.config.current_version(space.space_id) + 1
        await uow.config.add_version(
            ConfigVersion(space.space_id, version, diff.doc_type, diff.document, diff.diff_id, now)
        )
        applied = replace(
            diff, status=DiffStatus.APPLIED, decided_by=approver, decided_at=now, applied_version=version
        )
        await uow.config.update_diff(applied)
        await self.publish(uow, space.cell_id)
        return applied

    async def publish(self, uow: ControlUnitOfWork, cell_id: str) -> Snapshot:
        """Builds the cell's manifest and key list; stores a new signed version only when either changed."""
        await uow.directory.get_cell(cell_id, lock=True)
        now = self._clock.now()
        spaces = await uow.directory.spaces_in_cell(cell_id)
        documents: dict[str, Any] = {}
        manifest_spaces: dict[str, str] = {}
        limits: dict[UUID, SpaceLimits | None] = {}
        for space in spaces:
            if space.tenant_id not in limits:
                limits[space.tenant_id] = contract_limits(await uow.directory.quotas_of(space.tenant_id))
            assembled = (await self._assemble(uow, space, {})).model_copy(
                update={"limits": limits[space.tenant_id]}
            )
            # A field at its default stays out of the stored document, so a server one version behind, whose
            # models forbid what they do not know, still reads what this one writes: a new optional field
            # reaches the cells only once a diff sets it, after the whole fleet runs the version that has it.
            config = assembled.model_dump(mode="json", exclude_defaults=True)
            digest = sha256_hex(canonical_json(config))
            documents[digest] = config
            manifest_spaces[str(space.space_id)] = digest
        keys: dict[str, Any] = {str(s.space_id): [] for s in spaces}
        for key in await uow.access.keys_of_spaces([s.space_id for s in spaces]):
            keys[str(key.space_id)].append(_key_entry(key, now))
        removed = await self._removed(uow, cell_id)
        moved = {
            str(space_id): to_cell
            for space_id, to_cell in (await uow.directory.moved_from(cell_id, now - MOVED_NOTICE)).items()
        }
        latest = await uow.config.latest_snapshot(cell_id)
        if (
            latest
            and latest.manifest["spaces"] == manifest_spaces
            and latest.manifest["removed"] == removed
            and latest.manifest["moved"] == moved
            and latest.keys == keys
        ):
            return latest
        manifest = SnapshotManifest(
            cell_id=cell_id,
            version=(latest.version if latest else 0) + 1,
            created_at=now,
            spaces=manifest_spaces,
            removed=removed,
            moved=moved,
        ).model_dump(mode="json")
        snapshot = Snapshot(
            cell_id=cell_id,
            version=manifest["version"],
            manifest=manifest,
            signature=self._signer.sign(canonical_json(manifest)),
            kid=self._signer.kid,
            documents=documents,
            keys=keys,
            keys_signature=self._signer.sign(canonical_json(keys)),
            created_at=now,
        )
        await uow.config.add_snapshot(snapshot)
        return snapshot

    @staticmethod
    async def _removed(uow: ControlUnitOfWork, cell_id: str) -> dict[str, str]:
        """Deleted spaces the cell has not confirmed yet; a receipt takes a space off the list."""
        deleted = [
            s for s in await uow.directory.spaces_in_cell(cell_id, include_deleted=True) if s.deleted_at
        ]
        confirmed = await uow.directory.deletion_receipts([s.space_id for s in deleted])
        return {
            str(s.space_id): s.deleted_at.astimezone(UTC).isoformat()
            for s in deleted
            if s.deleted_at is not None and s.space_id not in confirmed
        }

    def _check_password(self, password: str) -> None:
        if len(password) < self._settings.min_password_length:
            raise InvalidInput(f"the password needs at least {self._settings.min_password_length} characters")


async def _other_approvers(
    uow: ControlUnitOfWork,
    tenant_id: UUID,
    space: Space,
    roles: frozenset[Role],
    author_id: UUID | None,
    escalated: frozenset[Role] = frozenset(),
) -> int:
    """Active people besides the author who may approve a document of the space: a binding with one of
    the roles, tenant-wide or on that space, and one with each escalated role unless they are admins. None
    for a sandbox: its author approves their own changes, so tests never wait for a second person."""
    if space.environment is Environment.SANDBOX:
        return 0
    space_id = space.space_id
    found = 0
    for user in await uow.access.users_of(tenant_id):
        if user.user_id == author_id or not user.active:
            continue
        held = {b.role for b in await uow.access.bindings_of(user.user_id) if b.space_id in (None, space_id)}
        if held & roles and (Role.ADMIN in held or escalated <= held):
            found += 1
    return found


async def _escalation(uow: ControlUnitOfWork, space: Space, name: str, document: Any) -> frozenset[Role]:
    """The roles a change of the document needs besides its own, against the document of now (24.2)."""
    spec = doc_type(name)
    if spec.escalate is None:
        return frozenset()
    return spec.escalated(await _before(uow, space, name), document)


async def _before(uow: ControlUnitOfWork, space: Space, name: str) -> Any:
    """The document of now, which a diff would replace."""
    latest = await uow.config.latest_documents(space.space_id)
    return (
        latest[name].document if name in latest else current_document(build_space_config(space, [], {}), name)
    )


def _require_each(caller: Caller, roles: frozenset[Role]) -> None:
    for role in sorted(roles):
        if not caller.has(role):
            raise Forbidden(f"this change also needs the {role} role")


def contract_limits(quotas: Sequence[Quota]) -> SpaceLimits | None:
    """The tenant's quotas that the cell enforces itself, as the space document carries them."""
    budgets = {
        q.metric.split(":", 1)[1]: q.limit
        for q in quotas
        if q.metric.startswith(f"{LLM_BUDGET_METRIC}:") and q.period == "day" and q.limit >= 0
    }
    return SpaceLimits(llm_daily_micros=budgets) if budgets else None


def _require(caller: Caller, *roles: Role) -> None:
    if not caller.has(*roles):
        raise Forbidden("the role cannot do this")


async def _role_covers(uow: ControlUnitOfWork, caller: Caller, *roles: Role) -> Callable[[Space], bool]:
    """Which spaces of the tenant one of the caller's bindings with these roles (or admin) reaches: all
    of them for a tenant-wide binding, otherwise only the spaces the bindings name. The token's roles are
    those of one space; a report across spaces reads the bindings again."""
    wanted = {*roles, Role.ADMIN}
    bindings = [
        b
        for b in await uow.access.bindings_of(caller.user_id)
        if b.role in wanted and b.tenant_id == caller.tenant_id
    ]
    named = {b.space_id for b in bindings}
    return lambda space: space.tenant_id == caller.tenant_id and (None in named or space.space_id in named)


async def _person(uow: ControlUnitOfWork, caller: Caller) -> User:
    user = await uow.access.get_user(caller.user_id)
    if user is None or not user.active:
        raise Unauthenticated("the person is no longer active")
    return user


async def _tenant_person(uow: ControlUnitOfWork, caller: Caller, user_id: UUID) -> User:
    """A person of the caller's tenant; people of another tenant look exactly like missing ones."""
    user = await uow.access.get_user(user_id)
    if user is None or user.tenant_id != caller.tenant_id:
        raise NotFound("unknown user")
    return user


async def _covered_by_sso(uow: ControlUnitOfWork, user: User) -> bool:
    connection = await uow.sso.connection_for_domain(email_domain(user.email))
    return connection is not None and connection.tenant_id == user.tenant_id and connection.enabled


def _roles_detail(bindings: Sequence[RoleBinding]) -> list[dict[str, Any]]:
    return [
        {
            "role": b.role.value,
            "space_id": str(b.space_id) if b.space_id else None,
            "source_ids": sorted(str(s) for s in b.source_ids),
        }
        for b in bindings
    ]


async def _space_of(uow: ControlUnitOfWork, caller: Caller, space_id: UUID) -> Space:
    """Spaces of another tenant, and deleted ones, look exactly like missing ones."""
    space = await uow.directory.get_space(space_id)
    if space is None or space.tenant_id != caller.tenant_id or space.deleted_at is not None:
        raise NotFound("unknown space")
    return space


async def _active_key(uow: ControlUnitOfWork, source_id: UUID, key_id: str) -> ApiKey:
    key = await uow.access.get_key(key_id)
    if key is None or key.source_id != source_id:
        raise NotFound("unknown key")
    if key.status is not KeyStatus.ACTIVE:
        raise Conflict("the key is already revoked")
    return key


def _pick_space(spaces: Sequence[Space], bindings: Sequence[RoleBinding], wanted: UUID | None) -> Space:
    allowed = [s for s in spaces if any(b.space_id in (None, s.space_id) for b in bindings)]
    if wanted is not None:
        allowed = [s for s in allowed if s.space_id == wanted]
    if not allowed:
        raise Forbidden("no role on the space")
    # Sandbox first: a new person lands where nothing real can break.
    return min(allowed, key=lambda s: (s.environment is not Environment.SANDBOX, s.created_at))


def _binding(user: User, spec: BindingSpec) -> RoleBinding:
    return RoleBinding(uuid7(), user.user_id, user.tenant_id, spec.role, spec.space_id, spec.source_ids)


def _key_entry(key: ApiKey, now: datetime) -> dict[str, Any]:
    # The cell only takes a row with a higher version (upsert guard), so a time-based expiry bumps it too.
    expired = key.status is KeyStatus.ACTIVE and key.expires_at is not None and key.expires_at <= now
    return {
        "key_id": key.key_id,
        "source_id": str(key.source_id),
        "scopes": sorted(s.value for s in key.scopes),
        "fast_hash": key.fast_hash.hex(),
        "slow_hash": key.slow_hash,
        "status": KeyStatus.REVOKED.value if expired else key.status.value,
        "version": key.version + 1 if expired else key.version,
        "expires_at": key.expires_at.astimezone(UTC).isoformat() if key.expires_at else None,
    }


def _reason(reason: str) -> str:
    reason = reason.strip()
    if not 3 <= len(reason) <= 1000:
        raise InvalidInput("the reason must have 3 to 1000 characters")
    return reason


def _normalize_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise InvalidInput("invalid e-mail")
    return email
