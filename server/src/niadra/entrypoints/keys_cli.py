"""Operator commands for the cell's keys (5.14.1). They run in a cell pod, with its environment.

python -m niadra.entrypoints.keys_cli rotate --space <space_id> [--space ...]
    A new data key version for each space; new writes use it, old rows keep opening with theirs.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from niadra.adapters.outbound.keys.kms import KMS_PREFIX, KmsKeyVault, open_kms
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.postgres.uow import make_engine
from niadra.entrypoints.settings import Settings


async def _rotate(settings: Settings, spaces: Sequence[UUID]) -> str:
    key_id = _kms_key(settings)
    engine = cell_engine(settings)
    lines = []
    try:
        async with open_kms(settings.region, settings.kms_endpoint) as kms:
            store = PgWrappedKeyStore(engine, KMS_PREFIX + key_id)
            vault, _ = await KmsKeyVault.open(kms, key_id, store)
            for space_id in spaces:
                lines.append(f"{space_id} -> data key version {await vault.rotate_space(space_id)}\n")
    finally:
        await engine.dispose()
    return "".join(lines)


def cell_engine(settings: Settings) -> AsyncEngine:
    """The keys live in the cell database (`data_keys`), never in a space's."""
    return make_engine(settings.servers.cell_url("system"), pool_size=1, application_name="niadra-keys")


def _kms_key(settings: Settings) -> str:
    if not settings.kms_key_id:
        raise SystemExit("set NIADRA_KMS_KEY_ID to the master key in KMS first")
    return settings.kms_key_id


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="niadra-keys")
    commands = parser.add_subparsers(dest="command", required=True)
    rotate = commands.add_parser("rotate", help="a new data key version for spaces")
    rotate.add_argument("--space", type=UUID, action="append", required=True)
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    sys.stdout.write(asyncio.run(_rotate(settings, args.space)))


if __name__ == "__main__":
    main()
