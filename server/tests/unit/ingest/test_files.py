"""File imports (5.2, 5.3): streamed into parts, ingested in batches, resumable and bounded."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import orjson
import pytest

from niadra.app.collaborators import PendingMerge
from niadra.app.ingest import IngestService
from niadra.config.models import WebhookEndpoint
from niadra.domain.common.errors import Forbidden, InvalidInput, NotFound
from niadra.domain.ingest.files import FileFormat, ImportStatus, RecordSplitter, split_records
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import AssertionMethod, Scope
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, Cell, principal
from tests.unit.ingest.fakes import build, message

CSV = (
    b"phone_e164,email,system_id,system_id_scope,idempotency_key\n"
    b"+5511988887777,ana@example.com,C-1,crm,\n"
    b"\n"
    b'+5511977776666,"bruno@example.com",C-2,crm,row-3\n'
    b"+5511966665555,,,,\n"
)


async def chunks(data: bytes, size: int = 7) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


def cut(
    format: FileFormat, data: bytes, part_bytes: int, chunk: int = 5
) -> tuple[RecordSplitter, list[bytes]]:
    splitter = RecordSplitter(format, part_bytes)
    parts = [p for start in range(0, len(data), chunk) for p in splitter.feed(data[start : start + chunk])]
    parts += splitter.finish()
    return splitter, [p.data for p in parts]


def test_parts_never_cut_a_quoted_newline_and_count_every_record() -> None:
    data = b'email,system_id\n"a@x.com","line one\nline two"\nb@x.com,2\nc@x.com,3'
    splitter, parts = cut(FileFormat.CSV, data, part_bytes=10)

    assert splitter.header == ("email", "system_id")
    assert b"".join(parts) == data[data.index(b"\n") + 1 :]
    assert all(p.count(b'"') % 2 == 0 for p in parts)
    rows = [
        row for i, part in enumerate(parts) for row, _ in split_records(FileFormat.CSV, part, i * 100 + 1)
    ]
    assert len(rows) == 3


def test_split_records_numbers_rows_like_the_splitter_counts_them() -> None:
    data = b'{"a": 1}\n\n{"b": 2}\n'
    splitter = RecordSplitter(FileFormat.JSONL, part_bytes=1)
    parts = splitter.feed(data) + splitter.finish()

    assert [p.records for p in parts] == [3]
    assert list(split_records(FileFormat.JSONL, parts[0].data, 1)) == [(1, b'{"a": 1}'), (3, b'{"b": 2}')]


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (b"ana@example.com,+5511988887777\n", "2 unknown CSV columns"),
        (b"email,email\n", "repeats a column"),
        (b"idempotency_key\n", "no handle column"),
        (b'email\n"never closed\n', "never closed"),
    ],
)
def test_bad_files_are_refused_without_echoing_values(data: bytes, problem: str) -> None:
    with pytest.raises(InvalidInput) as refused:
        cut(FileFormat.CSV, data, part_bytes=1024)

    assert problem in str(refused.value.detail)
    assert "ana@" not in str(refused.value.detail)


def test_a_record_over_one_megabyte_is_refused() -> None:
    with pytest.raises(InvalidInput):
        RecordSplitter(FileFormat.JSONL).feed(b"x" * 1_000_001)


async def run(cell: Cell, service: IngestService) -> list[Task]:
    tasks = [t for t in cell.uow.store.data["pipeline"]["outbox"] if t.name is TaskName.IMPORT_FILE]
    for task in tasks:
        await service.import_file(task)
    return tasks


async def test_a_csv_seeds_identity_row_by_row_and_reports_row_errors(cell: Cell) -> None:
    service, _, identity = build(cell)
    identity.merge = PendingMerge(UUID(int=1), (UUID(int=2),), (UUID(int=3),))
    caller = principal()

    record = await service.upload_file(caller, FileFormat.CSV, chunks(CSV))
    await run(cell, service)
    done = await service.file_import(caller, record.import_id)

    assert record.status is ImportStatus.QUEUED
    assert (done.status, done.accepted, done.rejected, done.records) == (ImportStatus.COMPLETED, 2, 1, 4)
    assert done.errors == ({"row": 4, "code": "invalid_input", "detail": done.errors[0]["detail"]},)
    assert "5511966665555" not in str(done.errors)
    assert {c.method for c in identity.calls} == {AssertionMethod.SYSTEM_IMPORT}
    assert [len(c.handles) for c in identity.calls] == [3, 3]
    # An import leaves its merges to the workers, row after row, instead of waiting on each one.
    assert identity.merged == []
    assert not [k for k in cell.objects.objects if k.startswith("imports/")]


async def test_reimporting_the_same_file_only_finds_duplicates(cell: Cell) -> None:
    service, _, _ = build(cell)
    first = await service.upload_file(principal(), FileFormat.CSV, chunks(CSV))
    await run(cell, service)
    cell.uow.store.data["pipeline"]["outbox"].clear()

    second = await service.upload_file(principal(), FileFormat.CSV, chunks(CSV))
    await run(cell, service)

    again = await service.file_import(principal(), second.import_id)
    assert first.sha256 == second.sha256
    assert (again.accepted, again.duplicates) == (0, 2)


async def test_jsonl_runs_in_batches_and_a_retry_resumes_where_the_commit_left(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = b"".join(orjson.dumps(message(f"m{i}")) + b"\n" for i in range(1203))
    service, _, _ = build(cell)
    record = await service.upload_file(principal(), FileFormat.JSONL, chunks(lines, 4096))
    [task] = [t for t in cell.uow.store.data["pipeline"]["outbox"] if t.name is TaskName.IMPORT_FILE]
    real = service._ingest
    calls = 0

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("worker died")
        return await real(*args, **kwargs)

    monkeypatch.setattr(service, "_ingest", flaky)
    with pytest.raises(RuntimeError):
        await service.import_file(task)
    halfway = await service.file_import(principal(), record.import_id)
    await service.import_file(task)
    done = await service.file_import(principal(), record.import_id)

    assert (halfway.status, halfway.accepted, halfway.next_row) == (ImportStatus.RUNNING, 500, 500)
    assert (done.status, done.accepted, done.duplicates) == (ImportStatus.COMPLETED, 1203, 0)
    assert calls == 4


async def test_uploads_need_track_and_follow_the_uploader(cell: Cell) -> None:
    service, _, _ = build(cell)
    reader = principal(scopes=frozenset({Scope.CONTEXT}))

    with pytest.raises(Forbidden):
        await service.upload_file(reader, FileFormat.JSONL, chunks(b'{"a": 1}\n'))
    with pytest.raises(InvalidInput):
        await service.upload_file(principal(), FileFormat.JSONL, chunks(b""))
    record = await service.upload_file(principal(), FileFormat.JSONL, chunks(b'{"a": 1}\n'))

    with pytest.raises(NotFound):
        await service.file_import(principal(VOICE_SOURCE), record.import_id)
    assert len([k for k in cell.objects.objects if k.startswith(f"imports/{SPACE_ID}/")]) == 1


async def test_a_missing_part_fails_the_import_instead_of_retrying_forever(cell: Cell) -> None:
    service, _, _ = build(cell)
    record = await service.upload_file(principal(), FileFormat.JSONL, chunks(orjson.dumps(message("m1"))))
    await cell.objects.delete_prefix("imports/")

    [task] = await run(cell, service)
    await service.import_file(task)

    failed = await service.file_import(principal(), record.import_id)
    assert failed.status is ImportStatus.FAILED
    assert failed.errors[-1]["code"] == "import_failed"


def endpoint(endpoint_id: str, source_id: Any) -> WebhookEndpoint:
    return WebhookEndpoint(
        endpoint_id=endpoint_id,
        url=f"https://{endpoint_id}.example/hooks",
        source_id=source_id,
        event_types=["import.completed", "import.failed"],
        secret_refs=[f"spaces/x/webhook/{endpoint_id}"],
    )


def import_events(cell: Cell) -> list[tuple[str, str, dict[str, Any]]]:
    rows = cell.uow.store.module("notify").get("deliveries", {}).values()
    return [(d.endpoint_id, d.event_type, d.body) for d in rows if d.event_type.startswith("import.")]


async def test_the_end_of_an_import_reaches_only_the_importing_sources_endpoints(cell: Cell) -> None:
    config = cell.config.space(SPACE_ID)
    mine, theirs = endpoint("mine", principal().source_id), endpoint("theirs", VOICE_SOURCE)
    cell.config.put(config.model_copy(update={"webhook_endpoints": [mine, theirs]}))
    service, _, _ = build(cell)

    record = await service.upload_file(principal(), FileFormat.CSV, chunks(CSV))
    await run(cell, service)
    [(to, event_type, body)] = import_events(cell)
    assert (to, event_type) == ("mine", "import.completed")
    data = body["data"]
    assert data["import_id"] == str(record.import_id)
    assert (data["records"], data["accepted"], data["rejected"]) == (4, 2, 1)
    assert data["error_codes"] == ["invalid_input"]
    assert "5511" not in str(body)

    failing = await service.upload_file(principal(), FileFormat.JSONL, chunks(orjson.dumps(message("m9"))))
    await cell.objects.delete_prefix("imports/")
    await run(cell, service)
    last = import_events(cell)[-1]
    assert (last[0], last[1], last[2]["data"]["import_id"]) == (
        "mine",
        "import.failed",
        str(failing.import_id),
    )
