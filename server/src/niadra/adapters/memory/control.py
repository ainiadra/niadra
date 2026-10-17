"""In-memory control database for unit tests; same contract as PostgreSQL."""

import copy
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.common.errors import Conflict
from niadra.domain.control.records import (
    AccessChange,
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
    SsoAttempt,
    SsoAttemptStatus,
    SsoConnection,
    Tenant,
    UsageAggregate,
    User,
)


def _table(store: MemoryStore, name: str) -> dict[Any, Any]:
    tables: dict[str, dict[Any, Any]] = store.module("control")
    return tables.setdefault(name, {})


class InMemoryDirectoryRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._tenants = _table(store, "tenants")
        self._projects = _table(store, "projects")
        self._spaces = _table(store, "spaces")
        self._cells = _table(store, "cells")
        self._moves = _table(store, "placement_moves")
        self._quotas = _table(store, "quotas")
        self._receipts = _table(store, "deletion_receipts")

    async def add_tenant(self, tenant: Tenant) -> None:
        if any(t.slug == tenant.slug for t in self._tenants.values()):
            raise Conflict("the tenant slug is taken")
        self._tenants[tenant.tenant_id] = tenant

    async def get_tenant(self, tenant_id: UUID) -> Tenant | None:
        return self._tenants.get(tenant_id)

    async def tenant_by_slug(self, slug: str) -> Tenant | None:
        return next((t for t in self._tenants.values() if t.slug == slug), None)

    async def add_project(self, project: Project) -> None:
        if any(p.tenant_id == project.tenant_id and p.slug == project.slug for p in self._projects.values()):
            raise Conflict("the project slug is taken")
        self._projects[project.project_id] = project

    async def projects_of(self, tenant_id: UUID) -> list[Project]:
        return sorted(
            (p for p in self._projects.values() if p.tenant_id == tenant_id), key=lambda p: p.created_at
        )

    async def get_project(self, project_id: UUID) -> Project | None:
        return self._projects.get(project_id)

    async def spaces_of_project(self, project_id: UUID) -> list[Space]:
        return sorted(
            (s for s in self._spaces.values() if s.project_id == project_id and s.deleted_at is None),
            key=lambda s: s.space_id,
        )

    async def add_space(self, space: Space) -> None:
        if any(s.region == space.region and s.slug == space.slug for s in self._spaces.values()):
            raise Conflict("the space slug is taken in the region")
        self._spaces[space.space_id] = space

    async def get_space(self, space_id: UUID, *, lock: bool = False) -> Space | None:  # noqa: ARG002
        return self._spaces.get(space_id)

    async def set_space_key(self, space_id: UUID, kms_key_arn: str | None) -> None:
        if space_id in self._spaces:
            self._spaces[space_id] = replace(self._spaces[space_id], kms_key_arn=kms_key_arn)

    async def spaces_of_tenant(self, tenant_id: UUID) -> list[Space]:
        return sorted(
            (s for s in self._spaces.values() if s.tenant_id == tenant_id and s.deleted_at is None),
            key=lambda s: s.space_id,
        )

    async def spaces_in_cell(self, cell_id: str, *, include_deleted: bool = False) -> list[Space]:
        return sorted(
            (
                s
                for s in self._spaces.values()
                if s.cell_id == cell_id and (include_deleted or s.deleted_at is None)
            ),
            key=lambda s: s.space_id,
        )

    async def mark_space_deleted(self, space_id: UUID, at: datetime) -> None:
        space = self._spaces[space_id]
        if space.deleted_at is None:
            self._spaces[space_id] = replace(space, deleted_at=at)

    async def add_deletion_receipt(self, receipt: DeletionReceipt) -> None:
        self._receipts.setdefault(receipt.space_id, receipt)

    async def deletion_receipts(self, space_ids: Sequence[UUID]) -> dict[UUID, DeletionReceipt]:
        return {s: self._receipts[s] for s in space_ids if s in self._receipts}

    async def set_space_cell(self, space_id: UUID, cell_id: str) -> None:
        self._spaces[space_id] = replace(self._spaces[space_id], cell_id=cell_id)

    async def add_cell(self, cell: Cell) -> None:
        if cell.cell_id in self._cells:
            raise Conflict("the cell already exists")
        self._cells[cell.cell_id] = cell

    async def get_cell(self, cell_id: str, *, lock: bool = False) -> Cell | None:  # noqa: ARG002
        return self._cells.get(cell_id)

    async def list_cells(self) -> list[Cell]:
        return sorted(self._cells.values(), key=lambda c: c.cell_id)

    async def set_cell_token(self, cell_id: str, token_hash: bytes) -> None:
        self._cells[cell_id] = replace(self._cells[cell_id], token_hash=token_hash)

    async def set_cell_status(self, cell_id: str, status: CellStatus) -> None:
        self._cells[cell_id] = replace(self._cells[cell_id], status=status)

    async def placed_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for space in self._spaces.values():
            counts[space.cell_id] = counts.get(space.cell_id, 0) + 1
        return counts

    async def add_move(self, move: PlacementMove) -> None:
        self._moves[move.move_id] = move

    async def open_move(self, space_id: UUID) -> PlacementMove | None:
        return next(
            (m for m in self._moves.values() if m.space_id == space_id and m.status is MoveStatus.COPYING),
            None,
        )

    async def moved_from(self, cell_id: str, since: datetime) -> dict[UUID, str]:
        return {
            m.space_id: m.to_cell
            for m in self._moves.values()
            if m.from_cell == cell_id
            and m.status is MoveStatus.COMPLETED
            and m.completed_at is not None
            and m.completed_at >= since
        }

    async def complete_move(self, move_id: UUID, at: datetime) -> None:
        self._moves[move_id] = replace(self._moves[move_id], status=MoveStatus.COMPLETED, completed_at=at)

    async def set_quota(self, quota: Quota) -> None:
        self._quotas[(quota.tenant_id, quota.metric)] = quota

    async def quotas_of(self, tenant_id: UUID) -> list[Quota]:
        return sorted((q for q in self._quotas.values() if q.tenant_id == tenant_id), key=lambda q: q.metric)


class InMemoryAccessRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._sources = _table(store, "sources")
        self._keys = _table(store, "api_keys")
        self._revocations = _table(store, "revocations")
        self._users = _table(store, "users")
        self._bindings = _table(store, "role_bindings")
        self._signing = _table(store, "signing_keys")
        self._recovery = _table(store, "recovery_codes")
        self._resets = _table(store, "second_factor_resets")
        self._invitations = _table(store, "invitations")
        self._changes = _table(store, "access_changes")
        self._preferences = _table(store, "user_preferences")

    async def add_source(self, source: Source) -> None:
        self._sources[source.source_id] = source

    async def get_source(self, source_id: UUID) -> Source | None:
        return self._sources.get(source_id)

    async def sources_of(self, space_id: UUID) -> list[Source]:
        return sorted(
            (s for s in self._sources.values() if s.space_id == space_id), key=lambda s: s.source_id
        )

    async def revoke_source(self, source_id: UUID, at: datetime) -> None:
        self._sources[source_id] = replace(self._sources[source_id], revoked_at=at)

    async def add_key(self, key: ApiKey) -> None:
        if key.key_id in self._keys or any(k.fast_hash == key.fast_hash for k in self._keys.values()):
            raise Conflict("duplicate key")
        self._keys[key.key_id] = key

    async def get_key(self, key_id: str) -> ApiKey | None:
        return self._keys.get(key_id)

    async def keys_of_source(self, source_id: UUID) -> list[ApiKey]:
        return sorted(
            (k for k in self._keys.values() if k.source_id == source_id), key=lambda k: k.created_at
        )

    async def keys_of_spaces(self, space_ids: Sequence[UUID]) -> list[ApiKey]:
        wanted = set(space_ids)
        return sorted((k for k in self._keys.values() if k.space_id in wanted), key=lambda k: k.key_id)

    async def revoke_key(self, key_id: str, at: datetime) -> None:
        key = self._keys[key_id]
        self._keys[key_id] = replace(key, status=KeyStatus.REVOKED, revoked_at=at, version=key.version + 1)

    async def expire_key(self, key_id: str, at: datetime) -> None:
        key = self._keys[key_id]
        self._keys[key_id] = replace(key, expires_at=at, version=key.version + 1)

    async def add_revocation(
        self,
        space_id: UUID,
        kind: RevocationKind,
        source_id: UUID,
        key_id: str | None,
        reason: str,
        at: datetime,
    ) -> Revocation:
        revocation = Revocation(len(self._revocations) + 1, space_id, kind, source_id, at, reason, key_id)
        self._revocations[revocation.seq] = revocation
        return revocation

    async def revocations_after(self, seq: int, space_ids: Sequence[UUID], limit: int) -> list[Revocation]:
        wanted = set(space_ids)
        items = sorted(
            (r for r in self._revocations.values() if r.seq > seq and r.space_id in wanted),
            key=lambda r: r.seq,
        )
        return items[:limit]

    async def add_user(self, user: User) -> None:
        if any(u.email == user.email for u in self._users.values()):
            raise Conflict("the e-mail is taken")
        self._users[user.user_id] = user

    async def get_user(self, user_id: UUID) -> User | None:
        return self._users.get(user_id)

    async def user_by_email(self, email: str) -> User | None:
        return next((u for u in self._users.values() if u.email == email), None)

    async def users_of(self, tenant_id: UUID) -> list[User]:
        return sorted((u for u in self._users.values() if u.tenant_id == tenant_id), key=lambda u: u.email)

    async def touch_login(self, user_id: UUID, at: datetime) -> None:
        self._users[user_id] = replace(self._users[user_id], last_login_at=at)

    async def set_second_factor(
        self, user_id: UUID, secret: str | None, confirmed_at: datetime | None
    ) -> None:
        self._users[user_id] = replace(
            self._users[user_id], totp_secret=secret, totp_confirmed_at=confirmed_at, totp_pending_secret=None
        )

    async def set_pending_second_factor(self, user_id: UUID, secret: str) -> None:
        self._users[user_id] = replace(self._users[user_id], totp_pending_secret=secret)

    async def use_totp_step(self, user_id: UUID, step: int) -> bool:
        user = self._users[user_id]
        if user.totp_last_step is not None and step <= user.totp_last_step:
            return False
        self._users[user_id] = replace(user, totp_last_step=step)
        return True

    async def preferences_of(self, user_id: UUID) -> dict[str, Any]:
        return {key: value for (owner, key), value in sorted(self._preferences.items()) if owner == user_id}

    async def set_preference(self, user_id: UUID, key: str, value: Any) -> None:
        self._preferences[(user_id, key)] = value

    async def recovery_codes_of(self, user_id: UUID) -> list[RecoveryCode]:
        return sorted(
            (c for c in self._recovery.values() if c.user_id == user_id),
            key=lambda c: (c.created_at, c.code_hash),
        )

    async def replace_recovery_codes(self, user_id: UUID, codes: Sequence[RecoveryCode]) -> None:
        for code_hash in [h for h, c in self._recovery.items() if c.user_id == user_id]:
            del self._recovery[code_hash]
        for code in codes:
            if code.code_hash in self._recovery:
                raise Conflict("duplicate recovery code")
            self._recovery[code.code_hash] = code

    async def use_recovery_code(self, user_id: UUID, code_hash: bytes, at: datetime) -> bool:
        code = self._recovery.get(code_hash)
        if code is None or code.user_id != user_id or code.used_at is not None:
            return False
        self._recovery[code_hash] = replace(code, used_at=at)
        return True

    async def add_second_factor_reset(self, reset: SecondFactorReset) -> None:
        self._resets[reset.reset_id] = reset

    async def unused_recovery_codes(self, user_ids: Sequence[UUID]) -> dict[UUID, int]:
        wanted = set(user_ids)
        counts: dict[UUID, int] = {}
        for code in self._recovery.values():
            if code.user_id in wanted and code.used_at is None:
                counts[code.user_id] = counts.get(code.user_id, 0) + 1
        return counts

    async def set_active(self, user_id: UUID, active: bool, at: datetime | None) -> None:
        self._users[user_id] = replace(self._users[user_id], active=active, deactivated_at=at)

    async def set_password(self, user_id: UUID, password_hash: str) -> None:
        self._users[user_id] = replace(self._users[user_id], password_hash=password_hash)

    async def touch_sso_login(self, user_id: UUID, at: datetime) -> None:
        self._users[user_id] = replace(self._users[user_id], last_sso_login_at=at)

    async def add_invitation(self, invitation: Invitation) -> None:
        self._invitations[invitation.invitation_id] = invitation

    async def invitation_by_token(self, token_hash: bytes) -> Invitation | None:
        return next((i for i in self._invitations.values() if i.token_hash == token_hash), None)

    async def open_invitations(self, user_ids: Sequence[UUID], now: datetime) -> dict[UUID, Invitation]:
        wanted = set(user_ids)
        found: dict[UUID, Invitation] = {}
        for i in sorted(self._invitations.values(), key=lambda i: i.created_at):
            if i.user_id in wanted and i.used_at is None and i.expires_at > now:
                found[i.user_id] = i
        return found

    async def close_invitations(self, user_id: UUID, at: datetime) -> None:
        for key, i in list(self._invitations.items()):
            if i.user_id == user_id and i.used_at is None:
                self._invitations[key] = replace(i, used_at=at)

    async def add_access_change(self, change: AccessChange) -> None:
        self._changes[change.change_id] = change

    async def access_changes(self, tenant_id: UUID, user_id: UUID | None, limit: int) -> list[AccessChange]:
        found = [
            c
            for c in self._changes.values()
            if c.tenant_id == tenant_id and (user_id is None or c.user_id == user_id)
        ]
        return sorted(found, key=lambda c: (c.at, c.change_id), reverse=True)[:limit]

    async def access_changes_since(
        self, tenant_ids: Sequence[UUID], after: datetime, limit: int
    ) -> list[AccessChange]:
        wanted = set(tenant_ids)
        found = [c for c in self._changes.values() if c.tenant_id in wanted and c.at > after]
        return sorted(found, key=lambda c: (c.at, c.change_id))[:limit]

    async def set_bindings(self, user_id: UUID, bindings: Sequence[RoleBinding]) -> None:
        for binding_id in [k for k, b in self._bindings.items() if b.user_id == user_id]:
            del self._bindings[binding_id]
        for binding in bindings:
            self._bindings[binding.binding_id] = binding

    async def bindings_of(self, user_id: UUID) -> list[RoleBinding]:
        return sorted(
            (b for b in self._bindings.values() if b.user_id == user_id), key=lambda b: b.binding_id
        )

    async def count_admins(self, tenant_id: UUID) -> int:
        return len(
            {
                b.user_id
                for b in self._bindings.values()
                if b.tenant_id == tenant_id
                and b.role is Role.ADMIN
                and b.space_id is None
                and self._users[b.user_id].active
            }
        )

    async def save_signing_key(self, key: SigningKey) -> None:
        self._signing.setdefault(key.kid, key)

    async def signing_keys(self) -> list[SigningKey]:
        return sorted(self._signing.values(), key=lambda k: k.created_at, reverse=True)


class InMemorySsoRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._connections = _table(store, "sso_connections")
        self._attempts = _table(store, "sso_attempts")

    async def connection_of(self, tenant_id: UUID) -> SsoConnection | None:
        return next((c for c in self._connections.values() if c.tenant_id == tenant_id), None)

    async def connection_for_domain(self, domain: str) -> SsoConnection | None:
        return next((c for c in self._connections.values() if domain in c.domains), None)

    async def save_connection(self, connection: SsoConnection) -> None:
        for other in self._connections.values():
            if other.tenant_id != connection.tenant_id and set(other.domains) & set(connection.domains):
                raise Conflict("another tenant already uses one of the domains")
        for key in [k for k, c in self._connections.items() if c.tenant_id == connection.tenant_id]:
            del self._connections[key]
        self._connections[connection.connection_id] = connection

    async def delete_connection(self, connection_id: UUID) -> None:
        self._connections.pop(connection_id, None)
        for key in [k for k, a in self._attempts.items() if a.connection_id == connection_id]:
            del self._attempts[key]

    async def add_attempt(self, attempt: SsoAttempt) -> None:
        self._attempts[attempt.attempt_id] = attempt

    async def attempt_by_state(self, state_hash: bytes) -> SsoAttempt | None:
        return next((a for a in self._attempts.values() if a.state_hash == state_hash), None)

    async def attempt_by_login_code(self, code_hash: bytes) -> SsoAttempt | None:
        return next((a for a in self._attempts.values() if a.login_code_hash == code_hash), None)

    async def get_attempt(self, attempt_id: UUID) -> SsoAttempt | None:
        return self._attempts.get(attempt_id)

    async def update_attempt(self, attempt: SsoAttempt) -> None:
        self._attempts[attempt.attempt_id] = attempt

    async def pending_attempts(self, connection_id: UUID, since: datetime) -> int:
        return sum(
            1
            for a in self._attempts.values()
            if a.connection_id == connection_id
            and a.status is SsoAttemptStatus.PENDING
            and a.created_at >= since
        )

    async def purge_attempts(self, before: datetime) -> int:
        gone = [k for k, a in self._attempts.items() if a.expires_at < before]
        for key in gone:
            del self._attempts[key]
        return len(gone)


class InMemoryConfigStore:
    def __init__(self, store: MemoryStore) -> None:
        self._diffs = _table(store, "config_diffs")
        self._versions = _table(store, "config_versions")
        self._snapshots = _table(store, "snapshots")
        self._secrets = _table(store, "secret_refs")

    async def add_diff(self, diff: ConfigDiff) -> None:
        self._diffs[diff.diff_id] = diff

    async def get_diff(self, diff_id: UUID) -> ConfigDiff | None:
        return self._diffs.get(diff_id)

    async def update_diff(self, diff: ConfigDiff) -> None:
        self._diffs[diff.diff_id] = diff

    async def diffs_of(
        self, space_id: UUID, status: DiffStatus | None = None, limit: int = 100
    ) -> list[ConfigDiff]:
        found = [d for d in self._diffs.values() if d.space_id == space_id and status in (None, d.status)]
        return sorted(found, key=lambda d: d.diff_id, reverse=True)[:limit]

    async def add_version(self, version: ConfigVersion) -> None:
        key = (version.space_id, version.version)
        if key in self._versions:
            raise Conflict("the version exists")
        self._versions[key] = version

    async def current_version(self, space_id: UUID) -> int:
        return max((v.version for v in self._versions.values() if v.space_id == space_id), default=0)

    async def latest_documents(self, space_id: UUID) -> dict[str, ConfigVersion]:
        latest: dict[str, ConfigVersion] = {}
        for v in sorted(self._versions.values(), key=lambda v: v.version):
            if v.space_id == space_id:
                latest[v.doc_type] = v
        return latest

    async def history(self, space_id: UUID, doc_type: str | None, limit: int) -> list[ConfigVersion]:
        found = [
            v for v in self._versions.values() if v.space_id == space_id and doc_type in (None, v.doc_type)
        ]
        return sorted(found, key=lambda v: v.version, reverse=True)[:limit]

    async def version_before(self, space_id: UUID, doc_type: str, version: int) -> ConfigVersion | None:
        older = [
            v
            for v in self._versions.values()
            if v.space_id == space_id and v.doc_type == doc_type and v.version < version
        ]
        return max(older, key=lambda v: v.version, default=None)

    async def changed_since(self, space_id: UUID, doc_type: str, version: int) -> bool:
        return any(
            v.space_id == space_id and v.doc_type == doc_type and v.version > version
            for v in self._versions.values()
        )

    async def latest_snapshot(self, cell_id: str) -> Snapshot | None:
        return max(
            (s for s in self._snapshots.values() if s.cell_id == cell_id),
            key=lambda s: s.version,
            default=None,
        )

    async def add_snapshot(self, snapshot: Snapshot) -> None:
        key = (snapshot.cell_id, snapshot.version)
        if key in self._snapshots:
            raise Conflict("the snapshot version exists")
        self._snapshots[key] = snapshot

    async def put_secret_ref(self, ref: SecretRef) -> None:
        self._secrets[(ref.space_id, ref.ref)] = ref

    async def secret_refs(self, space_id: UUID) -> list[SecretRef]:
        return sorted((r for r in self._secrets.values() if r.space_id == space_id), key=lambda r: r.ref)


class InMemoryUsageStore:
    def __init__(self, store: MemoryStore) -> None:
        self._rows = _table(store, "usage_aggregates")

    async def upsert(self, aggregates: Sequence[UsageAggregate]) -> None:
        for a in aggregates:
            self._rows[(a.space_id, a.period_start, a.metric)] = a

    async def usage(
        self, space_ids: Sequence[UUID], since: datetime, until: datetime
    ) -> list[UsageAggregate]:
        wanted = set(space_ids)
        found = [a for a in self._rows.values() if a.space_id in wanted and since <= a.period_start < until]
        return sorted(found, key=lambda a: (a.period_start, a.metric, a.space_id))


class InMemoryControlUnitOfWork:
    def __init__(self, store: MemoryStore) -> None:
        self.directory = InMemoryDirectoryRepository(store)
        self.access = InMemoryAccessRepository(store)
        self.config = InMemoryConfigStore(store)
        self.usage = InMemoryUsageStore(store)
        self.sso = InMemorySsoRepository(store)


class InMemoryControlUnitOfWorkFactory:
    def __init__(self, store: MemoryStore | None = None) -> None:
        self.store = store or MemoryStore()

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[InMemoryControlUnitOfWork]:
        snapshot = copy.deepcopy(self.store.data)
        try:
            yield InMemoryControlUnitOfWork(self.store)
        except BaseException:
            self.store.data = snapshot
            raise
