import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import jwt
import pytest
import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from niadra.adapters.memory.control import InMemoryControlUnitOfWorkFactory
from niadra.adapters.memory.fakes import FixedClock
from niadra.adapters.outbound.keys.ed25519 import Ed25519Signer
from niadra.app.control import AGENT_SCOPES, BindingSpec, ControlService, SourceSpec
from niadra.config.models import SnapshotManifest, SpaceConfig, SpaceLimits
from niadra.domain.common.errors import (
    Conflict,
    Forbidden,
    InvalidInput,
    NotFound,
    Unauthenticated,
    VerificationNotAllowed,
)
from niadra.domain.control.records import Caller, DiffStatus, KeyStatus, Role, UsageAggregate
from niadra.domain.control.rules import canonical_json
from niadra.domain.edge.keys import fast_hash, parse_source_key, verify_slow_hash
from niadra.domain.vocabulary import Audience, Environment, Scope, Verification
from tests.unit.control.conftest import PASSWORD, PEPPER, World

# The sectors' type declarations, shared with the registry's own tests.
DECLARATIONS = Path(__file__).resolve().parents[1] / "state" / "declarations"
POLICY = {"version": "p2", "rules": [{"effect": "deny", "when": {"sensitivity": "sensitive"}}]}


def _public_key(jwks: dict[str, object], kid: str) -> Ed25519PublicKey:
    keys = jwks["keys"]
    assert isinstance(keys, list)
    jwk = next(k for k in keys if k["kid"] == kid)
    return Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(jwk["x"] + "=="))


async def _second_admin(world: World, admin: Caller) -> Caller:
    other = await world.service.create_user(admin, "second@acme.com", PASSWORD, [BindingSpec(Role.ADMIN)])
    token = await world.service.login("second@acme.com", PASSWORD, world.totp(other.totp_secret), None)
    return world.service.authenticate(token.token)


# Bootstrap, keys and snapshot


async def test_bootstrap_names_the_project_apart_from_the_company() -> None:
    clock, uow = FixedClock(), InMemoryControlUnitOfWorkFactory()
    service = ControlService(uow, Ed25519Signer(Ed25519PrivateKey.generate()), clock, PEPPER)
    boot = await service.bootstrap(
        tenant_name="Niadra",
        tenant_slug="niadra",
        project_name="Atendimento e cobrança",
        project_slug="atendimento",
        email="e2e@niadra.com",
        region="us-east-2",
        cell_id="us-east-2-a",
        cell_address="https://cell-a.us-east-2.niadra.test",
        password=PASSWORD,
    )
    assert (boot.tenant.name, boot.project.name, boot.project.slug) == (
        "Niadra",
        "Atendimento e cobrança",
        "atendimento",
    )
    assert boot.space.slug == "atendimento-sandbox"


async def test_bootstrap_issues_parseable_keys_whose_hashes_match_the_snapshot(world: World) -> None:
    issued = world.boot.keys["whatsapp"]
    parsed = parse_source_key(issued.plaintext)
    assert (parsed.environment, parsed.region, parsed.space_slug) == (
        Environment.SANDBOX,
        "sa-east-1",
        "acme-sandbox",
    )
    assert parsed.key_id == issued.key.key_id
    assert issued.key.fast_hash == fast_hash(PEPPER, parsed.key_id, parsed.secret)
    assert verify_slow_hash(issued.key.slow_hash, parsed.key_id, parsed.secret)
    # Zero touch: an agent's key made with no scopes works for every agent call, never admin.
    assert issued.key.scopes == AGENT_SCOPES
    assert issued.key.scopes.isdisjoint({Scope.ADMIN, Scope.ANALYTICS, Scope.IDENTITY_LINK, Scope.STATE_PUSH})
    assert world.boot.keys["billing"].key.scopes == AGENT_SCOPES

    snapshot = await world.service.snapshot(world.boot.space.cell_id)
    entries = snapshot.keys[str(world.boot.space.space_id)]
    entry = next(e for e in entries if e["key_id"] == parsed.key_id)
    assert entry["fast_hash"] == issued.key.fast_hash.hex()
    assert parsed.secret not in str(snapshot.keys)


async def test_the_snapshot_is_signed_and_its_documents_match_the_manifest(world: World) -> None:
    snapshot = await world.service.snapshot(world.boot.space.cell_id)
    public = _public_key(await world.service.jwks(), snapshot.kid)

    public.verify(snapshot.signature, canonical_json(snapshot.manifest))
    public.verify(snapshot.keys_signature, canonical_json(snapshot.keys))
    manifest = SnapshotManifest.model_validate(snapshot.manifest)
    assert set(manifest.spaces) == {str(world.boot.space.space_id), *[str(s) for s in _production_ids(world)]}
    for digest in manifest.spaces.values():
        assert hashlib.sha256(canonical_json(snapshot.documents[digest])).hexdigest() == digest
    sandbox = snapshot.documents[manifest.spaces[str(world.boot.space.space_id)]]
    assert sorted(s["name"] for s in sandbox["sources"]) == ["billing", "voice", "whatsapp"]

    tampered = {**snapshot.manifest, "version": snapshot.manifest["version"] + 1}
    with pytest.raises(InvalidSignature):
        public.verify(snapshot.signature, canonical_json(tampered))


def _production_ids(world: World) -> list[object]:
    spaces = world.uow.store.data["control"]["spaces"].values()
    return [s.space_id for s in spaces if s.environment is Environment.PRODUCTION]


async def test_a_new_snapshot_version_appears_only_when_something_changed(world: World) -> None:
    cell = world.boot.space.cell_id
    first = await world.service.snapshot(cell)
    assert (await world.service.snapshot(cell)).version == first.version
    admin = await world.admin()
    await world.service.issue_key(admin, world.boot.keys["voice"].key.source_id)
    assert (await world.service.snapshot(cell)).version == first.version + 1
    assert (await world.service.wait_snapshot(cell, after=first.version, wait_s=0)) is not None
    assert (await world.service.wait_snapshot(cell, after=first.version + 1, wait_s=0)) is None


async def test_analytics_scope_is_never_granted_to_an_agent_source(world: World) -> None:
    admin = await world.admin()
    with pytest.raises(Forbidden):
        await world.service.issue_key(
            admin, world.boot.keys["whatsapp"].key.source_id, frozenset({Scope.ANALYTICS})
        )
    analyst = await world.service.create_source(
        admin, world.boot.space.space_id, SourceSpec("bi-copilot", Audience.ANALYST)
    )
    issued = await world.service.issue_key(admin, analyst.source_id)
    assert issued.key.scopes == {Scope.ANALYTICS}


async def test_identity_link_is_never_granted_to_a_customer_facing_source(world: World) -> None:
    # A link opens an organization's memory to a person: a customer's agent never makes one.
    admin = await world.admin()
    customer_facing = world.boot.keys["whatsapp"].key.source_id
    with pytest.raises(Forbidden, match="identity:link"):
        await world.service.issue_key(admin, customer_facing, frozenset({Scope.IDENTITY_LINK, Scope.TRACK}))
    crm = await world.service.create_source(
        admin, world.boot.space.space_id, SourceSpec("crm-sync", Audience.INTERNAL_AGENT)
    )
    issued = await world.service.issue_key(admin, crm.source_id, frozenset({Scope.IDENTITY_LINK}))
    assert issued.key.scopes == {Scope.IDENTITY_LINK}


async def test_a_customer_agent_source_cannot_be_created_with_a_v4_ceiling(world: World) -> None:
    admin = await world.admin()
    spec = SourceSpec("rogue-bot", Audience.CUSTOMER_AGENT, verification_ceiling=Verification.V4)
    with pytest.raises(VerificationNotAllowed):
        await world.service.create_source(admin, world.boot.space.space_id, spec)


# Revocation


async def test_revoking_a_key_feeds_the_signed_revocation_feed_of_its_cell(world: World) -> None:
    admin = await world.admin()
    key = world.boot.keys["whatsapp"].key
    revocation = await world.service.revoke_key(admin, key.source_id, key.key_id, "vendor left")
    feed = await world.service.revocations(world.boot.space.cell_id, after=0)

    assert feed.body["keys"] == [{"space_id": str(key.space_id), "key_id": key.key_id}]
    assert feed.body["seq"] == revocation.seq
    assert feed.body["cell_id"] == world.boot.space.cell_id
    _public_key(await world.service.jwks(), feed.kid).verify(feed.signature, canonical_json(feed.body))
    later = await world.service.revocations(world.boot.space.cell_id, after=feed.body["seq"])
    assert later.body["keys"] == []
    assert later.body["seq"] == feed.body["seq"]
    with pytest.raises(Conflict):
        await world.service.revoke_key(admin, key.source_id, key.key_id, "again")
    snapshot = await world.service.snapshot(world.boot.space.cell_id)
    entry = next(e for e in snapshot.keys[str(key.space_id)] if e["key_id"] == key.key_id)
    assert entry["status"] == "revoked"


async def test_revoking_a_source_cuts_every_key_and_drops_it_from_the_config(world: World) -> None:
    admin = await world.admin()
    source_id = world.boot.keys["voice"].key.source_id
    await world.service.issue_key(admin, source_id)
    await world.service.revoke_source(admin, source_id, "contract ended")

    keys = await world.service.list_keys(admin, source_id)
    assert {k.status for k in keys} == {KeyStatus.REVOKED}
    feed = await world.service.revocations(world.boot.space.cell_id, after=0)
    assert feed.body["sources"] == [str(source_id)]
    assert {k["key_id"] for k in feed.body["keys"]} == {k.key_id for k in keys}
    snapshot = await world.service.snapshot(world.boot.space.cell_id)
    sandbox = snapshot.documents[snapshot.manifest["spaces"][str(world.boot.space.space_id)]]
    assert "voice" not in {s["name"] for s in sandbox["sources"]}
    with pytest.raises(Conflict):
        await world.service.issue_key(admin, source_id)


async def test_rotation_keeps_the_old_key_for_the_grace_period(world: World) -> None:
    admin = await world.admin()
    old = world.boot.keys["billing"].key
    new = await world.service.rotate_key(admin, old.source_id, old.key_id, grace_s=3600)
    assert new.key.scopes == old.scopes
    assert new.key.key_id != old.key_id

    snapshot = await world.service.snapshot(world.boot.space.cell_id)
    entry = next(e for e in snapshot.keys[str(old.space_id)] if e["key_id"] == old.key_id)
    assert entry["status"] == "active"
    assert entry["expires_at"] is not None
    assert (await world.service.revocations(world.boot.space.cell_id, after=0)).body["keys"] == []

    immediate = await world.service.rotate_key(admin, old.source_id, new.key.key_id, grace_s=0)
    feed = await world.service.revocations(world.boot.space.cell_id, after=0)
    assert [k["key_id"] for k in feed.body["keys"]] == [new.key.key_id]
    assert immediate.key.status is KeyStatus.ACTIVE


# People, login and tokens


async def test_login_issues_a_short_eddsa_token_with_the_space_roles_and_kid(world: World) -> None:
    token = await world.service.login("founder@acme.com", PASSWORD, world.totp(), None)
    header = jwt.get_unverified_header(token.token)
    claims = jwt.decode(token.token, options={"verify_signature": False})

    assert header == {"alg": "EdDSA", "kid": world.signer.kid, "typ": "JWT"}
    assert claims["aud"] == "niadra-cell"
    assert claims["iss"] == "niadra-control"
    assert claims["exp"] - claims["iat"] == 900 == token.expires_in
    assert claims["space"] == str(world.boot.space.space_id)
    assert claims["tenant"] == str(world.boot.tenant.tenant_id)
    assert claims["roles"] == ["admin"]
    assert claims["sources"] is None
    assert claims["sub"] == str(world.boot.admin.user.user_id)
    # The cell names the person on their receipts from this claim.
    assert claims["email"] == "founder@acme.com"
    jwt.decode(
        token.token,
        _public_key(await world.service.jwks(), header["kid"]),
        algorithms=["EdDSA"],
        audience="niadra-cell",
        options={"verify_exp": False},
    )


async def test_tokens_expire_after_fifteen_minutes(world: World) -> None:
    token = await world.service.login("founder@acme.com", PASSWORD, world.totp(), None)
    world.clock.advance(minutes=14)
    world.service.authenticate(token.token)
    world.clock.advance(minutes=2)
    with pytest.raises(Unauthenticated):
        world.service.authenticate(token.token)


async def test_a_token_signed_by_another_key_is_refused(world: World) -> None:
    token = await world.service.login("founder@acme.com", PASSWORD, world.totp(), None)
    claims = jwt.decode(token.token, options={"verify_signature": False})
    forged = jwt.encode(
        claims, Ed25519PrivateKey.generate(), algorithm="EdDSA", headers={"kid": world.signer.kid}
    )
    with pytest.raises(Unauthenticated):
        world.service.authenticate(forged)


@pytest.mark.parametrize(
    ("password", "totp"),
    [("wrong password here", "valid"), (PASSWORD, None), (PASSWORD, "000000")],
)
async def test_login_refuses_a_wrong_password_or_second_factor(
    world: World, password: str, totp: str | None
) -> None:
    code = world.totp() if totp == "valid" else totp
    with pytest.raises(Unauthenticated):
        await world.service.login("founder@acme.com", password, code, None)


async def test_login_without_second_factor_goes_to_the_enrollment_when_the_tenant_requires_it(
    world: World,
) -> None:
    admin = await world.admin()
    await world.service.create_user(
        admin, "nomfa@acme.com", PASSWORD, [BindingSpec(Role.ANALYSIS)], with_totp=False
    )
    with pytest.raises(Unauthenticated, match="enrollment required"):
        await world.service.login("nomfa@acme.com", PASSWORD, None, None)
    secret = await world.service.second_factor_setup("nomfa@acme.com", PASSWORD)
    assert await world.service.login("nomfa@acme.com", PASSWORD, world.totp(secret), None)


async def test_unknown_e_mail_and_wrong_password_look_the_same(world: World) -> None:
    with pytest.raises(Unauthenticated, match="invalid credentials"):
        await world.service.login("nobody@acme.com", PASSWORD, None, None)


async def test_vendor_people_get_only_their_sources(world: World) -> None:
    admin = await world.admin()
    voice = world.boot.keys["voice"].key.source_id
    vendor = await world.service.create_user(
        admin, "vendor@callcenter.com", PASSWORD, [BindingSpec(Role.VENDOR, source_ids=frozenset({voice}))]
    )
    token = await world.service.login("vendor@callcenter.com", PASSWORD, world.totp(vendor.totp_secret), None)
    caller = world.service.authenticate(token.token)

    assert caller.roles == {Role.VENDOR}
    assert caller.source_ids == {voice}
    sources = await world.service.list_sources(caller, world.boot.space.space_id)
    assert [s.source_id for s in sources] == [voice]
    with pytest.raises(Forbidden):
        await world.service.issue_key(caller, voice)


async def test_roles_can_be_scoped_to_one_space(world: World) -> None:
    admin = await world.admin()
    production = _production_ids(world)[0]
    user = await world.service.create_user(
        admin,
        "analyst@acme.com",
        PASSWORD,
        [BindingSpec(Role.ANALYSIS, space_id=production)],  # type: ignore[arg-type]
    )
    token = await world.service.login("analyst@acme.com", PASSWORD, world.totp(user.totp_secret), None)
    assert token.space_id == production
    with pytest.raises(Forbidden):
        await world.service.login(
            "analyst@acme.com", PASSWORD, world.totp(user.totp_secret), world.boot.space.space_id
        )


async def test_the_last_admin_cannot_be_demoted(world: World) -> None:
    admin = await world.admin()
    with pytest.raises(Conflict):
        await world.service.set_roles(admin, admin.user_id, [BindingSpec(Role.SECURITY)])
    renewed = world.service.authenticate((await world.service.reissue(admin)).token)
    assert renewed.roles == {Role.ADMIN}


# Configuration diffs


async def test_a_diff_needs_a_second_admin_to_approve_and_then_reaches_the_snapshot(world: World) -> None:
    admin = await world.admin()
    second = await _second_admin(world, admin)
    space = _production_ids(world)[0]
    diff = await world.service.submit_diff(admin, space, "policies", POLICY, "deny sensitive by default")
    assert diff.status is DiffStatus.PENDING

    with pytest.raises(Forbidden):
        await world.service.approve_diff(admin, diff.diff_id)
    applied = await world.service.approve_diff(second, diff.diff_id)

    assert applied.status is DiffStatus.APPLIED
    assert applied.decided_by == second.user_id
    version, document = await world.service.config_document(admin, space, "policies")
    assert version == applied.applied_version
    assert document["version"] == "p2"
    snapshot = await world.service.snapshot(world.boot.space.cell_id)
    assert snapshot.documents[snapshot.manifest["spaces"][str(space)]]["policy"]["version"] == "p2"


async def test_in_a_sandbox_the_author_approves_the_own_diff_with_another_admin_present(world: World) -> None:
    admin = await world.admin()
    await _second_admin(world, admin)
    diff = await world.service.submit_diff(
        admin, world.boot.space.space_id, "policies", POLICY, "deny sensitive by default"
    )
    assert (await world.service.approve_diff(admin, diff.diff_id)).status is DiffStatus.APPLIED


async def test_a_single_admin_approves_the_own_diff(world: World) -> None:
    admin = await world.admin()
    diff = await world.service.submit_diff(
        admin, world.boot.space.space_id, "review-sampling", {"base_rate": 0.05}, "more"
    )
    assert (await world.service.approve_diff(admin, diff.diff_id)).status is DiffStatus.APPLIED


async def test_the_author_needs_the_other_person_who_holds_the_documents_role(world: World) -> None:
    # One admin and one integration person. Before, a tenant with a single admin approved its own
    # diffs even with someone else able to approve that document.
    admin = await world.admin()
    created = await world.service.create_user(
        admin, "integrator@acme.com", PASSWORD, [BindingSpec(Role.INTEGRATION)]
    )
    token = await world.service.login("integrator@acme.com", PASSWORD, world.totp(created.totp_secret), None)
    integrator = world.service.authenticate(token.token)
    space = _production_ids(world)[0]
    views = [{"name": "task:billing", "purposes": ["billing"]}]
    diff = await world.service.submit_diff(admin, space, "views", views, "billing task")
    with pytest.raises(Forbidden):
        await world.service.approve_diff(admin, diff.diff_id)
    assert (await world.service.approve_diff(integrator, diff.diff_id)).status is DiffStatus.APPLIED
    # Nobody else approves policies (security or admin): the admin still approves their own.
    policy = await world.service.submit_diff(admin, space, "policies", POLICY, "deny sensitive by default")
    assert (await world.service.approve_diff(admin, policy.diff_id)).status is DiffStatus.APPLIED


async def test_invalid_documents_and_broken_references_are_refused_without_echoing_values(
    world: World,
) -> None:
    admin = await world.admin()
    space = world.boot.space.space_id
    with pytest.raises(InvalidInput) as bad:
        await world.service.submit_diff(
            admin, space, "review-sampling", {"base_rate": "secret-value"}, "typo"
        )
    assert "secret-value" not in str(bad.value.detail)
    rule = {"rule_id": "late", "condition": "promise.overdue", "version": "1", "endpoint_id": "missing"}
    with pytest.raises(InvalidInput):
        await world.service.submit_diff(admin, space, "trigger-rules", [rule], "alert on promises")
    with pytest.raises(NotFound):
        await world.service.submit_diff(admin, space, "prompts", [], "unknown type")


async def test_roles_limit_which_documents_a_person_may_change(world: World) -> None:
    admin = await world.admin()
    user = await world.service.create_user(admin, "integ@acme.com", PASSWORD, [BindingSpec(Role.INTEGRATION)])
    token = await world.service.login("integ@acme.com", PASSWORD, world.totp(user.totp_secret), None)
    integration = world.service.authenticate(token.token)
    with pytest.raises(Forbidden):
        await world.service.submit_diff(
            integration, world.boot.space.space_id, "policies", POLICY, "not mine"
        )
    views = [{"name": "task:collections", "budget_tokens": 600}]
    diff = await world.service.submit_diff(
        integration, world.boot.space.space_id, "views", views, "billing agent view"
    )
    assert diff.document[0]["name"] == "task:collections"


async def _person(world: World, admin: Caller, email: str, *roles: Role) -> Caller:
    created = await world.service.create_user(admin, email, PASSWORD, [BindingSpec(r) for r in roles])
    token = await world.service.login(email, PASSWORD, world.totp(created.totp_secret), None)
    return world.service.authenticate(token.token)


def _quote_types(**claim_purpose: object) -> dict[str, object]:
    quote = yaml.safe_load((DECLARATIONS / "cotacao_plano_saude.yaml").read_text())
    quote["purposes"]["claim"] = {**quote["purposes"]["claim"], **claim_purpose}
    return {"types": [quote]}


async def test_a_diff_that_loosens_a_purpose_needs_security_on_top_of_integration(world: World) -> None:
    admin = await world.admin()
    integrator = await _person(world, admin, "integrator@acme.com", Role.INTEGRATION)
    space = _production_ids(world)[0]
    first = await world.service.submit_diff(admin, space, "object-types", _quote_types(), "the quote")
    # A new type loosens nothing: what it declares only restricts, so integration approves.
    assert (await world.service.approve_diff(integrator, first.diff_id)).status is DiffStatus.APPLIED

    renamed = _quote_types()
    renamed["types"][0]["fields"]["price_full"]["role"] = "full_price"  # type: ignore[index]
    rename = await world.service.submit_diff(integrator, space, "object-types", renamed, "a role")
    with pytest.raises(Forbidden, match="another person can"):
        await world.service.approve_diff(integrator, rename.diff_id)
    assert (await world.service.approve_diff(admin, rename.diff_id)).status is DiffStatus.APPLIED

    loosened = _quote_types(prohibitions=[])
    loosened["types"][0]["fields"]["price_full"]["role"] = "full_price"  # type: ignore[index]
    with pytest.raises(Forbidden, match="also needs the security role"):
        await world.service.submit_diff(integrator, space, "object-types", loosened, "claim the price")
    both = await _person(world, admin, "both@acme.com", Role.INTEGRATION, Role.SECURITY)
    diff = await world.service.submit_diff(both, space, "object-types", loosened, "claim the price")
    with pytest.raises(Forbidden, match="also needs the security role"):
        await world.service.approve_diff(integrator, diff.diff_id)
    with pytest.raises(Forbidden, match="another person can"):
        await world.service.approve_diff(both, diff.diff_id)
    assert (await world.service.approve_diff(admin, diff.diff_id)).status is DiffStatus.APPLIED

    # Putting the prohibition back is a change of the purposes too: rolling back needs security as well.
    with pytest.raises(Forbidden, match="also needs the security role"):
        await world.service.rollback_diff(integrator, diff.diff_id, "undo")


async def test_a_coordination_change_that_opens_a_closed_purpose_needs_security(world: World) -> None:
    admin = await world.admin()
    integrator = await _person(world, admin, "integrator@acme.com", Role.INTEGRATION)
    space = world.boot.space.space_id
    _, document = await world.service.config_document(admin, space, "coordination")
    shorter = {**document, "decision_valid_s": 20}
    assert (
        await world.service.submit_diff(integrator, space, "coordination", shorter, "shorter")
    ).status is (DiffStatus.PENDING)
    opened = {
        **document,
        "purposes": [
            {**p, "fail": "open", "token": False} if p["name"] == "marketing" else p
            for p in document["purposes"]
        ],
    }
    with pytest.raises(Forbidden, match="also needs the security role"):
        await world.service.submit_diff(
            integrator, space, "coordination", opened, "marketing without a token"
        )
    diff = await world.service.submit_diff(admin, space, "coordination", opened, "marketing without a token")
    with pytest.raises(Forbidden, match="also needs the security role"):
        await world.service.approve_diff(integrator, diff.diff_id)


async def test_a_stale_diff_cannot_be_approved(world: World) -> None:
    admin = await world.admin()
    space = world.boot.space.space_id
    first = await world.service.submit_diff(admin, space, "policies", POLICY, "first")
    second = await world.service.submit_diff(admin, space, "policies", {**POLICY, "version": "p3"}, "second")
    await world.service.approve_diff(admin, first.diff_id)
    with pytest.raises(Conflict):
        await world.service.approve_diff(admin, second.diff_id)


async def test_rollback_restores_the_previous_document_as_a_new_version(world: World) -> None:
    admin = await world.admin()
    space = world.boot.space.space_id
    first = await world.service.approve_diff(
        admin, (await world.service.submit_diff(admin, space, "policies", POLICY, "policy p2")).diff_id
    )
    second = await world.service.approve_diff(
        admin,
        (
            await world.service.submit_diff(
                admin, space, "policies", {**POLICY, "version": "p3"}, "policy p3"
            )
        ).diff_id,
    )
    with pytest.raises(Conflict):
        await world.service.rollback_diff(admin, first.diff_id, "must roll back the newest first")

    rollback = await world.service.rollback_diff(admin, second.diff_id, "p3 broke the billing agent")
    assert rollback.rollback_of == second.diff_id
    assert rollback.status is DiffStatus.APPLIED
    version, document = await world.service.config_document(admin, space, "policies")
    assert (version, document["version"]) == (3, "p2")
    history = await world.service.history(admin, space, "policies")
    assert [v.version for v in history] == [3, 2, 1]


async def test_a_rollback_waits_for_the_second_person_like_any_diff(world: World) -> None:
    # A rollback is a change too; with someone else able to approve, it waits for them.
    admin = await world.admin()
    second = await _second_admin(world, admin)
    space = _production_ids(world)[0]
    diff = await world.service.submit_diff(admin, space, "policies", POLICY, "policy p2")
    applied = await world.service.approve_diff(second, diff.diff_id)
    rollback = await world.service.rollback_diff(admin, applied.diff_id, "p2 broke the billing agent")
    assert rollback.status is DiffStatus.PENDING
    assert (await world.service.config_document(admin, space, "policies"))[1]["version"] == "p2"
    with pytest.raises(Forbidden):
        await world.service.approve_diff(admin, rollback.diff_id)
    done = await world.service.approve_diff(second, rollback.diff_id)
    assert (done.status, done.rollback_of) == (DiffStatus.APPLIED, applied.diff_id)
    assert (await world.service.config_document(admin, space, "policies"))[1]["version"] != "p2"


async def test_assistant_proposals_wait_for_a_person(world: World) -> None:
    admin = await world.admin()
    space = world.boot.space
    views = [{"name": "task:support", "budget_tokens": 800}]
    proposal = await world.service.propose_diff(
        space.cell_id, space.space_id, "views", views, "suggested by the assistant"
    )
    assert proposal.status is DiffStatus.PROPOSED
    assert proposal.author_label == f"assistant@{space.cell_id}"
    assert [
        d.diff_id for d in await world.service.list_diffs(admin, space.space_id, DiffStatus.PROPOSED)
    ] == [proposal.diff_id]
    with pytest.raises(Forbidden):
        await world.service.propose_diff("other-cell", space.space_id, "views", views, "not my space")
    rejected = await world.service.reject_diff(admin, proposal.diff_id)
    assert rejected.status is DiffStatus.REJECTED


async def test_what_a_cell_inferred_is_recorded_as_observed_and_changes_nothing(world: World) -> None:
    admin = await world.admin()
    space = world.boot.space
    before = await world.service.config_document(admin, space.space_id, "object-types")
    observed = {"types": [{"type": "deadline", "ownership": "subject", "mirror_of": {"system": "court"}}]}
    diff = await world.service.record_observed(
        space.cell_id, space.space_id, "object-types", observed, "observed from the traffic"
    )
    assert (diff.status, diff.author_id, diff.author_label) == (DiffStatus.APPLIED, None, "niadra:observed")
    assert diff.applied_version is None
    assert await world.service.config_document(admin, space.space_id, "object-types") == before
    listed = await world.service.list_diffs(admin, space.space_id, DiffStatus.APPLIED)
    assert diff.diff_id in [d.diff_id for d in listed]
    # Nothing a person does to a diff applies to it: it is a record of what the cell serves beneath.
    with pytest.raises(Conflict):
        await world.service.reject_diff(admin, diff.diff_id)
    with pytest.raises(Conflict):
        await world.service.rollback_diff(admin, diff.diff_id, "undo")
    with pytest.raises(InvalidInput):
        await world.service.record_observed(space.cell_id, space.space_id, "policies", {}, "not inferred")
    with pytest.raises(Forbidden):
        await world.service.record_observed(
            "other-cell", space.space_id, "object-types", observed, "not mine"
        )


async def test_other_tenants_spaces_look_missing(world: World) -> None:
    stranger = Caller(uuid4(), uuid4(), uuid4(), frozenset({Role.ADMIN}))
    with pytest.raises(NotFound):
        await world.service.config_document(stranger, world.boot.space.space_id, "policies")
    with pytest.raises(NotFound):
        await world.service.issue_key(stranger, world.boot.keys["voice"].key.source_id)


# Placement and usage


async def test_new_projects_land_on_the_cell_with_room_and_can_move(world: World) -> None:
    await world.service.register_cell("sa-east-1-b", "sa-east-1", "https://cell-b", capacity=1000)
    admin = await world.admin()
    view = await world.service.create_project(admin, "Acme Brasil", "acme-br", "sa-east-1")
    assert {s.cell_id for s in view.spaces} == {"sa-east-1-b"}

    space = view.spaces[0]
    move = await world.service.request_move(space.space_id, "sa-east-1-a", "rebalance")
    with pytest.raises(Conflict):
        await world.service.request_move(space.space_id, "sa-east-1-a", "again")
    _, pending = await world.service.placement(admin, space.space_id)
    assert pending == move
    await world.service.complete_move(space.space_id)
    moved, pending = await world.service.placement(admin, space.space_id)
    assert moved.cell_id == "sa-east-1-a"
    assert pending is None
    snapshot = await world.service.snapshot("sa-east-1-a")
    assert str(space.space_id) in snapshot.manifest["spaces"]
    # The old cell hears where the space went, so it answers 421 instead of serving stale data.
    old = await world.service.snapshot("sa-east-1-b")
    assert old.manifest["moved"] == {str(space.space_id): "sa-east-1-a"}
    assert str(space.space_id) not in old.manifest["spaces"]
    assert snapshot.manifest["moved"] == {}


async def test_the_contracts_spend_ceiling_reaches_the_cell_in_the_snapshot(world: World) -> None:
    # The operator sets the contract's quota; the snapshot carries it to every space of the tenant.
    cell_id, space = world.boot.space.cell_id, str(world.boot.space.space_id)
    before = await world.service.snapshot(cell_id)
    assert before.documents[before.manifest["spaces"][space]].get("limits") is None
    tenant = world.boot.space.tenant_id
    await world.service.set_quota(tenant, "conversations", 1000)  # not one the cell enforces
    assert (await world.service.snapshot(cell_id)).version == before.version
    await world.service.set_quota(tenant, "llm_daily_micros:extraction", 2_000_000, "day")
    after = await world.service.snapshot(cell_id)
    assert after.version == before.version + 1
    document = after.documents[after.manifest["spaces"][space]]
    assert document["limits"] == {"llm_daily_micros": {"extraction": 2_000_000}}
    assert SpaceConfig.model_validate(document).limits == SpaceLimits(
        llm_daily_micros={"extraction": 2_000_000}
    )


async def test_cells_push_usage_only_for_their_spaces(world: World) -> None:
    admin = await world.admin()
    now = world.clock.now()
    row = UsageAggregate(world.boot.space.space_id, now, "conversations", 42, "", now)
    assert await world.service.push_usage(world.boot.space.cell_id, [row]) == 1
    await world.service.push_usage(
        world.boot.space.cell_id, [UsageAggregate(row.space_id, now, "conversations", 50, "", now)]
    )
    report = await world.service.usage(admin, now - timedelta(hours=1), now + timedelta(hours=1))
    assert [(a.metric, a.quantity) for a in report.aggregates] == [("conversations", 50)]
    with pytest.raises(Forbidden):
        await world.service.push_usage("sa-east-1-z", [row])


async def test_a_customer_key_is_set_by_an_operator_in_the_spaces_region_and_reaches_the_cell(
    world: World,
) -> None:
    # 10 D.1: the key is a contract; the snapshot carries it.
    cell_id, space = world.boot.space.cell_id, world.boot.space.space_id
    before = await world.service.snapshot(cell_id)
    assert before.documents[before.manifest["spaces"][str(space)]].get("kms_key_arn") is None
    arn = "arn:aws:kms:sa-east-1:444455556666:key/0b6f7a1e-5c1d-4d7e-9a0b-1c2d3e4f5a6b"
    with pytest.raises(InvalidInput):
        await world.service.set_space_key(space, "alias/customer")
    with pytest.raises(InvalidInput):
        await world.service.set_space_key(space, arn.replace("sa-east-1", "us-east-2"))
    with pytest.raises(NotFound):
        await world.service.set_space_key(uuid4(), arn)
    updated = await world.service.set_space_key(space, arn)
    assert updated.kms_key_arn == arn
    after = await world.service.snapshot(cell_id)
    document = after.documents[after.manifest["spaces"][str(space)]]
    assert SpaceConfig.model_validate(document).kms_key_arn == arn
    await world.service.set_space_key(space, None)
    back = await world.service.snapshot(cell_id)
    assert back.documents[back.manifest["spaces"][str(space)]].get("kms_key_arn") is None


async def test_usage_shows_a_person_only_the_spaces_their_role_covers(world: World) -> None:
    # An analyst bound to one space read the statement and the usage of every space of the tenant.
    admin = await world.admin()
    sandbox, production = world.boot.space, _production_ids(world)[0]
    at = datetime(2026, 9, 10, 12, tzinfo=UTC)
    rows = [UsageAggregate(s, at, "session_billable", 9, "", at) for s in (sandbox.space_id, production)]
    await world.service.push_usage(sandbox.cell_id, rows)
    user = await world.service.create_user(
        admin,
        "analyst@acme.com",
        PASSWORD,
        [BindingSpec(Role.ANALYSIS, space_id=production)],  # type: ignore[arg-type]
    )
    token = await world.service.login("analyst@acme.com", PASSWORD, world.totp(user.totp_secret), None)
    analyst = world.service.authenticate(token.token)

    lines = await world.service.usage_statement(analyst, "2026-09")
    assert [line.space_id for line in lines] == [production]
    report = await world.service.usage(analyst, at - timedelta(hours=1), at + timedelta(hours=1))
    assert {a.space_id for a in report.aggregates} == {production}
    everything = await world.service.usage_statement(admin, "2026-09")
    assert {sandbox.space_id, production} <= {line.space_id for line in everything}


async def test_the_monthly_statement_lists_the_spaces_of_the_month_and_the_operator_sees_all(
    world: World,
) -> None:
    # A space deleted during the month is billed for it; one deleted before is not listed.
    admin = await world.admin()
    space = world.boot.space
    at = datetime(2026, 9, 10, 12, tzinfo=UTC)
    await world.service.push_usage(
        space.cell_id, [UsageAggregate(space.space_id, at, "session_billable", 9, "", at)]
    )
    lines = await world.service.usage_statement(admin, "2026-09")
    assert {line.space_id: line.billable for line in lines}[space.space_id] == 9
    assert {line.month for line in lines} == {"2026-09"}
    operator = await world.service.usage_statement(None, "2026-09")
    assert {line.space_id for line in operator} >= {line.space_id for line in lines}
    async with world.uow() as uow:
        await uow.directory.mark_space_deleted(space.space_id, datetime(2026, 9, 20, tzinfo=UTC))
    assert space.space_id in {line.space_id for line in await world.service.usage_statement(admin, "2026-09")}
    assert space.space_id not in {
        line.space_id for line in await world.service.usage_statement(admin, "2026-10")
    }
    with pytest.raises(InvalidInput):
        await world.service.usage_statement(admin, "2026-13")
