"""The constraints block as text for a model (`constraints.v0`, text): what the subject requires, prefers and
is, in the space's language. Each line names the field by the label its type gives it (its own name
otherwise) and says the operator in words: a model reading "copay: no" does not tell it means "without
copay", and chose the wrong plan three times in four. The SDK places the text in the turn block beside the
state view's; the block's version never covers it, since it is only the block said another way.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from niadra.domain.coordinate.window import Window, window_words
from niadra.domain.memory.bound import own_words
from niadra.domain.signals.render import satisfies

MAX_OPTIONS = 10
"""Options shown that a constraint's line names on each side."""

WORDS: dict[str, dict[str, str]] = {
    "pt": {
        "tag": "restrições",
        "required": "exigido",
        "prefer": "prefere",
        "avoid": "evita",
        "with": "com {label}",
        "without": "sem {label}",
        "eq": "{label} igual a {value}",
        "ne": "{label} diferente de {value}",
        "in": "{label} sendo um de: {values}",
        "not_in": "{label} sem nenhum de: {values}",
        "lt": "{label} abaixo de {value}",
        "lte": "{label} de no máximo {value}",
        "gt": "{label} acima de {value}",
        "gte": "{label} de no mínimo {value}",
        "between": "{label} entre {low} e {high}",
        "in_category": "{line} (em {category})",
        "exclude": "não mostrar: {refs}",
        "ask": "perguntar antes de supor: {what}",
        "shown": "das opções mostradas, {verdicts}",
        "meets": "atende: {refs}",
        "fails": "não atende: {refs}",
        "yes": "sim",
        "no": "não",
        "about": "{text} ({object})",
        "account": "{text} (instrução da conta)",
        "only_with": "exigido se quem fala for {who}: {text} (Com qualquer outro contato, não se aplica.)",
        "contact_tag": "preferências de contato",
        "contact_of": "{who}: {text} (para quando a equipe procurar essa pessoa; não muda o que se pode "
        "responder a ela)",
        "contact_any": "{text} (para quando a equipe fizer contato; não muda o que se pode responder)",
        "omitted": "fora deste bloco por tamanho: {what}",
        "omitted_hard": "{count} exigências",
        "omitted_soft": "{count} preferências",
        "omitted_exclude": "{count} itens a não mostrar",
        "omitted_already_presented": "{count} itens já mostrados",
        "omitted_instructions": "{count} instruções",
    },
    "en": {
        "tag": "constraints",
        "required": "required",
        "prefer": "prefers",
        "avoid": "avoids",
        "with": "with {label}",
        "without": "without {label}",
        "eq": "{label} equal to {value}",
        "ne": "{label} other than {value}",
        "in": "{label} one of: {values}",
        "not_in": "{label} none of: {values}",
        "lt": "{label} below {value}",
        "lte": "{label} at most {value}",
        "gt": "{label} above {value}",
        "gte": "{label} at least {value}",
        "between": "{label} between {low} and {high}",
        "in_category": "{line} (in {category})",
        "exclude": "do not show: {refs}",
        "ask": "ask before assuming: {what}",
        "shown": "of the options shown, {verdicts}",
        "meets": "meets it: {refs}",
        "fails": "does not: {refs}",
        "yes": "yes",
        "no": "no",
        "about": "{text} ({object})",
        "account": "{text} (account instruction)",
        "only_with": (
            "required if the one speaking is {who}: {text} (With any other contact, it does not apply.)"
        ),
        "contact_tag": "contact preferences",
        "contact_of": "{who}: {text} (for when the team reaches out to this person; it does not change what "
        "may be said to them)",
        "contact_any": "{text} (for when the team reaches out; it does not change what may be said)",
        "omitted": "left out of this block for size: {what}",
        "omitted_hard": "{count} requirements",
        "omitted_soft": "{count} preferences",
        "omitted_exclude": "{count} items not to show",
        "omitted_already_presented": "{count} items already shown",
        "omitted_instructions": "{count} instructions",
    },
}


def block_text(
    block: Mapping[str, Any],
    labels: Mapping[str, str],
    lang: str,
    shown: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    hours_said: bool = False,
) -> str | None:
    """The block's lines inside their tag, or None when it holds nothing to say. `labels` maps a field
    (`type.field`) to its label. A hard constraint that lost a conflict is left out, as from rendering. A
    hard constraint on a field the subject was shown (`shown`: each item's fields as last shown, by ref)
    says which of the options shown meet it: the filter alone left the agent unable to pick an option the
    customer heard of only in an earlier conversation."""
    words = WORDS.get(lang, WORDS["en"])
    lost = {i for c in block.get("conflicts") or () for i in c["ids"] if i != c["kept"]}
    lines: list[str] = []
    contact: list[str] = []
    for i in block.get("instructions") or ():
        if i.get("contact"):
            # How or when a person wants to be reached, apart from what is required: read as "exigido se
            # quem fala for Marcos Lima: contatar por WhatsApp e só depois das 10h", it left an attendant
            # refusing Marcos his own claim amount 5 times of 5 (juridico-sandbox, 06/10/2026); apart, 5 of
            # 5 answered him, Clara was still refused and the preference still kept.
            contact.append(contact_line(words, i["text"], i.get("only_with")))
            continue
        # What someone said about the object or the client the read is about, in their words.
        # One that holds with one person is required only if that person is the one speaking, and says it
        # does not apply to any other contact: "(com este cliente)", "(vale para quem ela nomeia)" and "(vale
        # só com Clara; não vale para quem fala, se for outra pessoa)" each left an attendant refusing Marcos
        # his own claim amount over "não falar de valores com Clara" (juridico-sandbox, 05 and 06/10/2026:
        # 0 of 5); the condition first gave it 7 of 8, and Clara was still refused 8 of 8.
        if i.get("only_with"):
            lines.append(words["only_with"].format(text=i["text"], who=i["only_with"]))
            continue
        if not i.get("object"):
            lines.append(f"{words['required']}: {words['account'].format(text=i['text'])}")
            continue
        kind, _, rest = str(i["object"]).partition(":")
        named = f"{labels.get(kind) or kind.replace('_', ' ')} {rest.partition(':')[2]}"
        lines.append(f"{words['required']}: {words['about'].format(text=i['text'], object=named)}")
    for h in block.get("hard") or ():
        if h["id"] in lost or h.get("scope") == "object":
            continue
        said = _phrase(words, _label(h["attr"], labels), h["op"], h["values"])
        line = f"{words['required']}: {_in(words, said, h.get('category'))}"
        lines.append(line + _options(words, h, shown or {}))
    for s in block.get("soft") or ():
        said = _phrase(words, _label(s["attr"], labels), "eq", [s["value"]], plain=True)
        lines.append(f"{words[s.get('polarity', 'prefer')]}: {_in(words, said, s.get('category'))}")
    for a in block.get("attributes") or ():
        said = f"{_label(a['name'], labels)}: {_value(a['value'], words)}"
        lines.append(_in(words, said, a.get("category")))
    # The pack beside the block already says the person's contact time as a contact preference
    # (`hours_said`): the windows stay in the entries for a program, and are not said twice.
    for w in () if hours_said else block.get("contact_windows") or ():
        # The hours the person said they may be reached in, in words ("ligação: não antes das 09:00,
        # horário de America/Sao_Paulo"): a call at 07:00 waits, one at 14:00 goes (the suppression-list spec,
        # 6.1); the same line holds it for the team, never for what may be answered to them.
        said = window_words(Window.from_json(w["window"]), w.get("channel"), lang)
        contact.append(words["contact_any"].format(text=said))
    if block.get("exclude"):
        lines.append(words["exclude"].format(refs=", ".join(block["exclude"])))
    lines += [words["ask"].format(what=what) for what in block.get("ask") or ()]
    if omitted := block.get("omitted"):
        what = ", ".join(words[f"omitted_{k}"].format(count=n) for k, n in sorted(omitted.items()) if n)
        lines.append(words["omitted"].format(what=what))
    sections = [
        "\n".join([f"<{tag}>", *(f"- {line}" for line in said), f"</{tag}>"])
        for tag, said in ((words["tag"], lines), (words["contact_tag"], contact))
        if said
    ]
    return "\n".join(sections) or None


def contact_line(words: Mapping[str, str], text: str, who: str | None) -> str:
    """A contact preference as its line says it: whose it is and that it only says how to reach them. The
    name the text already starts with is not said twice ("Marcos Lima: só por WhatsApp")."""
    if not who:
        return words["contact_any"].format(text=text)
    return words["contact_of"].format(who=who, text=own_words(text, who))


def _options(words: Mapping[str, str], h: Mapping[str, Any], shown: Mapping[str, Mapping[str, Any]]) -> str:
    """What a hard constraint's line adds for the items of its type shown with its field ("; of the options
    shown, meets it: a; does not: b"), by the id of each; nothing when none was shown."""
    type_, _, field = h["attr"].partition(".")
    meets: list[str] = []
    fails: list[str] = []
    for ref, fields in sorted(shown.items()):
        if ref.split(":", 1)[0] != type_ or fields.get(field) is None:
            continue
        name = ref.rsplit(":", 1)[-1]
        (meets if satisfies(h["op"], h["values"], fields[field]) else fails).append(name)
    verdicts = [
        words[key].format(refs=", ".join(names[:MAX_OPTIONS]))
        for key, names in (("meets", meets), ("fails", fails))
        if names
    ]
    return f"; {words['shown'].format(verdicts='; '.join(verdicts))}" if verdicts else ""


def _phrase(
    words: Mapping[str, str], label: str, op: str, values: Sequence[Any], *, plain: bool = False
) -> str:
    if len(values) == 1 and isinstance(values[0], bool) and op in ("eq", "ne", "in", "not_in"):
        present = values[0] if op in ("eq", "in") else not values[0]
        return words["with" if present else "without"].format(label=label)
    shown = [_value(v, words) for v in values]
    if plain:
        return f"{label} {shown[0]}"
    if op in ("in", "not_in") and len(shown) == 1:
        op = "eq" if op == "in" else "ne"
    if op == "between" and len(shown) == 2:
        return words["between"].format(label=label, low=shown[0], high=shown[1])
    if op in ("in", "not_in"):
        return words[op].format(label=label, values=", ".join(shown))
    return words[op].format(label=label, value=shown[0])


def _in(words: Mapping[str, str], line: str, category: str | None) -> str:
    return words["in_category"].format(line=line, category=category) if category else line


def _label(attr: str, labels: Mapping[str, str]) -> str:
    """The field's label, or its own name in words (`monthly_price`: `monthly price`)."""
    return labels.get(attr) or attr.rsplit(".", 1)[-1].replace("_", " ")


def _value(value: Any, words: Mapping[str, str]) -> str:
    if isinstance(value, bool):
        return words["yes"] if value else words["no"]
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    return str(value)
