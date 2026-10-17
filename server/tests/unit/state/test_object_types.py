"""The type registry's format (24.2): the three declarations the sectors wrote validate as written, the sector
templates carry them, and a broken copy of each fails with the path and the reason."""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from niadra.app.control_config import DOC_TYPES, validate_document
from niadra.config.object_types import ObjectType, ObjectTypeRegistry, escalate
from niadra.domain.common.errors import InvalidInput
from niadra.domain.control.records import Role

DECLARATIONS = Path(__file__).parent / "declarations"
TEMPLATES = Path(__file__).resolve().parents[3] / "src" / "niadra" / "config" / "templates"
SECTOR_OF = {"item_variant": "retail", "cotacao_plano_saude": "health", "intimacao_com_prazo": "legal"}


def declaration(name: str) -> dict[str, Any]:
    loaded = yaml.safe_load((DECLARATIONS / f"{name}.yaml").read_text())
    assert isinstance(loaded, dict)
    return loaded


def template(sector: str) -> dict[str, Any]:
    loaded = json.loads((TEMPLATES / f"{sector}.json").read_text())
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.parametrize("name", sorted(SECTOR_OF))
def test_each_declaration_of_the_sectors_validates_as_written(name: str) -> None:
    declared = ObjectType.model_validate(declaration(name))
    assert declared.type == name
    assert declared.mirror_of is not None


def test_the_three_declarations_load_into_one_registry_through_the_document() -> None:
    document = {"version": "1", "types": [declaration(name) for name in sorted(SECTOR_OF)]}
    stored = validate_document("object-types", document)
    registry = ObjectTypeRegistry.model_validate(stored)
    assert [t.type for t in registry.types] == sorted(SECTOR_OF)
    assert ObjectTypeRegistry.model_validate(json.loads(json.dumps(stored))) == registry


def test_the_published_names_of_python_keywords_survive_a_round_trip() -> None:
    stored = validate_document("object-types", {"types": [declaration("intimacao_com_prazo")]})
    notice = stored["types"][0]
    assert notice["lifecycle"]["transitions"][0]["from"] == "observed"
    assert '"from_"' not in json.dumps(stored)
    assert '"class_"' not in json.dumps(stored)
    variant = validate_document("object-types", {"types": [declaration("item_variant")]})["types"][0]
    assert variant["fields"]["price_sale"]["claim"]["class"] == "money"
    assert variant["fields"]["available"]["freshness"]["class"] == "volatile"


def test_the_time_axes_are_read_beside_the_timers_axis() -> None:
    notice = ObjectType.model_validate(declaration("intimacao_com_prazo"))
    assert notice.time is not None
    assert notice.time.timers_on == "published_on"
    assert set(notice.axes) == {
        "occurred_on",
        "made_available_on",
        "published_on",
        "known_at",
        "content_changed_at",
    }
    assert notice.axes["published_on"].rule == "publicacao_primeiro_dia_util@v3"


@pytest.mark.parametrize("sector", ["retail", "health", "legal"])
def test_each_sector_template_validates(sector: str) -> None:
    registry = ObjectTypeRegistry.model_validate(template(sector))
    assert len(registry.types) >= 5
    ownerships = {t.ownership for t in registry.types}
    assert "subject" in ownerships


@pytest.mark.parametrize(("name", "sector"), sorted(SECTOR_OF.items()))
def test_each_template_carries_its_sector_declaration_verbatim(name: str, sector: str) -> None:
    types = {t["type"]: t for t in template(sector)["types"]}
    assert types[name] == declaration(name)


def test_the_templates_use_every_part_of_the_format() -> None:
    types = [
        ObjectType.model_validate(t) for s in ("retail", "health", "legal") for t in template(s)["types"]
    ]
    assert {t.ownership for t in types} == {"subject", "shared", "agent"}
    assert any(t.nature == "derived" for t in types)
    assert any(t.values for t in types)
    assert any(t.derived_fields for t in types)
    assert any(r.match for t in types for r in t.relations.values())
    assert any(t.lifecycle and t.lifecycle.outcome and t.lifecycle.outcome.vocabularies for t in types)
    assert any(f.access for t in types for f in t.fields.values())
    assert any(s.licence for t in types for s in t.sources.values())
    assert any(t.agent_state for t in types)


Mutation = Callable[[dict[str, Any]], None]


def _set(path: str, value: Any) -> Mutation:
    def apply(doc: dict[str, Any]) -> None:
        *parents, last = path.split("/")
        node: Any = doc
        for part in parents:
            node = node[int(part)] if isinstance(node, list) else node[part]
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value

    return apply


def _drop(key: str) -> Mutation:
    return lambda doc: doc.pop(key)


BROKEN = [
    (
        "item_variant",
        _set("timers/launch_at/on_fire", ["transition(scheduled->actve)"]),
        "timers.launch_at.on_fire: transition(scheduled->actve) is not a transition of the lifecycle",
    ),
    (
        "item_variant",
        _set("readings/claim.available/expr", "available == yes and agee(available) <= 5s"),
        "readings.claim.available.expr: unknown function 'agee'",
    ),
    (
        "item_variant",
        _drop("mirror_of"),
        "mirror_of: a subject or shared type mirrors a system of the company",
    ),
    (
        "item_variant",
        _set("fields/price_sale/freshness/claim_max_age", "90s"),
        "fields.price_sale.freshness: claim_max_age is at most max_age",
    ),
    (
        "item_variant",
        _set("readings/checkout.buyable/expr", "quote(platform_live).available == yes"),
        "quote() takes a source of kind quote, and 'platform_live' is pull",
    ),
    (
        "item_variant",
        _set("refetch/reasons/5/when", "age(available) > 90"),
        "refetch.reasons.floor_expired.when: > compares a duration with a number",
    ),
    (
        "cotacao_plano_saude",
        _set("sources/quote_api/known_defects/0/use_source", "budget_doc"),
        "sources.quote_api.known_defects: 'budget_doc' is not another declared source",
    ),
    (
        "cotacao_plano_saude",
        _set("purposes/decide", {"on_stale": "structured_refusal"}),
        "purposes.decide: a structured refusal names its reason",
    ),
    (
        "cotacao_plano_saude",
        _drop("inputs"),
        "inputs: a derived type names the inputs it is a function of",
    ),
    (
        "cotacao_plano_saude",
        _set("fields/price_full/unobserved_blocks", ["claim"]),
        "fields.price_full: observers and unobserved_blocks belong to a field with logic: tri",
    ),
    (
        "cotacao_plano_saude",
        _set("refetch/reasons/1/when", "age(price_full) >"),
        "refetch.reasons.time_expired.when: the expression ends too early",
    ),
    (
        "intimacao_com_prazo",
        _set("time/timers_on", "published"),
        "time.timers_on: 'published' is not a declared time axis",
    ),
    (
        "intimacao_com_prazo",
        _set("lifecycle/transitions/0/when", "state == open"),
        "lifecycle.transitions.0.when: 'open' is a state: write it as a string, 'open'",
    ),
    (
        "intimacao_com_prazo",
        _set("lifecycle/forbidden", [{"from": "open", "to": "fulfilled"}]),
        "lifecycle.forbidden.0: open -> fulfilled is also a transition",
    ),
    (
        "intimacao_com_prazo",
        _set("time/published_on", {"origin": "derived"}),
        "time.published_on: a derived axis names the rule that computes it",
    ),
    (
        "intimacao_com_prazo",
        _set("lifecycle/latches", ["filed"]),
        "lifecycle.latches: 'filed' is not a declared state",
    ),
    (
        "intimacao_com_prazo",
        _set("mirror_of", {"system": "banco_do_produto", "derived_by": "introspection"}),
        "mirror_of.fingerprint: a type derived by introspection keeps the fingerprint it read",
    ),
    (
        "intimacao_com_prazo",
        _set("readings/night_batch/expr", "state == 'open' and under_seal.completeness == 'full'"),
        "readings.night_batch.expr: field 'under_seal' declares no completeness levels",
    ),
    (
        "intimacao_com_prazo",
        _set("fields/under_seal/observers/1/overrides", ["source:court"]),
        "fields.under_seal.observers: human overrides source:court, not another observer",
    ),
]


@pytest.mark.parametrize(
    ("name", "mutate", "expected"),
    BROKEN,
    ids=[f"{name}:{expected.split(':')[0]}" for name, _, expected in BROKEN],
)
def test_a_broken_copy_fails_with_the_path_and_the_reason(name: str, mutate: Mutation, expected: str) -> None:
    broken = copy.deepcopy(declaration(name))
    mutate(broken)
    with pytest.raises(ValidationError) as caught:
        ObjectType.model_validate(broken)
    assert expected in str(caught.value)


@pytest.mark.parametrize(
    ("name", "path", "value", "location"),
    [
        ("item_variant", "ownership", "global", "ownership"),
        (
            "item_variant",
            "fields/available/freshness/max_age",
            "30 seconds",
            "fields.available.freshness.max_age",
        ),
        ("cotacao_plano_saude", "refetch/budget/never_money", False, "refetch.budget.never_money"),
        ("intimacao_com_prazo", "type", "Intimacao", "type"),
        ("intimacao_com_prazo", "fields/content/sensitivity", "secret", "fields.content.sensitivity"),
    ],
)
def test_a_copy_outside_the_schema_fails_at_its_location(
    name: str, path: str, value: Any, location: str
) -> None:
    broken = copy.deepcopy(declaration(name))
    _set(path, value)(broken)
    with pytest.raises(ValidationError) as caught:
        ObjectType.model_validate(broken)
    locations = {".".join(str(part) for part in error["loc"]) for error in caught.value.errors()}
    assert any(found.startswith(location) for found in locations), locations


def test_the_document_names_the_problem_without_echoing_values() -> None:
    broken = copy.deepcopy(declaration("item_variant"))
    broken["timers"]["launch_at"]["on_fire"] = ["transition(scheduled->actve)"]
    with pytest.raises(InvalidInput) as caught:
        validate_document("object-types", {"types": [broken]})
    assert "types.0" in caught.value.detail


def test_a_type_is_declared_once_per_registry() -> None:
    with pytest.raises(ValidationError, match="types declared twice: item_variant"):
        ObjectTypeRegistry.model_validate({"types": [declaration("item_variant")] * 2})


def test_ownership_rules() -> None:
    agent = {
        "type": "working_notes",
        "ownership": "agent",
        "agent_state": {"write": "merge_by_key"},
        "fields": {"step": {"type": "enum"}},
    }
    assert ObjectType.model_validate(agent).agent_state is not None
    with pytest.raises(ValidationError, match="only a shared type has a working set"):
        ObjectType.model_validate({**agent, "working_set": {"enter_on": ["presented"], "leave_after": "1d"}})
    subject = {**declaration("cotacao_plano_saude"), "agent_state": {}}
    with pytest.raises(ValidationError, match="only an agent's type has an agent_state policy"):
        ObjectType.model_validate(subject)
    with pytest.raises(ValidationError, match="only a shared type is kept while in the working set"):
        ObjectType.model_validate(
            {**declaration("cotacao_plano_saude"), "retention": {"p": "while_in_working_set"}}
        )


def test_names_are_one_namespace_and_never_reserved() -> None:
    doc = copy.deepcopy(declaration("intimacao_com_prazo"))
    doc["fields"]["due_date"] = {"type": "date"}
    doc["fields"]["state"] = {"type": "string"}
    with pytest.raises(ValidationError) as caught:
        ObjectType.model_validate(doc)
    text = str(caught.value)
    assert "values.due_date: 'due_date' is already declared in fields" in text
    assert "fields.state: 'state' is reserved by the expression language" in text


def _registry(*types: dict[str, Any]) -> ObjectTypeRegistry:
    return ObjectTypeRegistry.model_validate({"types": list(types)})


TIGHTENINGS = [
    ("fields/price_full/sensitivity", "health"),
    ("fields/price_full/pii", True),
    ("fields/price_full/access", [{"readers": ["agent:sales"], "effect": "deny"}]),
    ("fields/price_full/access", [{"readers": ["agent:closing"], "effect": "allow"}]),
    ("sources/quote_api/licence", {"commercial_use": "forbidden"}),
    ("readings/commercial/purposes", []),
]
"""Each narrows who reads what: a reading whose purposes empty out is the one exception below."""


def test_a_diff_that_only_tightens_privacy_needs_no_security_role() -> None:
    # Zero touch: narrowing who reads what never waits for anyone.
    quote = declaration("cotacao_plano_saude")
    before = _registry(quote)
    assert escalate(before, before) == frozenset()
    renamed = copy.deepcopy(quote)
    renamed["fields"]["price_full"]["role"] = "full_price"
    assert escalate(before, _registry(renamed)) == frozenset(), "a role is the integration's"
    for path, value in TIGHTENINGS[:-1]:
        changed = copy.deepcopy(quote)
        _set(path, value)(changed)
        assert escalate(before, _registry(changed)) == frozenset(), path
    notice = declaration("intimacao_com_prazo")
    assert escalate(before, _registry(quote, notice)) == frozenset(), "a new type with a licence narrows"
    assert escalate(_registry(), _registry(declaration("item_variant"))) == frozenset()


def test_a_diff_that_loosens_privacy_or_changes_what_a_read_may_do_asks_security() -> None:
    quote = declaration("cotacao_plano_saude")
    guarded = copy.deepcopy(quote)
    for path, value in TIGHTENINGS[:5]:
        _set(path, value)(guarded)
    _set("fields/price_full/access", [{"readers": ["agent:sales"], "effect": "deny"}])(guarded)
    before = _registry(guarded)
    for path, value in [
        ("fields/price_full/sensitivity", None),
        ("fields/price_full/sensitivity", "religion"),
        ("fields/price_full/pii", False),
        ("fields/price_full/access", []),
        ("fields/price_full/access", [{"readers": ["agent:sales"], "effect": "mask"}]),
        ("sources/quote_api/licence", None),
        ("sources/quote_api/licence", {"commercial_use": "allowed"}),
        ("readings/commercial/purposes", ["sales", "marketing"]),
        ("readings/commercial/purposes", []),
        ("purposes/claim/prohibitions", ["affirm_price", "affirm_discount"]),
    ]:
        changed = copy.deepcopy(guarded)
        _set(path, value)(changed)
        assert escalate(before, _registry(changed)) == {Role.SECURITY}, (path, value)
    dropped = copy.deepcopy(guarded)
    del dropped["fields"]["price_full"]
    assert escalate(before, _registry(dropped)) == {Role.SECURITY}, "a guarded field declared no more"


def test_access_is_compared_reader_by_reader_and_purpose_by_purpose() -> None:
    quote = declaration("cotacao_plano_saude")
    before = copy.deepcopy(quote)
    _set(
        "fields/price_full/access", [{"readers": ["agent:sales"], "purposes": ["sales"], "effect": "allow"}]
    )(before)
    narrower = copy.deepcopy(quote)
    rules = [
        {"readers": ["agent:sales"], "purposes": ["sales"], "effect": "mask"},
        {"readers": ["public"], "effect": "deny"},
    ]
    _set("fields/price_full/access", rules)(narrower)
    assert escalate(_registry(before), _registry(narrower)) == frozenset()
    wider = copy.deepcopy(quote)
    _set("fields/price_full/access", [{"readers": ["source:sales"], "effect": "allow"}])(wider)
    assert escalate(_registry(before), _registry(wider)) == {Role.SECURITY}, "sales for any purpose now"


def test_the_object_types_document_asks_the_registrys_escalation_of_every_diff() -> None:
    doc = DOC_TYPES["object-types"]
    assert doc.escalate is escalate
    quote = declaration("cotacao_plano_saude")
    loosened = copy.deepcopy(quote)
    _set("purposes/decide/on_stale", "serve_with_age")(loosened)
    before = {"version": "0", "types": [quote]}
    assert doc.escalated(before, before) == frozenset()
    assert doc.escalated(before, {"version": "0", "types": [loosened]}) == {Role.SECURITY}
