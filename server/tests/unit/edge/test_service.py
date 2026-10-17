from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from niadra.app.edge import EdgeService
from niadra.domain.common.errors import Conflict, RateLimited, Unauthenticated
from niadra.domain.edge.keys import SourceKeyRow, fast_hash, slow_hash
from niadra.domain.vocabulary import Audience, Environment, Scope, Verification
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, Cell, mint_source_key

PEPPER = b"pepper" * 4


def make_edge(cell: Cell, **kwargs: Any) -> EdgeService:
    return EdgeService(
        cell.uow,
        kwargs.pop("cache", cell.cache),
        cell.config,
        cell.clock,
        {},
        PEPPER,
        databases=cell.databases,
        **kwargs,
    )


async def issue(
    edge: EdgeService, *, environment: Environment = Environment.SANDBOX, version: int = 1
) -> tuple[str, SourceKeyRow]:
    token, parsed = mint_source_key(environment, "us-east-2", "acme")
    row = SourceKeyRow(
        space_id=SPACE_ID,
        key_id=parsed.key_id,
        source_id=VOICE_SOURCE,
        fast_hash=fast_hash(PEPPER, parsed.key_id, parsed.secret),
        slow_hash=slow_hash(parsed.key_id, parsed.secret),
        scopes=frozenset({Scope.TRACK, Scope.CONTEXT}),
        version=version,
    )
    await edge.mirror_keys(SPACE_ID, [row])
    return token, row


async def test_a_key_resolves_to_its_source_with_config_attributes(cell: Cell) -> None:
    edge = make_edge(cell)
    token, row = await issue(edge)

    principal = await edge.authenticate_key(token)

    assert principal.space.space_id == SPACE_ID
    assert principal.source_id == VOICE_SOURCE
    assert principal.key_id == row.key_id
    assert principal.scopes == {Scope.TRACK, Scope.CONTEXT}
    assert principal.audience is Audience.CUSTOMER_AGENT
    assert principal.verification_ceiling is Verification.V3


async def test_second_call_is_served_from_the_positive_cache(cell: Cell) -> None:
    edge = make_edge(cell)
    token, row = await issue(edge)
    await edge.authenticate_key(token)
    cell.uow.store.data["edge"]["keys"].clear()  # the database no longer knows the key

    assert (await edge.authenticate_key(token)).key_id == row.key_id


async def test_local_revocation_wins_over_the_positive_cache(cell: Cell) -> None:
    edge = make_edge(cell)
    token, row = await issue(edge)
    await edge.authenticate_key(token)

    await edge.revoke_local(row.key_id)

    with pytest.raises(Unauthenticated):
        await edge.authenticate_key(token)
    assert cell.uow.store.data["edge"]["keys"][row.key_id].revoked


async def test_revoked_row_from_the_snapshot_is_refused(cell: Cell) -> None:
    edge = make_edge(cell)
    token, row = await issue(edge)
    await edge.mirror_keys(SPACE_ID, [replace(row, status="revoked", version=2)])

    with pytest.raises(Unauthenticated):
        await edge.authenticate_key(token)


async def test_unknown_key_id_is_cached_negative_but_a_wrong_secret_is_not(cell: Cell) -> None:
    edge = make_edge(cell)
    unknown, parsed = mint_source_key(Environment.SANDBOX, "us-east-2", "acme")
    with pytest.raises(Unauthenticated):
        await edge.authenticate_key(unknown)
    assert f"srckey:unknown:{parsed.key_id}" in cell.cache.values

    token, row = await issue(edge)
    wrong = token[:-5] + ("xxxxx" if not token.endswith("xxxxx") else "yyyyy")
    with pytest.raises(Unauthenticated):
        await edge.authenticate_key(wrong)
    assert f"srckey:unknown:{row.key_id}" not in cell.cache.values
    assert (await edge.authenticate_key(token)).key_id == row.key_id


async def test_key_of_another_environment_is_refused(cell: Cell) -> None:
    edge = make_edge(cell)
    token, _ = await issue(edge, environment=Environment.PRODUCTION)

    with pytest.raises(Unauthenticated):
        await edge.authenticate_key(token)


class BrokenCache:
    async def get_many(self, keys: Sequence[str]) -> list[bytes | None]:
        raise ConnectionError("cache down")

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        raise ConnectionError("cache down")

    async def incr_window(self, key: str, window_s: int) -> int:
        raise ConnectionError("cache down")


async def test_cache_outage_falls_back_to_the_database_and_rate_limit_fails_open(cell: Cell) -> None:
    edge = make_edge(cell, cache=BrokenCache())
    token, _ = await issue(edge)

    principal = await edge.authenticate_key(token)
    await edge.check_rate(principal, "/v1/batch")


async def test_rate_limit_per_key_per_second(cell: Cell) -> None:
    edge = make_edge(cell, rate_per_s=2)
    token, _ = await issue(edge)
    principal = await edge.authenticate_key(token)

    await edge.check_rate(principal, "/v1/batch")
    await edge.check_rate(principal, "/v1/batch")
    with pytest.raises(RateLimited):
        await edge.check_rate(principal, "/v1/batch")
    await edge.check_rate(principal, "/v1/context")  # reads are counted apart
    cell.clock.advance(seconds=1)
    await edge.check_rate(principal, "/v1/batch")


async def test_webhook_rate_is_counted_per_source_and_fails_open(cell: Cell) -> None:
    edge = make_edge(cell, rate_per_s=1)
    source, other = uuid4(), uuid4()

    await edge.check_webhook_rate(source, "/v1/ingest/webhook/x")
    await edge.check_webhook_rate(other, "/v1/ingest/webhook/y")
    with pytest.raises(RateLimited):
        await edge.check_webhook_rate(source, "/v1/ingest/webhook/x")
    broken = make_edge(cell, cache=BrokenCache(), rate_per_s=0)
    await broken.check_webhook_rate(source, "/v1/ingest/webhook/x")


async def test_revoking_a_source_cuts_every_key_even_after_the_cache_forgets(cell: Cell) -> None:
    edge = make_edge(cell)
    first, row = await issue(edge)
    second, _ = await issue(edge)
    await edge.authenticate_key(first)

    key_ids, at = await edge.revoke_source(SPACE_ID, VOICE_SOURCE)

    assert row.key_id in key_ids
    assert len(key_ids) == 2
    assert at == cell.clock.now()
    for token in (first, second):
        with pytest.raises(Unauthenticated):
            await edge.authenticate_key(token)
    cell.cache.values.clear()
    with pytest.raises(Unauthenticated):
        await edge.authenticate_key(first)
    assert await edge.revoke_source(SPACE_ID, uuid4()) == ([], at)


async def test_idempotent_governance_post(cell: Cell) -> None:
    edge = make_edge(cell)
    calls: list[int] = []

    async def compute() -> tuple[int, dict[str, Any]]:
        calls.append(1)
        return 201, {"merge_id": len(calls)}

    first = await edge.idempotent(SPACE_ID, "idem-1", b"body", compute)
    again = await edge.idempotent(SPACE_ID, "idem-1", b"body", compute)
    assert first == again == (201, {"merge_id": 1})
    with pytest.raises(Conflict):
        await edge.idempotent(SPACE_ID, "idem-1", b"other body", compute)

    cell.clock.advance(hours=24)
    assert await edge.idempotent(SPACE_ID, "idem-1", b"other body", compute) == (201, {"merge_id": 2})


async def test_snapshot_keeps_the_newest_version(cell: Cell) -> None:
    edge = make_edge(cell)
    _, row = await issue(edge, version=3)

    assert await edge.mirror_keys(SPACE_ID, [replace(row, status="revoked", version=2)]) == []
    assert not cell.uow.store.data["edge"]["keys"][row.key_id].revoked


async def test_person_token_resolves_the_space(cell: Cell) -> None:
    key = Ed25519PrivateKey.generate()
    jwks = {"k1": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)}
    edge = EdgeService(cell.uow, cell.cache, cell.config, cell.clock, jwks, PEPPER, databases=cell.databases)
    user = uuid4()
    claims = {
        "sub": str(user),
        "space": str(SPACE_ID),
        "roles": ["admin"],
        "aud": "niadra-cell",
        "exp": int(cell.clock.now().timestamp()) + 300,
    }

    person = await edge.authenticate_person(jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "k1"}))

    assert (person.user_id, person.space.space_id, person.roles) == (user, SPACE_ID, frozenset({"admin"}))
    claims["space"] = str(uuid4())
    unknown_space = jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "k1"})
    with pytest.raises(Unauthenticated):
        await edge.authenticate_person(unknown_space)


async def test_a_space_that_moved_answers_wrong_cell_to_keys_and_people(cell: Cell) -> None:
    # After a placement move the old cell answers 421 and the SDKs retry on a fresh connection.
    from niadra.domain.common.errors import WrongCell

    edge = make_edge(cell)
    token, _ = await issue(edge)
    await edge.authenticate_key(token)
    cell.config.move(SPACE_ID, "us-east-2-b")
    with pytest.raises(WrongCell):
        await edge.authenticate_key(token)
    key = Ed25519PrivateKey.generate()
    jwks = {"k1": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)}
    people = EdgeService(
        cell.uow, cell.cache, cell.config, cell.clock, jwks, PEPPER, databases=cell.databases
    )
    claims = {
        "sub": str(uuid4()),
        "space": str(SPACE_ID),
        "roles": ["admin"],
        "aud": "niadra-cell",
        "exp": int(cell.clock.now().timestamp()) + 300,
    }
    with pytest.raises(WrongCell):
        await people.authenticate_person(jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "k1"}))
