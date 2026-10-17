"""PostgreSQL implementation of the control unit of work (5.16), on its own database."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, delete, func, insert, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql import Executable

from niadra.adapters.outbound.postgres.control_schema.base import Base
from niadra.adapters.outbound.postgres.control_schema.tables import (
    AccessChangeRow,
    ApiKeyRow,
    CellRow,
    ConfigDiffRow,
    ConfigVersionRow,
    DeletionReceiptRow,
    InvitationRow,
    PlacementMoveRow,
    ProjectRow,
    QuotaRow,
    RecoveryCodeRow,
    RevocationRow,
    RoleBindingRow,
    SecondFactorResetRow,
    SecretRefRow,
    SigningKeyRow,
    SnapshotRow,
    SourceRow,
    SpaceRow,
    SsoAttemptRow,
    SsoConnectionRow,
    SsoDomainRow,
    TenantRow,
    UsageAggregateRow,
    UserPreferenceRow,
    UserRow,
)
from niadra.domain.common.errors import Conflict
from niadra.domain.control.records import (
    AccessChange,
    AccessChangeKind,
    ApiKey,
    Cell,
    CellStatus,
    ConfigDiff,
    ConfigVersion,
    DeletionReceipt,
    DiffStatus,
    Invitation,
    KeyStatus,
    MoveStatus,
    OidcSettings,
    PlacementMove,
    Project,
    Quota,
    RecoveryCode,
    Revocation,
    RevocationKind,
    Role,
    RoleBinding,
    RoleMapping,
    SamlSettings,
    SecondFactorReset,
    SecretRef,
    SigningKey,
    Snapshot,
    Source,
    Space,
    SsoAttempt,
    SsoAttemptStatus,
    SsoConnection,
    SsoProtocol,
    SsoPurpose,
    Tenant,
    UsageAggregate,
    User,
)
from niadra.domain.spaces.directory import Tier
from niadra.domain.vocabulary import Audience, Environment, Scope, Verification


async def _insert_unique(
    session: AsyncSession, table: type[Base], values: dict[str, Any], message: str
) -> None:
    """Inserts or raises Conflict, without aborting the transaction on a unique violation."""
    stmt: Executable = (
        pg_insert(table).values(**values).on_conflict_do_nothing().returning(literal_column("1"))
    )
    if (await session.execute(stmt)).first() is None:
        raise Conflict(message)


class PgDirectoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def add_tenant(self, tenant: Tenant) -> None:
        await _insert_unique(
            self._db,
            TenantRow,
            {
                "tenant_id": tenant.tenant_id,
                "name": tenant.name,
                "slug": tenant.slug,
                "mfa_required": tenant.mfa_required,
                "created_at": tenant.created_at,
            },
            "the tenant slug is taken",
        )

    async def get_tenant(self, tenant_id: UUID) -> Tenant | None:
        row = (
            await self._db.execute(select(TenantRow.__table__).where(TenantRow.tenant_id == tenant_id))
        ).first()
        return Tenant(row.tenant_id, row.name, row.slug, row.created_at, row.mfa_required) if row else None

    async def tenant_by_slug(self, slug: str) -> Tenant | None:
        row = (await self._db.execute(select(TenantRow.__table__).where(TenantRow.slug == slug))).first()
        return Tenant(row.tenant_id, row.name, row.slug, row.created_at, row.mfa_required) if row else None

    async def add_project(self, project: Project) -> None:
        await _insert_unique(
            self._db,
            ProjectRow,
            {
                "project_id": project.project_id,
                "tenant_id": project.tenant_id,
                "name": project.name,
                "slug": project.slug,
                "region": project.region,
                "created_at": project.created_at,
            },
            "the project slug is taken",
        )

    async def projects_of(self, tenant_id: UUID) -> list[Project]:
        rows = await self._db.execute(
            select(ProjectRow.__table__)
            .where(ProjectRow.tenant_id == tenant_id)
            .order_by(ProjectRow.created_at)
        )
        return [_project(r) for r in rows]

    async def get_project(self, project_id: UUID) -> Project | None:
        row = (
            await self._db.execute(select(ProjectRow.__table__).where(ProjectRow.project_id == project_id))
        ).first()
        return _project(row) if row else None

    async def spaces_of_project(self, project_id: UUID) -> list[Space]:
        rows = await self._db.execute(
            select(SpaceRow.__table__)
            .where(SpaceRow.project_id == project_id, SpaceRow.deleted_at.is_(None))
            .order_by(SpaceRow.space_id)
        )
        return [_space(r) for r in rows]

    async def add_space(self, space: Space) -> None:
        await _insert_unique(
            self._db,
            SpaceRow,
            {
                "space_id": space.space_id,
                "tenant_id": space.tenant_id,
                "project_id": space.project_id,
                "environment": space.environment.value,
                "slug": space.slug,
                "region": space.region,
                "cell_id": space.cell_id,
                "created_at": space.created_at,
                "tier": space.tier.value,
                "instance": space.instance,
            },
            "the space slug is taken in the region",
        )

    async def get_space(self, space_id: UUID, *, lock: bool = False) -> Space | None:
        stmt = select(SpaceRow.__table__).where(SpaceRow.space_id == space_id)
        row = (await self._db.execute(stmt.with_for_update() if lock else stmt)).first()
        return _space(row) if row else None

    async def set_space_key(self, space_id: UUID, kms_key_arn: str | None) -> None:
        await self._db.execute(
            update(SpaceRow).where(SpaceRow.space_id == space_id).values(kms_key_arn=kms_key_arn)
        )

    async def spaces_of_tenant(self, tenant_id: UUID) -> list[Space]:
        rows = await self._db.execute(
            select(SpaceRow.__table__)
            .where(SpaceRow.tenant_id == tenant_id, SpaceRow.deleted_at.is_(None))
            .order_by(SpaceRow.space_id)
        )
        return [_space(r) for r in rows]

    async def spaces_in_cell(self, cell_id: str, *, include_deleted: bool = False) -> list[Space]:
        stmt = select(SpaceRow.__table__).where(SpaceRow.cell_id == cell_id)
        if not include_deleted:
            stmt = stmt.where(SpaceRow.deleted_at.is_(None))
        rows = await self._db.execute(stmt.order_by(SpaceRow.space_id))
        return [_space(r) for r in rows]

    async def mark_space_deleted(self, space_id: UUID, at: datetime) -> None:
        await self._db.execute(
            update(SpaceRow)
            .where(SpaceRow.space_id == space_id, SpaceRow.deleted_at.is_(None))
            .values(deleted_at=at)
        )

    async def add_deletion_receipt(self, receipt: DeletionReceipt) -> None:
        stmt = pg_insert(DeletionReceiptRow).values(
            space_id=receipt.space_id,
            cell_id=receipt.cell_id,
            receipt=receipt.receipt,
            receipt_hash=receipt.receipt_hash,
            received_at=receipt.received_at,
        )
        await self._db.execute(stmt.on_conflict_do_nothing())

    async def deletion_receipts(self, space_ids: Sequence[UUID]) -> dict[UUID, DeletionReceipt]:
        if not space_ids:
            return {}
        rows = await self._db.execute(
            select(DeletionReceiptRow.__table__).where(DeletionReceiptRow.space_id.in_(space_ids))
        )
        return {
            r.space_id: DeletionReceipt(r.space_id, r.cell_id, dict(r.receipt), r.receipt_hash, r.received_at)
            for r in rows
        }

    async def set_space_cell(self, space_id: UUID, cell_id: str) -> None:
        await self._db.execute(update(SpaceRow).where(SpaceRow.space_id == space_id).values(cell_id=cell_id))

    async def add_cell(self, cell: Cell) -> None:
        await _insert_unique(
            self._db,
            CellRow,
            {
                "cell_id": cell.cell_id,
                "region": cell.region,
                "address": cell.address,
                "capacity": cell.capacity,
                "status": cell.status.value,
                "code_version": cell.code_version,
                "schema_revision": cell.schema_revision,
                "created_at": cell.created_at,
                "tenant_id": cell.tenant_id,
                "token_hash": cell.token_hash,
            },
            "the cell already exists",
        )

    async def get_cell(self, cell_id: str, *, lock: bool = False) -> Cell | None:
        stmt = select(CellRow.__table__).where(CellRow.cell_id == cell_id)
        row = (await self._db.execute(stmt.with_for_update() if lock else stmt)).first()
        return _cell(row) if row else None

    async def list_cells(self) -> list[Cell]:
        return [_cell(r) for r in await self._db.execute(select(CellRow.__table__).order_by(CellRow.cell_id))]

    async def set_cell_token(self, cell_id: str, token_hash: bytes) -> None:
        await self._db.execute(
            update(CellRow).where(CellRow.cell_id == cell_id).values(token_hash=token_hash)
        )

    async def set_cell_status(self, cell_id: str, status: CellStatus) -> None:
        await self._db.execute(update(CellRow).where(CellRow.cell_id == cell_id).values(status=status.value))

    async def placed_counts(self) -> dict[str, int]:
        rows = await self._db.execute(select(SpaceRow.cell_id, func.count()).group_by(SpaceRow.cell_id))
        return {cell_id: count for cell_id, count in rows.tuples()}

    async def add_move(self, move: PlacementMove) -> None:
        await _insert_unique(
            self._db,
            PlacementMoveRow,
            {
                "move_id": move.move_id,
                "space_id": move.space_id,
                "from_cell": move.from_cell,
                "to_cell": move.to_cell,
                "status": move.status.value,
                "reason": move.reason,
                "requested_at": move.requested_at,
                "completed_at": move.completed_at,
            },
            "the space is already moving",
        )

    async def open_move(self, space_id: UUID) -> PlacementMove | None:
        row = (
            await self._db.execute(
                select(PlacementMoveRow.__table__).where(
                    PlacementMoveRow.space_id == space_id, PlacementMoveRow.status == MoveStatus.COPYING.value
                )
            )
        ).first()
        if row is None:
            return None
        return PlacementMove(
            row.move_id,
            row.space_id,
            row.from_cell,
            row.to_cell,
            MoveStatus(row.status),
            row.reason,
            row.requested_at,
            row.completed_at,
        )

    async def moved_from(self, cell_id: str, since: datetime) -> dict[UUID, str]:
        rows = await self._db.execute(
            select(PlacementMoveRow.space_id, PlacementMoveRow.to_cell).where(
                PlacementMoveRow.from_cell == cell_id,
                PlacementMoveRow.status == MoveStatus.COMPLETED.value,
                PlacementMoveRow.completed_at >= since,
            )
        )
        return {space_id: to_cell for space_id, to_cell in rows.all()}

    async def complete_move(self, move_id: UUID, at: datetime) -> None:
        await self._db.execute(
            update(PlacementMoveRow)
            .where(PlacementMoveRow.move_id == move_id)
            .values(status=MoveStatus.COMPLETED.value, completed_at=at)
        )

    async def set_quota(self, quota: Quota) -> None:
        stmt = pg_insert(QuotaRow).values(
            tenant_id=quota.tenant_id, metric=quota.metric, limit=quota.limit, period=quota.period
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=["tenant_id", "metric"],
                set_={"limit": stmt.excluded.limit, "period": stmt.excluded.period},
            )
        )

    async def quotas_of(self, tenant_id: UUID) -> list[Quota]:
        rows = await self._db.execute(
            select(QuotaRow.__table__).where(QuotaRow.tenant_id == tenant_id).order_by(QuotaRow.metric)
        )
        return [Quota(r.tenant_id, r.metric, r.limit, r.period) for r in rows]


class PgAccessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def add_source(self, source: Source) -> None:
        await self._db.execute(
            insert(SourceRow).values(
                source_id=source.source_id,
                space_id=source.space_id,
                name=source.name,
                vendor=source.vendor,
                channel=source.channel,
                purposes=sorted(source.purposes),
                audience=source.audience.value,
                verification_ceiling=source.verification_ceiling.value,
                confirms_before_acting=source.confirms_before_acting,
                navigation_enabled=source.navigation_enabled,
                trusted_action_ops=sorted(source.trusted_action_ops),
                critical=source.critical,
                created_at=source.created_at,
                revoked_at=source.revoked_at,
            )
        )

    async def get_source(self, source_id: UUID) -> Source | None:
        row = (
            await self._db.execute(select(SourceRow.__table__).where(SourceRow.source_id == source_id))
        ).first()
        return _source(row) if row else None

    async def sources_of(self, space_id: UUID) -> list[Source]:
        rows = await self._db.execute(
            select(SourceRow.__table__).where(SourceRow.space_id == space_id).order_by(SourceRow.source_id)
        )
        return [_source(r) for r in rows]

    async def revoke_source(self, source_id: UUID, at: datetime) -> None:
        await self._db.execute(
            update(SourceRow).where(SourceRow.source_id == source_id).values(revoked_at=at)
        )

    async def add_key(self, key: ApiKey) -> None:
        await _insert_unique(
            self._db,
            ApiKeyRow,
            {
                "key_id": key.key_id,
                "space_id": key.space_id,
                "source_id": key.source_id,
                "scopes": sorted(s.value for s in key.scopes),
                "fast_hash": key.fast_hash,
                "slow_hash": key.slow_hash,
                "status": key.status.value,
                "version": key.version,
                "created_at": key.created_at,
                "expires_at": key.expires_at,
                "revoked_at": key.revoked_at,
            },
            "duplicate key",
        )

    async def get_key(self, key_id: str) -> ApiKey | None:
        row = (await self._db.execute(select(ApiKeyRow.__table__).where(ApiKeyRow.key_id == key_id))).first()
        return _key(row) if row else None

    async def keys_of_source(self, source_id: UUID) -> list[ApiKey]:
        rows = await self._db.execute(
            select(ApiKeyRow.__table__).where(ApiKeyRow.source_id == source_id).order_by(ApiKeyRow.created_at)
        )
        return [_key(r) for r in rows]

    async def keys_of_spaces(self, space_ids: Sequence[UUID]) -> list[ApiKey]:
        if not space_ids:
            return []
        rows = await self._db.execute(
            select(ApiKeyRow.__table__).where(ApiKeyRow.space_id.in_(space_ids)).order_by(ApiKeyRow.key_id)
        )
        return [_key(r) for r in rows]

    async def revoke_key(self, key_id: str, at: datetime) -> None:
        await self._db.execute(
            update(ApiKeyRow)
            .where(ApiKeyRow.key_id == key_id)
            .values(status=KeyStatus.REVOKED.value, revoked_at=at, version=ApiKeyRow.version + 1)
        )

    async def expire_key(self, key_id: str, at: datetime) -> None:
        await self._db.execute(
            update(ApiKeyRow)
            .where(ApiKeyRow.key_id == key_id)
            .values(expires_at=at, version=ApiKeyRow.version + 1)
        )

    async def add_revocation(
        self,
        space_id: UUID,
        kind: RevocationKind,
        source_id: UUID,
        key_id: str | None,
        reason: str,
        at: datetime,
    ) -> Revocation:
        seq = (
            await self._db.execute(
                insert(RevocationRow)
                .values(
                    space_id=space_id,
                    kind=kind.value,
                    source_id=source_id,
                    key_id=key_id,
                    reason=reason,
                    at=at,
                )
                .returning(RevocationRow.seq)
            )
        ).scalar_one()
        return Revocation(seq, space_id, kind, source_id, at, reason, key_id)

    async def revocations_after(self, seq: int, space_ids: Sequence[UUID], limit: int) -> list[Revocation]:
        if not space_ids:
            return []
        rows = await self._db.execute(
            select(RevocationRow.__table__)
            .where(RevocationRow.seq > seq, RevocationRow.space_id.in_(space_ids))
            .order_by(RevocationRow.seq)
            .limit(limit)
        )
        return [
            Revocation(r.seq, r.space_id, RevocationKind(r.kind), r.source_id, r.at, r.reason, r.key_id)
            for r in rows
        ]

    async def add_user(self, user: User) -> None:
        await _insert_unique(
            self._db,
            UserRow,
            {
                "user_id": user.user_id,
                "tenant_id": user.tenant_id,
                "email": user.email,
                "password_hash": user.password_hash,
                "totp_secret": user.totp_secret,
                "active": user.active,
                "created_at": user.created_at,
                "last_login_at": user.last_login_at,
                "totp_confirmed_at": user.totp_confirmed_at,
                "totp_pending_secret": user.totp_pending_secret,
                "deactivated_at": user.deactivated_at,
                "last_sso_login_at": user.last_sso_login_at,
                "totp_last_step": user.totp_last_step,
            },
            "the e-mail is taken",
        )

    async def get_user(self, user_id: UUID) -> User | None:
        row = (await self._db.execute(select(UserRow.__table__).where(UserRow.user_id == user_id))).first()
        return _user(row) if row else None

    async def user_by_email(self, email: str) -> User | None:
        row = (await self._db.execute(select(UserRow.__table__).where(UserRow.email == email))).first()
        return _user(row) if row else None

    async def users_of(self, tenant_id: UUID) -> list[User]:
        rows = await self._db.execute(
            select(UserRow.__table__).where(UserRow.tenant_id == tenant_id).order_by(UserRow.email)
        )
        return [_user(r) for r in rows]

    async def touch_login(self, user_id: UUID, at: datetime) -> None:
        await self._db.execute(update(UserRow).where(UserRow.user_id == user_id).values(last_login_at=at))

    async def set_second_factor(
        self, user_id: UUID, secret: str | None, confirmed_at: datetime | None
    ) -> None:
        await self._db.execute(
            update(UserRow)
            .where(UserRow.user_id == user_id)
            .values(totp_secret=secret, totp_confirmed_at=confirmed_at, totp_pending_secret=None)
        )

    async def set_pending_second_factor(self, user_id: UUID, secret: str) -> None:
        await self._db.execute(
            update(UserRow).where(UserRow.user_id == user_id).values(totp_pending_secret=secret)
        )

    async def use_totp_step(self, user_id: UUID, step: int) -> bool:
        # One statement: a concurrent use of the same code waits for the row, then finds the step taken.
        row = (
            await self._db.execute(
                update(UserRow)
                .where(
                    UserRow.user_id == user_id,
                    or_(UserRow.totp_last_step.is_(None), UserRow.totp_last_step < step),
                )
                .values(totp_last_step=step)
                .returning(UserRow.user_id)
            )
        ).first()
        return row is not None

    async def preferences_of(self, user_id: UUID) -> dict[str, Any]:
        rows = await self._db.execute(
            select(UserPreferenceRow.key, UserPreferenceRow.value)
            .where(UserPreferenceRow.user_id == user_id)
            .order_by(UserPreferenceRow.key)
        )
        return {row.key: row.value for row in rows}

    async def set_preference(self, user_id: UUID, key: str, value: Any) -> None:
        stmt = pg_insert(UserPreferenceRow).values(user_id=user_id, key=key, value=value)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[UserPreferenceRow.user_id, UserPreferenceRow.key],
                set_={"value": stmt.excluded.value},
            )
        )

    async def recovery_codes_of(self, user_id: UUID) -> list[RecoveryCode]:
        rows = await self._db.execute(
            select(RecoveryCodeRow.__table__)
            .where(RecoveryCodeRow.user_id == user_id)
            .order_by(RecoveryCodeRow.created_at, RecoveryCodeRow.code_hash)
        )
        return [RecoveryCode(r.user_id, bytes(r.code_hash), r.created_at, r.used_at) for r in rows]

    async def replace_recovery_codes(self, user_id: UUID, codes: Sequence[RecoveryCode]) -> None:
        await self._db.execute(delete(RecoveryCodeRow).where(RecoveryCodeRow.user_id == user_id))
        if codes:
            await self._db.execute(
                insert(RecoveryCodeRow),
                [
                    {
                        "user_id": c.user_id,
                        "code_hash": c.code_hash,
                        "created_at": c.created_at,
                        "used_at": c.used_at,
                    }
                    for c in codes
                ],
            )

    async def use_recovery_code(self, user_id: UUID, code_hash: bytes, at: datetime) -> bool:
        # The condition on used_at makes two logins with the same code race to one winner.
        used = await self._db.execute(
            update(RecoveryCodeRow)
            .where(
                RecoveryCodeRow.user_id == user_id,
                RecoveryCodeRow.code_hash == code_hash,
                RecoveryCodeRow.used_at.is_(None),
            )
            .values(used_at=at)
            .returning(RecoveryCodeRow.code_hash)
        )
        return used.first() is not None

    async def add_second_factor_reset(self, reset: SecondFactorReset) -> None:
        await self._db.execute(
            insert(SecondFactorResetRow).values(
                reset_id=reset.reset_id,
                tenant_id=reset.tenant_id,
                user_id=reset.user_id,
                reset_by=reset.reset_by,
                reason=reset.reason,
                at=reset.at,
            )
        )

    async def unused_recovery_codes(self, user_ids: Sequence[UUID]) -> dict[UUID, int]:
        if not user_ids:
            return {}
        rows = await self._db.execute(
            select(RecoveryCodeRow.user_id, func.count())
            .where(RecoveryCodeRow.user_id.in_(user_ids), RecoveryCodeRow.used_at.is_(None))
            .group_by(RecoveryCodeRow.user_id)
        )
        return {user_id: count for user_id, count in rows.tuples()}

    async def set_active(self, user_id: UUID, active: bool, at: datetime | None) -> None:
        await self._db.execute(
            update(UserRow).where(UserRow.user_id == user_id).values(active=active, deactivated_at=at)
        )

    async def set_password(self, user_id: UUID, password_hash: str) -> None:
        await self._db.execute(
            update(UserRow).where(UserRow.user_id == user_id).values(password_hash=password_hash)
        )

    async def touch_sso_login(self, user_id: UUID, at: datetime) -> None:
        await self._db.execute(update(UserRow).where(UserRow.user_id == user_id).values(last_sso_login_at=at))

    async def add_invitation(self, invitation: Invitation) -> None:
        await self._db.execute(
            insert(InvitationRow).values(
                invitation_id=invitation.invitation_id,
                tenant_id=invitation.tenant_id,
                user_id=invitation.user_id,
                token_hash=invitation.token_hash,
                created_by=invitation.created_by,
                created_at=invitation.created_at,
                expires_at=invitation.expires_at,
                used_at=invitation.used_at,
            )
        )

    async def invitation_by_token(self, token_hash: bytes) -> Invitation | None:
        row = (
            await self._db.execute(
                select(InvitationRow.__table__)
                .where(InvitationRow.token_hash == token_hash)
                .with_for_update()
            )
        ).first()
        return _invitation(row) if row else None

    async def open_invitations(self, user_ids: Sequence[UUID], now: datetime) -> dict[UUID, Invitation]:
        if not user_ids:
            return {}
        rows = await self._db.execute(
            select(InvitationRow.__table__)
            .where(
                InvitationRow.user_id.in_(user_ids),
                InvitationRow.used_at.is_(None),
                InvitationRow.expires_at > now,
            )
            .order_by(InvitationRow.created_at)
        )
        return {r.user_id: _invitation(r) for r in rows}

    async def close_invitations(self, user_id: UUID, at: datetime) -> None:
        await self._db.execute(
            update(InvitationRow)
            .where(InvitationRow.user_id == user_id, InvitationRow.used_at.is_(None))
            .values(used_at=at)
        )

    async def add_access_change(self, change: AccessChange) -> None:
        await self._db.execute(
            insert(AccessChangeRow).values(
                change_id=change.change_id,
                tenant_id=change.tenant_id,
                kind=change.kind.value,
                at=change.at,
                user_id=change.user_id,
                actor_id=change.actor_id,
                reason=change.reason,
                detail=change.detail,
            )
        )

    async def access_changes_since(
        self, tenant_ids: Sequence[UUID], after: datetime, limit: int
    ) -> list[AccessChange]:
        if not tenant_ids:
            return []
        stmt = (
            select(AccessChangeRow.__table__)
            .where(AccessChangeRow.tenant_id.in_(list(tenant_ids)), AccessChangeRow.at > after)
            .order_by(AccessChangeRow.at, AccessChangeRow.change_id)
            .limit(limit)
        )
        return [_access_change(r) for r in await self._db.execute(stmt)]

    async def access_changes(self, tenant_id: UUID, user_id: UUID | None, limit: int) -> list[AccessChange]:
        stmt = select(AccessChangeRow.__table__).where(AccessChangeRow.tenant_id == tenant_id)
        if user_id is not None:
            stmt = stmt.where(AccessChangeRow.user_id == user_id)
        rows = await self._db.execute(
            stmt.order_by(AccessChangeRow.at.desc(), AccessChangeRow.change_id.desc()).limit(limit)
        )
        return [_access_change(r) for r in rows]

    async def set_bindings(self, user_id: UUID, bindings: Sequence[RoleBinding]) -> None:
        await self._db.execute(delete(RoleBindingRow).where(RoleBindingRow.user_id == user_id))
        if bindings:
            await self._db.execute(
                insert(RoleBindingRow),
                [
                    {
                        "binding_id": b.binding_id,
                        "user_id": b.user_id,
                        "tenant_id": b.tenant_id,
                        "role": b.role.value,
                        "space_id": b.space_id,
                        "source_ids": sorted(b.source_ids),
                    }
                    for b in bindings
                ],
            )

    async def bindings_of(self, user_id: UUID) -> list[RoleBinding]:
        rows = await self._db.execute(
            select(RoleBindingRow.__table__)
            .where(RoleBindingRow.user_id == user_id)
            .order_by(RoleBindingRow.binding_id)
        )
        return [
            RoleBinding(
                r.binding_id, r.user_id, r.tenant_id, Role(r.role), r.space_id, frozenset(r.source_ids)
            )
            for r in rows
        ]

    async def count_admins(self, tenant_id: UUID) -> int:
        stmt = (
            select(func.count(func.distinct(RoleBindingRow.user_id)))
            .join(UserRow, UserRow.user_id == RoleBindingRow.user_id)
            .where(
                RoleBindingRow.tenant_id == tenant_id,
                RoleBindingRow.role == Role.ADMIN.value,
                RoleBindingRow.space_id.is_(None),
                UserRow.active.is_(True),
            )
        )
        return int((await self._db.execute(stmt)).scalar_one())

    async def save_signing_key(self, key: SigningKey) -> None:
        await self._db.execute(
            pg_insert(SigningKeyRow)
            .values(
                kid=key.kid,
                algorithm=key.algorithm,
                public_jwk=key.public_jwk,
                created_at=key.created_at,
                retired_at=key.retired_at,
            )
            .on_conflict_do_nothing()
        )

    async def signing_keys(self) -> list[SigningKey]:
        rows = await self._db.execute(
            select(SigningKeyRow.__table__).order_by(SigningKeyRow.created_at.desc())
        )
        return [SigningKey(r.kid, r.algorithm, r.public_jwk, r.created_at, r.retired_at) for r in rows]


class PgSsoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def connection_of(self, tenant_id: UUID) -> SsoConnection | None:
        row = (
            await self._db.execute(
                select(SsoConnectionRow.__table__).where(SsoConnectionRow.tenant_id == tenant_id)
            )
        ).first()
        return await self._connection(row) if row else None

    async def connection_for_domain(self, domain: str) -> SsoConnection | None:
        row = (
            await self._db.execute(
                select(SsoConnectionRow.__table__)
                .join(SsoDomainRow, SsoDomainRow.connection_id == SsoConnectionRow.connection_id)
                .where(SsoDomainRow.domain == domain)
            )
        ).first()
        return await self._connection(row) if row else None

    async def save_connection(self, connection: SsoConnection) -> None:
        values = _connection_values(connection)
        stmt = pg_insert(SsoConnectionRow).values(**values)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=["tenant_id"],
                # The connection keeps its id and creation time: attempts and domains point at it.
                set_={
                    k: stmt.excluded[k]
                    for k in values
                    if k not in ("connection_id", "tenant_id", "created_at")
                },
            )
        )
        connection_id = (
            await self._db.execute(
                select(SsoConnectionRow.connection_id).where(
                    SsoConnectionRow.tenant_id == connection.tenant_id
                )
            )
        ).scalar_one()
        await self._db.execute(delete(SsoDomainRow).where(SsoDomainRow.connection_id == connection_id))
        for domain in connection.domains:
            await _insert_unique(
                self._db,
                SsoDomainRow,
                {"domain": domain, "connection_id": connection_id},
                "another tenant already uses one of the domains",
            )

    async def delete_connection(self, connection_id: UUID) -> None:
        await self._db.execute(
            delete(SsoConnectionRow).where(SsoConnectionRow.connection_id == connection_id)
        )

    async def add_attempt(self, attempt: SsoAttempt) -> None:
        await self._db.execute(insert(SsoAttemptRow).values(**_attempt_values(attempt)))

    async def attempt_by_state(self, state_hash: bytes) -> SsoAttempt | None:
        return await self._attempt_where(SsoAttemptRow.state_hash == state_hash, lock=True)

    async def attempt_by_login_code(self, code_hash: bytes) -> SsoAttempt | None:
        return await self._attempt_where(SsoAttemptRow.login_code_hash == code_hash, lock=True)

    async def get_attempt(self, attempt_id: UUID) -> SsoAttempt | None:
        return await self._attempt_where(SsoAttemptRow.attempt_id == attempt_id, lock=False)

    async def update_attempt(self, attempt: SsoAttempt) -> None:
        await self._db.execute(
            update(SsoAttemptRow)
            .where(SsoAttemptRow.attempt_id == attempt.attempt_id)
            .values(**_attempt_values(attempt))
        )

    async def pending_attempts(self, connection_id: UUID, since: datetime) -> int:
        stmt = select(func.count()).where(
            SsoAttemptRow.connection_id == connection_id,
            SsoAttemptRow.status == SsoAttemptStatus.PENDING.value,
            SsoAttemptRow.created_at >= since,
        )
        return int((await self._db.execute(stmt)).scalar_one())

    async def purge_attempts(self, before: datetime) -> int:
        gone = await self._db.execute(
            delete(SsoAttemptRow).where(SsoAttemptRow.expires_at < before).returning(SsoAttemptRow.attempt_id)
        )
        return len(gone.all())

    async def _attempt_where(self, condition: Any, *, lock: bool) -> SsoAttempt | None:
        stmt = select(SsoAttemptRow.__table__).where(condition)
        row = (await self._db.execute(stmt.with_for_update() if lock else stmt)).first()
        return _attempt(row) if row else None

    async def _connection(self, row: Row[Any]) -> SsoConnection:
        domains = await self._db.execute(
            select(SsoDomainRow.domain)
            .where(SsoDomainRow.connection_id == row.connection_id)
            .order_by(SsoDomainRow.domain)
        )
        return _sso_connection(row, tuple(domains.scalars()))


class PgConfigStore:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def add_diff(self, diff: ConfigDiff) -> None:
        await self._db.execute(insert(ConfigDiffRow).values(**_diff_values(diff)))

    async def get_diff(self, diff_id: UUID) -> ConfigDiff | None:
        row = (
            await self._db.execute(select(ConfigDiffRow.__table__).where(ConfigDiffRow.diff_id == diff_id))
        ).first()
        return _diff(row) if row else None

    async def update_diff(self, diff: ConfigDiff) -> None:
        await self._db.execute(
            update(ConfigDiffRow).where(ConfigDiffRow.diff_id == diff.diff_id).values(**_diff_values(diff))
        )

    async def diffs_of(
        self, space_id: UUID, status: DiffStatus | None = None, limit: int = 100
    ) -> list[ConfigDiff]:
        stmt = select(ConfigDiffRow.__table__).where(ConfigDiffRow.space_id == space_id)
        if status is not None:
            stmt = stmt.where(ConfigDiffRow.status == status.value)
        rows = await self._db.execute(stmt.order_by(ConfigDiffRow.diff_id.desc()).limit(limit))
        return [_diff(r) for r in rows]

    async def add_version(self, version: ConfigVersion) -> None:
        await _insert_unique(
            self._db,
            ConfigVersionRow,
            {
                "space_id": version.space_id,
                "version": version.version,
                "doc_type": version.doc_type,
                "document": version.document,
                "diff_id": version.diff_id,
                "created_at": version.created_at,
            },
            "the version exists",
        )

    async def current_version(self, space_id: UUID) -> int:
        stmt = select(func.coalesce(func.max(ConfigVersionRow.version), 0)).where(
            ConfigVersionRow.space_id == space_id
        )
        return int((await self._db.execute(stmt)).scalar_one())

    async def latest_documents(self, space_id: UUID) -> dict[str, ConfigVersion]:
        stmt = (
            select(ConfigVersionRow.__table__)
            .where(ConfigVersionRow.space_id == space_id)
            .distinct(ConfigVersionRow.doc_type)
            .order_by(ConfigVersionRow.doc_type, ConfigVersionRow.version.desc())
        )
        return {r.doc_type: _version(r) for r in await self._db.execute(stmt)}

    async def history(self, space_id: UUID, doc_type: str | None, limit: int) -> list[ConfigVersion]:
        stmt = select(ConfigVersionRow.__table__).where(ConfigVersionRow.space_id == space_id)
        if doc_type is not None:
            stmt = stmt.where(ConfigVersionRow.doc_type == doc_type)
        rows = await self._db.execute(stmt.order_by(ConfigVersionRow.version.desc()).limit(limit))
        return [_version(r) for r in rows]

    async def version_before(self, space_id: UUID, doc_type: str, version: int) -> ConfigVersion | None:
        row = (
            await self._db.execute(
                select(ConfigVersionRow.__table__)
                .where(
                    ConfigVersionRow.space_id == space_id,
                    ConfigVersionRow.doc_type == doc_type,
                    ConfigVersionRow.version < version,
                )
                .order_by(ConfigVersionRow.version.desc())
                .limit(1)
            )
        ).first()
        return _version(row) if row else None

    async def changed_since(self, space_id: UUID, doc_type: str, version: int) -> bool:
        stmt = select(
            select(ConfigVersionRow.version)
            .where(
                ConfigVersionRow.space_id == space_id,
                ConfigVersionRow.doc_type == doc_type,
                ConfigVersionRow.version > version,
            )
            .exists()
        )
        return bool((await self._db.execute(stmt)).scalar_one())

    async def latest_snapshot(self, cell_id: str) -> Snapshot | None:
        row = (
            await self._db.execute(
                select(SnapshotRow.__table__)
                .where(SnapshotRow.cell_id == cell_id)
                .order_by(SnapshotRow.version.desc())
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        return Snapshot(
            row.cell_id,
            row.version,
            row.manifest,
            row.signature,
            row.kid,
            row.documents,
            row.keys,
            row.keys_signature,
            row.created_at,
        )

    async def add_snapshot(self, snapshot: Snapshot) -> None:
        await _insert_unique(
            self._db,
            SnapshotRow,
            {
                "cell_id": snapshot.cell_id,
                "version": snapshot.version,
                "manifest": snapshot.manifest,
                "signature": snapshot.signature,
                "kid": snapshot.kid,
                "documents": snapshot.documents,
                "keys": snapshot.keys,
                "keys_signature": snapshot.keys_signature,
                "created_at": snapshot.created_at,
            },
            "the snapshot version exists",
        )

    async def put_secret_ref(self, ref: SecretRef) -> None:
        stmt = pg_insert(SecretRefRow).values(
            space_id=ref.space_id,
            ref=ref.ref,
            kind=ref.kind,
            version=ref.version,
            rotated_by=ref.rotated_by,
            rotated_at=ref.rotated_at,
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=["space_id", "ref"],
                set_={k: stmt.excluded[k] for k in ("kind", "version", "rotated_by", "rotated_at")},
            )
        )

    async def secret_refs(self, space_id: UUID) -> list[SecretRef]:
        rows = await self._db.execute(
            select(SecretRefRow.__table__).where(SecretRefRow.space_id == space_id).order_by(SecretRefRow.ref)
        )
        return [SecretRef(r.space_id, r.ref, r.kind, r.version, r.rotated_by, r.rotated_at) for r in rows]


class PgUsageStore:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def upsert(self, aggregates: Sequence[UsageAggregate]) -> None:
        if not aggregates:
            return
        stmt = pg_insert(UsageAggregateRow).values(
            [
                {
                    "space_id": a.space_id,
                    "period_start": a.period_start,
                    "metric": a.metric,
                    "quantity": a.quantity,
                    "cost_micros": a.cost_micros,
                    "cell_id": a.cell_id,
                    "reported_at": a.reported_at,
                }
                for a in aggregates
            ]
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=["space_id", "period_start", "metric"],
                set_={k: stmt.excluded[k] for k in ("quantity", "cost_micros", "cell_id", "reported_at")},
            )
        )

    async def usage(
        self, space_ids: Sequence[UUID], since: datetime, until: datetime
    ) -> list[UsageAggregate]:
        if not space_ids:
            return []
        rows = await self._db.execute(
            select(UsageAggregateRow.__table__)
            .where(
                UsageAggregateRow.space_id.in_(space_ids),
                UsageAggregateRow.period_start >= since,
                UsageAggregateRow.period_start < until,
            )
            .order_by(UsageAggregateRow.period_start, UsageAggregateRow.metric, UsageAggregateRow.space_id)
        )
        return [
            UsageAggregate(
                r.space_id, r.period_start, r.metric, r.quantity, r.cell_id, r.reported_at, r.cost_micros
            )
            for r in rows
        ]


class PgControlUnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.directory = PgDirectoryRepository(session)
        self.access = PgAccessRepository(session)
        self.config = PgConfigStore(session)
        self.usage = PgUsageStore(session)
        self.sso = PgSsoRepository(session)


class PgControlUnitOfWorkFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[PgControlUnitOfWork]:
        async with self._sessions() as session, session.begin():
            yield PgControlUnitOfWork(session)


def _project(r: Row[Any]) -> Project:
    return Project(r.project_id, r.tenant_id, r.name, r.slug, r.region, r.created_at)


def _space(r: Row[Any]) -> Space:
    return Space(
        r.space_id,
        r.tenant_id,
        r.project_id,
        Environment(r.environment),
        r.slug,
        r.region,
        r.cell_id,
        r.created_at,
        r.deleted_at,
        r.kms_key_arn,
        Tier(r.tier),
        r.instance,
    )


def _cell(r: Row[Any]) -> Cell:
    return Cell(
        r.cell_id,
        r.region,
        r.address,
        r.capacity,
        CellStatus(r.status),
        r.created_at,
        r.code_version,
        r.schema_revision,
        r.tenant_id,
        r.token_hash,
    )


def _source(r: Row[Any]) -> Source:
    return Source(
        source_id=r.source_id,
        space_id=r.space_id,
        name=r.name,
        audience=Audience(r.audience),
        created_at=r.created_at,
        vendor=r.vendor,
        channel=r.channel,
        purposes=frozenset(r.purposes),
        verification_ceiling=Verification(r.verification_ceiling),
        confirms_before_acting=r.confirms_before_acting,
        navigation_enabled=r.navigation_enabled,
        trusted_action_ops=frozenset(r.trusted_action_ops),
        critical=r.critical,
        revoked_at=r.revoked_at,
    )


def _key(r: Row[Any]) -> ApiKey:
    return ApiKey(
        key_id=r.key_id,
        space_id=r.space_id,
        source_id=r.source_id,
        scopes=frozenset(Scope(s) for s in r.scopes),
        fast_hash=bytes(r.fast_hash),
        slow_hash=r.slow_hash,
        status=KeyStatus(r.status),
        version=r.version,
        created_at=r.created_at,
        expires_at=r.expires_at,
        revoked_at=r.revoked_at,
    )


def _user(r: Row[Any]) -> User:
    return User(
        r.user_id,
        r.tenant_id,
        r.email,
        r.password_hash,
        r.created_at,
        r.totp_secret,
        r.active,
        r.last_login_at,
        r.totp_confirmed_at,
        r.totp_pending_secret,
        r.deactivated_at,
        r.last_sso_login_at,
        r.totp_last_step,
    )


def _invitation(r: Row[Any]) -> Invitation:
    return Invitation(
        r.invitation_id,
        r.tenant_id,
        r.user_id,
        bytes(r.token_hash),
        r.created_by,
        r.created_at,
        r.expires_at,
        r.used_at,
    )


def _connection_values(c: SsoConnection) -> dict[str, Any]:
    return {
        "connection_id": c.connection_id,
        "tenant_id": c.tenant_id,
        "protocol": c.protocol.value,
        "enabled": c.enabled,
        "enforced": c.enforced,
        "default_role": c.default_role.value if c.default_role else None,
        "role_mappings": [{"group": m.group, "role": m.role.value} for m in c.role_mappings],
        "groups_attribute": c.groups_attribute,
        "require_second_factor": c.require_second_factor,
        "oidc_issuer": c.oidc.issuer if c.oidc else None,
        "oidc_client_id": c.oidc.client_id if c.oidc else None,
        "oidc_client_secret": c.oidc.client_secret if c.oidc else None,
        "oidc_scopes": list(c.oidc.scopes) if c.oidc else None,
        "saml_idp_entity_id": c.saml.idp_entity_id if c.saml else None,
        "saml_idp_sso_url": c.saml.idp_sso_url if c.saml else None,
        "saml_idp_certificate": c.saml.idp_certificate if c.saml else None,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
        "updated_by": c.updated_by,
    }


def _sso_connection(r: Row[Any], domains: tuple[str, ...]) -> SsoConnection:
    protocol = SsoProtocol(r.protocol)
    return SsoConnection(
        connection_id=r.connection_id,
        tenant_id=r.tenant_id,
        protocol=protocol,
        domains=domains,
        enabled=r.enabled,
        created_at=r.created_at,
        updated_at=r.updated_at,
        updated_by=r.updated_by,
        default_role=Role(r.default_role) if r.default_role else None,
        role_mappings=tuple(RoleMapping(m["group"], Role(m["role"])) for m in r.role_mappings),
        groups_attribute=r.groups_attribute,
        require_second_factor=r.require_second_factor,
        enforced=r.enforced,
        oidc=OidcSettings(
            r.oidc_issuer, r.oidc_client_id, bytes(r.oidc_client_secret), tuple(r.oidc_scopes or ())
        )
        if protocol is SsoProtocol.OIDC
        else None,
        saml=SamlSettings(r.saml_idp_entity_id, r.saml_idp_sso_url, r.saml_idp_certificate)
        if protocol is SsoProtocol.SAML
        else None,
    )


def _attempt_values(a: SsoAttempt) -> dict[str, Any]:
    return {
        "attempt_id": a.attempt_id,
        "tenant_id": a.tenant_id,
        "connection_id": a.connection_id,
        "purpose": a.purpose.value,
        "state_hash": a.state_hash,
        "nonce": a.nonce,
        "code_verifier": a.code_verifier,
        "callback_url": a.callback_url,
        "created_at": a.created_at,
        "expires_at": a.expires_at,
        "status": a.status.value,
        "binding_hash": a.binding_hash,
        "return_origin": a.return_origin,
        "saml_request_id": a.saml_request_id,
        "requested_by": a.requested_by,
        "user_id": a.user_id,
        "login_code_hash": a.login_code_hash,
        "code_expires_at": a.code_expires_at,
        "code_failures": a.code_failures,
        "completed_at": a.completed_at,
        "result": a.result,
    }


def _attempt(r: Row[Any]) -> SsoAttempt:
    return SsoAttempt(
        attempt_id=r.attempt_id,
        tenant_id=r.tenant_id,
        connection_id=r.connection_id,
        purpose=SsoPurpose(r.purpose),
        state_hash=bytes(r.state_hash),
        nonce=r.nonce,
        code_verifier=r.code_verifier,
        callback_url=r.callback_url,
        created_at=r.created_at,
        expires_at=r.expires_at,
        status=SsoAttemptStatus(r.status),
        binding_hash=bytes(r.binding_hash) if r.binding_hash is not None else None,
        return_origin=r.return_origin,
        saml_request_id=r.saml_request_id,
        requested_by=r.requested_by,
        user_id=r.user_id,
        login_code_hash=bytes(r.login_code_hash) if r.login_code_hash is not None else None,
        code_expires_at=r.code_expires_at,
        code_failures=r.code_failures,
        completed_at=r.completed_at,
        result=r.result,
    )


def _diff_values(d: ConfigDiff) -> dict[str, Any]:
    return {
        "diff_id": d.diff_id,
        "space_id": d.space_id,
        "doc_type": d.doc_type,
        "document": d.document,
        "reason": d.reason,
        "status": d.status.value,
        "base_version": d.base_version,
        "author_id": d.author_id,
        "author_label": d.author_label,
        "decided_by": d.decided_by,
        "decided_at": d.decided_at,
        "applied_version": d.applied_version,
        "rollback_of": d.rollback_of,
        "created_at": d.created_at,
    }


def _diff(r: Row[Any]) -> ConfigDiff:
    return ConfigDiff(
        diff_id=r.diff_id,
        space_id=r.space_id,
        doc_type=r.doc_type,
        document=r.document,
        reason=r.reason,
        status=DiffStatus(r.status),
        base_version=r.base_version,
        created_at=r.created_at,
        author_id=r.author_id,
        author_label=r.author_label,
        decided_by=r.decided_by,
        decided_at=r.decided_at,
        applied_version=r.applied_version,
        rollback_of=r.rollback_of,
    )


def _version(r: Row[Any]) -> ConfigVersion:
    return ConfigVersion(r.space_id, r.version, r.doc_type, r.document, r.diff_id, r.created_at)


def _access_change(r: Any) -> AccessChange:
    return AccessChange(
        r.change_id,
        r.tenant_id,
        AccessChangeKind(r.kind),
        r.at,
        r.user_id,
        r.actor_id,
        r.reason,
        r.detail,
    )
