"""The control repositories against PostgreSQL, on a database of their own (the control never shares one)."""

import asyncio
import hashlib
from datetime import timedelta

import psycopg
import pytest

from niadra.adapters.memory.fakes import FixedClock
from niadra.app.control import BindingSpec, BootstrapResult, ControlService, IssuedToken, SourceSpec
from niadra.config.starter import STARTER_POLICY_VERSION
from niadra.domain.common.errors import Conflict, Forbidden, Unauthenticated
from niadra.domain.control.credentials import totp_code
from niadra.domain.control.records import DiffStatus, KeyStatus, Role, UsageAggregate
from niadra.domain.edge.keys import parse_source_key, verify_slow_hash
from niadra.domain.spaces.directory import Tier
from niadra.domain.vocabulary import Audience, Environment

PASSWORD = "correct horse battery staple"


async def _bootstrap(service: ControlService) -> BootstrapResult:
    await service.ensure_signing_key()
    return await service.bootstrap(
        tenant_name="Acme",
        tenant_slug="acme",
        email="founder@acme.com",
        region="sa-east-1",
        cell_id="sa-east-1-a",
        cell_address="https://cell-a.test",
        password=PASSWORD,
        sources=[
            SourceSpec("whatsapp", Audience.CUSTOMER_AGENT),
            SourceSpec("billing", Audience.INTERNAL_AGENT),
        ],
    )


async def test_bootstrap_login_and_snapshot_round_trip(service: tuple[ControlService, FixedClock]) -> None:
    control, clock = service
    boot = await _bootstrap(control)
    parsed = parse_source_key(boot.keys["whatsapp"].plaintext)
    assert verify_slow_hash(boot.keys["whatsapp"].key.slow_hash, parsed.key_id, parsed.secret)

    assert boot.admin.totp_secret is not None
    token = await control.login(
        "founder@acme.com", PASSWORD, totp_code(boot.admin.totp_secret, clock.now().timestamp()), None
    )
    caller = control.authenticate(token.token)
    assert caller.roles == {Role.ADMIN}
    assert caller.space_id == boot.space.space_id

    snapshot = await control.snapshot("sa-east-1-a")
    assert set(snapshot.manifest["spaces"]) == {
        str(s.space_id) for p in await control.projects(caller) for s in p.spaces
    }
    assert len(snapshot.keys[str(boot.space.space_id)]) == 2
    assert (await control.jwks())["keys"][0]["kid"] == snapshot.kid


async def test_uniqueness_is_enforced_without_breaking_the_transaction(
    service: tuple[ControlService, FixedClock],
) -> None:
    control, _ = service
    await _bootstrap(control)
    with pytest.raises(Conflict):
        await control.create_tenant("Acme again", "acme")
    tenant = await control.create_tenant("Other", "other")
    assert tenant.slug == "other"


async def test_diffs_versions_and_rollback(service: tuple[ControlService, FixedClock]) -> None:
    control, clock = service
    boot = await _bootstrap(control)
    assert boot.admin.totp_secret
    admin = control.authenticate(
        (
            await control.login(
                "founder@acme.com", PASSWORD, totp_code(boot.admin.totp_secret, clock.now().timestamp()), None
            )
        ).token
    )
    second = await control.create_user(admin, "second@acme.com", PASSWORD, [BindingSpec(Role.ADMIN)])
    assert second.totp_secret
    other = control.authenticate(
        (
            await control.login(
                "second@acme.com", PASSWORD, totp_code(second.totp_secret, clock.now().timestamp()), None
            )
        ).token
    )
    # Four eyes hold on a production space; a sandbox's author approves their own.
    [view] = await control.projects(admin)
    space = next(s.space_id for s in view.spaces if s.environment is Environment.PRODUCTION)
    policy = {"version": "p2", "rules": []}

    diff = await control.submit_diff(admin, space, "policies", policy, "tighten the defaults")
    with pytest.raises(Forbidden):
        await control.approve_diff(admin, diff.diff_id)
    applied = await control.approve_diff(other, diff.diff_id)
    assert applied.status is DiffStatus.APPLIED
    assert applied.applied_version == 1
    assert (await control.config_document(admin, space, "policies")) == (
        1,
        {**policy, "categories": [], "competes_with": {}, "compatible_purposes": {}},
    )

    pending = await control.rollback_diff(admin, diff.diff_id, "back to the default policy")
    # A rollback is a diff: with a second admin, it waits for them.
    assert pending.status is DiffStatus.PENDING
    rollback = await control.approve_diff(other, pending.diff_id)
    version, document = await control.config_document(admin, space, "policies")
    assert version == 2
    assert document["version"] == STARTER_POLICY_VERSION  # the rollback returns to the starter policy
    assert [v.version for v in await control.history(admin, space, None)] == [2, 1]
    assert [d.diff_id for d in await control.list_diffs(admin, space, DiffStatus.APPLIED)] == [
        rollback.diff_id,
        diff.diff_id,
    ]


async def test_revocations_rotation_and_usage(service: tuple[ControlService, FixedClock]) -> None:
    control, clock = service
    boot = await _bootstrap(control)
    assert boot.admin.totp_secret
    admin = control.authenticate(
        (
            await control.login(
                "founder@acme.com", PASSWORD, totp_code(boot.admin.totp_secret, clock.now().timestamp()), None
            )
        ).token
    )
    key = boot.keys["billing"].key
    await control.rotate_key(admin, key.source_id, key.key_id, grace_s=60)
    keys = await control.list_keys(admin, key.source_id)
    assert len(keys) == 2
    assert keys[0].expires_at == clock.now() + timedelta(seconds=60)

    await control.revoke_source(admin, key.source_id, "vendor contract ended")
    assert {k.status for k in await control.list_keys(admin, key.source_id)} == {KeyStatus.REVOKED}
    feed = await control.revocations("sa-east-1-a", after=0)
    assert feed.body["sources"] == [str(key.source_id)]
    assert {k["key_id"] for k in feed.body["keys"]} == {k.key_id for k in keys}

    now = clock.now()
    rows = [UsageAggregate(boot.space.space_id, now, "tasks", n, "", now) for n in (3, 5)]
    await control.push_usage("sa-east-1-a", rows[:1])
    await control.push_usage("sa-east-1-a", rows[1:])
    report = await control.usage(admin, now - timedelta(days=1), now + timedelta(days=1))
    assert [(a.metric, a.quantity, a.cell_id) for a in report.aggregates] == [("tasks", 5, "sa-east-1-a")]


async def test_placement_moves(service: tuple[ControlService, FixedClock]) -> None:
    control, _ = service
    boot = await _bootstrap(control)
    await control.register_cell("sa-east-1-b", "sa-east-1", "https://cell-b.test", 10)
    await control.request_move(boot.space.space_id, "sa-east-1-b", "rebalance the region")
    with pytest.raises(Conflict):
        await control.request_move(boot.space.space_id, "sa-east-1-b", "twice")
    move = await control.complete_move(boot.space.space_id)
    assert move.to_cell == "sa-east-1-b"
    snapshot = await control.snapshot("sa-east-1-b")
    assert list(snapshot.manifest["spaces"]) == [str(boot.space.space_id)]
    assert str(boot.space.space_id) not in (await control.snapshot("sa-east-1-a")).manifest["spaces"]


async def test_a_dedicated_cell_and_its_placed_spaces_round_trip(
    service: tuple[ControlService, FixedClock], control_dsn: str
) -> None:
    # The cell keeps its tenant and the hash of its own credential; the spaces keep their tier and
    # database instance, and the snapshot carries both to the cell.
    control, _ = service
    await _bootstrap(control)
    tenant = await control.tenant_by_slug("acme")
    token_hash = hashlib.sha256(b"dedicated-secret").digest()
    await control.register_cell(
        "niadra-acme",
        "sa-east-1",
        "https://niadra-acme.test",
        10,
        tenant_id=tenant.tenant_id,
        token_sha256=token_hash,
    )
    assert await control.cell_credential("niadra-acme") == token_hash
    view = await control.place_project("acme", "Regulated", "acme-reg", "sa-east-1", "niadra-acme", "acme-db")
    assert {(s.cell_id, s.tier, s.instance) for s in view.spaces} == {("niadra-acme", Tier.T2, "acme-db")}
    assert {(s.tier, s.instance) for s in await control.routes("niadra-acme")} == {(Tier.T2, "acme-db")}
    snapshot = await control.snapshot("niadra-acme")
    assert {(d["tier"], d["instance"]) for d in snapshot.documents.values()} == {("t2", "acme-db")}
    rows = _sql(control_dsn, "SELECT tenant_id, token_hash FROM cells WHERE cell_id = %s", "niadra-acme")
    assert rows == [(tenant.tenant_id, token_hash)]
    # The spaces of before keep the defaults the migration gave them.
    assert _sql(
        control_dsn, "SELECT DISTINCT tier, instance FROM spaces WHERE cell_id = %s", "sa-east-1-a"
    ) == [("t0", "primary")]
    await control.rekey_cell("niadra-acme", hashlib.sha256(b"next").digest())
    assert await control.cell_credential("niadra-acme") == hashlib.sha256(b"next").digest()
    with pytest.raises(Conflict):
        await control.close_cell("niadra-acme")


def _sql(dsn: str, query: str, *params: object) -> list[tuple[object, ...]]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(query, params).fetchall()


async def test_recovery_codes_are_hashed_single_use_and_replaced(
    service: tuple[ControlService, FixedClock], control_dsn: str
) -> None:
    control, clock = service
    boot = await _bootstrap(control)
    assert boot.admin.totp_secret
    first = await control.login(
        "founder@acme.com", PASSWORD, totp_code(boot.admin.totp_secret, clock.now().timestamp()), None
    )
    codes = first.recovery_codes
    assert codes is not None
    assert len(codes) == 10
    caller = control.authenticate(first.token)

    rows = _sql(control_dsn, "SELECT code_hash, used_at FROM recovery_codes")
    assert len(rows) == 10
    assert all(isinstance(h, bytes) and len(h) == 32 and used is None for h, used in rows)
    assert not any(c.encode() in bytes(h) for c in codes for h, _ in rows)

    assert (
        await control.login("founder@acme.com", PASSWORD, None, None, recovery_code=codes[0].lower())
    ).recovery_codes_left == 9
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await control.login("founder@acme.com", PASSWORD, None, None, recovery_code=codes[0])
    assert _sql(control_dsn, "SELECT count(*) FROM recovery_codes WHERE used_at IS NOT NULL") == [(1,)]

    fresh = await control.regenerate_recovery_codes(caller, PASSWORD)
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await control.login("founder@acme.com", PASSWORD, None, None, recovery_code=codes[1])
    assert (await control.second_factor_status(caller)).recovery_codes_left == 10
    assert _sql(control_dsn, "SELECT count(*), count(used_at) FROM recovery_codes") == [(10, 0)]

    # Two logins racing with the same code: exactly one gets in.
    raced = await asyncio.gather(
        control.login("founder@acme.com", PASSWORD, None, None, recovery_code=fresh[0]),
        control.login("founder@acme.com", PASSWORD, None, None, recovery_code=fresh[0]),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in raced) == ["IssuedToken", "Unauthenticated"]
    assert next(r for r in raced if isinstance(r, IssuedToken)).recovery_codes_left == 9


async def test_changing_the_app_and_the_admin_reset(
    service: tuple[ControlService, FixedClock], control_dsn: str
) -> None:
    control, clock = service
    boot = await _bootstrap(control)
    assert boot.admin.totp_secret

    def code(secret: str | None) -> str:
        assert secret
        return totp_code(secret, clock.now().timestamp())

    admin = control.authenticate(
        (await control.login("founder@acme.com", PASSWORD, code(boot.admin.totp_secret), None)).token
    )
    rui = await control.create_user(admin, "rui@acme.com", PASSWORD, [BindingSpec(Role.SECURITY)])
    person = control.authenticate(
        (await control.login("rui@acme.com", PASSWORD, code(rui.totp_secret), None)).token
    )
    user_row = "SELECT totp_secret, totp_confirmed_at, totp_pending_secret FROM users WHERE user_id = %s"

    new = await control.start_second_factor_change(person, PASSWORD)
    assert _sql(control_dsn, user_row, rui.user.user_id) == [(rui.totp_secret, clock.now(), new.secret)]
    clock.advance(minutes=2)
    assert len(await control.confirm_second_factor_change(person, code(new.secret))) == 10
    assert _sql(control_dsn, user_row, rui.user.user_id) == [(new.secret, clock.now(), None)]
    # The code that confirmed the new app is spent: the login takes the next one.
    clock.advance(seconds=30)
    assert await control.login("rui@acme.com", PASSWORD, code(new.secret), None)

    reset = await control.reset_second_factor(admin, rui.user.user_id, "lost the phone and the codes")
    assert _sql(control_dsn, user_row, rui.user.user_id) == [(None, None, None)]
    assert _sql(control_dsn, "SELECT count(*) FROM recovery_codes WHERE user_id = %s", rui.user.user_id) == [
        (0,)
    ]
    assert _sql(
        control_dsn, "SELECT reset_id, tenant_id, user_id, reset_by, reason, at FROM second_factor_resets"
    ) == [
        (
            reset.reset_id,
            boot.tenant.tenant_id,
            rui.user.user_id,
            admin.user_id,
            "lost the phone and the codes",
            clock.now(),
        )
    ]
    with pytest.raises(Unauthenticated, match="enrollment required"):
        await control.login("rui@acme.com", PASSWORD, None, None)
    # The founder's codes are untouched, and they leave with the person.
    assert _sql(control_dsn, "SELECT count(*) FROM recovery_codes WHERE user_id = %s", admin.user_id) == [
        (10,)
    ]
    with psycopg.connect(control_dsn) as conn:
        conn.execute("DELETE FROM second_factor_resets")
        conn.execute("DELETE FROM access_changes")
        conn.execute("DELETE FROM users WHERE user_id = %s", (admin.user_id,))
    assert _sql(control_dsn, "SELECT count(*) FROM recovery_codes") == [(0,)]


async def test_an_authenticator_code_signs_in_once_on_postgres(
    service: tuple[ControlService, FixedClock], control_dsn: str
) -> None:
    control, clock = service
    boot = await _bootstrap(control)
    assert boot.admin.totp_secret
    code = totp_code(boot.admin.totp_secret, clock.now().timestamp())
    assert await control.login("founder@acme.com", PASSWORD, code, None)
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await control.login("founder@acme.com", PASSWORD, code, None)
    step = int(clock.now().timestamp() // 30)
    assert _sql(control_dsn, "SELECT totp_last_step FROM users WHERE email = %s", "founder@acme.com") == [
        (step,)
    ]

    # Two logins racing with the next code: the compare-and-set on the row lets exactly one in.
    clock.advance(seconds=30)
    fresh = totp_code(boot.admin.totp_secret, clock.now().timestamp())
    raced = await asyncio.gather(
        control.login("founder@acme.com", PASSWORD, fresh, None),
        control.login("founder@acme.com", PASSWORD, fresh, None),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in raced) == ["IssuedToken", "Unauthenticated"]
    assert _sql(control_dsn, "SELECT totp_last_step FROM users WHERE email = %s", "founder@acme.com") == [
        (step + 1,)
    ]


async def test_preferences_are_kept_per_person_and_go_with_them(
    service: tuple[ControlService, FixedClock], control_dsn: str
) -> None:
    control, clock = service
    boot = await _bootstrap(control)
    assert boot.admin.totp_secret
    token = await control.login(
        "founder@acme.com", PASSWORD, totp_code(boot.admin.totp_secret, clock.now().timestamp()), None
    )
    caller = control.authenticate(token.token)

    await control.set_preference(caller, "tutorials", {"done": {"tour": "2026-10-01"}})
    await control.set_preference(caller, "welcome_seen", True)
    kept = await control.set_preference(caller, "tutorials", {"done": {}})

    assert kept == await control.preferences(caller) == {"tutorials": {"done": {}}, "welcome_seen": True}
    assert _sql(control_dsn, "SELECT key, value FROM user_preferences ORDER BY key") == [
        ("tutorials", {"done": {}}),
        ("welcome_seen", True),
    ]
    _sql_do(control_dsn, "DELETE FROM role_bindings")
    _sql_do(control_dsn, "DELETE FROM recovery_codes")
    _sql_do(control_dsn, "DELETE FROM users")
    assert _sql(control_dsn, "SELECT count(*) FROM user_preferences") == [(0,)]


def _sql_do(dsn: str, statement: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(statement)
