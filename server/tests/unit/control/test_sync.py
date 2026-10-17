"""The cell pulling from the control plane, end to end over HTTP against the control's own router."""

import base64
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.control import ControlContainer, router
from niadra.adapters.outbound.controlplane.client import HttpControlClient
from niadra.app.config import ConfigService
from niadra.app.control import BindingSpec
from niadra.app.control_sync import ControlSync
from niadra.config.models import SnapshotManifest
from niadra.config.validation import AccessFeed, RevocationFeed
from niadra.domain.common.errors import Unavailable
from niadra.domain.config import AppliedSnapshot
from niadra.domain.config.signing import SignedSnapshot, content_hash, parse_json, verify_signed
from niadra.domain.control.records import Role
from niadra.domain.edge.keys import SourceKeyRow, fast_hash, parse_source_key, verify_slow_hash
from niadra.domain.metering import Metric, UsageRow, hour_floor
from tests.unit.control.conftest import PASSWORD, PEPPER, World


class FakeConfig:
    """Checks what `ConfigService.apply` checks, without a cell database; revocations use the real one."""

    def __init__(self, keys: list[Ed25519PublicKey], cell_id: str) -> None:
        self._keys = keys
        self._real = ConfigService(None, keys, cell_id, "1.0")  # type: ignore[arg-type]
        self.applied: list[SnapshotManifest] = []

    async def apply(self, snapshot: SignedSnapshot) -> AppliedSnapshot:
        manifest = SnapshotManifest.model_validate(
            verify_signed(snapshot.manifest, snapshot.signature, self._keys)
        )
        for digest in manifest.spaces.values():
            assert content_hash(parse_json(snapshot.documents[digest], "doc")) == digest
        self.applied.append(manifest)
        return AppliedSnapshot(manifest.cell_id, manifest.version, len(manifest.spaces))

    async def active_version(self) -> int | None:
        return self.applied[-1].version if self.applied else None

    def verify_revocations(self, body: bytes, signature: bytes) -> RevocationFeed:
        return self._real.verify_revocations(body, signature)


class FakeEdge:
    def __init__(self) -> None:
        self.rows: dict[str, SourceKeyRow] = {}
        self.revoked: list[str] = []

    async def mirror_keys(self, space_id: UUID, rows: Sequence[SourceKeyRow]) -> list[SourceKeyRow]:
        changed = [r for r in rows if r.key_id not in self.rows or self.rows[r.key_id].version < r.version]
        self.rows.update({r.key_id: r for r in rows})
        return changed

    async def revoke_local(self, key_id: str) -> None:
        self.revoked.append(key_id)


@pytest.fixture
async def sync(world: World) -> AsyncIterator[tuple[ControlSync, FakeConfig, FakeEdge]]:
    app = create_app("niadra-control", ControlContainer(world.service, "cell-secret", "op"), [router])
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://control.test")
    jwk = (await world.service.jwks())["keys"][0]
    public = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(jwk["x"] + "=="))
    config, edge = FakeConfig([public], world.boot.space.cell_id), FakeEdge()
    client = HttpControlClient("https://control.test", "cell-secret", http)
    yield ControlSync(client, config, edge, [public], world.clock, world.boot.space.cell_id), config, edge
    await client.aclose()


async def test_the_cell_applies_the_snapshot_and_mirrors_keys_the_edge_can_verify(
    sync: tuple[ControlSync, FakeConfig, FakeEdge], world: World
) -> None:
    control_sync, config, edge = sync
    version = await control_sync.pull_snapshot(wait_s=0)
    assert version == config.applied[-1].version
    assert await control_sync.pull_snapshot(wait_s=0) is None

    parsed = parse_source_key(world.boot.keys["whatsapp"].plaintext)
    row = edge.rows[parsed.key_id]
    assert row.fast_hash == fast_hash(PEPPER, parsed.key_id, parsed.secret)
    assert verify_slow_hash(row.slow_hash, parsed.key_id, parsed.secret)
    assert row.space_id == world.boot.space.space_id
    assert not row.revoked


async def test_revocations_and_expired_rotations_reach_the_edge(
    sync: tuple[ControlSync, FakeConfig, FakeEdge], world: World
) -> None:
    control_sync, _, edge = sync
    admin = await world.admin()
    whatsapp, billing = world.boot.keys["whatsapp"].key, world.boot.keys["billing"].key
    await world.service.revoke_key(admin, whatsapp.source_id, whatsapp.key_id, "leaked in a log")
    await world.service.rotate_key(admin, billing.source_id, billing.key_id, grace_s=600)
    await control_sync.pull_snapshot(wait_s=0)
    assert edge.rows[whatsapp.key_id].revoked
    assert edge.rows[whatsapp.key_id].version == 2

    assert await control_sync.pull_revocations() == 1
    assert edge.revoked == [whatsapp.key_id]
    assert await control_sync.pull_revocations() == 0

    world.clock.advance(minutes=11)
    assert await control_sync.pull_revocations() == 1
    assert edge.revoked[-1] == billing.key_id


class FakeUsage:
    """The cell's hourly usage, as `MeteringService.usage` returns it (summed over sources)."""

    def __init__(self, rows: dict[UUID, list[UsageRow]]) -> None:
        self.rows = rows
        self.asked: list[tuple[UUID, datetime, datetime]] = []

    async def usage(self, space_id: UUID, since: datetime, until: datetime) -> list[UsageRow]:
        self.asked.append((space_id, since, until))
        if space_id not in self.rows:
            raise Unavailable("the space's database is being created")
        return [r for r in self.rows[space_id] if since <= r.hour < until]


async def test_the_cell_pushes_its_hourly_usage_and_the_control_plane_totals_it(world: World) -> None:
    # The tenant's totals in the control plane came only from a push no cell made.
    app = create_app("niadra-control", ControlContainer(world.service, "cell-secret", "op"), [router])
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://control.test")
    client = HttpControlClient("https://control.test", "cell-secret", http)
    space = world.boot.space.space_id
    hour = hour_floor(world.clock.now())
    usage = FakeUsage(
        {
            space: [
                UsageRow(hour - timedelta(hours=1), Metric.SESSION_BILLABLE.value, 7, 0),
                UsageRow(hour, Metric.SESSION_BILLABLE.value, 3, 0),
                UsageRow(hour, Metric.EXTRACTION_RUN.value, 2, 900),
                UsageRow(hour - timedelta(days=5), Metric.SESSION_BILLABLE.value, 99, 0),  # long sent
            ]
        }
    )
    restoring = uuid4()
    sync = ControlSync(
        client,
        FakeConfig([], world.boot.space.cell_id),
        FakeEdge(),
        [],
        world.clock,
        world.boot.space.cell_id,
        usage=usage,
        spaces=lambda: [space, restoring],
    )
    try:
        assert await sync.push_usage() == 3
        usage.rows[space][1] = UsageRow(hour, Metric.SESSION_BILLABLE.value, 5, 0)  # the hour went on
        assert await sync.push_usage() == 3
    finally:
        await client.aclose()
    admin = await world.admin()
    report = await world.service.usage(admin, hour - timedelta(days=1), hour + timedelta(hours=1))
    assert sorted((a.period_start, a.metric, a.quantity, a.cost_micros) for a in report.aggregates) == [
        (hour - timedelta(hours=1), Metric.SESSION_BILLABLE.value, 7, 0),
        (hour, Metric.EXTRACTION_RUN.value, 2, 900),
        (hour, Metric.SESSION_BILLABLE.value, 5, 0),
    ]
    assert {asked[0] for asked in usage.asked} == {space, restoring}


async def test_without_a_usage_source_nothing_is_pushed(
    sync: tuple[ControlSync, FakeConfig, FakeEdge],
) -> None:
    control_sync, _, _ = sync
    assert await control_sync.push_usage() == 0


async def test_the_control_planes_changes_reach_the_spaces_receipt_chain(world: World) -> None:
    # Keys issued and revoked, and a configuration applied, leave an admin receipt in the space.
    app = create_app("niadra-control", ControlContainer(world.service, "cell-secret", "op"), [router])
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://control.test")
    jwk = (await world.service.jwks())["keys"][0]
    public = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(jwk["x"] + "=="))
    config, edge = FakeConfig([public], world.boot.space.cell_id), FakeEdge()
    recorded: list[tuple[UUID, str, str]] = []

    async def audit(space_id: UUID, action: str, target: str) -> None:
        recorded.append((space_id, action, target))

    client = HttpControlClient("https://control.test", "cell-secret", http)
    sync = ControlSync(client, config, edge, [public], world.clock, world.boot.space.cell_id, audit=audit)
    await sync.pull_snapshot(wait_s=0)
    space = world.boot.space.space_id
    issued = {t for s, a, t in recorded if a == "key.issued" and s == space}
    whatsapp = world.boot.keys["whatsapp"].key
    # The receipt names the source the key belongs to, not only the key.
    assert f"key:{whatsapp.key_id}:source:{whatsapp.source_id}" in issued
    recorded.clear()
    admin = await world.admin()
    await world.service.revoke_key(admin, whatsapp.source_id, whatsapp.key_id, "leaked in a log")
    await sync.pull_snapshot(wait_s=0)
    assert (space, "key.revoked", f"key:{whatsapp.key_id}:source:{whatsapp.source_id}") in recorded
    # Nothing else changed: no receipt for the keys that stayed as they were.
    assert all(a == "key.revoked" for _, a, _ in recorded)
    await client.aclose()


class FakeAccess:
    """The cursor the cell keeps in `cell_state`, and the real signature check."""

    def __init__(self, config: FakeConfig) -> None:
        self._config = config
        self.cursor: datetime | None = None

    def verify_access_feed(self, body: bytes, signature: bytes) -> AccessFeed:
        return self._config._real.verify_access_feed(body, signature)

    async def access_cursor(self) -> datetime | None:
        return self.cursor

    async def save_access_cursor(self, at: datetime) -> None:
        self.cursor = at


async def test_role_and_sso_changes_reach_every_space_of_the_tenant_once(world: World) -> None:
    # People's roles and the tenant's SSO are the tenant's, and each of its spaces records them.
    app = create_app("niadra-control", ControlContainer(world.service, "cell-secret", "op"), [router])
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://control.test")
    jwk = (await world.service.jwks())["keys"][0]
    public = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(jwk["x"] + "=="))
    config, edge = FakeConfig([public], world.boot.space.cell_id), FakeEdge()
    access = FakeAccess(config)
    recorded: list[tuple[UUID, str, str]] = []

    async def audit(space_id: UUID, action: str, target: str) -> None:
        recorded.append((space_id, action, target))

    space, tenant = world.boot.space.space_id, world.boot.space.tenant_id
    other_tenant_space = uuid4()
    client = HttpControlClient("https://control.test", "cell-secret", http)
    sync = ControlSync(
        client,
        config,
        edge,
        [public],
        world.clock,
        world.boot.space.cell_id,
        audit=audit,
        access=access,
        space_tenants=lambda: [(space, tenant), (other_tenant_space, uuid4())],
    )
    admin = await world.admin()
    rui = await world.service.create_user(admin, "rui@acme.com", PASSWORD, [BindingSpec(Role.REVIEW)])
    # The first pull starts the feed now: what happened before this cell listened stays in the control plane.
    assert await sync.pull_access_changes() == 0
    assert access.cursor is not None
    world.clock.advance(seconds=5)
    rui_id = rui.user.user_id
    await world.service.set_roles(admin, rui_id, [BindingSpec(Role.SECURITY)], "moved to the DPO team")
    assert await sync.pull_access_changes() == 1
    [(where, action, target)] = recorded
    assert (where, action) == (space, "access.roles_changed")
    assert target.startswith("access_change:")
    assert target.endswith(f":user:{rui_id}")
    assert "DPO" not in target
    # Pulled again, nothing is written twice.
    assert await sync.pull_access_changes() == 0
    assert len(recorded) == 1
    await client.aclose()
