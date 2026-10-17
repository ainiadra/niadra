import pytest

from niadra.domain.common.errors import InvalidInput
from niadra.domain.ingest.rules import channel_proof, check_namespaces, sent_by_systems
from niadra.domain.objects.state import COORDINATION_NAMESPACE
from niadra.domain.records import ObjectKey
from niadra.domain.vocabulary import Audience, EventKind, Speaker, Verification

V = Verification


def test_an_inbound_customer_turn_proves_the_channel_level_only() -> None:
    assert channel_proof(V.V1, V.V3, Speaker.CUSTOMER, "inbound") is V.V1
    assert channel_proof(V.V4, V.V4, Speaker.CUSTOMER, "inbound") is V.V2  # a challenge needs `verify`
    assert channel_proof(V.V2, V.V1, Speaker.CUSTOMER, None) is V.V1  # never above the source ceiling


def test_agent_turns_outbound_turns_and_bare_claims_prove_nothing() -> None:
    assert channel_proof(V.V1, V.V3, Speaker.AI_AGENT, "outbound") is None
    assert channel_proof(V.V1, V.V3, Speaker.CUSTOMER, "outbound") is None
    assert channel_proof(V.V0, V.V3, Speaker.CUSTOMER, "inbound") is None
    assert channel_proof(None, V.V3, Speaker.CUSTOMER, "inbound") is None
    assert channel_proof(V.V1, V.NO_CUSTOMER, Speaker.CUSTOMER, "inbound") is None


def test_coordinations_own_records_are_never_checked_as_a_systems_objects() -> None:
    """A handoff's outcome names its handoff in coordination's namespace: never one a system sent, whatever
    type a space observed (e2e, 07/10/2026: `handoff` observed, and the outcome refused as unknown)."""
    handoff = ObjectKey("handoff", COORDINATION_NAMESPACE, "h-1")
    case = ObjectKey("legal_case", "court", "c-1")
    named = sent_by_systems(
        Audience.CUSTOMER_AGENT, EventKind.ACTION, [handoff, case], {"handoff", "legal_case"}
    )
    assert named == (case,)


def test_only_coordination_writes_in_its_namespace_so_the_unknown_object_exemption_cannot_be_borrowed() -> (
    None
):
    """`sent_by_systems` never checks the coordination namespace; an agent that put a made-up object there to
    skip the check is refused before it, since only coordination's own records are written there."""
    with pytest.raises(InvalidInput):
        check_namespaces({COORDINATION_NAMESPACE, "court"}, coordination=False)
    check_namespaces({COORDINATION_NAMESPACE}, coordination=True)
    check_namespaces({"court"}, coordination=False)
