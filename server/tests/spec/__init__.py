"""A copy of files of the open specs (`spec/` in the monorepo), at the same paths, that the server's reference
implementations must pass: the vectors (`vectors/<name>.v0.json`) and the example claim contracts. They are
copied as published, never edited here; a case field a test does not know fails the test instead of being
skipped."""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent


def load(path: str) -> Any:
    return json.loads((ROOT / path).read_text())


def cases(name: str, fields: set[str]) -> list[dict[str, Any]]:
    """The cases of `vectors/<name>.v0.json`, each checked to hold only `fields` (and `id`, `expect`)."""
    document = load(f"vectors/{name}.v0.json")
    assert document["vectors"] == name
    assert document["version"] == "v0"
    for case in document["cases"]:
        unknown = set(case) - fields - {"id", "expect"}
        assert not unknown, f"{case['id']}: unknown case fields {sorted(unknown)}"
    ids = [case["id"] for case in document["cases"]]
    assert len(set(ids)) == len(ids), "case ids are unique"
    return list(document["cases"])
