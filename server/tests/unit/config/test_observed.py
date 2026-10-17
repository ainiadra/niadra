"""A space's configuration with what its traffic showed beneath it: the declaration wins key
by key, an observed type that does not hold together is left out, and every process serves the observed types
within one refresh of the cell writing them."""

from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niadra.app.config import CachedConfigProvider, ConfigService
from niadra.config.object_types import ObjectTypeRegistry
from niadra.config.observed import with_observed
from niadra.domain.config import OBSERVED_DOC, OBSERVED_VERSION, StoredDocument
from niadra.testing.cell import SPACE_ID, Cell, sample_config
from tests.unit.config.conftest import CELL_ID, SnapshotFactory

DECLARED = ObjectTypeRegistry.model_validate(
    {
        "types": [
            {
                "type": "legal_case",
                "ownership": "subject",
                "mirror_of": {"system": "court"},
                "fields": {"cnj": {"type": "string", "label": "Número CNJ"}},
            }
        ]
    }
)
DEADLINE: dict[str, Any] = {
    "type": "deadline",
    "ownership": "subject",
    "mirror_of": {"system": "court", "derived_by": "observation"},
    "fields": {"due_on": {"type": "date"}, "protocol": {"type": "string", "label": "Protocol"}},
    "relations": {"legal_case": {"type": "legal_case", "via": "case_id"}},
    "timers": {"notice": {"due": "due_on - 48h", "on_fire": ["notify"]}},
}


def test_an_observed_type_joins_and_a_declared_type_is_never_touched() -> None:
    observed = [DEADLINE, {**DEADLINE, "type": "legal_case", "relations": {}}]
    merged = DECLARED.under(observed)
    case = merged.get("legal_case")
    assert case is not None
    assert case == DECLARED.get("legal_case")
    deadline = merged.get("deadline")
    assert deadline is not None
    assert deadline.relations["legal_case"].via == "case_id"
    assert "due_on" in merged.date_fields()["deadline"]


def test_an_observed_text_field_stays_held_value_by_value_as_an_undeclared_one() -> None:
    """No person ruled the field not personal: it names the object and finds it in a conversation, and its
    values pass the same hold an undeclared field's do (`domain.objects.undeclared`)."""
    deadline = DECLARED.under([DEADLINE]).get("deadline")
    assert deadline is not None
    assert deadline.observed_fields == {"protocol"}
    assert "protocol" not in DECLARED.under([DEADLINE]).declared_names()["deadline"]
    assert "due_on" in DECLARED.under([DEADLINE]).declared_names()["deadline"]
    case = DECLARED.get("legal_case")
    assert case is not None
    assert case.observed_fields == frozenset()


def test_an_observed_type_that_does_not_hold_together_is_left_out_and_the_declaration_serves() -> None:
    broken = {**DEADLINE, "lifecycle": {"outcome": {"final": ["arquivado"]}}}
    assert [t.type for t in DECLARED.under([broken]).types] == ["legal_case"]


def test_a_space_without_observed_types_is_served_as_declared() -> None:
    config = sample_config()
    assert with_observed(config, []) is config


async def test_another_process_serves_the_observed_types_within_one_refresh(
    cell: Cell, control_key: Ed25519PrivateKey, make_snapshot: SnapshotFactory
) -> None:
    service = ConfigService(cell.uow, [control_key.public_key()], CELL_ID, cell_version="1.4.0")
    declared = sample_config().model_copy(update={"object_types": DECLARED})
    await service.apply(make_snapshot(1, declared))
    provider = CachedConfigProvider(cell.uow, CELL_ID)
    await provider.refresh()
    assert provider.space(SPACE_ID).object_types.get("deadline") is None
    document = {"version": 1, "types": [DEADLINE]}
    async with cell.uow.cell() as uow:
        await uow.config.save_document(StoredDocument(SPACE_ID, OBSERVED_DOC, "h1", 1, document))
        await uow.config.set_state(OBSERVED_VERSION, 1)
    await provider.refresh()
    assert provider.space(SPACE_ID).object_types.get("deadline") is not None
    # A new snapshot of the declaration keeps the observed types beneath it.
    await service.apply(make_snapshot(2, declared.model_copy(update={"region": "sa-east-1"})))
    await provider.refresh()
    assert provider.space(SPACE_ID).region == "sa-east-1"
    assert provider.space(SPACE_ID).object_types.get("deadline") is not None
