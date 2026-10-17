"""Runs one deployable of a cell: `python -m niadra.entrypoints.serve <role>`.

    ingest | read | admin | insights   HTTP on :8000, with the loops their services need
    workers --pools realtime,...       task consumers of those pools; scale each pool on its queue
    loops                              the cell's single-instance loops: the per-space relay, scheduler,
                                       sweeper and vector backfill, the space database provisioner,
                                       receipt writer, control-plane sync. Run exactly one
    control                            the control plane (its own database and container)
    setup                              one-shot: migrations of the control and cell databases and of
                                       every space database, broker topology, bucket rules

`workers` and `loops` serve only /healthz and /metrics on :8000.

Every process stops on SIGTERM: HTTP drains, workers finish what they hold and release leases.

A process imports only what its role runs: the control plane nothing of the cell, an HTTP deployable only
its own route groups, and only `workers` the task handlers. Every module imported is code and models held
for the life of the process, in every pod.
"""

import argparse
import os
import sys
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter

# The image runs from its project root, where alembic.ini and migrations/ are copied.
ALEMBIC_INI = Path(os.environ.get("NIADRA_ALEMBIC_INI", "alembic.ini"))

# 7.1, the "served by" column: the proxy sends each path group to the deployable that owns it. Each entry is
# `<module of adapters.inbound.http>:<router>`.
ROUTERS: dict[str, Sequence[str]] = {
    "ingest": (
        "ingest:router",
        "turns:router",
        "state:push_router",
    ),
    "read": (
        "read:router",
        "objects:router",
        "mcp:router",
        "turns:feed_router",
        "state:router",
        "coordinate:router",
        "signals:router",
    ),
    "admin": (
        "identity:router",
        "graph:router",
        "governance:router",
        "measure:router",
        "operate:router",
        "usage:router",
        "sources:router",
        "read:policy_router",
        "objects:profiles_router",
        "agent_memory:router",
        "turns:admin_router",
        "state:admin_router",
        "coordinate:admin_router",
        "signals:admin_router",
    ),
    "insights": ("insights:router",),
    "workers": (),
    "loops": (),
}

# Modules of adapters.inbound.workers whose `handlers` the task consumers run.
WORKER_MODULES = (
    "agent_memory",
    "audit",
    "compile",
    "configure",
    "extract",
    "identity",
    "ingest",
    "insights",
    "measure",
    "memory",
    "metering",
    "notify",
    "objects",
    "pipeline",
    "privacy",
    "review",
    "sessions",
    "traits",
    "triggers",
    "turns",
    "coordinate",
    "signals",
)


def routers(role: str) -> list["APIRouter"]:
    """The route groups of one deployable, imported now."""
    found = []
    for entry in ROUTERS[role]:
        module, name = entry.split(":")
        found.append(getattr(import_module(f"niadra.adapters.inbound.http.{module}"), name))
    return found


def worker_modules() -> list[ModuleType]:
    return [import_module(f"niadra.adapters.inbound.workers.{name}") for name in WORKER_MODULES]


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="niadra-serve")
    parser.add_argument("role", choices=[*ROUTERS, "control", "setup"])
    parser.add_argument("--pools", default="all", help="workers only: comma-separated pools, or `all`")
    args = parser.parse_args(argv)
    if args.role == "control":
        from niadra.entrypoints.control import run as run_control

        run_control()
        return
    if args.role == "setup":
        from niadra.entrypoints.setup import main as run_setup

        sys.exit(run_setup())
    from niadra.adapters.inbound.workers.runtime import POOLS
    from niadra.entrypoints import roles

    pools = list(POOLS) if args.pools == "all" else [p.strip() for p in args.pools.split(",") if p.strip()]
    if unknown := set(pools) - set(POOLS):
        parser.error(f"unknown pools: {', '.join(sorted(unknown))}")
    roles.main(args.role, pools)


if __name__ == "__main__":
    main()
