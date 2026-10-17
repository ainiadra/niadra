"""A pattern rule changed by an approved diff reaches every profile it may touch, not only the next one
whose memory moves."""

from niadra.app.traits import TraitsService, rules_fingerprint
from niadra.config.models import TraitRuleConfig
from niadra.domain.common.ids import uuid7
from niadra.domain.records import Trait
from niadra.domain.tasks import Task, TaskName
from niadra.testing.cell import SPACE_ID, Cell, sample_config
from tests.unit.objects.fakes import T0

RULE = TraitRuleConfig(rule_id="visitas", catalog="recurring_complaint", version="v1")


def outbox(cell: Cell, name: TaskName) -> list:  # type: ignore[type-arg]
    return [t for t in cell.uow.store.module("pipeline").setdefault("outbox", []) if t.name is name]


async def test_a_changed_rule_asks_a_space_wide_derivation_once(cell: Cell) -> None:
    service = TraitsService(cell.uow, cell.clock, cell.config)
    cell.config.put(sample_config().model_copy(update={"trait_rules": [RULE]}))
    assert await service.watch_rules(SPACE_ID) == 0  # the first pass only records the rules
    assert await service.watch_rules(SPACE_ID) == 0
    assert outbox(cell, TaskName.REDERIVE_TRAITS) == []
    changed = RULE.model_copy(update={"version": "v2", "params": {"min_count": 3}})
    cell.config.put(sample_config().model_copy(update={"trait_rules": [changed]}))
    assert await service.watch_rules(SPACE_ID) == 1
    assert await service.watch_rules(SPACE_ID) == 0
    assert len(outbox(cell, TaskName.REDERIVE_TRAITS)) == 1
    # Turning a rule off is a change too.
    cell.config.put(
        sample_config().model_copy(update={"trait_rules": [changed.model_copy(update={"enabled": False})]})
    )
    assert await service.watch_rules(SPACE_ID) == 1


def test_the_fingerprint_follows_rules_windows_and_sensitive_categories() -> None:
    base = sample_config().model_copy(update={"trait_rules": [RULE]})
    assert rules_fingerprint(base) == rules_fingerprint(base.model_copy())
    wider = base.model_copy(
        update={"settings": base.settings.model_copy(update={"trait_allowed_categories": ["billing"]})}
    )
    assert rules_fingerprint(wider) != rules_fingerprint(base)
    assert 0 <= rules_fingerprint(base) < 2**63


async def test_the_space_wide_derivation_reaches_active_profiles_and_pattern_holders(cell: Cell) -> None:
    service = TraitsService(cell.uow, cell.clock, cell.config)
    holder = uuid7()
    async with cell.uow(SPACE_ID) as uow:
        trait = Trait(uuid7(), holder, 1, "recurring_complaint", "visit", "volatile", "r1", T0, T0, T0, 0.9)
        await uow.traits.save_trait(trait, [])
    await service.rederive_space(Task(TaskName.REDERIVE_TRAITS, SPACE_ID))
    assert [t.profile_id for t in outbox(cell, TaskName.DERIVE_TRAITS)] == [holder]
