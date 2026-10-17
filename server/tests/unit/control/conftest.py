from dataclasses import dataclass

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niadra.adapters.memory.control import InMemoryControlUnitOfWorkFactory
from niadra.adapters.memory.fakes import FixedClock
from niadra.adapters.outbound.keys.ed25519 import Ed25519Signer
from niadra.app.control import BootstrapResult, ControlService, SourceSpec
from niadra.domain.control.credentials import totp_code
from niadra.domain.control.records import Caller
from niadra.domain.vocabulary import Audience, Verification

PEPPER = b"pepper-for-tests-only-0123456789abcdef"
PASSWORD = "correct horse battery staple"
SOURCES = (
    SourceSpec("whatsapp", Audience.CUSTOMER_AGENT, channel="whatsapp", purposes=frozenset({"support"})),
    SourceSpec("voice", Audience.CUSTOMER_AGENT, channel="voice", verification_ceiling=Verification.V3),
    SourceSpec("billing", Audience.INTERNAL_AGENT, channel="erp", purposes=frozenset({"billing"})),
)


@dataclass
class World:
    service: ControlService
    clock: FixedClock
    signer: Ed25519Signer
    uow: InMemoryControlUnitOfWorkFactory
    boot: BootstrapResult

    last_step: int | None = None

    def totp(self, secret: str | None = None) -> str:
        """A code the control plane has not seen: each call takes a later 30-second step than the one
        before, for any secret, since an accepted step is never accepted again (RFC 6238, 5.2). It starts
        one step before now, as the one step of drift allows, and moves the clock only when the steps
        around now are used up."""
        now = int(self.clock.now().timestamp() // 30)
        step = now - 1 if self.last_step is None else max(now - 1, self.last_step + 1)
        if step > now + 1:
            self.clock.advance(seconds=30 * (step - now - 1))
        self.last_step = step
        return totp_code(secret or self.boot.admin.totp_secret or "", step * 30)

    async def admin(self) -> Caller:
        token = await self.service.login(self.boot.admin.user.email, PASSWORD, self.totp(), None)
        return self.service.authenticate(token.token)


@pytest.fixture
async def world() -> World:
    clock, signer, uow = (
        FixedClock(),
        Ed25519Signer(Ed25519PrivateKey.generate()),
        InMemoryControlUnitOfWorkFactory(),
    )
    service = ControlService(uow, signer, clock, PEPPER)
    boot = await service.bootstrap(
        tenant_name="Acme",
        tenant_slug="acme",
        email="Founder@Acme.com",
        region="sa-east-1",
        cell_id="sa-east-1-a",
        cell_address="https://cell-a.sa-east-1.niadra.test",
        password=PASSWORD,
        sources=SOURCES,
    )
    return World(service, clock, signer, uow, boot)
