"""Words that mean the same thing, without a model (11, 3.3): a light stemmer for Portuguese, English and
Spanish, and concept groups ("senha", "passcode", "access code") the space configures, the starter's by
default. The lexical slot channel and the count line read a turn and a memory line through the same
`Lexicon`, so "the package sticker" finds "the box label" and "quantas vezes reclamei do guincho atrasado"
counts "o guincho demorou demais".

A line's keys are its stems (`w:<stem>`), the concepts its words and phrases belong to (`c:<concept>`),
and for a conversation the category the extraction gave it (`k:<category>`). A turn is read as units:
one per content word, or one per phrase of the lexicon ("did not go through"), each with the keys that
answer it. A unit counts once, by its best key: the word itself outranks a synonym, which outranks the
category. Nothing here reads a clock or a model; the same text gives the same keys.
"""

import re
import unicodedata
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

WORD_KEY = "w:"
CONCEPT_KEY = "c:"
CATEGORY_KEY = "k:"
WORD_WEIGHT = 1.0
CONCEPT_WEIGHT = 0.6
"""A synonym says less than the word itself: "senha" in a line answers "senha" before "código" does."""
CATEGORY_WEIGHT = 0.3
"""The category the extraction gave a conversation: evidence that it is about the matter, not of what it
says."""
MIN_STEM = 4
MAX_PHRASE = 5
KEYS_CACHE = 16_384

_WORD = re.compile(r"[a-z0-9]+")
# Folded, longest first; a suffix comes off only when at least `MIN_STEM` letters stay. Portuguese and
# Spanish nouns, adjectives, participles and regular verb endings; English ones; plurals and gender.
_SUFFIX_TEXT = """
    amentos imentos amento imento mente acoes icoes acao icao ciones cion ando endo indo adas ados idas idos
    ada ado ida ido aram eram iram avam amos emos imos ava aste aron ieron ou ei eu iu am em ar er ir as os es
    ings ing ied ies ed ly
    s a o e y
"""
_SUFFIXES = tuple(sorted(set(_SUFFIX_TEXT.split()), key=lambda s: (-len(s), s)))
_DOUBLED = re.compile(r"([b-df-hj-np-tv-z])\1$")


def fold_words(text: str) -> list[str]:
    """Lower case, no accents, letters and digits: "Não passou" -> ["nao", "passou"]."""
    # Accents off by decomposing and dropping what is not ASCII: the three languages spell in Latin letters.
    return _WORD.findall(unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode())


@lru_cache(maxsize=65_536)
def stem(word: str) -> str:
    """A light stem of a folded word: inflections of one word meet ("recusado", "recusou", "recusada";
    "dropped", "dropping", "drop"); different words stay apart. A number is its own stem."""
    if len(word) <= MIN_STEM or not word.isalpha():
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= MIN_STEM:
            base = word[: -len(suffix)]
            if suffix in ("ing", "ed", "ings") and _DOUBLED.search(base) and len(base) > MIN_STEM:
                base = base[:-1]  # "dropped" -> "dropp" -> "drop"
            return base
    return word


def stems(text: str) -> tuple[str, ...]:
    return tuple(stem(w) for w in fold_words(text))


@dataclass(frozen=True, slots=True)
class Concept:
    """A group of words and phrases that mean one thing, in any of the three languages, and the episode
    categories that thing is evidence of."""

    name: str
    terms: tuple[str, ...]
    categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Unit:
    """One thing a turn says: a content word or a phrase of the lexicon, with the keys that answer it."""

    word: str | None
    """The stem key of a single word; None for a phrase, which only its concepts answer."""
    concepts: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()

    def weights(self) -> dict[str, float]:
        found: dict[str, float] = {}
        for key in self.categories:
            found[key] = CATEGORY_WEIGHT
        for key in self.concepts:
            found[key] = CONCEPT_WEIGHT
        if self.word is not None:
            found[self.word] = WORD_WEIGHT
        return found

    def matched_by(self, keys: frozenset[str], *, categories: bool = False) -> bool:
        return (self.word is not None and self.word in keys) or any(
            k in keys for k in (*self.concepts, *(self.categories if categories else ()))
        )


@dataclass(frozen=True, slots=True, eq=False)
class Lexicon:
    """The concepts compiled once per configuration: phrases as stem tuples, longest first."""

    concepts: tuple[Concept, ...] = ()
    _phrases: Mapping[tuple[str, ...], tuple[str, ...]] = field(default_factory=dict)
    _category_of: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    _sizes: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    """Per first stem, the lengths of the phrases starting with it, longest first."""
    _keys: "OrderedDict[str, frozenset[str]]" = field(default_factory=OrderedDict)

    @classmethod
    def build(cls, concepts: Iterable[Concept]) -> "Lexicon":
        return _build(tuple(concepts))

    def _matches(self, words: Sequence[str]) -> list[tuple[int, int, tuple[str, ...]]]:
        """(start, end, concepts) of every phrase of the lexicon in the stemmed words, longest first at
        each position; a shorter phrase inside a longer one still counts for a line's keys."""
        found = []
        for start, word in enumerate(words):
            for size in self._sizes.get(word, ()):
                if (
                    start + size <= len(words)
                    and (names := self._phrases.get(tuple(words[start : start + size]))) is not None
                ):
                    found.append((start, start + size, names))
        return found

    def keys(self, text: str, category: str | None = None) -> frozenset[str]:
        """What a memory line can answer with: its stems, its concepts, and its conversation's category."""
        cache_key = f"{category or ''}\x00{text}"
        if (cached := self._keys.get(cache_key)) is not None:
            self._keys.move_to_end(cache_key)
            return cached
        words = stems(text)
        keys = {WORD_KEY + w for w in words}
        for _, _, names in self._matches(words):
            keys.update(CONCEPT_KEY + n for n in names)
        if category:
            keys.add(CATEGORY_KEY + category)
        found = frozenset(keys)
        self._keys[cache_key] = found
        if len(self._keys) > KEYS_CACHE:
            self._keys.popitem(last=False)
        return found

    def units(self, text: str, content: Iterable[str]) -> tuple[Unit, ...]:
        """What a turn asks about: each phrase of the lexicon it says (stop words may be part of one: "did
        not go through"), then each content word (`content`, folded, from the turn analysis) no phrase
        covered, with its own concepts. Units answered by the same concepts are one."""
        folded = fold_words(text)
        words = [stem(w) for w in folded]
        wanted = {stem(w) for w in content}
        covered: set[int] = set()
        units: list[Unit] = []
        for start, end, names in self._matches(words):
            if end - start < 2 or any(i in covered for i in range(start, end)):
                continue
            covered.update(range(start, end))
            units.append(self._unit(None, names))
        for i, word in enumerate(words):
            if i in covered or word not in wanted:
                continue
            covered.add(i)
            units.append(self._unit(WORD_KEY + word, self._phrases.get((word,), ())))
        merged: dict[tuple[str | None, tuple[str, ...]], Unit] = {}
        for unit in units:
            same = next((u for u in merged.values() if unit.concepts and u.concepts == unit.concepts), None)
            if same is None:
                merged[(unit.word, unit.concepts)] = unit
        return tuple(merged.values())

    def _unit(self, word: str | None, names: Sequence[str]) -> Unit:
        categories = tuple(dict.fromkeys(c for n in names for c in self._category_of.get(n, ())))
        return Unit(
            word,
            tuple(CONCEPT_KEY + n for n in names),
            tuple(CATEGORY_KEY + c for c in categories),
        )


@lru_cache(maxsize=64)
def _build(concepts: tuple[Concept, ...]) -> Lexicon:
    phrases: dict[tuple[str, ...], list[str]] = {}
    categories: dict[str, list[str]] = {}
    for concept in concepts:
        for term in concept.terms:
            if key := stems(term):
                names = phrases.setdefault(key[:MAX_PHRASE], [])
                if concept.name not in names:
                    names.append(concept.name)
        categories.setdefault(concept.name, []).extend(concept.categories)
    return Lexicon(
        concepts,
        {k: tuple(v) for k, v in phrases.items()},
        {k: tuple(dict.fromkeys(v)) for k, v in categories.items() if v},
        {
            first: tuple(sorted({len(k) for k in phrases if k[0] == first}, reverse=True))
            for first in {k[0] for k in phrases}
        },
    )


EMPTY = Lexicon()


def category_concepts(names: Iterable[str], known: Iterable[Concept]) -> list[Concept]:
    """A concept for each episode category of the space that no concept already points to: its name's
    words, so a space's own category ("guincho", "roaming") is a word a turn can say."""
    pointed = {c for concept in known for c in concept.categories}
    return [
        Concept(f"category.{name}", (name.replace("_", " ").replace("-", " "),), (name,))
        for name in dict.fromkeys(names)
        if name not in pointed and name != "other"
    ]
