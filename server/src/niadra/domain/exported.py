"""Rows of the continuous export (22.3), built from the decrypted records the repositories return."""

import dataclasses
from typing import Any


def export_row(record: Any, change_seq: int, *, drop: tuple[str, ...] = ()) -> dict[str, Any]:
    """The record's fields (nested records as objects) plus `change_seq`, which orders the export."""
    row = {k: v for k, v in dataclasses.asdict(record).items() if k not in drop}
    row["change_seq"] = change_seq
    return row
