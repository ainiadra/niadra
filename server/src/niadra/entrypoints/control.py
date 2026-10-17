"""The `niadra-control` deployable: `python -m niadra.entrypoints.serve control`.

Environment: NIADRA_CONTROL_DATABASE_URL (or the NIADRA_DB_* parts), NIADRA_CONTROL_SIGNING_KEY_FILE
(or NIADRA_CONTROL_SIGNING_KEY), NIADRA_KEY_PEPPER (the same value the cells use for the fast key
hash), NIADRA_CELL_TOKEN and NIADRA_CONTROL_OPERATOR_TOKEN. For single sign-on: NIADRA_ALLOWED_ORIGINS
(the Console's origins, where a sign-in may return), NIADRA_CONTROL_PUBLIC_URL (optional; the address the
identity providers call back, read from each request otherwise) and NIADRA_CONTROL_SECRETS_KEY
(optional; seals OIDC client secrets, derived from the pepper otherwise).
"""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from niadra.adapters.inbound.http.app import create_app as create_http_app
from niadra.adapters.inbound.http.control import ControlContainer, router
from niadra.adapters.inbound.http.sso import router as sso_router
from niadra.adapters.inbound.workers.runtime import stop_on_signals
from niadra.adapters.observability import logging as json_logging
from niadra.adapters.outbound.clock import SystemClock
from niadra.adapters.outbound.egress import SafeEgress
from niadra.adapters.outbound.keys.control_secrets import AesGcmSealer
from niadra.adapters.outbound.keys.ed25519 import Ed25519Signer
from niadra.adapters.outbound.postgres.control import PgControlUnitOfWorkFactory
from niadra.adapters.outbound.postgres.uow import make_engine
from niadra.adapters.outbound.sso.oidc import HttpOidcClient
from niadra.adapters.outbound.sso.saml import SignXmlSamlService
from niadra.app.control import ControlService
from niadra.app.sso import SsoService, SsoSettings
from niadra.entrypoints.http_server import serve_http
from niadra.entrypoints.settings import control_database_url


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing environment variable {name}")
    return value


def control_engine(pool_size: int = 5) -> AsyncEngine:
    return make_engine(control_database_url(), pool_size=pool_size, application_name="niadra-control")


def build_service(engine: AsyncEngine) -> ControlService:
    return ControlService(
        PgControlUnitOfWorkFactory(engine),
        Ed25519Signer.from_env(),
        SystemClock(),
        _env("NIADRA_KEY_PEPPER").encode(),
    )


def build_sso(
    engine: AsyncEngine, control: ControlService, egress: SafeEgress, origins: list[str]
) -> SsoService:
    return SsoService(
        PgControlUnitOfWorkFactory(engine),
        SystemClock(),
        AesGcmSealer.from_env(_env("NIADRA_KEY_PEPPER").encode()),
        HttpOidcClient(egress),
        SignXmlSamlService(),
        control,
        SsoSettings(console_origins=tuple(origins)),
    )


def create_app() -> FastAPI:
    engine = control_engine()
    service = build_service(engine)
    # The Console calls the control plane straight from the browser, and an SSO sign-in returns there.
    origins = [o.strip() for o in os.environ.get("NIADRA_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    egress = SafeEgress()
    container = ControlContainer(
        service,
        _env("NIADRA_CELL_TOKEN"),
        _env("NIADRA_CONTROL_OPERATOR_TOKEN"),
        sso=build_sso(engine, service, egress, origins),
        public_url=os.environ.get("NIADRA_CONTROL_PUBLIC_URL") or None,
    )
    app = create_http_app("niadra-control", container, [router, sso_router], origins)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await service.ensure_signing_key()
        yield
        await egress.aclose()
        await engine.dispose()

    app.router.lifespan_context = lifespan
    return app


def run() -> None:
    """`niadra-serve control`: the same signal handling and HTTP server as the cell deployables."""
    json_logging.configure(os.environ.get("NIADRA_LOG_LEVEL", "INFO"))

    async def main() -> None:
        stop = asyncio.Event()
        stop_on_signals(stop)
        # Uvicorn runs the app's lifespan: the signing key is ensured before the first request.
        await serve_http(create_app(), stop)

    asyncio.run(main())
