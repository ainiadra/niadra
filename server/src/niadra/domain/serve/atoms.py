"""Atomic lines (17, 6.3): a conversation said again as the few short lines that each hold one matter and
one value, "17/08/2026 · voice · o guincho não chegou no horário · protocolo 28206", so a slot that answers
"which protocol did you give me" carries the number with what it was about, in a third of the tokens of a
cut summary.

An atom is read from the conversation's line by rules, without a model: the matter is the summary's first
sentence without its reporting clause ("Cliente relata que ..."), cut at its first clause; the value is the
clause around a number of three or more digits, an amount or a day ("até o dia 20", "by the 24th") of any
sentence of the summary or the solution. The same line always gives the same atoms, so a turn's slots stay
the same bytes.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.serve.lexicon import stem
from niadra.domain.serve.search import tokenize
from niadra.domain.serve.turn import (
    PROXIMITY,
    TEXT_CACHE,
    QuestionKind,
    TurnAnalysis,
    fold,
    identifiers_in,
    typed_values,
    types_in,
)

ATOM_TOKENS = 27
"""The matter and the value of an atom fit this many tokens of the estimate together, which counts a token
every three characters: about 20 of a real tokenizer. With the date and the channel a slot line of one atom
stays near 25 real tokens, where a cut summary took 40 to 60."""
ATOMS_PER_LINE = 3
"""A conversation says at most this many atoms: its numbers, amounts and days, the first ones said."""
MATTER_WORDS = 8
VALUE_WORDS = 5
"""A value without a word naming its type keeps at most this many words ending on it: "troca até o dia 20"."""
ANSWERS = 2
"""An atom stands in for its conversation only when it answers the turn at least this well
(`atom_score`): a number or a type asked, a day asked when, or words of the turn with a value."""

_SENTENCE = re.compile(r"(?<=[.!?;])\s+|(?<=[.!?;][\"'”’])\s+|\s+/\s+")
_EDGES = ".,;:!?()[]{}\"'“”‘’«»"
_REPORTING = frozenset({"que", "that"})
"""Where a reporting clause ends: "Cliente relata que", "The customer said that"."""
_ASKING = frozenset(
    {
        "se", "if", "whether", "como", "how", "qual", "quais", "what", "which", "quando", "when", "onde",
        "where",
    }
)  # fmt: skip
"""Where a reported question starts: "Cliente perguntou como aumentar o limite" keeps "como aumentar"."""
_REPORTING_SPAN = 6
_BREAKS = frozenset(
    {"e", "and", "y", "porque", "because", "pois", "mas", "but", "pero", "while", "enquanto", "since"}
)
"""Words that open a second clause, compared in lower case with their accents ("é" is not "e"): the matter
and the value stop before them."""
_VALUE_BREAKS = _BREAKS | _REPORTING
_TRAILING = frozenset(
    {
        "a", "o", "as", "os", "um", "uma", "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
        "por", "para", "com", "e", "ou", "que", "the", "an", "of", "to", "in", "on", "at", "for", "with",
        "by", "and", "or", "that", "el", "la", "los", "las", "del", "al", "y", "en", "con",
    }
)  # fmt: skip
"""Words a matter or a value never ends on: "o guincho não chegou no" is cut back to a word that says."""
_DAY = re.compile(r"^\d{1,2}(?:º|°|st|nd|rd|th)?$")
_DAY_SUFFIX = re.compile(r"^\d{1,2}(?:st|nd|rd|th)$")
_DAY_MONTH = re.compile(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?$")
_DAY_CUES = frozenset(
    {"dia", "ate", "by", "until", "till", "before", "antes", "hasta", "prazo", "deadline", "day", "hoje"}
)
_UNITS = frozenset({"reais", "real", "dolares", "dollars", "euros", "dias", "days", "horas", "hours"})


@dataclass(frozen=True, slots=True)
class Atom:
    head: str
    """The conversation's date and channel, as its line says them."""
    matter: str
    value: str
    """The clause holding the value; empty when the matter already says it."""
    values: tuple[str, ...]
    """The identifiers it holds, digits only."""
    types: tuple[str, ...]
    """The value types named near them (`typed_values`)."""
    dated: bool
    """It holds a day: a deadline, a date."""
    order: int

    @property
    def text(self) -> str:
        return " · ".join(p for p in (self.head, self.matter, self.value) if p)


def _bare(word: str) -> str:
    return word.strip(_EDGES)


def _low(word: str) -> str:
    return _bare(word).lower()


def _dated(words: list[str], j: int) -> bool:
    bare = _bare(words[j])
    if _DAY_MONTH.match(bare) or _DAY_SUFFIX.match(bare):
        return True
    if not _DAY.match(bare):
        return False
    return any(fold(w) in _DAY_CUES for w in words[max(0, j - 3) : j])


def _valued(words: list[str], j: int) -> bool:
    return bool(identifiers_in(_bare(words[j]))) or _dated(words, j)


def _clean(words: list[str]) -> str:
    kept = list(words)
    while len(kept) > 1 and _low(kept[-1]) in _TRAILING:
        kept.pop()
    while len(kept) > 1 and _low(kept[0]) in _VALUE_BREAKS:
        kept.pop(0)
    text = " ".join(kept).strip(_EDGES.replace("(", "").replace(")", "") + " ")
    return text.lstrip("“\"'")


def _broken(word: str) -> bool:
    return word.rstrip("”\"'").endswith((",", ";", ":"))


def _unreported(words: list[str]) -> list[str]:
    """The words after a reporting clause ("Cliente relata que", "The customer said that"), or all."""
    for k, word in enumerate(words[:_REPORTING_SPAN]):
        if k >= len(words) - 2:
            break
        if _low(word) in _REPORTING or word.endswith(":"):
            return words[k + 1 :]
        if k > 0 and _low(word) in _ASKING:
            return words[k:]
    return words


def without_reporting(sentence: str) -> str:
    """A summary's sentence without its reporting clause: "Marina relatou que a visita não aconteceu" says
    "a visita não aconteceu". A clause that names a number stays: "escreveu sobre a contestação 70710:"
    says which record the rest is about."""
    words = sentence.split()
    kept = _unreported(words)
    dropped = words[: len(words) - len(kept)]
    return sentence if any(ch.isdigit() for w in dropped for ch in w) else " ".join(kept)


def _matter(sentence: str) -> str:
    """The first sentence without its reporting clause, its longest clause between commas ("Após a
    vistoria, apareceu um barulho na suspensão"), cut at a second clause and to `MATTER_WORDS`."""
    words = _unreported(sentence.split())
    clauses: list[list[str]] = [[]]
    for word in words:
        clauses[-1].append(word)
        if _broken(word):
            clauses.append([])
    clause = max((c for c in clauses if c), key=len, default=[])
    kept: list[str] = []
    for word in clause:
        if len(kept) >= 3 and _low(word) in _BREAKS:
            break
        kept.append(word)
        if len(kept) >= MATTER_WORDS:
            break
    return _clean(kept)


def _value(words: list[str], j: int) -> str:
    """The clause around the value at `j`, at most `VALUE_WORDS` words ending on it (and its unit)."""
    start = 0
    for i in range(j - 1, -1, -1):
        if _broken(words[i]) or _low(words[i]) in _VALUE_BREAKS:
            start = i + 1
            break
    end = j + 1
    if end < len(words) and fold(words[end]) in _UNITS:
        end += 1
    # The value starts at the nearest word that names its type, when the clause holds one within
    # `PROXIMITY` ("protocolo 28206", "protocolo do seu atendimento é 54286"); else `VALUE_WORDS` words.
    named = [k for k in range(max(start, j - PROXIMITY), j) if types_in(fold(words[k]))]
    return _clean(words[(named[-1] if named else max(start, j + 1 - VALUE_WORDS)) : end])


def _sentences(fields: list[str]) -> list[str]:
    """The summary's sentences, then the solution's: the fields of four words or more after the head, a
    one-word label ("solução:") left out."""
    found: list[str] = []
    for text in fields:
        if len(text.split()) < 4:
            continue  # the outcome, a speaker
        text = re.sub(r"^\w+:\s+", "", text, count=1) if found else text
        found += [s for s in _SENTENCE.split(text) if s]
    return found


def _fit(matter: str, value: str) -> tuple[str, str]:
    """Matter and value within `ATOM_TOKENS`: the matter loses words from its end, to three, then goes."""
    words = matter.split()
    while words and estimate_tokens(" · ".join(p for p in (" ".join(words), value) if p)) > ATOM_TOKENS:
        words = words[:-1] if len(words) > 3 else []
    return _clean(words) if words else "", value


@lru_cache(maxsize=TEXT_CACHE)
def atoms_of(text: str) -> tuple[Atom, ...]:
    """The atoms of a conversation line's text (`date · channel · summary · outcome · solução: ...`), in
    the order said; none when the line is not one (a masked line, a fact) or holds no value."""
    fields = text.split(" · ")
    if len(fields) < 3 or not re.search(r"\d", fields[0]) or len(fields[1].split()) != 1:
        return ()
    head = " · ".join(fields[:2])
    sentences = _sentences(fields[2:])
    if not sentences:
        return ()
    subject = _matter(sentences[0])
    found: list[Atom] = []
    said: set[str] = set()
    for sentence in sentences:
        words = sentence.split()
        for j in range(len(words)):
            if len(found) >= ATOMS_PER_LINE or not _valued(words, j):
                continue
            value = _value(words, j)
            key = "".join(identifiers_in(value)) or fold(value)
            if key in said:
                continue
            said.add(key)
            ids = identifiers_in(value)
            in_matter = bool(ids) and set(ids) <= set(identifiers_in(subject))
            matter, value = _fit(subject, "" if in_matter or value == subject else value)
            if not matter and not value:
                continue
            both = f"{matter} {value}"
            found.append(
                Atom(
                    head,
                    matter,
                    value,
                    identifiers_in(both),
                    tuple(typed_values(both)),
                    any(_dated(both.split(), i) for i in range(len(both.split()))),
                    len(found),
                )
            )
    return tuple(found)


def atom_score(atom: Atom, turn: TurnAnalysis) -> int:
    """How well an atom answers the turn: a number it names (4), a type it asks for (3), a day when it
    asks when (3), a value where it asks for one (1), and each word of the turn the atom says (1)."""
    points = 4 * bool(set(turn.identifiers) & set(atom.values))
    points += 3 * bool(set(turn.asked_types) & set(atom.types))
    points += 3 * (atom.dated and turn.question is QuestionKind.WHEN)
    points += int(bool(atom.values) and bool(turn.asked_types))
    words = {stem(w) for w in tokenize(f"{atom.matter} {atom.value}")}
    return points + len({stem(w) for w in turn.topic} & words)


def best_atom(text: str, turn: TurnAnalysis) -> Atom | None:
    """The atom of a conversation line that answers the turn best, the first said on a tie; None when none
    answers at least `ANSWERS`."""
    scored = [(atom_score(a, turn), a) for a in atoms_of(text)]
    best = max(scored, key=lambda p: (p[0], -p[1].order), default=None)
    return best[1] if best is not None and best[0] >= ANSWERS else None
