import dataclasses
import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from niadra.domain.privacy.erasure import (
    COLD_FEEDBACK,
    ErasedRow,
    ErasureRequest,
    ErasureScope,
    ErasureStatus,
    TargetKind,
    blob_prefixes,
    erasure_receipt_hash,
    request_id_for,
    tally,
)
from niadra.domain.privacy.export import (
    SETTLE,
    ExportRun,
    ExportStatus,
    TombstoneRecord,
    corrections,
    file_path,
    pseudonymized,
    settled,
    split_erased,
)
from niadra.domain.privacy.files import JsonLinesZip, from_parquet, to_parquet
from niadra.domain.privacy.retention import DataClass, cutoffs, month_bounds, month_expired, month_to_archive

NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)


def test_request_ids_are_stable_per_space_and_key() -> None:
    space = uuid4()
    assert request_id_for(space, "k") == request_id_for(space, "k")
    assert request_id_for(space, "k") != request_id_for(space, "k2")
    assert request_id_for(space, "k") != request_id_for(uuid4(), "k")
    assert request_id_for(space, "k").version == 8


def test_scope_survives_its_json_round_trip() -> None:
    scope = ErasureScope(
        uuid4(), TargetKind.CONVERSATION, (uuid4(), uuid4()), uuid4(), "c-1", NOW, pseudonym="pz"
    )
    assert ErasureScope.from_json(json.loads(json.dumps(scope.to_json()))) == scope
    assert scope.with_events((uuid4(),)).event_ids
    # Requests stored before scopes carried the pseudonym still load.
    older = {k: v for k, v in scope.to_json().items() if k != "pseudonym"}
    assert ErasureScope.from_json(older).pseudonym is None


def test_tally_counts_tables_and_collects_runs_once() -> None:
    run = uuid4()
    counts = tally(
        {}, [ErasedRow("events", {"event_id": "1"}), ErasedRow("events", {"event_id": "2"})], [run]
    )
    counts = tally(counts, [ErasedRow("facts", {"fact_id": "3"})], [run])
    assert counts["tables"] == {"events": 2, "facts": 1}
    assert counts["export_runs"] == [str(run)]


def test_tally_keeps_the_cold_feedback_ids_off_the_receipt() -> None:
    cold = [ErasedRow(COLD_FEEDBACK, {"event_id": "b"}), ErasedRow(COLD_FEEDBACK, {"event_id": "a"})]
    counts = tally({}, [*cold, ErasedRow("facts", {"fact_id": "3"})], [])
    counts = tally(counts, [ErasedRow(COLD_FEEDBACK, {"event_id": "a"})], [])
    assert counts["tables"] == {"facts": 1}
    assert counts[COLD_FEEDBACK] == ["a", "b"]


def test_the_erasure_receipt_commits_to_what_was_erased() -> None:
    request = ErasureRequest(
        uuid4(),
        TargetKind.HANDLE,
        "h",
        ErasureStatus.COMPLETED,
        "user:u",
        NOW,
        counts={"tables": {"events": 2}},
    )
    same = erasure_receipt_hash(request, NOW)
    assert same == erasure_receipt_hash(request, NOW)
    more = dataclasses.replace(request, counts={"tables": {"events": 3}})
    assert erasure_receipt_hash(more, NOW) != same


def test_blob_prefixes_cover_raw_and_media_of_each_handle() -> None:
    space, handle = uuid4(), uuid4()
    assert blob_prefixes(space, (handle,)) == [f"raw/{space}/{handle}/", f"media/{space}/{handle}/"]


def test_retention_cutoffs_and_months() -> None:
    cut = cutoffs(NOW)
    assert cut[DataClass.PRIVACY_FEED] == NOW - timedelta(hours=72)
    # Memory never cuts: the company's events stay until it sets a window; they went after five
    # years.
    assert DataClass.COLD_EVENTS not in cut
    assert cutoffs(NOW, {"cold_events": 730})[DataClass.COLD_EVENTS] == NOW - timedelta(days=730)
    tuned = cutoffs(NOW, {"review_samples": 30, "tombstones": 30})
    assert tuned[DataClass.REVIEW_SAMPLES] == NOW - timedelta(days=30)
    # Tombstones keep the export's promise; a tenant cannot shorten them.
    assert tuned[DataClass.TOMBSTONES] == NOW - timedelta(days=395)
    assert month_bounds("2026-12") == (datetime(2026, 12, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    assert month_to_archive(NOW, 90) == "2026-05"
    assert month_expired("2026-05", datetime(2026, 6, 1, tzinfo=UTC))
    assert not month_expired("2026-05", datetime(2026, 5, 31, tzinfo=UTC))


def test_exports_settle_and_skip_erased_rows() -> None:
    run = ExportRun(uuid4(), "lake", 10, 20, ExportStatus.WAITING, NOW)
    assert not settled(run, NOW + SETTLE - timedelta(seconds=1))
    assert settled(run, NOW + SETTLE)
    kept, erased = split_erased(
        [{"event_id": "1", "change_seq": 11}, {"event_id": "2", "change_seq": 12, "erased": True}]
    )
    assert kept == [{"event_id": "1", "change_seq": 11, "op": "upsert"}]
    assert erased == [{"event_id": "2", "change_seq": 12}]
    assert (
        file_path("lake/", "events", run, 3)
        == "lake/events/00000000000000000010-00000000000000000020/part-00003.parquet"
    )
    later = [TombstoneRecord(21, "events", {"event_id": "1"}, NOW), TombstoneRecord(22, "packs", {}, NOW)]
    assert corrections({"events"}, later) == later[:1]


def test_parquet_round_trip_keeps_ids_times_and_bytes() -> None:
    row = {
        "id": uuid4(),
        "at": NOW,
        "blob": b"\x00\x01",
        "tags": ("a", "b"),
        "nested": {"k": 1},
        "day": date(2026, 9, 1),
    }
    (back,) = from_parquet(to_parquet([row]))
    assert back["id"] == str(row["id"])
    assert back["at"] == NOW
    assert back["blob"] == b"\x00\x01"
    assert back["tags"] == ["a", "b"]
    assert json.loads(back["nested"]) == {"k": 1}


def test_zipped_json_lines_carry_a_digest_per_file() -> None:
    archive = JsonLinesZip()
    for name, records in {"facts": [{"id": uuid4(), "at": NOW}], "empty": []}.items():
        archive.section(name)
        archive.write(records)
    data = archive.finish()
    with zipfile.ZipFile(io.BytesIO(data)) as unzipped:
        assert sorted(unzipped.namelist()) == ["empty.jsonl", "facts.jsonl"]
        assert json.loads(unzipped.read("facts.jsonl"))["at"] == NOW.isoformat()
    assert set(archive.digests) == {"empty.jsonl", "facts.jsonl"}


def test_a_pseudonymized_export_leaves_the_handle_value_out_and_keeps_its_keyed_hash() -> None:
    # `pseudonymize` was never read.
    row = {"handle_id": "h1", "type": "phone_e164", "value": "+5511999990001", "value_hmac": "ab12"}
    assert pseudonymized("handles", row) == {"handle_id": "h1", "type": "phone_e164", "value_hmac": "ab12"}
    fact = {"fact_id": "f1", "predicate": "plan", "value": "fibra"}
    assert pseudonymized("facts", fact) == fact
