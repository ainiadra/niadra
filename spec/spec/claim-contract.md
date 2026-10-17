# Niadra Claim Contract, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schema:** [`schemas/claim-contract.v0.json`](../schemas/claim-contract.v0.json)
- **Vectors:** [`vectors/claim-parser.v0.json`](../vectors/claim-parser.v0.json),
  [`vectors/claim-detect.v0.json`](../vectors/claim-detect.v0.json),
  [`vectors/claim-evidence.v0.json`](../vectors/claim-evidence.v0.json),
  [`vectors/claim-anchor.v0.json`](../vectors/claim-anchor.v0.json)
- **License:** Apache 2.0

## 1. Purpose

An agent fails less for lack of memory than for stating what it did not consult: a price nobody quoted,
a deadline three days off, a promise with no action behind it, a citation of an article that says
something else. A claim contract declares what an agent may state and the evidence each kind of claim
needs in the turn record. The check runs without a model, in the agent's process, at the last point
before the customer or before a document is saved; the server only receives the verdicts and adds them
up.

The contract joins four checks: a value with its provenance, a number with its role, the boundary of
what an agent may know, and the provenance of a text. It rewrites nothing by default, never rewrites an
immutable output, and comes with a versioned list of phrases of the trade that must never trigger it.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

- An **output** is the text the check reads: a chat message, a voice turn before speech, a document. Its
  **context** is a name (`chat`, `proposal`, `contestacao`) that the contract lists as immutable or
  mutable, and that picks the action.
- Offsets are counted in Unicode code points of the output, end excluded. An implementation whose strings
  count in UTF-16 units MUST convert.
- **Folded** text is lower case without accents, one character for each character.

## 3. The document

| Field | Meaning |
|---|---|
| `version` | The contract's own version, cited by the turn record. |
| `languages` | `pt`, `en` or `es`; the first reads an output whose language is unknown. |
| `categories` | The kinds of claim (section 4), at most 64, each with its own `id`. |
| `negative_corpus` | `version` and `phrases`: phrases of the trade that must never trigger (section 10). REQUIRED when there are categories. |
| `internal_text` | Fingerprints of the company's own prompt (section 11). |
| `outputs` | `immutable` and `mutable`: the contexts of each kind, apart. |

A contract without categories checks nothing. The SDK receives the contract, without the phrases of its
negative corpus (only its version), in its profile, and keeps it in its local cache.

## 4. Categories

| Field | Meaning |
|---|---|
| `id` | The category: `price`, `deadline`, `action_promise`. |
| `agents` | The agents it applies to; empty for every agent. |
| `detect` | How its claims are found (section 4.1). |
| `evidence` | What the turn must hold for a claim to stand (section 4.2). |
| `natures` | How a number is handled by where it came from (section 7). |
| `actions` | What happens to a claim that does not stand (section 8). |

4.1. **Detection** is lexical and REQUIRES at least one of:
- `classes`: numbers of these classes (section 5) that the output asserts: a hedged number (5.4) is never
  a claim. With `terms`, a number counts only in a sentence that holds one of the terms;
- `roles`: for each role, the words that give a number that role (section 6). Roles qualify numbers, so
  they REQUIRE `classes`;
- `terms`: words of the trade. Without `classes`, each occurrence of a term is a claim ("separei", "já
  enviei a proposta");
- `patterns`: `article_citation` (a statute's article: "art. 186") or `precedent_citation` (a case law
  reference: "REsp 1.234.567/SP", "Súmula 7", "Tema 1.234");
- `document_sections`: every sentence of these sections of a document is a claim of fact.

Terms and role words match whole words of the folded text, in sequence; the list holds every synonym
and every language. A sentence ends at `!`, `?` or `;` before a space, at a period before a space and a
capital letter, and at a line break.

4.2. **Evidence** is exactly one of:
- `value`: a value with provenance in the turn record (a field of a tool's result or of a state read).
  `same_role` REQUIRES the number's role to be the value's; `fresh_for: claim` REQUIRES the value to be
  within its type's age for claims; `type` and `value` narrow it to one field or computed value of one
  object type; `must_state_gaps` REQUIRES every gap the value declares to be said with it, by the words
  of `gap_terms` for that gap (or by its name, underscores as spaces, when there are none);
- `tool`: a call of this tool in the turn (the boundary of what the agent may know: an agent without the
  tool always fails it);
- `tool_any`: a call of any of these tools (a promise of an action needs the action);
- `anchor`: the text cites a source through an anchor that matches it (section 9), at `min_match`,
  0.90 or more, counted apart by `coverage` (`law` or `fact`), which never add up.

4.3. **Evidence a context read serves.** Besides the turn's tools, the values the include blocks of the
turn's context reads served are `value` evidence, and nothing else the read returned is: the pack's text,
its slots and each block's own `text` are prose for the model, and a number only they state has no origin
(7.3). The values are:
- of the state view ([object state](object-state.md)): each field of its objects and of its interests'
  objects whose logical value is `yes` and that is not masked, fresh for a claim when it is `claim_safe` and
  the object blocks no `claim`; each computed value that is `claim_safe`, with the gaps it and its object
  declare; and the `now` of each change since seen whose object and field the view did not serve, never
  fresh for a claim. The value a change was seen at is not evidence;
- of the constraints block ([constraints](constraints.md)): the values of its hard constraints (except one
  that lost a conflict), soft entries and attributes, always fresh for a claim and named by their field; and
  the numbers each object in `already_presented` was last shown with (`values`: an offer's price, total,
  discount or installment), each with its role, fresh for a claim when it is `claim_safe`.

A value served with a role that is a role of a category is a value of that role and of that category's
classes, as a field named like a role of a tool's result is; any other number, amount or date backs a number
said with the same value, of the class the output gives it. A number that matches a value not fresh for a
claim is `stale` (8.1); one that matches no value served, `unsupported` or `mismatch`, as any other.

## 5. Numbers

The parser reads every number of an output in one language. Each number lands in at most one
**mention**, found in this order; a later step never takes what an earlier one took:

1. **Labels by shape:** a case number (`0001234-56.2026.8.26.0100`), a tax id, a postal code
   (`01310-100`), a phone, a time of day (`14:30`, `14h30`, `3 pm`, "às 14h", "at 3", "a las 10").
2. **Labels by the word before:** a number right after a word that names something (an order, a
   protocol, a case, an article, a law, a precedent, a size, an item, a position, a page, a clause, a
   code), with only number marks between (`nº`, `#`, `:`). Appendix A lists the words.
3. **Dates:** ISO (`2026-10-05`); numeric with `/` (`20/10/2026`, `20/10`, `20/10/26`), with `.` or `-`
   only with the year; day first in `pt` and `es`, month first in `en`; with a month name, day first or
   month first (`5 de outubro de 2026`, `October 5, 2026`, `5 October 2026`), a short month name only
   with its period (`3 set. 2026`); a month of a year (`outubro de 2026`, `10/2026`); a day alone after
   "dia" or "day" (`dia 20`); a range of days in one month (`de 24 a 26 de dezembro`). An impossible date
   is no date.
4. **Amounts**, by what comes before or after them:
   - money: a currency before (`R$`, `US$`, `U$S`, `€`, `£`, `$`, or a code such as `BRL`, `USD`,
     `EUR`), or a currency word after ("reais", "dólares", "euros", "pesos"), with an optional scale
     ("mil", "milhões", "million", "thousand", and "k" only with a currency);
   - percent: `%`, "por cento", "percent", "por ciento";
   - duration: minutes, hours (`48h`), days, business days ("dias úteis", "business days", "días
     hábiles"), calendar days ("dias corridos"), weeks, months, years;
   - dose: `mg`, `mcg`, `IU`, drops, tablets, capsules;
   - quantity: units of measure (`kg`, `g`, `l`, `ml`, `cm`, `m`, `GB`, `mAh`, inches) and units of stock
     ("unidades", "peças", "items", "pairs");
   - count of installments: a number with `x` (`10x`).

   A number written in words counts only right before one of these units ("quinze dias úteis",
   "trezentos reais", "twenty percent"): a number word alone is never a number, because "um", "one" and
   "un" are also articles. Two amounts joined by "a", "ao", "até", "to", "al", "hasta" or a hyphen, or by
   "e", "and" or "y" after "entre" or "between", make a **range**, when the first is smaller; the unit may
   come once, after the second (`de 3 a 5 dias`), and a second amount alone takes the first one's unit
   (`R$ 300 a 400`).
5. **Codes and ordinals:** a word that mixes letters and digits (`PX-4471`, `BEMVINDO10`, `3º`) is a
   label.
6. **The rest:** five digits or more, a leading zero or a year (1900 to 2099) is a label; a number with
   two decimals is money without a currency (`49,90`); an integer before a word is a count (`2
   dependentes`); anything else is not read.

5.1. **Decimal marks.** When a number has both `.` and `,`, the last one is the decimal mark. A mark that
repeats groups thousands. A single mark before exactly three digits groups them when it is the
language's grouping mark (`.` in `pt` and `es`, `,` in `en`) and is the decimal mark otherwise; before
one, two, four or more digits it is the decimal mark. So `R$ 511.06` in Portuguese is 511.06, and
`1.234` is 1234 in Portuguese and 1.234 in English.

5.2. **Classes.** `money`, `percent`, `date`, `duration`, `quantity`, `count`, `dosage`, and `label`. A
label names instead of measuring; no category detects it and nothing ever rewrites it.

5.3. **The value** of a mention, as vectors and verdicts write it:

| Shape | Fields |
|---|---|
| An amount | `amount`, a decimal string without trailing zeros (`"511.06"`, `"1500"`), and `unit` |
| A range | `min` and `max`, and `unit` |
| A date | `date`: `2026-10-05`, `2026-10` (a month of a year), `--10-05` (no year), `---20` (a day) |
| A range of dates | `date_from` and `date_to` |

`unit` is the currency code of money (absent when the text names no currency, as with `$` alone or
"pesos"), `%` for percent, `minute`, `hour`, `day`, `business_day`, `week`, `month` or `year` for a
duration, `mg`, `mcg`, `IU`, `drop`, `tablet` or `capsule` for a dose, the unit of a quantity (`kg`,
`g`, `l`, `ml`, `cm`, `mm`, `m`, `km`, `m2`, `GB`, `MB`, `TB`, `W`, `V`, `mAh`, `in`, `unit`, `pair`),
and `x` for installments. A count has no unit. Each mention also says whether it was `written` in
`digits` or `words`.

5.4. **Hedges.** A number the output says it cannot confirm, says as a value that no longer holds, or
doubts, is not asserted: "Não consigo confirmar se o frete de R$ 24,90 ainda vale" states no price. Such a
number is **hedged**, and no category detects it. The words are in Appendix A.10. They match whole words
of the folded text, in sequence, with `'` and `’` alike, and never inside a mention's span.

A number's **clause** is its sentence (4.1) cut at clause breaks: a comma, semicolon or colon before a
space or the end of the text, a parenthesis, an em dash or en dash, a hyphen between spaces, and the
clause words (A.10.8). The clause word `e` breaks only as written, without an accent, since `é` folds to
it. A break inside a mention's span does not count. Distances are counted in words, as in section 6.1,
among the mentions of the output that are not labels. A number is hedged when:

1. a **denial** (A.10.1) stands before it in its clause: "não consigo confirmar se o frete de R$ 24,90
   ainda vale", "I can't confirm whether it is still $412.70";
2. it is the number of its clause nearest to a **past marker** (A.10.2), on either side, at most 6 words
   away, with no **present word** (A.10.7) between them; at the same distance, the one after it: "o valor
   de R$ 612,00 era o anterior", "it was previously listed at $612.00";
3. it is the number of its clause nearest before a **doubt** (A.10.3), at most 6 words away: "the $1,240.00
   price has changed", "R$ 24,90 pode ter mudado";
4. its clause follows, in the same sentence, a clause that holds only an **opener** (A.10.4): "antes, ele
   estava em R$ 1.240,00";
5. its clause holds a **past word** (A.10.5), and a later clause of its sentence holds a denial followed,
   in that clause, by a **word of continuity** (A.10.6): "a última cotação foi de R$ 238,40, mas não
   consigo confirmar se esse total continua igual";
6. a **negation** (A.10.9) stands right before it, with no word between: "$689.00 per month, not
   $612.00". The Spanish `no` is not one, since it is also the Portuguese "no" ("no dia 20").

A hedge that governs something else leaves a number asserted, and so does a light one: "O total é
R$ 500,00, mas não consigo confirmar o prazo", "Acho que o total é R$ 500,00", "The price changed to
$700.00" and "O plano custa R$ 700,00 agora, antes era R$ 612,00" all state their first amount. A hedged
number is never a claim, so it never reaches the turn record's `claims`.

## 6. Roles

The same R$ 511,06 is a full price, a discounted one or a price per person by the words next to it.

6.1. A role's term counts when at most 6 words stand between it and the number, in the same sentence.

6.2. A term belongs to the nearest number of the same class; when two are as near, to both.

6.3. The nearest term gives the role. At the same distance, a term of the number's own beats one it
shares: in "de R$ 299,90 por R$ 199,90", "por" is as near to both numbers, and "de" is the first one's
alone. Terms of different roles still tied leave the number `ambiguous`, which is never approved. A
number without a term within reach has no role.

6.4. Where terms overlap, the longest at the leftmost place wins ("de frete" over "de").

## 7. The three natures of a number

7.1. **Quoted:** the number lies inside quotation marks (`"…"`, `“…”`, `«…»`, `„…“`). It is reproduced as
the source wrote it, even when false, because contesting an allegation requires stating it; the check
confirms only that the quoted passage is in a document of the turn (whitespace collapsed, without case).

7.2. **Computed:** the turn holds a value of the number's class with the number's role, or with the
number's value. It came from a value with provenance, calculated by a rule or observed from a source, and
is checked against it.

7.3. **Said by the model:** neither. It has no origin.

7.4. `natures` sets the handling: `computed` is `check` (the default) or `count`; `quoted` is `verbatim`
(the default) or `count`; `model` is `block`, `warn` or `count`, or absent to take the category's action.
The validator that drops every amount absent from the history is one category of class `money` with
`model: block`.

## 8. Verdicts and actions

8.1. **Verdicts** of a number checked against `value` evidence:

| Verdict | When |
|---|---|
| `matched` | A value of the evidence equals the number (for a role, one of that role; without a role, any of its class). |
| `mismatch` | Values of the evidence exist, and none equals the number. |
| `no_evidence` | The evidence holds no value to check against. |
| `stale` | The equal value is not fresh enough for a claim. |
| `gap_not_stated` | The equal value declares gaps the output does not say. |
| `role_ambiguous` | Terms of two roles tie (section 6.3). |
| `unsupported` | The number has no origin (section 7.3). |
| `quoted_found`, `quoted_missing` | A quoted number's passage is, or is not, in a document of the turn. |
| `not_checked` | The nature's handling is `count`, or an anchor was not emitted. |
| `internal_text_found` | The output repeated fingerprints of the company's own prompt (section 11). |

With `tool` or `tool_any` evidence, a claim is `matched` when the turn called the tool and `no_evidence`
otherwise. With `anchor` evidence, a claim is `anchored`, `below_threshold` (the anchor's score is under
`min_match`), `source_missing` (the anchor cites a document the turn does not hold) or `not_checked` (no
anchor); `source_exists_claim_unverified` is kept for a check that finds the cited source but cannot
verify what the output says about it.

8.2. **Actions.** `matched`, `quoted_found` and `anchored` take no action (`none`); `not_checked` is
counted (`count`). Every other verdict takes the category's action for the output's context
(`actions.contexts`, or `actions.default`), except `unsupported`, which takes `natures.model` when set:

| Action | What happens |
|---|---|
| `block` | In a mutable output, the passage gives way to `replace_with`; in an immutable one, the whole output goes to a person, untouched. |
| `warn` | The output goes as it is, and the claim is marked. |
| `count` | Only measured. |
| `rewrite_if_unequivocal` | The number is rewritten only when unequivocal (8.3), and otherwise marked as `warn`. |
| `discard_anchor_and_count` | The anchor is dropped and counted. |

8.3. **A rewrite is unequivocal** only when all hold: the output is mutable; the class is money, percent,
date or duration (a dose, a technical quantity, a size, a list position and an identifier are never
rewritten, in any context); the number is a single value, not a range; the verdict is `stale` and the
number is a literal copy of one field of one object (`ref` and field), whose fresh value in the turn is
different; and no other number of its class is in the sentence (a derived one would go wrong). The new
value is that fresh one.

8.4. A contract MUST NOT name `rewrite_if_unequivocal` as `default`, nor for a context it does not list
as mutable. Nothing ever changes an immutable output in silence.

8.5. The principle behind the table: correct, annotate or count; never block with a generic message, and
never rewrite what is immutable. Stating the gap is an acceptable answer.

## 9. The text anchor

9.1. A tool or the agent emits anchors: where in the output, the quoted text, and the document it cites.
The check compares the quote with the document it has in hand (in a metadata-only mode, rematerialized
inside the company's boundary).

9.2. Both texts are normalized the same way: lower case, accents removed (Unicode NFKD, combining marks
dropped), every run of anything but `a-z` and `0-9` made one space, and trimmed.

9.3. The distance `d` is the fewest insertions, deletions and substitutions that turn the normalized quote
into some passage of the normalized document, starting and ending anywhere in it. The score is
`1 - d / len(quote)`, and 1.0 when the quote is a passage of the document. An empty quote scores 0.

9.4. An anchor holds at 0.90 or above. An anchor below it is discarded and counted, never repaired. A
number inside an anchored passage is still checked as a number: the score alone lets a changed amount
through (`vectors/claim-anchor.v0.json`, `anchor.fact_amount_changed`).

## 10. The negative corpus

10.1. Every contract with categories comes with the versioned list of phrases of the trade that must
never trigger it: phrases a naive lexicon would catch ("não temos certeza do prazo", "o prazo para
contestação é de 15 dias úteis", "Seu CEP é 01310-100?").

10.2. A phrase triggers when, read in any of the contract's languages and for any of its agents, a
category finds a claim in it. `niadra contract test` (the SDK's command line, for the company's CI) MUST
fail when one does. A false positive that blocks a document with a deadline running is the main risk of
this contract, and the test is part of it.

10.3. The three example contracts ([`examples/claim-contract/`](../examples/claim-contract/): retail,
legal, health plan sales) carry the initial corpus, version `2026-09-29`.

## 11. Internal text

11.1. A company MAY register fingerprints of its own prompt: hashes of every `n` consecutive words (8 by
default), computed by the SDK, never the prompt. An output that repeats one gives way to `redact`.

11.2. Each passage that gave way is one entry of the turn record's `claims` (12.2):
- `category` is `internal_text`, a name reserved for this: a contract MUST NOT declare a category with it;
- `verdict` is `internal_text_found`;
- `action` is `block`: the passage gave way to `redact` in a mutable output, and in an immutable one the whole
  output went to a person, untouched (8.2);
- `span` is the passage that repeated the prompt, in the output as it was before the redaction;
- `evidence` is `{"document": <shingle_hashes_ref>}`, the prompt version the fingerprints came from;
- it has no `class`, `nature`, `role` or `value`.

11.3. The turn that redacted is flagged `guard_acted`. The record never holds the redacted text.

## 12. Where the check runs

12.1. In the SDK: at the stream bridge, before the customer (holding a candidate at most 150 ms and a
message at most 300 ms; past that, the text goes as it is, annotated `guard_budget_exceeded`), or before
a document is saved. The server never runs the check in a turn.

12.2. Each claim becomes one entry of the turn record's `claims`:

| Field | Meaning |
|---|---|
| `category` | The category. |
| `class`, `nature`, `role` | For a number. |
| `span` | Where, in code points. |
| `value` | The normalized value (section 5.3), for a number. |
| `evidence` | The call and field, or the object and field, or the document and score. |
| `verdict` | Section 8.1. |
| `action` | `none`, `block`, `warn`, `count`, `rewrite` or `discard_anchor`: what happened (12.3). |

12.3. The record's `action` is what was done, and the category's configured action (section 8.2)
decides it:

| Configured | Recorded |
|---|---|
| `block` | `block` |
| `warn` | `warn` |
| `count` | `count` |
| `rewrite_if_unequivocal` | `rewrite` when the rewrite is unequivocal (8.3), `warn` otherwise |
| `discard_anchor_and_count` | `discard_anchor` |
| none: the claim stands (`matched`, `quoted_found`, `anchored`) | `none` |
| an internal text redacted (section 11) | `block` |

## 13. Vectors

The vectors follow the shared layout (`{"vectors", "version", "spec", "cases"}`); a runner MUST fail on a
case field it does not know.

13.1. [`claim-parser.v0.json`](../vectors/claim-parser.v0.json): `lang` and `text`; optionally `roles`
(role terms) and `evidence` (values of the turn: `class`, `value`, `role`). `expect.mentions` lists every
mention in order: `span`, `text`, `class`; for all but labels, `value` and `written`; with `roles`, `role`
and `role_status` (`matched`, `ambiguous` or `none`), roles competing among mentions of one class; with
`evidence`, `nature`.

13.2. [`claim-detect.v0.json`](../vectors/claim-detect.v0.json): `contract` (the path of an example
contract), `output` (`text`, `lang`, `context`, `immutable`, `agent`) and `turn` (`values`, with `class`,
`value`, `role`, `fresh`, `object_type`, `name`, `call_id`, `ref`, `declared_gaps`; `tools`;
`documents`; `anchors`, with `span`, `quote`, `document`; `sections`, spans by name).
`expect.findings` lists every claim in the contract's order and then the text's: `category`, `span`,
`text`, for a number `class`, `nature`, `role` and `value`, then `verdict`, `action`, and the `evidence`
used as an index into `turn.values` or `turn.anchors`.

13.3. [`claim-evidence.v0.json`](../vectors/claim-evidence.v0.json): `contract`, `output` (as in 13.2) and
`blocks`, the `state` view and the `constraints` block a context read served (section 4.3). The turn holds
only what they serve. `expect.findings` is as in 13.2, with `evidence` the `ref` and `field` of the value
used (no `ref` for a constraint's value).

13.4. [`claim-anchor.v0.json`](../vectors/claim-anchor.v0.json): `quote` and `document`;
`expect.normalized_quote_length`, `expect.distance` and `expect.holds`.

13.5. A conforming checker passes the four files and finds no claim in any phrase of the negative corpus
of each example contract.

## 14. Examples

See [`examples/claim-contract/`](../examples/claim-contract/), and the invalid ones in
[`examples/invalid/`](../examples/invalid/) whose names start with `claim-contract-`.

## Appendix A. Lexicons

The words and forms of section 5, exactly. Matching is on folded text (section 2), so every entry is
written without accents.

A.1. **Label words** (section 5, step 2), a whole word each, optionally followed by a period:

`pedido`, `order`, `orden`, `protocolo`, `protocol`, `numero`, `nr`, `processo`, `proceso`, `case`, `cep`, `zip`, `art`, `arts`, `artigo`, `artigos`, `article`, `articulo`, `inciso`, `paragrafo`, `lei`, `law`, `ley`, `decreto`, `sumula`, `tema`, `tamanho`, `tam`, `size`, `talla`, `numeracao`, `item`, `itens`, `opcao`, `option`, `opcion`, `posicao`, `position`, `pagina`, `page`, `pag`, `versao`, `version`, `capitulo`, `chapter`, `clausula`, `clause`, `apto`, `apartamento`, `sala`, `vara`, `nf`, `cupom`, `coupon`, `codigo`, `code`, `cpf`, `cnpj`, `rg`, `rastreio`, `tracking`, `chamado`, `ticket`, `sinistro`, `apolice`, `poliza`, `matricula`, `ref`, `referencia`, `resp`, `aresp`, `agrg`, `agint`, `adi`, `adpf`, `hc`, `rr`, `airr`.

The number marks that may stand between: `n`, `no`, `n.`, `numero`, `nr`, `nr.`, `#`, `:`. A paragraph sign
(`§`) makes the number after it a label too.

A.2. **Labels by shape** (section 5, step 1), as a regular expression over folded text:

```
(?<![\w.])(?:\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2}|\d{5}-\d{3}|\(?\d{2}\)?\s?9?\d{4}-\d{4}|\+\d{1,3}(?:[\s-]?\d{2,5}){2,4}|\d{1,2}:\d{2}(?::\d{2})?(?:\s?(?:am|pm))?|\d{1,2}h\d{2}|\d{1,2}\s?(?:am|pm)\b)(?![\w-])|(?<=\bas\s)\d{1,2}h\b|(?<=\bat\s)\d{1,2}(?:h\b|\b)|(?<=\blas\s)\d{1,2}(?:h\b|\b)
```

A.3. **Currencies before a number:** `r$` (BRL), `us$` (USD), `u$s` (USD), `usd` (USD), `brl` (BRL), `eur` (EUR), `€` (EUR), `gbp` (GBP), `£` (GBP), `mxn` (MXN), `ars` (ARS), `clp` (CLP), `cop` (COP), `$` (no code); the letter codes only when no letter precedes them.

A.4. **Scales** after a number: `mil`, `thousand` and `k` multiply by 1,000, `milhao`, `milhoes`, `millon`, `millones`, `million`, `millions` by 1,000,000; `k` only with a currency.

A.5. **Units after a number**, tried in this order, the first that matches deciding. A unit follows the
number (or its scale) after at most one space, `x` right after it, and ends a word; `?` makes the letter
before it optional:

- percent, `%`: `(%|por ?cento|percent|per ?cent|por ?ciento)`
- money, `BRL`: `(reais|real)`
- money, `USD`: `(dolares|dolar|dollars|dollar)`
- money, `EUR`: `(euros|euro)`
- money, no unit: `(pesos|peso)`
- money, `BRL`: `(brl)`
- money, `USD`: `(usd)`
- money, `EUR`: `(eur)`
- duration, `business_day`: `(dias? uteis|dia util|business days?|working days?|dias? habiles|dia habil)`
- duration, `day`: `(dias? corridos|calendar days?|dias? naturales|dias? calendario)`
- duration, `day`: `(dias?|days?)`
- duration, `hour`: `(horas?|hours?|hrs?|h)`
- duration, `minute`: `(minutos?|minutes?|mins?)`
- duration, `week`: `(semanas?|weeks?)`
- duration, `month`: `(meses|mes|months?)`
- duration, `year`: `(anos?|years?)`
- dosage, `mcg`: `(mcg|ug|μg)`
- dosage, `mg`: `mg`
- dosage, `IU`: `(ui|iu)`
- dosage, `drop`: `(gotas?|drops?)`
- dosage, `tablet`: `(comprimidos?|tablets?|pastillas?)`
- dosage, `capsule`: `(capsulas?|capsules?)`
- quantity, `kg`: `kg`
- quantity, `ml`: `(ml)`
- quantity, `l`: `(litros?|liters?|litres?|l)`
- quantity, `GB`: `(gb)`
- quantity, `MB`: `(mb)`
- quantity, `TB`: `(tb)`
- quantity, `mAh`: `(mah)`
- quantity, `cm`: `(cm)`
- quantity, `mm`: `(mm)`
- quantity, `km`: `(km)`
- quantity, `m2`: `(m2|m²)`
- quantity, `m`: `(metros?|meters?|metres?|m)`
- quantity, `g`: `g`
- quantity, `W`: `(w)`
- quantity, `V`: `(v)`
- quantity, `in`: `(polegadas?|pulgadas?|inches|inch)`
- quantity, `unit`: `(unidades?|units?|unidad|pecas?|pieces?|piezas?|itens|items?)`
- quantity, `pair`: `(pares|pairs?)`
- count, `x`: `x`

A.6. **Month names:** 1: `enero`, `janeiro`, `january`; 2: `febrero`, `february`, `fevereiro`; 3: `march`, `marco`, `marzo`; 4: `abril`, `april`; 5: `maio`, `may`, `mayo`; 6: `june`, `junho`, `junio`; 7: `julho`, `julio`, `july`; 8: `agosto`, `august`; 9: `september`, `septiembre`, `setembro`, `setiembre`; 10: `october`, `octubre`, `outubro`; 11: `november`, `novembro`, `noviembre`; 12: `december`, `dezembro`, `diciembre`.
Short names, only with their period: 1: `ene.`, `jan.`; 2: `feb.`, `fev.`; 3: `mar.`; 4: `abr.`, `apr.`; 5: `mai.`; 6: `jun.`; 7: `jul.`; 8: `ago.`, `aug.`; 9: `sep.`, `sept.`, `set.`; 10: `oct.`, `out.`; 11: `nov.`; 12: `dec.`, `dez.`, `dic.`.

A.7. **Range joiners:** a hyphen, an en dash or an em dash, and the words `a`, `ao`, `al`, `ate`, `to`, `hasta`; after `entre` or `between`, also `e`, `and`, `y`.

A.8. **Number words.** Units and tens add; `hundred` multiplies what comes before it (1 when nothing does); `mil` and `thousand` multiply what comes before them into the total; `milhao`, `milhoes`, `millon`, `millones`, `million`, `millions` multiply the total. `e`, `and` and `y` join two number words; after a unit or a teen, another unit, teen or ten needs one of them first (`un veinticinco` is two numbers). A hyphen separates words (`twenty-five`).

- `pt`: `zero` 0, `um` 1, `uma` 1, `dois` 2, `duas` 2, `tres` 3, `quatro` 4, `cinco` 5, `seis` 6, `sete` 7, `oito` 8, `nove` 9, `dez` 10, `onze` 11, `doze` 12, `treze` 13, `quatorze` 14, `catorze` 14, `quinze` 15, `dezesseis` 16, `dezasseis` 16, `dezessete` 17, `dezassete` 17, `dezoito` 18, `dezenove` 19, `dezanove` 19, `vinte` 20, `trinta` 30, `quarenta` 40, `cinquenta` 50, `cincoenta` 50, `sessenta` 60, `setenta` 70, `oitenta` 80, `noventa` 90, `cem` 100, `cento` 100, `duzentos` 200, `duzentas` 200, `trezentos` 300, `trezentas` 300, `quatrocentos` 400, `quatrocentas` 400, `quinhentos` 500, `quinhentas` 500, `seiscentos` 600, `seiscentas` 600, `setecentos` 700, `setecentas` 700, `oitocentos` 800, `oitocentas` 800, `novecentos` 900, `novecentas` 900.
- `en`: `zero` 0, `one` 1, `two` 2, `three` 3, `four` 4, `five` 5, `six` 6, `seven` 7, `eight` 8, `nine` 9, `ten` 10, `eleven` 11, `twelve` 12, `thirteen` 13, `fourteen` 14, `fifteen` 15, `sixteen` 16, `seventeen` 17, `eighteen` 18, `nineteen` 19, `twenty` 20, `thirty` 30, `forty` 40, `fifty` 50, `sixty` 60, `seventy` 70, `eighty` 80, `ninety` 90.
- `es`: `cero` 0, `un` 1, `uno` 1, `una` 1, `dos` 2, `tres` 3, `cuatro` 4, `cinco` 5, `seis` 6, `siete` 7, `ocho` 8, `nueve` 9, `diez` 10, `once` 11, `doce` 12, `trece` 13, `catorce` 14, `quince` 15, `dieciseis` 16, `diecisiete` 17, `dieciocho` 18, `diecinueve` 19, `veinte` 20, `veintiun` 21, `veintiuno` 21, `veintiuna` 21, `veintidos` 22, `veintitres` 23, `veinticuatro` 24, `veinticinco` 25, `veintiseis` 26, `veintisiete` 27, `veintiocho` 28, `veintinueve` 29, `treinta` 30, `cuarenta` 40, `cincuenta` 50, `sesenta` 60, `setenta` 70, `ochenta` 80, `noventa` 90, `cien` 100, `ciento` 100, `doscientos` 200, `doscientas` 200, `trescientos` 300, `trescientas` 300, `cuatrocientos` 400, `cuatrocientas` 400, `quinientos` 500, `quinientas` 500, `seiscientos` 600, `seiscientas` 600, `setecientos` 700, `setecientas` 700, `ochocientos` 800, `ochocientas` 800, `novecientos` 900, `novecientas` 900.

A.9. **Citation patterns** (section 4.1), over folded text:

```
article_citation: \b(?:art|arts|artigo|artigos|article|articles|articulo|articulos)\b\.?\s*(?:n[o.]?\s*)?\d+(?:o|-[a-z])?(?![\w])
precedent_citation: \b(?:resp|aresp|re|are|agrg|agint|hc|ms|adi|adpf|rr|airr)\b\s*(?:n[o.]?\s*)?\d[\d.]*(?:/[a-z]{2})?|\bsumula\s+(?:vinculante\s+)?(?:n[o.]?\s*)?\d+|\btema\s+(?:n[o.]?\s*)?\d[\d.]*\d
```

A.10. **Hedges** (section 5.4), each list for every language:

A.10.1. **Denials**, before a number, to the end of its clause (rule 1): `nao posso confirmar`, `nao consigo confirmar`, `nao tenho como confirmar`, `nao da para confirmar`, `nao e possivel confirmar`, `nao posso garantir`, `nao consigo garantir`, `nao tenho como garantir`, `nao posso afirmar`, `nao consigo afirmar`, `nao tenho como afirmar`, `nao posso verificar`, `nao consigo verificar`, `nao tenho como verificar`, `nao tenho confirmacao`, `sem confirmacao`, `nao sei se`, `nao tenho informacao`, `nao tenho informacoes`, `nao tenho a informacao`, `nao tenho essa informacao`, `preciso verificar`, `precisa verificar`, `precisamos verificar`, `confirmar se`, `verificar se`, `conferir se`, `saber se`, `dizer se`, `can't confirm`, `cannot confirm`, `can not confirm`, `couldn't confirm`, `unable to confirm`, `not able to confirm`, `can't verify`, `cannot verify`, `unable to verify`, `can't guarantee`, `cannot guarantee`, `can't promise`, `cannot promise`, `don't have confirmation`, `do not have confirmation`, `no confirmation`, `don't have information`, `do not have information`, `don't have the information`, `do not have the information`, `need to verify`, `whether`, `confirm if`, `verify if`, `check if`, `know if`, `sure if`, `no puedo confirmar`, `no logro confirmar`, `no consigo confirmar`, `no tengo como confirmar`, `no es posible confirmar`, `no puedo garantizar`, `no puedo asegurar`, `no puedo verificar`, `no tengo confirmacion`, `sin confirmacion`, `no se si`, `no tengo informacion`, `necesito verificar`, `hay que verificar`, `confirmar si`, `verificar si`, `saber si`, `decir si`.

A.10.2. **Past markers**, beside a number (rule 2): `valor anterior`, `preco anterior`, `total anterior`, `valor antigo`, `preco antigo`, `o anterior`, `a anterior`, `os anteriores`, `anteriormente`, `quando visto`, `quando vista`, `quando voce viu`, `antes era`, `era antes`, `previous price`, `previous total`, `previous value`, `earlier price`, `earlier total`, `earlier value`, `old price`, `previous amount`, `earlier amount`, `old amount`, `was the earlier`, `was previously`, `were previously`, `previously listed`, `previously quoted`, `previously shown`, `previously priced`, `seen previously`, `shown previously`, `quoted previously`, `listed previously`, `last shown`, `last quoted`, `last listed`, `when seen`, `when you saw it`, `precio anterior`, `precio antiguo`, `el anterior`, `la anterior`, `cuando lo vio`, `cuando lo viste`.

A.10.3. **Doubts**, after a number (rule 3): `pode ter mudado`, `pode ter sido alterado`, `pode ter sido alterada`, `pode ter sido atualizado`, `pode ter sido atualizada`, `pode nao valer`, `pode nao estar valendo`, `nao vale mais`, `ja nao vale`, `precisa ser confirmado`, `precisa ser confirmada`, `precisa ser verificado`, `precisa ser verificada`, `nao esta confirmado`, `nao esta confirmada`, `nao foi confirmado`, `nao foi confirmada`, `mudou`, `foi alterado`, `foi alterada`, `venceu`, `expirou`, `esta desatualizado`, `esta desatualizada`, `may have changed`, `might have changed`, `could have changed`, `has changed`, `have changed`, `had changed`, `is outdated`, `is out of date`, `no longer applies`, `no longer holds`, `no longer valid`, `may no longer`, `might no longer`, `may not apply`, `has expired`, `expired`, `needs to be confirmed`, `needs to be verified`, `needs to be checked`, `is not confirmed`, `isn't confirmed`, `not confirmed`, `unconfirmed`, `puede haber cambiado`, `pudo haber cambiado`, `ha cambiado`, `ya cambio`, `ya no vale`, `ya no es valido`, `ya no aplica`, `vencio`, `expiro`, `necesita confirmarse`, `no esta confirmado`, `no esta confirmada`, `esta desactualizado`, `esta desactualizada`.

A.10.4. **Openers**, a clause of their own (rule 4): `antes`, `anteriormente`, `da ultima vez`, `na ultima vez`, `na ultima consulta`, `na ultima cotacao`, `previously`, `earlier`, `before`, `last time`, `the last time`, `in the last quote`, `la ultima vez`, `en la ultima cotizacion`.

A.10.5. **Past words**, in the number's clause (rule 5): `foi`, `foram`, `era`, `eram`, `estava`, `estavam`, `ficou`, `ficava`, `custava`, `custavam`, `registrado`, `registrada`, `informado`, `informada`, `was`, `were`, `had been`, `used to be`, `on file`, `on record`, `quoted`, `fue`, `fueron`, `eran`, `estaba`, `estaban`, `costaba`, `costaban`, `quedo`.

A.10.6. **Words of continuity**, after the denial of rule 5: `ainda`, `continua`, `continuam`, `segue`, `seguem`, `mesmo`, `mesma`, `igual`, `atual`, `atualizado`, `atualizada`, `vigente`, `vale`, `valendo`, `valido`, `valida`, `mantem`, `mantido`, `mantida`, `still`, `remains`, `remain`, `current`, `same`, `valid`, `applies`, `apply`, `holds`, `unchanged`, `anymore`, `todavia`, `aun`, `sigue`, `siguen`, `mismo`, `misma`, `actual`, `mantiene`.

A.10.7. **Present words**, between a past marker and a number (rule 2): `agora`, `hoje`, `atual`, `atualmente`, `novo`, `nova`, `para`, `now`, `today`, `current`, `currently`, `new`, `to`, `ahora`, `hoy`, `actual`, `actualmente`, `nuevo`, `nueva`.

A.10.8. **Clause words**, each one starts a clause: `mas`, `porem`, `contudo`, `entretanto`, `pois`, `porque`, `ja que`, `e`, `but`, `however`, `because`, `since`, `although`, `though`, `and`, `pero`, `sino`, `pues`, `aunque`, `ya que`, `y`.

A.10.9. **Negations**, right before a number (rule 6): `nao`, `em vez de`, `ao inves de`, `not`, `instead of`, `rather than`, `en vez de`, `en lugar de`.
