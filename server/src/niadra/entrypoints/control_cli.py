"""Operator CLI of the control plane.

    python -m niadra.entrypoints.control_cli bootstrap --tenant "Acme" --email founder@acme.com

A cell dedicated to one tenant (plan Regulado; deploy/scripts/cell.sh runs these):

    ... register-cell --cell niadra-acme --address https://niadra-acme.cells.niadra.com \
        --tenant acme --token-sha256 <hex>        the cell, with the SHA-256 of its own credential
    ... place-project --tenant acme --name Support --slug acme-support --cell niadra-acme
    ... routes --cell niadra-acme --domain niadra.com    the space addresses that must reach it
    ... rekey-cell --cell niadra-acme --token-sha256 <hex>
    ... close-cell --cell niadra-acme                    before its machines go; refused with spaces

`bootstrap` creates the first tenant, its project with sandbox and production spaces, the whatsapp,
voice and billing sources, the first admin and one sandbox key per source, and prints the secrets once.
It reads the same environment as the deployable (see `niadra.entrypoints.control`).
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path

from niadra.app.control import BootstrapResult, SourceSpec
from niadra.domain.common.errors import NiadraError
from niadra.domain.control.credentials import totp_uri
from niadra.domain.control.usage import to_csv
from niadra.domain.vocabulary import Audience, Verification
from niadra.entrypoints.control import build_service, control_engine

# A source reads for exactly the purposes it declares: each declares the starter's `service`, which a space
# on the starter purposes collects everything for.
FIRST_SOURCES = (
    SourceSpec(
        "whatsapp", Audience.CUSTOMER_AGENT, channel="whatsapp", purposes=frozenset({"service", "support"})
    ),
    SourceSpec(
        "voice",
        Audience.CUSTOMER_AGENT,
        channel="voice",
        purposes=frozenset({"service", "support"}),
        verification_ceiling=Verification.V3,
    ),
    SourceSpec(
        "billing",
        Audience.INTERNAL_AGENT,
        channel="erp",
        purposes=frozenset({"billing", "service"}),
        trusted_action_ops=frozenset({"credit"}),
    ),
)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30].strip("-")
    return slug if len(slug) >= 3 else f"{slug}-tenant"


async def _bootstrap(args: argparse.Namespace) -> BootstrapResult:
    engine = control_engine(pool_size=1)
    service = build_service(engine)
    try:
        await service.ensure_signing_key()
        return await service.bootstrap(
            tenant_name=args.tenant,
            tenant_slug=args.slug or _slugify(args.tenant),
            email=args.email,
            region=args.region,
            cell_id=args.cell or f"{args.region}-1",
            cell_address=args.cell_address or f"https://cell-1.{args.region}.api.niadra.com",
            password=args.password or secrets.token_urlsafe(18),
            with_totp=not args.no_totp,
            sources=FIRST_SOURCES,
            project_name=args.project,
            project_slug=args.project_slug or (_slugify(args.project) if args.project else None),
        )
    finally:
        await engine.dispose()


def _print_bootstrap(result: BootstrapResult) -> None:
    out = sys.stdout.write
    out(f"tenant      {result.tenant.slug}  {result.tenant.tenant_id}\n")
    out(f"project     {result.project.slug}  {result.project.project_id}\n")
    out(f"sandbox     {result.space.slug}  {result.space.space_id}  cell {result.space.cell_id}\n")
    out(f"admin       {result.admin.user.email}\n")
    out(f"password    {result.password}\n")
    if result.admin.totp_secret:
        out(f"totp        {totp_uri(result.admin.totp_secret, result.admin.user.email)}\n")
    for name, issued in result.keys.items():
        out(f"key {name:<9} {issued.plaintext}\n")
    out("\nThese secrets are shown once. Store them in a password manager now.\n")


def _write_bootstrap(result: BootstrapResult, path: Path) -> None:
    """The same secrets as the printout, as JSON readable only by the owner (for scripted sandboxes)."""
    document = {
        "tenant_id": str(result.tenant.tenant_id),
        "project_id": str(result.project.project_id),
        "space_id": str(result.space.space_id),
        "cell_id": result.space.cell_id,
        "admin_email": result.admin.user.email,
        "password": result.password,
        "totp_secret": result.admin.totp_secret,
        "keys": {name: issued.plaintext for name, issued in result.keys.items()},
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(document, handle, indent=2)
    sys.stdout.write(f"wrote the bootstrap secrets to {path}\n")


async def _add_admin(args: argparse.Namespace) -> None:
    engine = control_engine(pool_size=1)
    service = build_service(engine)
    password = secrets.token_urlsafe(18)
    try:
        created = await service.add_admin_beside(args.beside, args.email, password)
    finally:
        await engine.dispose()
    sys.stdout.write(
        f"admin       {created.user.email}\npassword    {password}\n"
        "second factor: enrolled in the Console at the first login\n"
    )


async def _usage_statement(args: argparse.Namespace) -> None:
    """Every tenant's month, the file the invoices are made from."""
    engine = control_engine(pool_size=1)
    service = build_service(engine)
    try:
        lines = await service.usage_statement(None, args.month)
    finally:
        await engine.dispose()
    body = to_csv(lines)
    if args.out:
        args.out.write_text(body)
        sys.stdout.write(f"wrote {len(lines)} spaces of {args.month} to {args.out}\n")
    else:
        sys.stdout.write(body)


def _token_hash(value: str) -> bytes:
    """The SHA-256 of a cell's credential, in hex. The credential itself never reaches this process."""
    try:
        digest = bytes.fromhex(value)
    except ValueError:
        raise SystemExit("--token-sha256 must be hexadecimal") from None
    if len(digest) != hashlib.sha256().digest_size:
        raise SystemExit("--token-sha256 must be a SHA-256 (64 hex digits)")
    return digest


async def _cells(args: argparse.Namespace) -> None:
    """The commands about cells and placement; each prints one line per result, for scripts."""
    engine = control_engine(pool_size=1)
    service = build_service(engine)
    out = sys.stdout.write
    try:
        if args.command == "register-cell":
            tenant = await service.tenant_by_slug(args.tenant) if args.tenant else None
            cell = await service.register_cell(
                args.cell,
                args.region,
                args.address,
                args.capacity,
                tenant_id=tenant.tenant_id if tenant else None,
                token_sha256=_token_hash(args.token_sha256) if args.token_sha256 else None,
            )
            owner = f"dedicated to {args.tenant}" if tenant else "shared"
            out(f"cell {cell.cell_id} {cell.region} {cell.address} {owner}\n")
        elif args.command == "rekey-cell":
            await service.rekey_cell(args.cell, _token_hash(args.token_sha256))
            out(f"cell {args.cell} re-keyed\n")
        elif args.command == "place-project":
            view = await service.place_project(
                args.tenant, args.name, args.slug, args.region, args.cell, args.instance
            )
            for space in view.spaces:
                where = f"cell {space.cell_id} instance {space.instance} tier {space.tier}"
                out(f"space {space.slug} {space.space_id} {where}\n")
        elif args.command == "routes":
            for space in await service.routes(args.cell):
                out(f"{space.slug}.{space.region}.api.{args.domain}\n")
        elif args.command == "close-cell":
            cell = await service.close_cell(args.cell)
            out(f"cell {cell.cell_id} {cell.status}\n")
    except NiadraError as exc:
        raise SystemExit(f"{args.command}: {exc}") from None
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="niadra-control")
    commands = parser.add_subparsers(dest="command", required=True)

    boot = commands.add_parser("bootstrap", help="create the first tenant, admin, sources and keys")
    boot.add_argument("--tenant", required=True, help="tenant display name")
    boot.add_argument("--email", required=True, help="the first admin's e-mail")
    boot.add_argument("--slug", help="tenant slug (and the project's); derived from the name when absent")
    boot.add_argument("--project", help="the first project's name; the tenant's name when absent")
    boot.add_argument("--project-slug", help="the first project's slug, the start of the space names")
    boot.add_argument("--region", default=os.environ.get("NIADRA_REGION", "sa-east-1"))
    boot.add_argument("--cell", help="cell id; created when missing")
    boot.add_argument("--cell-address", help="https address of the cell")
    boot.add_argument("--password", help="the admin password; generated when absent")
    boot.add_argument(
        "--no-totp", action="store_true", help="skip the second factor (local development only)"
    )
    boot.add_argument("--output", type=Path, help="write the secrets as JSON (mode 600) instead of printing")

    admin = commands.add_parser("add-admin", help="one more admin in a colleague's tenant")
    admin.add_argument("--beside", required=True, help="the e-mail of a person already in the tenant")
    admin.add_argument("--email", required=True, help="the new admin's e-mail")

    usage = commands.add_parser(
        "usage-statement", help="one month of usage per space of every tenant, as CSV"
    )
    usage.add_argument("--month", required=True, help="the UTC month, YYYY-MM")
    usage.add_argument("--out", type=Path, help="write the CSV here instead of printing it")

    region = os.environ.get("NIADRA_REGION", "us-east-2")
    register = commands.add_parser("register-cell", help="a new cell; with --tenant, dedicated to it")
    register.add_argument("--cell", required=True, help="cell id: the dedicated cell's stack name")
    register.add_argument("--address", required=True, help="https address of the cell")
    register.add_argument("--region", default=region)
    register.add_argument("--capacity", type=int, default=500, help="spaces the cell takes")
    register.add_argument("--tenant", help="the slug of the tenant the cell is dedicated to")
    register.add_argument("--token-sha256", help="SHA-256 (hex) of the cell's own credential")

    rekey = commands.add_parser("rekey-cell", help="a new credential of the cell's own")
    rekey.add_argument("--cell", required=True)
    rekey.add_argument("--token-sha256", required=True, help="SHA-256 (hex) of the new credential")

    place = commands.add_parser("place-project", help="a new project with its spaces on a named cell")
    place.add_argument("--tenant", required=True, help="tenant slug")
    place.add_argument("--name", required=True, help="the project's name")
    place.add_argument("--slug", required=True, help="the project's slug, the start of the space names")
    place.add_argument("--cell", required=True)
    place.add_argument("--instance", default="primary", help="the cell's database instance, by name")
    place.add_argument("--region", default=region)

    routes = commands.add_parser("routes", help="the space addresses that must reach a cell")
    routes.add_argument("--cell", required=True)
    routes.add_argument("--domain", default="niadra.com")

    close = commands.add_parser("close-cell", help="a cell that takes no more spaces")
    close.add_argument("--cell", required=True)

    args = parser.parse_args(argv)
    if args.command in ("register-cell", "rekey-cell", "place-project", "routes", "close-cell"):
        asyncio.run(_cells(args))
    elif args.command == "usage-statement":
        asyncio.run(_usage_statement(args))
    elif args.command == "add-admin":
        asyncio.run(_add_admin(args))
    else:
        result = asyncio.run(_bootstrap(args))
        if args.output:
            _write_bootstrap(result, args.output)
        else:
            _print_bootstrap(result)


if __name__ == "__main__":
    main()
