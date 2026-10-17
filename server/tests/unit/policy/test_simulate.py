"""The policy simulator (5.12): the real context compiler for a hypothetical reader."""

import pytest

from niadra.adapters.memory.measure import InMemoryMeasureRepository
from niadra.app.policy import PolicyService
from niadra.contracts.policy import SimulateRequest
from niadra.domain.common.errors import NotFound, VerificationNotAllowed
from niadra.domain.measure.trim import ContextTrim, TrimUnit
from niadra.domain.vocabulary import Audience, Verification
from niadra.testing.cell import BILLING_SOURCE, WHATSAPP_SOURCE, Cell, PatchedUnitOfWorkFactory, principal
from tests.unit.compile.factories import PROFILE, T0, WA, marina, uid
from tests.unit.compile.fakes import World
from tests.unit.serve.harness import Harness


def simulator(cell: Cell) -> PolicyService:
    return PolicyService(
        PatchedUnitOfWorkFactory(cell.uow, **World(snapshots=[marina()]).repos()), cell.config
    )


async def test_what_the_support_agent_would_see_at_v1(cell: Cell) -> None:
    result = await simulator(cell).simulate(
        principal(),
        SimulateRequest(profile_id=PROFILE, source_id=WHATSAPP_SOURCE, verification=Verification.V1),
    )
    by_id = {i.id: i for i in result.items}
    assert by_id[str(uid(257))].effect == "deny"
    assert by_id[str(uid(257))].reason == "verification"
    assert by_id[str(uid(0x102))].delivered
    # The sensitive fact is the policy's: no level releases it to a support agent.
    assert result.withheld == {"verification": 3, "policy": 1}
    assert "fibra 500 mega" in result.text
    assert "fatura de agosto" not in result.text.split("[Outro agente]")[0]
    assert (result.policy_version, result.audience) == ("p1", Audience.CUSTOMER_AGENT)


async def test_the_billing_agent_without_the_customer_sees_billing_only(cell: Cell) -> None:
    result = await simulator(cell).simulate(
        principal(),
        SimulateRequest(profile_id=PROFILE, source_id=BILLING_SOURCE, verification=Verification.NO_CUSTOMER),
    )
    delivered = {i.category for i in result.items if i.delivered}
    assert "billing" in delivered
    assert "contract" not in delivered


async def test_simulation_errors(cell: Cell) -> None:
    svc = simulator(cell)
    with pytest.raises(NotFound):
        await svc.simulate(principal(), SimulateRequest(profile_id=uid(9)))
    with pytest.raises(NotFound):
        await svc.simulate(principal(), SimulateRequest(profile_id=PROFILE, source_id=uid(8)))
    with pytest.raises(VerificationNotAllowed):
        await svc.simulate(
            principal(),
            SimulateRequest(
                profile_id=PROFILE, audience=Audience.CUSTOMER_AGENT, verification=Verification.NO_CUSTOMER
            ),
        )


async def test_the_simulation_leaves_out_what_the_source_never_used_as_its_reads_do(cell: Cell) -> None:
    trim = ContextTrim(WHATSAPP_SOURCE, "chat", (TrimUnit("fact:contract", 60),), T0, 28, 50)
    await InMemoryMeasureRepository(cell.uow.store).put_trim(trim)
    request = SimulateRequest(profile_id=PROFILE, source_id=WHATSAPP_SOURCE, verification=Verification.V1)
    result = await simulator(cell).simulate(principal(), request)
    plan = next(i for i in result.items if i.id == str(uid(0x102)))
    assert (plan.effect, plan.delivered, plan.reason, plan.rule) == ("allow", False, "unused", "context_use")
    assert "fibra 500 mega" not in result.text
    # The same reader described by audience and purposes, not as the source, has no trim.
    anonymous = await simulator(cell).simulate(
        principal(),
        SimulateRequest(profile_id=PROFILE, audience=Audience.CUSTOMER_AGENT, purposes=["support"]),
    )
    assert "fibra 500 mega" in anonymous.text


async def test_the_simulation_shows_the_live_turns_a_read_would_get_and_the_ones_it_would_not(
    cell: Cell,
) -> None:
    # The `live` block passes the same policy as the pack (screen_live); the simulation runs it too.
    h = Harness(cell)
    await h.add_turn(WA, minutes_ago=4, text="mudei o endereço de entrega", conversation_id="wa-7", event=1)
    await h.add_turn(
        WA, minutes_ago=3, text="Já saiu o resultado do exame de sangue?", conversation_id="wa-exam", event=2
    )
    service = PolicyService(h.uow, cell.config, live=h.serve)
    request = SimulateRequest(profile_id=PROFILE, source_id=WHATSAPP_SOURCE, verification=Verification.V1)
    at_v1 = (await service.simulate(principal(), request)).live
    assert at_v1.complete
    assert at_v1.delivered == 1
    [address, exam] = at_v1.turns
    assert (address.delivered, address.effect, address.text) == (True, "allow", "mudei o endereço de entrega")
    assert (address.channel, address.speaker) == ("whatsapp", "customer")
    # Withheld: the reason, never the text.
    assert (exam.delivered, exam.effect, exam.text) == (False, "deny", None)
    assert exam.reason == "policy"
    assert at_v1.withheld == {"policy": 1}

    blind = await service.simulate(principal(), request.model_copy(update={"verification": Verification.V0}))
    assert blind.live.delivered == 0
    assert [t.text for t in blind.live.turns] == [None, None]
    assert blind.live.withheld == {"verification": 1, "policy": 1}


async def test_without_the_read_path_the_simulation_says_the_live_block_is_not_known(cell: Cell) -> None:
    result = await simulator(cell).simulate(
        principal(),
        SimulateRequest(profile_id=PROFILE, source_id=WHATSAPP_SOURCE, verification=Verification.V1),
    )
    assert (result.live.turns, result.live.complete) == ([], False)
