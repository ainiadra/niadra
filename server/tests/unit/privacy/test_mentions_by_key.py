"""A write to the index of mentions takes each row once: a caller may give one row several entries (a page of
an episode's versions), which go in as one, their tokens together."""

from niadra.domain.privacy.mentions import MentionKey, MentionStore, by_key


def test_the_entries_of_one_row_are_one_entry_with_every_token() -> None:
    episode = MentionKey(MentionStore.EPISODE, "e-1")
    fact = MentionKey(MentionStore.FACT, "f-1")

    merged = by_key([(episode, frozenset({b"a"})), (fact, frozenset({b"c"})), (episode, frozenset({b"b"}))])

    assert merged == [(episode, frozenset({b"a", b"b"})), (fact, frozenset({b"c"}))]


def test_a_row_named_by_no_entry_stays_empty_and_the_order_is_the_first_seen() -> None:
    first, second = MentionKey(MentionStore.NOTE, "n-2"), MentionKey(MentionStore.NOTE, "n-1")

    assert by_key([(first, frozenset()), (second, frozenset({b"x"})), (first, frozenset())]) == [
        (first, frozenset()),
        (second, frozenset({b"x"})),
    ]
