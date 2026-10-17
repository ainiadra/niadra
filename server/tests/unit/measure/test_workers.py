"""Worker wiring: each task name reaches the service method that handles it."""

from types import SimpleNamespace

from niadra.adapters.inbound.workers import configure, measure, review
from niadra.domain.tasks import CATALOG, QueueClass, TaskName


def test_handlers_map_the_catalog_tasks():
    c = SimpleNamespace(
        measure=SimpleNamespace(measure="m", measure_transfer="mt"),
        review=SimpleNamespace(sample_review="sr"),
        configure=SimpleNamespace(assist="a"),
    )
    assert measure.handlers(c) == {TaskName.MEASURE: "m", TaskName.MEASURE_TRANSFER: "mt"}
    assert review.handlers(c) == {TaskName.SAMPLE_REVIEW: "sr"}
    assert configure.handlers(c) == {TaskName.ASSIST: "a"}
    assert {
        CATALOG[t].queue for t in (TaskName.MEASURE, TaskName.MEASURE_TRANSFER, TaskName.SAMPLE_REVIEW)
    } == {QueueClass.MEASURE}
