"""A key that works in one process names a space another process has not read yet: the snapshot wrote the
configuration before the keys, so that process reads its copy at once instead of answering 401."""

from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niadra.app.config import CachedConfigProvider, ConfigService
from niadra.app.edge import EdgeService
from niadra.domain.common.errors import Unauthenticated, Unavailable
from niadra.domain.edge.keys import SourceKeyRow, fast_hash, slow_hash
from niadra.domain.vocabulary import Environment, Scope
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, Cell, mint_source_key, sample_config
from tests.unit.config.conftest import CELL_ID, SnapshotFactory

PEPPER = b"pepper" * 4


class Ticks:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def key_for(edge: EdgeService, space_id: UUID = SPACE_ID) -> str:
    token, parsed = mint_source_key(Environment.SANDBOX, "us-east-2", "acme")
    row = SourceKeyRow(
        space_id=space_id,
        key_id=parsed.key_id,
        source_id=VOICE_SOURCE,
        fast_hash=fast_hash(PEPPER, parsed.key_id, parsed.secret),
        slow_hash=slow_hash(parsed.key_id, parsed.secret),
        scopes=frozenset({Scope.TRACK}),
        version=1,
    )
    await edge.mirror_keys(row.space_id, [row])
    return token


@pytest.fixture
def service(cell: Cell, control_key: Ed25519PrivateKey) -> ConfigService:
    return ConfigService(cell.uow, [control_key.public_key()], CELL_ID, cell_version="1.4.0")


async def test_a_new_space_is_served_on_its_first_key_without_waiting_for_the_timer(
    cell: Cell, service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    ticks = Ticks()
    other = sample_config(uuid4())
    await service.apply(make_snapshot(1, other))
    provider = CachedConfigProvider(cell.uow, CELL_ID, monotonic=ticks)
    await provider.refresh()
    edge = EdgeService(cell.uow, cell.cache, provider, cell.clock, {}, PEPPER, databases=cell.databases)
    # The snapshot that creates the space lands after this process's last timed read.
    await service.apply(make_snapshot(2, other, sample_config()))
    token = await key_for(edge)

    ticks.now += 0.2
    with pytest.raises(Unavailable) as waited:
        await edge.authenticate_key(token)
    assert waited.value.retry_after_s == 1

    ticks.now += 1.0
    principal = await edge.authenticate_key(token)
    assert principal.space.space_id == SPACE_ID
    assert provider.version == 2


async def test_a_key_of_a_space_the_fresh_copy_does_not_know_is_still_unknown(
    cell: Cell, service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    ticks = Ticks()
    await service.apply(make_snapshot(1, sample_config(uuid4())))
    provider = CachedConfigProvider(cell.uow, CELL_ID, monotonic=ticks)
    edge = EdgeService(cell.uow, cell.cache, provider, cell.clock, {}, PEPPER, databases=cell.databases)
    token = await key_for(edge)
    with pytest.raises(Unauthenticated, match="unknown space"):
        await edge.authenticate_key(token)


async def test_misses_read_the_copy_at_most_once_a_second(
    cell: Cell, service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    ticks = Ticks()
    await service.apply(make_snapshot(1, sample_config()))
    provider = CachedConfigProvider(cell.uow, CELL_ID, monotonic=ticks)
    assert await provider.refresh_on_miss() is True
    assert provider.version == 1
    await service.apply(make_snapshot(2, sample_config(), sample_config(uuid4())))
    ticks.now += 0.5
    assert await provider.refresh_on_miss() is False
    assert provider.version == 1
    ticks.now += 0.5
    assert await provider.refresh_on_miss() is True
    assert provider.version == 2
