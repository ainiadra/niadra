"""Dump names, restore names, the retired copies' retention, and the deletion receipt (9, 11)."""

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from niadra.domain.signals.storage import bi_key, daily_key, demand_key, intake_key, raw_key
from niadra.domain.spaces.backups import (
    DumpInfo,
    backup_prefix,
    dump_key,
    latest_key,
    restore_name,
    retired_at,
    retired_name,
    retired_to_drop,
)
from niadra.domain.spaces.deletion import (
    RECEIPT_FIELDS,
    DeletionReceipt,
    receipt_hash,
    space_object_prefixes,
)
from niadra.domain.spaces.directory import db_name_for

NOW = datetime(2026, 9, 24, 2, 30, 15, tzinfo=UTC)
SPACE = UUID("0192f5a0-0000-7000-8000-00000000a001")
SRC = Path(__file__).resolve().parents[3] / "src" / "niadra"


def test_dumps_are_named_by_database_and_minute_with_a_latest_pointer() -> None:
    name = db_name_for(SPACE)
    assert dump_key(name, NOW) == f"backups/{name}/20260924T0230.dump"
    assert latest_key(name) == f"backups/{name}/latest.json"
    assert dump_key(name, NOW).startswith(backup_prefix(name))


def test_the_latest_dump_document_round_trips() -> None:
    info = DumpInfo("backups/x/1.dump", "x", 42, "ab" * 32, "9a050517b420", NOW)
    assert DumpInfo.from_document(json.loads(json.dumps(info.document()))) == info


def test_restore_and_retired_names_are_valid_identifiers_and_carry_their_time() -> None:
    name = db_name_for(SPACE)
    for derived in (restore_name(name, NOW), retired_name(name, NOW)):
        assert re.fullmatch(r"[a-z0-9_]{1,63}", derived), derived
    assert retired_at(retired_name(name, NOW)) == NOW.replace(microsecond=0)
    assert retired_at(restore_name(name, NOW)) is None


def test_retired_copies_are_dropped_after_a_week() -> None:
    name = db_name_for(SPACE)
    names = [retired_name(name, NOW - timedelta(days=8)), retired_name(name, NOW - timedelta(days=6)), name]
    assert retired_to_drop(names, NOW) == [names[0]]


def test_the_receipt_document_has_every_field_and_a_stable_hash() -> None:
    receipt = DeletionReceipt(
        space_id=SPACE,
        cell_id="us-east-2-1",
        db_name=db_name_for(SPACE),
        dropped_at=NOW,
        dek_versions_destroyed=[2, 1],
        objects_deleted=7,
        backups_deleted=3,
        secrets_deleted=1,
        instance_snapshot_expiry=NOW + timedelta(days=1),
    )
    document = receipt.document()
    assert set(document) == RECEIPT_FIELDS
    assert document["dek_versions_destroyed"] == [1, 2]
    assert document["dropped_at"] == "2026-09-24T02:30:15+00:00"
    # The control plane recomputes the hash over the document as received: key order never matters.
    reordered = dict(reversed(list(document.items())))
    assert receipt_hash(reordered) == receipt_hash(document)
    assert len(receipt_hash(document)) == 32


def test_every_object_the_code_writes_for_a_space_is_under_a_prefix_a_deletion_removes() -> None:
    """A new kind of object under another prefix would outlive a deleted space: add it to
    `space_object_prefixes` (and here, if it is not a space's)."""
    prefixes = {p.replace(str(SPACE), "{space}") for p in space_object_prefixes(SPACE)}
    keyed = re.compile(r'f"([a-z_/]+)/\{(?:space_id|task\.space_id|space)\}')
    found = {
        match.group(1) + "/{space}/"
        for path in SRC.rglob("*.py")
        for match in keyed.finditer(path.read_text())
        if "/" in match.group(1) or match.group(1) in {"raw", "media", "imports", "audit", "exports"}
    }
    # Secret references and cache keys are not objects; the secret store and the cache purge cover them.
    found.discard("spaces/{space}/")
    assert found, "the scan found no object keys: the pattern is out of date"
    assert found <= prefixes, f"objects outside the deletion prefixes: {sorted(found - prefixes)}"


def test_deleting_a_space_removes_its_raw_interactions_waiting_batches_and_daily_aggregates() -> None:
    prefixes = space_object_prefixes(SPACE)
    handle, object_id = uuid4(), uuid4()
    day = date(2026, 9, 29)
    written = (
        raw_key(SPACE, handle, day, object_id),
        intake_key(SPACE, object_id),
        daily_key(SPACE, day),
        demand_key(SPACE, day),
        bi_key(SPACE, object_id),
    )
    assert all(any(key.startswith(p) for p in prefixes) for key in written)
