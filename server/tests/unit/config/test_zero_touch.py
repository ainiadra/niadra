"""Zero touch: a space nobody configured runs every feature with working defaults, and a
declaration replaces a default instead of being a task before the memory works."""

from uuid import uuid4

from niadra.app.control import AGENT_SCOPES, DEFAULT_SCOPES
from niadra.config.models import PurposeSettings, PurposeSpec, SourceConfig, SpaceConfig
from niadra.config.starter import DEFAULT_PURPOSE
from niadra.domain.compile.views import DEFAULT_TASK_VIEW_BUDGET, view_spec
from niadra.domain.policy import ItemAttrs, PolicyContext, Reader, evaluate
from niadra.domain.privacy.purposes import LegalBasis
from niadra.domain.vocabulary import Audience, Feature, Scope, Verification

SPACE = {"tenant_id": str(uuid4()), "region": "us-east-2", "environment": "sandbox"}


def _space(**documents: object) -> SpaceConfig:
    return SpaceConfig.model_validate({"space_id": str(uuid4()), **SPACE, **documents})


def _source(name: str, audience: Audience, purposes: list[str] | None = None) -> dict[str, object]:
    return {"source_id": str(uuid4()), "name": name, "audience": audience, "purposes": purposes or []}


def test_a_space_with_no_declaration_has_every_feature_and_agent_memory_on() -> None:
    config = _space()
    assert config.features.enabled == frozenset(Feature)
    assert config.agent_memory.enabled
    assert config.agent_memory.writes == "agent"


def test_its_one_purpose_serves_the_customer_on_the_contract_for_two_years() -> None:
    purposes = _space().purposes
    [service] = purposes.purposes
    assert (service.name, service.legal_basis, service.retention_days) == (
        DEFAULT_PURPOSE,
        LegalBasis.CONTRACT,
        730,
    )
    assert purposes.default == [DEFAULT_PURPOSE]


def test_a_source_reads_for_the_default_purpose_and_support_unless_it_says_otherwise() -> None:
    config = _space(
        sources=[
            _source("whatsapp", Audience.CUSTOMER_AGENT),
            _source("billing", Audience.INTERNAL_AGENT, ["billing"]),
        ]
    )
    whatsapp, billing = config.sources
    assert whatsapp.purposes == {DEFAULT_PURPOSE, "support"}
    # A source that declares its purposes keeps exactly those, while the space keeps the starter purposes too.
    assert billing.purposes == {"billing"}


def test_a_marketing_source_never_reads_what_was_collected_to_serve_the_customer() -> None:
    """A space on the starter purposes added `service` to a source that declared narrower ones: a campaign
    agent read every service item. Only a source that declares nothing reads for the default."""
    config = _space(
        sources=[
            _source("campaigns", Audience.CUSTOMER_AGENT, ["marketing"]),
            _source("whatsapp", Audience.CUSTOMER_AGENT),
        ]
    )
    served = ItemAttrs("fact", "orders", purposes=frozenset({DEFAULT_PURPOSE}))
    campaigns, whatsapp = (
        evaluate(
            config.policy, Reader(s.audience, s.purposes, s.source_id), served, PolicyContext(Verification.V4)
        )
        for s in config.sources
    )
    assert (campaigns.included, campaigns.rule) == (False, "purpose")
    assert whatsapp.included


def test_a_space_that_declares_its_purposes_keeps_its_sources_as_declared() -> None:
    declared = PurposeSettings(
        version="legal-1",
        purposes=[PurposeSpec(name="legal", legal_basis=LegalBasis.LEGAL_CLAIMS)],
        default=["legal"],
    )
    config = _space(
        purposes=declared.model_dump(), sources=[_source("assistant", Audience.INTERNAL_AGENT, ["legal"])]
    )
    assert config.sources[0].purposes == {"legal"}
    assert config.purposes == declared


def test_the_claim_contract_checks_dates_and_amounts_and_only_counts() -> None:
    contract = _space().claim_contract
    assert [c.id for c in contract.categories] == ["date", "money"]
    assert [c.detect.classes for c in contract.categories] == [["date"], ["money"]]
    assert {c.actions.default for c in contract.categories} == {"count"}
    assert contract.negative_corpus is not None


def test_contact_purposes_fail_open_for_service_and_closed_for_marketing_retention_and_collection() -> None:
    rules = {r.name: r for r in _space().coordination.purposes}
    assert {n for n, r in rules.items() if r.token} == {"marketing", "retention", "collection"}
    assert not rules["service"].token
    assert not rules["transactional"].token


def test_a_task_view_nobody_declared_reads_every_object_with_the_default_budget() -> None:
    spec = view_spec("task:revisar_contrato")
    assert (spec.budget, spec.object_types, spec.purposes) == (DEFAULT_TASK_VIEW_BUDGET, (), frozenset())
    assert DEFAULT_TASK_VIEW_BUDGET == 1500


def test_an_agent_key_made_without_scopes_works_for_every_agent_call_and_never_administers() -> None:
    for audience in (Audience.CUSTOMER_AGENT, Audience.INTERNAL_AGENT):
        assert DEFAULT_SCOPES[audience] == AGENT_SCOPES
    assert {Scope.AGENT_MEMORY, Scope.AGENT_MEMORY_WRITE, Scope.COORDINATE, Scope.REPLAY} <= AGENT_SCOPES
    assert AGENT_SCOPES.isdisjoint({Scope.ADMIN, Scope.ANALYTICS, Scope.IDENTITY_LINK, Scope.STATE_PUSH})


def test_a_declaration_still_replaces_each_default() -> None:
    config = _space(
        features={"enabled": ["turns"]},
        agent_memory={"enabled": False},
        sources=[SourceConfig(source_id=uuid4(), name="crm", audience=Audience.INTERNAL_AGENT).model_dump()],
    )
    assert config.features.enabled == {Feature.TURNS}
    assert not config.agent_memory.enabled
