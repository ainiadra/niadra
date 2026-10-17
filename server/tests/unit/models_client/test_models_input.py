import random
from itertools import pairwise

import pytest

from niadra.domain.models_input import (
    MAX_CHARS,
    OVERLAP,
    groups,
    merge_spans,
    split,
    units,
    windows,
)


def test_a_text_that_fits_is_one_window() -> None:
    assert windows("abc") == [(0, 3)]
    assert windows("a" * MAX_CHARS) == [(0, MAX_CHARS)]
    assert units(["a", "b" * MAX_CHARS]) == 2


@pytest.mark.parametrize("seed", range(40))
def test_windows_cover_the_text_fit_and_overlap(seed: int) -> None:
    rng = random.Random(seed)
    words = ["palavra", "fatura.", "ok!", "\n", "cliente", "x" * rng.randint(1, 60)]
    text = " ".join(rng.choice(words) for _ in range(rng.randint(3_000, 12_000)))
    size, overlap = rng.choice([(1_000, 100), (5_000, 500), (MAX_CHARS, OVERLAP)])
    spans = windows(text, size, overlap)
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    for (a, b), (c, _) in pairwise(spans):
        assert b - a <= size
        assert a < c <= b - overlap  # moves on, and shares at least `overlap` characters
    assert spans[-1][1] - spans[-1][0] <= size
    # Any value up to `overlap` characters long lies whole in one window.
    for _ in range(200):
        start = rng.randrange(0, len(text) - overlap)
        end = start + rng.randint(1, overlap)
        assert any(a <= start and end <= b for a, b in spans)


def test_a_window_ends_at_a_line_break_before_a_sentence_or_a_word() -> None:
    lines = "".join(f"customer: turno {i} com algum texto aqui. E mais.\n" for i in range(2_000))
    for a, b in windows(lines)[:-1]:
        assert lines[b - 1] == "\n"
        assert a == 0 or lines[a - 1] in ("\n", " ")
    sentences = "Uma frase. " * 5_000
    for _, b in windows(sentences)[:-1]:
        assert sentences[b - 2 : b] == ". "
    no_space = "x" * 45_000
    assert windows(no_space) == [(0, 20_000), (19_500, 39_500), (39_000, 45_000)]


def test_split_keeps_each_windows_text_and_offset() -> None:
    long = "abc " * 6_000
    parts = split(["short", long])
    assert parts[0].source == 0
    assert parts[0].text == "short"
    assert [p.source for p in parts[1:]] == [1] * (len(parts) - 1)
    assert all(long[p.start : p.start + len(p.text)] == p.text for p in parts[1:])


def test_groups_never_exceed_the_request_size() -> None:
    assert [len(g) for g in groups(list(range(600)), 256)] == [256, 256, 88]
    assert groups([], 256) == [[]]


def test_merge_spans_joins_one_value_found_by_two_windows() -> None:
    found = [(10, 30, "email"), (12, 30, "email"), (10, 30, "email"), (40, 45, "card"), (28, 35, "phone")]
    assert merge_spans(found) == [(10, 30, "email"), (28, 35, "phone"), (40, 45, "card")]
