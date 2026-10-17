# Niadra Constraints Block, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schema:** [`schemas/constraints.v0.json`](../schemas/constraints.v0.json)
- **Vectors:** [`vectors/constraint-render.v0.json`](../vectors/constraint-render.v0.json)
- **License:** Apache 2.0

## 1. Purpose

"Nothing in black", "not that network", "don't use that argument": what one person wants, refuses and
is, in a form an agent's tools can use. The block arrives once per turn with the context, and the SDK
renders it for each tool through the tool's bindings: the arguments it can carry, and what is left over.
This document fixes the block, the rules of what may enter it, how it renders, and how conformance is
measured after the call.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

Objects, fields, attributes and `for` are written as in the [interaction spec](interaction.md),
section 2. A tool's bindings (`tool-bindings`) are defined by the [object type spec](object-type.md), section
9; this document uses their `args`: `attr`, `param`, `transform`, `negation.param` and `ops`.

## 3. The block

| Field | Meaning |
|---|---|
| `version` | `cv_` and a hex digest of the block's content (section 3.1); the same content gives the same version, and the turn record cites it. |
| `subject.for` | Whose block it is: `self`, `beneficiary:<id>` or `gift`. |
| `hard` | What the person said they want or refuse, and must hold (section 4). |
| `soft` | Preferences with a weight: inferred affinity, or a stated `prefer`/`avoid`. |
| `attributes` | What the person is (a size, a network), with where it came from and when it applies. |
| `exclude` | Objects not to offer again. |
| `already_presented` | Objects shown before, with how many times, and the numbers each was last shown with (section 3.2). |
| `relaxation_order` | What gives way first when a search finds nothing: `soft` (all soft entries), then ids. |
| `precedence` | `current_utterance`, `stated_persistent`, `inferred`: what wins over what. |
| `rules` | The company's own rules delivered with the block, each with its origin and rank. |
| `conflicts` | Entries that cannot both hold, the one kept, and why. |
| `instructions` | What people said must or must not be done about the objects the read is about (section 5.5). |
| `ask` | Questions the agent should ask before assuming, as `for_whom`. |
| `contact_windows` | The local hours the person said they may not be contacted in, one per channel (null: every channel), the latest said (section 3.3). |
| `text` | Optional: the block as lines for a model, in the space's language (section 5.3). |
| `rendered` | Present when the block was requested for one tool: the rendering (section 6). |

3.1. A producer computes `version` over the block without `version`, `rendered` and `text`; the reference producer
takes the first 16 hex digits of the SHA-256 of its canonical JSON (keys sorted, no spaces). A consumer
never recomputes it: it only cites it.

3.2. Each object in `already_presented` has `ref`, `times` and `last_at`, and `values`: each field its object type
declares as `money`, `percent` or `number`, as the subject was last shown it (an offer's price, total, discount or
installment), by the field's name, with `v`, the field's `role` for claims, and `claim_safe`: true while the type
allows claims of the field ([object type](object-type.md), `claim.allowed`) and no more than the field's
`claim_max_age` (else its `max_age`, else its freshness class's default) has passed since it was shown. A field
the type holds private (personal data, a sensitive category) is never among them, and a field the pack's policy
does not let go leaves the block (section 5.4). A claim check takes them as evidence
([claim contract](claim-contract.md), section 4.3).

3.3. Each entry of `contact_windows` has `channel`, `window` (`from`, `to`, `tz`, `days`, as the suppression list
writes a window, [suppression list](suppression-list.md), section 6.4) and `said_at`. It comes from what the person
said ("não me liguem antes das 9h"); a person who takes it back leaves the block without it. It says when the team
may reach them, never what may be said to them: `text` writes it in words in a section of its own ("ligação: não
antes das 09:00, horário de America/Sao_Paulo").

Each hard constraint has `id` (`h` and a number), `attr`, `op` and `values` (as a preference of the
interaction spec, section 6.1), `source`, `scope`, `category`, `relax` (`never` or `ask`), `origin`
(`kind`, and the `event_key` or `turn_id` where it was said), `expires_at` and, with `scope` `object`, the
`object` (`type:namespace:id`) it is about (section 5.5). Soft entries have `id`
(`s…`), `attr`, `value`, `polarity`, `weight`, `confidence` and `source`; attributes have `id` (`z…`),
`name`, `system`, `value`, `confidence`, `source`, `apply` and `category`; company rules have `id`
(`r…`), `origin`, `rank` and, optionally, `attr`, `op` and `values`.

## 4. What may enter the block

4.1. A hard constraint comes only from what the person said: its `source` is `stated`, `tool_args` or
`correction`. An inference MUST NOT become a hard constraint, and a negative inferred from behavior is
at most soft.

4.2. An attribute the person did not say (`acquired`, `kept`, `returned_for_size`, `inferred`) MUST
have `apply` `when_asked`: an inferred size applies only when the person asks for "my size".

4.3. Each id is unique in the block. `relaxation_order` names only `soft` or entries that may give way:
a hard constraint with `relax` `never` never does.

4.4. The current utterance wins. When a hard constraint said now clashes with one said before, the block
keeps the new one and reports the pair in `conflicts` with `by` `current_utterance`; between a stated
entry and an inferred one, the stated entry is kept (`by` `precedence`). Both entries of a conflict stay
in the block, so a turn record can cite either; `kept` says which one stands.

4.5. A company rule carries its `origin` (`code`, `db:<table>`, `prompt:<version>`, `policy:<version>`)
and its `rank` in the order the company declared. The block never ranks two origins by itself; a clash
between two company rules is kept by rank (`by` `rule_rank`) and reported to the company as a data
issue.

4.6. The block of a beneficiary holds that beneficiary's entries: the size of the person the purchase
is for decides.

## 5. Delivery

5.1. The block reaches the agent once per turn, with the context (`include: ["constraints"]`), or through
`POST /v1/constraints` for one subject, optionally for one tool.

5.2. The turn record's reads cite the block by `version`.

5.3. **Text for a model.** A producer MUST send `text`: the block's lines inside one element named in the space's
language (`restrições`, `constraints`, `restricciones`), one line per entry, each opening with `- `. A field is
named by the `label` its object type gives it ([`object-type.md`](object-type.md), section 3.4), else by its own
name with `_` read as a space; an operator is said in words, and a yes-or-no field as its presence or absence
(`exigido: sem coparticipação`, `required: monthly price at most 700`); a hard constraint says it is required,
a soft entry says `prefers` or `avoids`, and an entry of one category names it. A hard constraint that lost a
conflict has no line, and a block with nothing to say has an empty `text`. A hard constraint on a field the subject
was shown (the items of its type in their latest `presented` interaction, with that field) says which of those
options meet it, each by the id of its ref (`exigido: sem coparticipação; das opções mostradas, atende: pleno-1;
não atende: essencial-1`): an agent that has the filter and not the options cannot pick one. A consumer places
`text` as it is (the reference SDKs put it in the turn block, beside the state view's text).

5.4. The block of a context read goes only as far as the read's pack, entry by entry: an entry goes only where
the pack's policy, at the read's level and for its reader, lets an item of the object type it names go (a
constraint's or an attribute's field, the ref of what was shown or excluded, the field of a number shown), and never when that type holds
the field private (personal data, a sensitive category, access rules). The block is then versioned again. What
the pack withholds never reaches the block, and nothing else is held back for it. `POST /v1/constraints` is a
program's own read, with its own receipt, and is not tied to a pack.

5.5. **Instructions about an object.** What a person said must or must not be done about one object ("in
this case, do not propose a settlement") is kept with the object, never as the subject's own constraint. It
reaches the block of a context read only while the read is about that object: a read by `object`, or a task
view that names the object's type. Each is an entry of `instructions`, with `id` (`i` and a number), `object`
(or `about`, below),
`text` (as it was said, short), `strength` (`must` or `must_not`), `attr` (the field of the object's type it
is about, when it names one), `hard` (the id of the entry of `hard` that carries it), `role` (who said it, by
their role on the object), `said_at` and `expires_at`. An instruction that names a field is also a hard
constraint with `scope` `object`, `op` `eq` (`must`) or `ne` (`must_not`) and `values` `[true]`, which a tool
binding renders like any other (section 6). An instruction never relaxes. Its `text` line says it is required,
with its words and the object it is about (`exigido: não propor acordo (processo c-1)`); its hard entry has no
line of its own. Out of focus, an instruction is said on its object's line in the pack and the state view, and
not in the block.

An instruction that names no object, said about an organization the speaker is linked to (a client of the
firm), is about that organization: it has `about` (the organization's profile id) in place of `object`, never
`attr` nor a hard entry, and reaches the block of every read of the organization, its own and a contact's read
with `about` (`exigido: não falar de valores com a esposa (com este cliente)`).

## 6. Rendering for one tool

A renderer takes the block, the tool's binding, the call the model made (its arguments, the call's
`for`, its `category` and the attributes the person asked to use in this turn) and a mode, and yields:
the arguments sent, the suggested values, what was injected, the hard constraints sent, the residual,
what is filtered after the call, and conflicts. A renderer MUST follow these rules, in order.

6.1. **Whose block.** When the call is for another `for` than the block, the block does not apply at
all: the call goes as it is, and nothing is suggested, sent or residual.

6.1.1. **Conflicts.** A hard constraint named in `conflicts` and not `kept` there is not rendered: it is
neither suggested, sent, injected nor residual.

6.2. **Category.** A hard constraint or an attribute with a `category` applies only to a call of that
category.

6.3. **Routing a hard constraint.** Through the binding argument whose `attr` is the constraint's:
- `in` and `eq` render to the argument's `param`, unless its `ops` exclude them;
- `not_in` and `ne` render to the argument's `negation.param`;
- `lt`, `lte`, `gt`, `gte` and `between` render to `param` only when `ops` lists the operator.

A constraint no argument can express is **residual**.

6.4. **Values.** One value renders as itself and several as a list; `between` renders as `[low, high]`.
`transform` `lower` or `upper` changes the case of text values. Two constraints routed to the same
parameter merge their values when both are `in`/`eq` or both `not_in`/`ne` (in order, without
repeats); any other second constraint on a parameter is residual.

6.5. **Attributes.** An attribute renders through the argument whose field belongs to the attribute's
family (the first part of its name: `size` for `size.pants`) when it applies: `apply` `always`, or
`when_asked` and the call's asked attributes name the family or the attribute. A parameter already
suggested by a hard constraint is not overwritten.

6.6. **Exclusions.** `exclude` is residual as a whole (`exclude`).

6.7. **Residual.** When the binding says the tool overfetches, the residual is filtered from the results
by the SDK (`post_filter`); otherwise it is unenforced, and the rendering says so.

6.8. **Conflicts.** When the call sets a constraint's own argument (`param`) to a value the constraint
refuses, the call's value stands (the current utterance wins) and the pair of constraint and argument is
reported as a conflict.

6.9. **Advisory mode** (the default) sends the call as it is and returns the suggestions.

6.10. **Apply mode** adds a suggested parameter only when the call left it out and every entry behind
it may be injected: a hard constraint with `source` `stated`, `tool_args` or `correction`, `scope`
`turn`, `session` or `object`, and no conflict; or an attribute with `source` `stated` or `correction`. It never
overrides an argument the call set, never injects an inferred size, and never injects a persistent
constraint (a persistent one is suggested).

6.11. **Sent.** A hard constraint is sent when the final arguments carry it: for `not_in` and `ne`, the
negation parameter holds all its values; for `in` and `eq`, every value of the parameter is one of its
values; for a comparison, every value of the parameter satisfies it. Text compares without case and
accents, numbers as numbers.

## 7. Measuring what was honored

What was sent is not what was applied: a tool may relax a filter on its own. The SDK reads the tool's
results (the fields the binding maps) and counts, over the hard constraints sent:

- `results_checked`: the results read;
- `violations`: results that break at least one of them;
- `unverifiable`: results that break none but lack the field of one. Without this count, conformance
  would lie upward;
- `relaxed`: when the tool says it relaxed (the binding's relax flag), or a suspicious relaxation.

The measure is "sent, verifiable, violated", never only "sent". These counts go to the turn record,
which aggregates them per day without the person.

## 8. Vectors

[`vectors/constraint-render.v0.json`](../vectors/constraint-render.v0.json) follows the shared vector
layout (`{"vectors", "version", "spec", "cases"}`). Each case has:

| Field | Meaning |
|---|---|
| `id` | Unique, dotted. |
| `block` | A block of this document. |
| `binding` | `tool`, `args` (`attr`, `param`, `transform`, `negation.param`, `ops`) and `overfetch`. |
| `families` | The attribute family of each bound field, as the type registry gives it. |
| `call` | `args`, `for`, `category` and `asked`. |
| `mode` | `advisory` or `apply`. |
| `results` | Optional: result items keyed by `type.field`. |
| `expect` | `applies`, `args`, `suggested`, `injected`, `hard_sent`, `residual`, `post_filter`, `conflicts` (`id`, `param`), and `honored` (`results_checked`, `violations`, `unverifiable`) when there are results. |

A conforming renderer yields every `expect` field exactly. A runner MUST fail on a case field it does not
know.

## 9. Examples

See [`examples/constraints/`](../examples/constraints/), and the invalid ones in
[`examples/invalid/`](../examples/invalid/) whose names start with `constraints-`.
