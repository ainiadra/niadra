"""One test per path that delays a task, each writing its delay to a real space database first.

Each test runs the unit test of the path (the scenario and its own assertions) and then checks what the
space database stored. The paths of the worker runtime, extraction, memory, ingest and audit live here;
the modules whose unit tests share fixture names have a file of their own.
"""

from niadra.domain.pipeline.delay import DelayReason
from niadra.testing.cell import Cell
from tests.unit.audit import test_service as audit
from tests.unit.audit.test_service import base, clock, serve  # the audit path's fixtures
from tests.unit.extract import test_reextract as reextract
from tests.unit.extract import test_service as extract
from tests.unit.flow import test_valid_until as valid_until
from tests.unit.ingest import test_service as ingest
from tests.unit.pipeline import test_runtime as runtime


async def test_space_busy(cell: Cell, delays_on_postgres: list[str]) -> None:
    await runtime.test_one_space_never_holds_every_slot_of_a_pool(cell)
    assert delays_on_postgres == [DelayReason.SPACE_BUSY]


async def test_lease_busy_before_the_handler(cell: Cell, delays_on_postgres: list[str]) -> None:
    await runtime.test_a_busy_lease_reschedules_without_counting_a_failure(cell)
    assert delays_on_postgres == [DelayReason.LEASE_BUSY]


async def test_lease_busy_when_the_lease_is_lost(cell: Cell, delays_on_postgres: list[str]) -> None:
    await runtime.test_lease_lost_inside_the_handler_reschedules_as_busy(cell)
    assert delays_on_postgres == [DelayReason.LEASE_BUSY]


async def test_retry_of_a_failed_attempt(cell: Cell, delays_on_postgres: list[str]) -> None:
    await runtime.test_a_failure_is_retried_with_backoff_then_buried(cell)
    assert delays_on_postgres == [DelayReason.RETRY] * 4


async def test_llm_retry(cell: Cell, delays_on_postgres: list[str]) -> None:
    await extract.test_llm_failure_is_retried_with_backoff_then_falls_back_to_a_template(cell)
    assert delays_on_postgres == [DelayReason.LLM_RETRY] * 2


async def test_llm_budget(cell: Cell, delays_on_postgres: list[str]) -> None:
    await extract.test_budget_exhaustion_waits_for_the_next_day_without_spending_an_attempt(cell)
    assert delays_on_postgres == [DelayReason.LLM_BUDGET]


async def test_reextract_page_pause(cell: Cell, monkeypatch, delays_on_postgres: list[str]) -> None:
    await reextract.test_the_pass_pages_and_goes_on_the_next_day_past_its_daily_share(cell, monkeypatch)
    assert delays_on_postgres == [DelayReason.REEXTRACT] * 2


async def test_valid_until(cell: Cell, delays_on_postgres: list[str]) -> None:
    await valid_until.test_a_declared_validity_reaches_the_fact_and_ends_it(cell)
    assert DelayReason.VALID_UNTIL in delays_on_postgres


async def test_safety_net(cell: Cell, delays_on_postgres: list[str]) -> None:
    await ingest.test_a_message_is_stored_encrypted_in_the_hot_layer_and_opens_a_session(cell)
    assert delays_on_postgres == [DelayReason.SAFETY_NET]


async def test_audit_anchor_waits_for_the_receipt_watermark(
    base, clock, serve, delays_on_postgres: list[str]
) -> None:
    await audit.test_the_anchor_waits_while_the_receipt_watermark_is_inside_the_day(base, clock, serve)
    assert delays_on_postgres == [DelayReason.RECEIPT_WATERMARK]
