"""Typed state at the read (24.3.1 to 24.3.5): four logical values with observers, sources with precedence
and known defects, freshness computed now for a purpose, what an unknown field blocks, and the three sector
declarations read as the sectors described them (the price read to claim, the deadline read, the quote read).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml

from niadra.app.typed_state import Purpose, ReadRequest, TypedObject, read, resolved_state
from niadra.config.object_types import ObjectType
from niadra.contracts.state import ObjectRead
from niadra.domain.objects.state import system_source
from niadra.domain.records import ObjectKey
from niadra.domain.state.observed import Holding, Report, merge

DECLARATIONS = Path(__file__).parent / "declarations"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def declared(name: str, **changes: Any) -> ObjectType:
    raw = yaml.safe_load((DECLARATIONS / f"{name}.yaml").read_text())
    for path, value in changes.items():
        target = raw
        *parents, leaf = path.split("__")
        for part in parents:
            target = target[part]
        target[leaf] = value
    return ObjectType.model_validate(raw)


class Object:
    """An object as its reports built it, read at `NOW`."""

    def __init__(self, declaration: ObjectType, key: ObjectKey) -> None:
        self.declaration = declaration
        self.key = key
        self.fields: Holding = {}
        self.names: dict[UUID, str] = {}
        self.derived_status: str | None = None

    def report(
        self,
        source: str,
        seconds_ago: float,
        fields: dict[str, Any],
        *,
        received: float | None = None,
        **kw: Any,
    ) -> "Object":
        at = NOW - timedelta(seconds=seconds_ago)
        known = NOW - timedelta(seconds=received) if received is not None else at + timedelta(seconds=1)
        allowed = bool(self.declaration.revisions and self.declaration.revisions.allowed)
        self.fields, _ = merge(
            self.fields,
            Report(source, fields, at, at, known, **kw),
            revisions=allowed,
            tracked=self.declaration.tracked_fields(),
        )
        return self

    def system(self, name: str, seconds_ago: float, fields: dict[str, Any], **kw: Any) -> "Object":
        """A system event from the key whose source is called `name`."""
        source_id = next((i for i, own in self.names.items() if own == name), None) or uuid4()
        self.names[source_id] = name
        return self.report(system_source(source_id), seconds_ago, fields, **kw)

    def read(
        self,
        purpose: Purpose = "display",
        *,
        declaration: ObjectType | None = None,
        readings: tuple[str, ...] = (),
        later: float = 0,
    ) -> ObjectRead:
        return read(
            TypedObject(self.key, self.fields, NOW, self.derived_status),
            declaration or self.declaration,
            ReadRequest(purpose, readings=readings),
            names=self.names,
            now=NOW + timedelta(seconds=later),
        )


def variant() -> Object:
    return Object(declared("item_variant"), ObjectKey("item_variant", "store", "v-10231"))


def quote() -> Object:
    return Object(declared("cotacao_plano_saude"), ObjectKey("cotacao_plano_saude", "operadora", "q-77"))


def notice() -> Object:
    return Object(declared("intimacao_com_prazo"), ObjectKey("intimacao_com_prazo", "produto", "pub-42"))


# Freshness is policy, computed at the read (L19, L26).


def test_freshness_changes_when_the_rule_changes_without_rewriting_the_object() -> None:
    item = variant().system("platform_live", 40, {"price_sale": 189.9}, provenance="live")
    stored = dict(item.fields)
    assert item.read().fields["price_sale"].status == "fresh"  # max_age 60s
    stricter = declared(
        "item_variant",
        fields__price_sale__freshness={"class": "price", "max_age": "30s", "claim_max_age": "15s"},
    )
    assert item.read(declaration=stricter).fields["price_sale"].status == "stale"
    assert item.fields == stored


def test_display_serves_stale_data_with_its_age_and_never_refuses() -> None:
    item = variant().system("platform_live", 90, {"price_sale": 189.9, "available": True})
    price = item.read("display").fields["price_sale"]
    assert (price.v, price.status, price.age_s, price.src) == (189.9, "stale", 90, "platform_live")
    assert item.read("display").refusal is None


# The retail price read to claim (item variant).


def test_a_price_may_be_claimed_only_live_and_within_its_claim_age() -> None:
    item = variant().system("platform_live", 10, {"price_sale": 189.9, "available": True})
    read_now = item.read("claim")
    assert read_now.fields["price_sale"].claim_safe
    assert not read_now.fields["available"].claim_safe  # claim_max_age 5s
    assert read_now.prohibitions == ["affirm_availability", "affirm_price"]

    fresh = variant().system("platform_live", 3, {"price_sale": 189.9, "available": True})
    assert fresh.read("claim").prohibitions == []

    # Forty seconds on, the price is still fresh to show and too old to claim: the claim contract gets the
    # prohibitions; the value is served all the same.
    older = fresh.read("claim", later=40)
    assert older.fields["price_sale"].status == "fresh"
    assert not older.fields["price_sale"].claim_safe
    assert older.prohibitions == ["affirm_availability", "affirm_price"]


def test_a_snapshot_shows_a_price_and_never_backs_a_claim() -> None:
    item = variant().report("tool:search_products", 2, {"price_sale": 179.9}, provenance="snapshot")
    price = item.read("claim").fields["price_sale"]
    assert (price.v, price.src, price.claim_safe) == (179.9, "search_snapshot", False)


def test_the_live_platform_outranks_the_search_snapshot_and_keeps_the_previous_price() -> None:
    item = variant().report("tool:search_products", 5, {"price_sale": 179.9}, provenance="snapshot")
    item.system("platform_live", 20, {"price_sale": 229.9})
    item.system("platform_live", 4, {"price_sale": 189.9})
    price = item.read("claim").fields["price_sale"]
    assert (price.v, price.src, price.claim_safe) == (189.9, "platform_live", True)
    assert price.was is not None
    assert price.was.v == 229.9


def test_a_color_nobody_enriched_is_unobserved_never_taken_from_elsewhere() -> None:
    item = variant().report("tool:search_products", 2, {"price_sale": 179.9}, provenance="snapshot")
    color = item.read().fields["color"]
    assert (color.logic, color.status, color.v) == ("unobserved", "expired", None)


def test_readings_compute_what_each_reader_asks_with_the_same_word() -> None:
    item = variant().system("platform_live", 3, {"available": True, "state": "active"})
    readings = item.read("claim", readings=("display.available", "claim.available")).readings
    assert readings["claim.available"].v is True
    later = item.read("claim", readings=("claim.available",), later=10).readings
    assert later["claim.available"].v is False
    nobody = variant().system("platform_live", 3, {"state": "active"})
    shown = nobody.read(readings=("display.available",))
    assert shown.state == "active"
    assert shown.readings["display.available"].v is True  # not observed, and the item is active


# The quote read (health plan quote): a known defect switches the source.


def test_a_known_defect_switches_the_source_and_unobserved_blocks_only_the_closing() -> None:
    q = quote().system(
        "quote_api", 60, {"price_full": 511.06, "price_discounted": 459.95, "enrollment_fee": 0}
    )
    first = q.read("claim")
    fee = first.fields["enrollment_fee"]
    assert (fee.logic, fee.src, fee.v, fee.claim_safe) == ("known_defect", "quote_api", None, False)
    assert first.blocked == {"decide:close": ["enrollment_fee"]}
    # Only what the type declares is blocked: the prices stay claimable, and the prohibitions are the fee's.
    assert first.fields["price_full"].claim_safe
    assert first.prohibitions == ["affirm_price"]
    assert first.refusal is None

    q.system("budget_document", 30, {"enrollment_fee": 120.0})
    fee = q.read("claim").fields["enrollment_fee"]
    assert (fee.logic, fee.src, fee.v, fee.claim_safe) == ("yes", "budget_document", 120.0, True)
    assert q.read("claim").blocked == {}
    assert q.read("claim").prohibitions == []
    assert resolved_state(q.fields, q.declaration, q.names, NOW)["enrollment_fee"] == 120.0


def test_a_curated_table_only_counts_when_the_company_turns_it_on() -> None:
    q = quote().system("curated_table", 10, {"price_discounted": 400.0})
    assert q.read().fields["price_discounted"].logic == "unobserved"


def test_decide_carries_the_refusal_the_company_asked_for_beside_the_values() -> None:
    q = quote().system("quote_api", 60, {"price_full": 511.06, "price_discounted": 459.95})
    q.system("budget_document", 30, {"enrollment_fee": 120.0})
    assert q.read("decide").refusal is None
    q.derived_status = "expired_by_input"  # an input of the quote changed
    decided = q.read("decide")
    assert decided.refusal is not None
    assert (decided.refusal.reason, decided.refusal.action) == ("stale_quote", "requote")
    assert decided.fields["price_full"].v == 511.06  # the values travel with it
    assert decided.fields["price_full"].status == "expired"
    assert q.read("claim").refusal is None
    assert q.read("display").refusal is None


# The deadline read (court notice): observers, revisions and a computed value with its gaps.


def test_a_machine_check_lapses_a_person_never_does_and_the_person_overrides_the_machine() -> None:
    n = notice().system("public_publications", 3600, {"addressee_is_client": True, "under_seal": False})
    first = n.read()
    addressee = first.fields["addressee_is_client"]
    assert (addressee.logic, addressee.observer) == ("yes", "machine")
    assert first.blocked == {}
    lapsed = n.read(later=25 * 3600)
    assert lapsed.fields["addressee_is_client"].logic == "unobserved"
    assert lapsed.blocked == {
        "count_deadline": ["addressee_is_client"],
        "draft": ["addressee_is_client"],
        "notify": ["addressee_is_client"],
        "derive": ["under_seal"],
        "model_read": ["under_seal"],
    }

    n.system("human", 7200, {"addressee_is_client": False})
    person = n.read(later=25 * 3600).fields["addressee_is_client"]
    assert (person.logic, person.observer, person.v) == ("no", "human", False)
    # Even while the machine's check stands, the person's is the one served.
    assert n.read().fields["addressee_is_client"].observer == "human"


def test_a_seal_nobody_checked_blocks_model_reads_and_derivation_and_nothing_else() -> None:
    n = notice().system("public_publications", 60, {"addressee_is_client": True})
    blocked = n.read("claim").blocked
    assert blocked == {"derive": ["under_seal"], "model_read": ["under_seal"]}
    assert "claim" not in blocked


def test_a_revision_of_the_notice_keeps_when_it_was_first_seen() -> None:
    held = Object(
        declared("intimacao_com_prazo", content__mode="full", content__scan="rules"),
        ObjectKey("intimacao_com_prazo", "produto", "pub-42"),
    )
    n = held.system("public_publications", 7200, {"content": "dispositive"}, received=7000)
    n.system("public_publications", 3600, {"content": "full text"}, received=100)
    content = n.read().fields["content"]
    assert content.v == "full text"
    assert content.known_at == NOW - timedelta(seconds=7000)
    # As declared, the court's text is a pointer the scan must clear in full: until then no read serves it.
    pointer = notice().system("public_publications", 3600, {"content": "s3://court/pub-42.txt"})
    assert pointer.read().withheld == {"content": "scan"}


def test_the_deadline_is_the_companys_value_with_its_gaps() -> None:
    n = notice().system("public_publications", 60, {"addressee_is_client": True})
    assert "due_date" not in n.read("claim").values
    n.system("public_publications", 30, {"due_date": "2026-10-15"}, version="2")
    deadline = n.read("claim")
    due = deadline.values["due_date"]
    # The version is the fact's own, counted by the producer as the value is revised, not the source's.
    assert (due.v, due.logic, due.version, due.rule, due.claim_safe) == (
        "2026-10-15",
        "yes",
        1,
        "prazo_forense@v9",
        True,
    )
    assert due.gap_effect == "errs_early"
    assert deadline.declared_gaps == [
        "feriado_estadual_e_municipal",
        "portaria_de_tribunal",
        "prazo_em_dobro",
    ]
    assert "due_date" not in deadline.fields
    assert deadline.prohibitions == []
    # A notice whose text named no term: a named absence, never zero or null.
    none = notice().system("public_publications", 30, {"due_date": "sem_prazo"})
    absent = none.read("claim").values["due_date"]
    assert (absent.v, absent.logic, absent.absent_as, absent.claim_safe) == (None, "no", "sem_prazo", True)


@pytest.mark.parametrize("purpose", ["display", "claim", "decide"])
def test_every_purpose_reads_the_same_values_and_stamps(purpose: Purpose) -> None:
    n = notice().system("public_publications", 60, {"addressee_is_client": True})
    served = n.read(purpose)
    assert served.fields["addressee_is_client"].v is True
    assert served.refusal is None  # the notice's decide serves with age
