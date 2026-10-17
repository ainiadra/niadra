# Niadra Object Type, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schema:** [`schemas/object-type.v0.json`](../schemas/object-type.v0.json) (one type),
  [`schemas/tool-binding.v0.json`](../schemas/tool-binding.v0.json) (one tool's binding, section 9)
- **Vectors:** [`vectors/niadra-expr.v0.json`](../vectors/niadra-expr.v0.json) (the expression language),
  [`vectors/type-derive.v0.json`](../vectors/type-derive.v0.json) (derivation by introspection)
- **License:** Apache 2.0

## 1. Purpose

A company's agents work on the company's own kinds of state: a sale, a lawsuit, a proposal, a claim, a
hospitalization, an order, an item on a shelf, the working state of an assistant. An **object type**
describes one such kind once, so that any memory and any agent read it the same way: which fields it has
and how sure each value is, where each value comes from and which source wins, how old a value may be
before it may no longer be claimed, what its lifecycle is, when its timers fire, and what a reader may ask
of it.

A type is declared by the company, or derived from the company's own database schema and confirmed by a
person. It always mirrors a system of the company: the memory reflects that system and points out drift,
and is never the barrier that enforces the company's rules.

This document fixes the format of a type and **niadra-expr**, the small language its conditions, timers,
keys and readings are written in. The state a read serves is [`object-state.md`](object-state.md).

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

The **producer** keeps the type registry and the objects; a **source** is a system, a tool or a person that
reports values; a **reader** is an agent or a program that reads objects. A **duration** is a whole number
followed by `ms`, `s`, `min`, `h` or `d`, as in `30s`, `10min` or `7d`. A **rule reference** is a rule's name
and version, `name@version`, as in `prazo_forense@v9`: the rule is the company's, and the producer keeps its
name, never its logic. Names of types, fields, values, time axes, states, sources, relations, timers and
reasons are lowercase ASCII identifiers (`^[a-z][a-z0-9_]{0,63}$`; a type name has at most 40 characters).

A space's registry is a document with `version` and `types`, a list of types each named once.

## 3. The format of a type

| Member | Meaning |
|---|---|
| `type` | The type's name. |
| `version` | The declaration's version, `1` by default. Every object keeps the version it was written under. |
| `ownership` | `subject` (a customer's: an order, a lawsuit, a proposal), `shared` (met by many subjects: an item, a court, a hospital of a network) or `agent` (an agent's working state). |
| `nature` | `observed` (the default) or `derived`: a function of `inputs`, as a quote is. |
| `mirror_of` | The company's system this type mirrors: `system`, `derived_by` (`declaration`, `introspection` or `observation`), the `fingerprint` of the schema an introspection read, and `drift` (`alert` or `ignore`). REQUIRED unless `ownership` is `agent`. |
| `key` | `natural`: the paths of the natural key in a source's payload; `fingerprint`: an expression whose value identifies one observation across sources; `variant`: the path of a variant. |
| `revisions` | `allowed`: whether a source may send the same observation again with new content; `novelty: keep_first_seen` keeps the time the producer first knew of it. |
| `content` | For fields that hold third-party text: `mode` (`full`, `pointer`, `hash_only`), `scan` (`required`, `rules`, `off`) and the `fields` that hold content. |
| `time` | Named time axes (section 3.3) and `timers_on`, the axis timers count on. |
| `inputs` | For a derived type: references to fields of other objects or parameters of the turn (`lead.ages`, `turn.product_type`). A change of any input expires the object. |
| `fields` | The fields, by name (section 3.4). |
| `values` | Values the company computes and the producer keeps as versioned facts (section 3.5). |
| `sources` | The sources, by name (section 3.6). |
| `union` | Per field or value (or `*` for all), how sources combine (section 3.6). |
| `states` | The lifecycle states. |
| `lifecycle` | Transitions, forbidden transitions, latches and the outcome (section 3.7). |
| `timers` | By name: when each is due and what it does (section 3.8). |
| `refetch` | Named reasons to fetch the object again, with a priority and a budget (section 3.9). |
| `purposes` | What a read of each purpose does with stale data (section 3.10). |
| `readings` | Named readings: an expression, a set of fields, or both (section 3.11). |
| `relations` | Related objects by role, and `derived_fields` computed over them (section 3.12). |
| `working_set` | For a shared type: when an object enters the working set and how long it stays (section 3.13). |
| `agent_state` | For an agent's type: scope and write modes (section 3.13, and [`agent-state.md`](agent-state.md)). |
| `retention` | By class of data, how long it is kept: a duration, or `while_in_working_set` for a shared type. |

### 3.1 Ownership and nature

3.1.1. A `subject` type's state comes only from its sources (system events, pushes, observations the type
admits): what someone said in a conversation, or what an agent declared it did, never changes it.

3.1.2. A `shared` type's objects exist in the producer only while something references them (section
3.13). They have no event history; the fields that declare `track_changes` keep their previous value.

3.1.3. `nature: derived` applies to any ownership: a lead's quote is a derived subject object, the best
offer of a product a derived shared one. A derived type names at least one input; an observed one names
none.

### 3.2 Mirroring and drift

3.2.1. `derived_by: introspection` means a tool read the company's database catalog (check constraints,
enumerations, foreign keys, the presence of triggers) inside the company's boundary and proposed the type,
which a person confirmed. Such a type MUST keep the `fingerprint` of the catalog it read. Later, the tool
sends the fingerprint again; a different one opens a drift notice when `drift` is `alert`. Section 8 fixes
what the tool reads, the fingerprint, the proposal and the drift report.

3.2.2. What the sources show that the type does not declare opens the same notice, for a type whose
`drift` is `alert`: a field the type names nowhere (among its fields, values, time axes and derived fields),
or a state that is not one of `states` once the source's vocabulary translated it. The notice names the type,
the field (`state` for a state) and the source, and references the object; it never carries the value. The
producer keeps the value as a plain field and points out the drift: it never refuses what the company's
system did. It MAY count one finding once in a while (an hour, for instance) rather than on every report.

### 3.3 Time

3.3.1. A type names as many time axes as it needs, each with an `origin`: `source` (reported by a source),
`derived` (computed by the company's `rule`, which such an axis MUST name, and only such an axis names) or
`platform` (kept by the producer, such as the time it first knew of the object). `immutable: true` marks an
axis that never moves once set.

3.3.2. `timers_on` names the axis the type's timers count on; it MUST be one of the declared axes.

### 3.4 Fields

| Member | Meaning |
|---|---|
| `type` | `string`, `text`, `number`, `money`, `percent`, `date`, `datetime`, `duration`, `bool`, `enum`, `list` or `ref`. REQUIRED. |
| `logic` | `plain` (the default) or `tri`: the field carries the four logical values of section 5.4 and its observers. |
| `observers` | For a `tri` field: who may observe it (`machine`, `human` or `source:<name>`), under which `rule`, for how long an observation stays valid (a duration or `never`), and which other observers it `overrides`. |
| `unobserved_blocks` | For a `tri` field: what a value nobody observed blocks: `model_read`, `derive`, `claim`, or a task of the company, such as `decide:close`. |
| `label` | The field's name in the space's language, as a text for a model says it (`coparticipação`); at most 60 characters. |
| `role` | The role of the number the field holds, such as `price_full` or `enrollment_fee`, for claims. |
| `claim` | Whether the field may be claimed (`allowed`), its `class` (`money`, `percent`, `date`, `quantity`, `count`, `duration`, `dosage`, `text`), REQUIRED when allowed, and the `nature` of the number (`computed`, `quoted`, `observed`). |
| `attribute` | The `family` and `vocabulary` the field's values belong to, so a preference or a refusal can name them. |
| `freshness` | `class` (`volatile`, `price`, `semi`, `stable`, `none`), `max_age` and `claim_max_age`. |
| `completeness` | `levels` (at least two, from least to most complete) or `total_field`, the field that holds the expected total. |
| `sensitivity` | `none`, or one of the sensitive categories the product fixes (`health`, `religion`, `philosophical_belief`, `political_opinion`, `union_membership`, `sexual_life`, `sexual_orientation`, `racial_or_ethnic_origin`, `genetic`, `biometric`, `criminal_record`). |
| `access` | Rules of `readers` (sources, agents or `public`), `purposes` and `effect` (`allow`, `mask`, `deny`). |
| `pii` | Whether the field holds personal data. |
| `track_changes` | Whether the previous value is kept, for `was()` and for what changed since a subject last saw the object. |

3.4.1. A `plain` field MUST NOT declare `observers` or `unobserved_blocks`. Within one field, an observer is
listed once, and `overrides` names other observers of the same list.

3.4.2. When `max_age` is absent, a field of class `volatile` is stale after 30 s, `price` after 60 s, `semi`
after 1 h and `stable` after 7 d; a field of class `none` never goes stale and declares no age.
`claim_max_age` defaults to `max_age` and MUST NOT exceed it. Freshness is never stored: a read computes
it against the declaration in force ([`object-state.md`](object-state.md), section 4.3), so a new
declaration applies to every object at once.

3.4.3. A change of a field's `sensitivity`, `access` or `pii`, of a source's `licence`, of the type's
`purposes`, of a reading's purposes or of the registry's `commercial_purposes` (section 3.6.4) is a privacy
decision: the producer SHOULD require, besides the role that maintains the registry, the approval of the role
that answers for security.

3.4.4. **Field access.** A reader is an agent or a system that reads objects through its key. It answers to
`public`, and to `agent:<name>` and `source:<name>`, where `<name>` is its source's name lowercased, each run
of other characters than `a` to `z` and `0` to `9` written as one `_`, with none at either end (`WhatsApp
Bot` is `agent:whatsapp_bot`). Its purposes are its key's purposes and, on a read, the read's own (`display`,
`claim` or `decide`). A field's `access` rules are read in order, and the first rule whose `readers` name the
reader and whose `purposes` include one of the reader's, or that names no purpose, decides its `effect`. A
field that declares rules and matches none is denied; a field that declares none is read by anyone who reads
its object.

- `allow`: the field is served.
- `mask`: the field is served with its stamps and without its value (`masked`, object-state section 4), never
  `claim_safe`.
- `deny`: the field is left out, and `withheld` names it with `access`.

The decision is the same wherever the producer serves a field to an agent: a state read, the subject's view,
a context pack's objects and the plain object state. A reading's expression never sees a field the reader's
access masks or denies (section 3.11).

3.4.5. **At a tool's output.** A tool of the company may return fields of an object to its agent's model
before the producer sees them. The producer hands each key, with the type registry it serves the SDK, the
fields of each type that key may not read and their effect (`field_access`, judged with the key's purposes
alone). An SDK that masks a tool's output removes each `deny` field and replaces each `mask` field's value
with a fixed placeholder, in what reaches the model. When the SDK never obtained the registry, it passes the
output through unchanged by default; an option withholds the whole output from the model instead, for a
company that needs to fail closed.

### 3.5 Computed values

A deadline, a price or a waiting period is computed by the company's rule; the producer keeps the result
as a fact.

| Member | Meaning |
|---|---|
| `rule` | The rule reference that computes it. REQUIRED. |
| `inputs` | What it is computed from: fields, time axes, other values or versioned references (`calendar@2026`). |
| `validity` | `until_input_or_rule_change` (the default) or a duration. |
| `declared_gaps` | What the rule does not know (a local holiday, a court's ordinance), which an agent MUST state when it claims the value. |
| `gap_effect` | Which way the gaps err: `errs_early`, `errs_late` or `unknown`. |
| `absent_as` | The name the value takes when the rule finds none, such as `sem_prazo`: a named absence, never zero or null. |

3.5.1. Every version of a value keeps its rule, the hash of its inputs and the version it supersedes. A new
version re-arms the timers that depend on it; a timer that already fired fires again, naming the firing it
supersedes.

### 3.6 Sources and union

| Member | Meaning |
|---|---|
| `kind` | `push`, `pull`, `tool_observation`, `batch`, `quote` or `human`. |
| `precedence` | Lower comes first. REQUIRED. |
| `authoritative_for` | The fields or time axes (or `*`) the source is the authority for. |
| `scope` | `global` (the default), `customer` or `context`. A value of scope other than `global` never feeds a shared object. |
| `sanity` | `min_items`: a live answer with fewer items is discarded. |
| `known_defects` | Fields this source reports wrongly, and the other source to `use_source` for each. |
| `active_when` | A condition (section 5) that turns the source on. |
| `empty_means` | What an empty answer means: `unobserved` (the default) or `absent`. |
| `cost` | `free` (the default) or `paid`. |
| `licence` | `commercial_use` (`allowed` or `forbidden`) and the purposes a value from this source is excluded from. |

3.6.1. A `quote` source answers for one context (a cart, a group of ages): its values live with the reader
for that context and outrank the others for it; `quote(<source>)` reads them (section 5.7).

3.6.2. `union` names, per field or value, how sources combine: `first_authoritative_live` (the first
authoritative source that answered live), `first_by_precedence`, `union` (all values together, as the
coverage of a network is the union of a live route, a snapshot and a document), `min`, `max`, `latest`, or
`divergence` (the same value from two implementations of one rule, compared, with a notice when they
differ). Every `union` key is a declared field or value, or `*`.

3.6.3. A known defect names a declared field or value, and a source other than its own.

3.6.4. **Licences.** A value from a source whose `licence` excludes one of the reader's purposes
(`exclude_from_purposes`), or forbids commercial use (`commercial_use: forbidden`) while one of the reader's
purposes is commercial, never reaches that reader: the field serves from the other sources, and when only such
a source observed it, the field is left out and `withheld` names it with `licence`. The registry names the
purposes whose reads are commercial in `commercial_purposes`, beside its `types`; with none named, a licence
bars only the purposes it excludes.

### 3.7 States and lifecycle

3.7.1. `transitions` list `from` (a state, or `*`), `to`, `by` (who may make it: `source`, `human`,
`agent`, `clock`), an optional `when` condition, `evidence: required` when the company demands evidence of
the system, and `erase_derived`: what derived memory goes when the transition happens (the parties of a
case sealed by a court, for instance).

3.7.2. `forbidden` lists transitions that never happen; a transition is not both declared and forbidden.
The producer does not enforce either list: an agent's declaration of a transition it may not make is
recorded as refused, and a source's transition outside the declaration is drift (section 3.2.2).

3.7.3. `latches` are states that, once reached, keep the time they were first reached, even when the object
leaves them: "was paid" and "is paid now" are different readings of one object.

3.7.4. `outcome` names `provisional` and `final` states (disjoint lists), `expired_without_outcome` (after a
duration, or when a declared value, such as a deadline, passes) as an outcome of its own, and
`vocabularies`: per source, how its codes map to the type's states, with a precedence.

3.7.5. Every state named by the lifecycle is one of `states`.

### 3.8 Timers

3.8.1. A timer's `due` is an expression whose value is a date or a datetime on the `timers_on` axis (a
date is due at the start of that day); `once_per` names what it fires once for; `on_fire` lists `notify`,
`refresh(<field>)` or `transition(<from>-><to>)`.

3.8.2. A `refresh` names a declared field. A `transition` names a transition the lifecycle declares, from
that state or from `*`, that the `clock` may make.

3.8.3. When is a timer due is computed from the rule and the values of the moment; a stored due time only
indexes the producer's sweep.

### 3.9 Refetch

3.9.1. `reasons` name, each once, a `when` condition, a `priority` (`high`, `normal`, `low`) and a
`budget`: `units` (a whole number, or `unlimited`) `per` a scope and period (`item/1h`, `turn`, `session`,
`day`; `item` and `object` both mean the object). `never` lists reasons that never authorize spending.

3.9.2. `budget` is in the company's `unit` (a paid update, a call), never in money (`never_money` is always
`true`), with a `min_interval` between fetches of one object and a `floor`: the age after which a fetch is
due whatever the reasons say.

3.9.3. The producer computes reasons when it reads and when a value arrives, and hands the company's own
worker only what the budget admits. It never calls the company's systems itself.

3.9.4. `unconfirmed_watch` (`fire`, the default, or `drop`) says what a watch whose condition held on a value
that did not come live does when no fresh value answers its revalidation: it fires with `revalidated: false`,
or tells nothing ([`object-state.md`](object-state.md), section 8.5). The revalidation takes the priority and
the budget of the reason named `interest_alert`.

### 3.10 Purposes

Every read names its purpose. The type says what each purpose does with stale data:

| Purpose | `on_stale` | Other members |
|---|---|---|
| `display` | `serve_with_age` | |
| `claim` | `serve_with_prohibitions` | `prohibitions`: what may not be claimed with stale data, such as `affirm_price` or `affirm_no_new_activity`. |
| `decide` | `serve_with_age` or `structured_refusal` | `reason` (REQUIRED with `structured_refusal`) and `action`: what the company's tool answers instead of deciding. |

A read never refuses: a refusal travels beside the values, for the company's tool to act on.

### 3.11 Readings

A reading is a question with a name, for when two readers ask different questions with the same word
(`commercial.paid` and `finance.paid`): an `expr`, a set of `fields` (or `*`), or both; the `purposes` it
serves, a `prefer_source`, which MUST be a declared source, and `include_inputs`. A reading that only
renames a field the product already has is a copy to avoid.

3.11.1. A read serves a reading it asks for only when the reading names no purpose, or one of the reader's
purposes (section 3.4.4), the read's own among them; otherwise the reading, and the fields it would add, are
left out. Its expression reads each field from its `prefer_source` when that source observed the field, and
by the union rule otherwise, and never sees a field the reader's access masks or denies. `include_inputs`
adds the derived object's inputs to the reading's result.

### 3.12 Relations and derived fields

3.12.1. A relation names the related `type`, the path (`via`) that holds the related key, whether there are
`many`, and the `status_field` and `required_field` a composite needs (a look's pieces, a policy's
coverages): whether each piece is exact, missing from the catalog or future, and whether it is required.

3.12.2. `derived_fields` compute `min`, `max`, `count`, `all` or `any` `over` a declared relation, with an
optional `where` condition on each related object, in the related type's terms: `count` counts the related
objects it holds for, `all` and `any` say whether it holds for every one or for some, and `min` and `max`
compare the related objects' `field` among the ones it holds for. Only `min` and `max` name a `field`, and they
must. They are computed at every read and never stored, so they follow every change of a related object
([object state spec](object-state.md), section 5.5).

3.12.3. A relation's `via` is a path in the object's state to the related objects' ids: a list of ids
(`variant_ids[*]`), or a list of entries with the id at the end of the path (`pieces[*].variant_id`), whose
`status_field` and `required_field` the entries carry. A bare id is in the object's own namespace; an id
written `type:namespace:id` is taken as it is.

3.12.3.1. A relation with `match` is reverse: its related objects are the objects of `type` whose field named
by `match` holds the value at `via` (a lawsuit's notices, by its `case_number`), rather than the ones the
object names. The related type is a customer's type (`ownership: subject`) that declares the `match` field; a
reverse relation has no entries, so no `status_field` or `required_field`. A value is matched as text, and a
`via` that finds several values matches any of them.

3.12.4. The registry's `derived_fields_in_text` (off by default) lets the state block's lines for a model say
the objects' derived fields; a state read carries them either way.

### 3.13 Working set, agent state and retention

3.13.1. A shared type's `working_set` says which references bring an object in (`presented`, `engaged`,
`related`, `watched`, `committed`), after how long without one it leaves (`leave_after`), and an optional
`cap`. Only a shared type has one.

3.13.2. An agent's type declares its fields like any type (with `pii` and `sensitivity`) and an
`agent_state` policy: `scope` (`conversation`, `task`, `object`, `subject`) and `write` (`cas`,
`merge_by_key` or `both`, the default). A state is kept whole, with no size cap ([`agent-state.md`](agent-state.md),
section 5). Only an agent's type has one, and it needs no `mirror_of`.

3.13.3. `retention` keeps each class of data for a duration; `while_in_working_set` is only for a shared
type.

3.13.4. **Legal retention.** A customer's type keeps its objects for its `projection` retention after each
last changed: past it, the object leaves with its computed values' versions, timers and links, and what the
company's system sent about it stays in the events, for their own retention. A computed value's superseded
versions are kept 90 days; the latest stays with its object. An agent's working state follows its
`agent_state` retention, and a field its own. A legal hold on an object, or on the customer who owns it,
keeps all of it, and the working set keeps a held shared object, until the hold is released.

## 4. Validation beyond the schema

The schema states the format and these rules of section 3: the ownership rules, `inputs` exactly on a
derived type, and the members only one ownership may have. An implementation MUST also refuse a type that
breaks any of the following, and name each problem with the path of the entry that breaks it (for example
`timers.launch_at.on_fire: transition(scheduled->actve) is not a transition of the lifecycle`):

1. Fields, values, time axes and derived fields share one namespace, and none takes a name the language
   reserves (section 5.5). An absence name is neither reserved nor a field.
2. Every state the lifecycle, the timers or a vocabulary names is declared; `timers_on` names a declared
   axis; content fields, `completeness.total_field`, known defects, `union` keys and `refresh` actions name
   declared fields (or values); `prefer_source`, `use_source`, `quote()` and vocabularies name declared
   sources; `derived_fields` name declared relations whose type the registry declares, and the `field` of
   `min` and `max` is a field of that type.
3. A derived axis names its rule, and only a derived axis does; a type derived by introspection keeps its
   fingerprint.
4. The rules of sections 3.4.1, 3.4.2, 3.6.3, 3.7.2, 3.7.4, 3.8.2, 3.9.1 and 3.10.
5. Every expression parses and resolves against the type (section 5.12), and a derived field's `where`
   against the related type: the conditions (`when`, `active_when`, `where`) give true or false, a timer's
   `due` gives a date or a datetime.

A field the type does not declare may still be observed and named: it is a `plain` field with no policy.

## 5. niadra-expr

### 5.1 Design

niadra-expr is small, deterministic and total: no loops, no recursion, no I/O and no clock but the one it is
given, bounded text and nesting, and a result or one of three errors for every input. The same text MUST
give the same result in every implementation: the producer's, each SDK's and the company's resolver
worker. The vectors of section 6 fix the behavior.

### 5.2 Lexical structure

1. Spaces, tabs and line breaks separate tokens and are otherwise ignored.
2. A **name** is `[a-z_][a-z0-9_]*`, at most 64 characters. Names are lowercase ASCII: any other letter is an
   error.
3. A **number** is `[0-9]+` with an optional `.` and at least one digit after it. There is no sign: a negative
   number comes from subtraction.
4. A **duration** is a whole number of at most six digits immediately followed by a unit: `ms`, `s`, `min`,
   `h` or `d` (`m` is not a unit). A fraction is an error: `1.5h` is written `90min`.
5. A **string** is enclosed in single quotes, on one line, with `\'` and `\\` as its only escapes, and holds
   at most 256 characters.
6. The **operators** are `==`, `!=`, `<`, `<=`, `>`, `>=`, `+`, `-`, `(`, `)`, `[`, `]`, `,` and `.`.
7. The **keywords** are `and`, `or`, `not`, `in`, `true`, `false`, `none`, `yes`, `no`, `unobserved` and
   `known_defect`.

### 5.3 Grammar

```
expression  = or ;
or          = and { "or" and } ;
and         = not { "and" not } ;
not         = "not" not | comparison ;
comparison  = sum [ ( "==" | "!=" | "<" | "<=" | ">" | ">=" | "in" ) sum ] ;
sum         = postfix { ( "+" | "-" ) postfix } ;
postfix     = primary { "." name } ;
primary     = number | duration | string | "true" | "false" | "none" | logical
            | name | call | "(" or ")" | "[" [ or { "," or } ] "]" ;
logical     = "yes" | "no" | "unobserved" | "known_defect" ;
call        = function "(" arguments ")" ;
```

`or` binds loosest, then `and`, then `not`, then comparison, then `+` and `-` (left to right), then `.`.
Comparisons do not chain: `a < b < c` is an error. A dotted name, such as `lead.city` or
`content.completeness`, is one name with parts; after a group or a call, `.` reads a field of a quote
(section 5.7). The arguments of each function are fixed by section 5.7.

The logical words (`yes`, `no`, `unobserved`, `known_defect`) appear only as one side of `==` or `!=`, with a
value on the other side.

### 5.4 Values and the four logical values

Every value carries one of four logical values:

| Logical value | Meaning |
|---|---|
| `yes` | Present: a source or an observer affirmed it (for a boolean: true). |
| `no` | Known and negative: false for a boolean; for anything else, **absent** (the source affirmed there is none), possibly with the name of the absence. |
| `unobserved` | Nobody observed it, or the valid observation ended. |
| `known_defect` | The source answered, and the type declares that this field of that source is wrong. |

`unobserved` and `known_defect` are **unknown**. A present value is a boolean, a number (IEEE 754 double), a
string, a duration (whole milliseconds), a date (a calendar day), a datetime (an instant, to the
millisecond) or a list. Two further kinds exist only inside an expression: business days (section 5.8) and a
quote (section 5.7).

Not observed is never false: a comparison with an unknown value is unknown, and so is its negation.

### 5.5 Names

A one-part name resolves, in order, to:

1. a context name: `state` (the lifecycle state, absent when none), `derived_status` (absent when none),
   `watch_count` (a number, 0 by default) and `purpose` (the read's purpose, `display` by default);
2. a slot of the object: a field, a computed value or a time axis;
3. a declared absence name (a value's `absent_as`), which is that absence;
4. otherwise, a field nobody observed: `unobserved`.

A dotted name resolves to a declared input of that exact path (`lead.city`); to `config.<key>`, the
company's configuration value under `<key>` (absent when not set); or to `<field>.completeness`, the
completeness level of a content field (absent when not given). Any other dotted name is `unobserved`.

The words of section 5.2.7 and `state`, `derived_status`, `watch_count`, `purpose` and `config` are reserved:
no field, value, axis or absence takes them.

### 5.6 Operators

5.6.1. **Logical words.** `x == yes` is true exactly when `x` carries `yes`; likewise for the other three
words, and `!=` negates. The result is always true or false, whatever `x` carries. This is how an
expression asks whether a value was observed: `available == unobserved`.

5.6.2. **Unknown operands.** Otherwise, when either operand of a comparison or of `+` and `-` is unknown,
the result is unknown: `known_defect` when either operand carries it, `unobserved` otherwise. Checking
kinds comes after this rule: `'a' + x` with `x` unknown is unknown, not an error.

5.6.3. **Equality** (`==`, `!=`) of known values:

1. Two absences are equal when either has no name or both have the same name: `none` is any absence, and
   `due_date == sem_prazo` holds when the value is absent under that name.
2. An absence is never equal to a present value, false included.
3. Numbers compare by value (`1 == 1.0`); strings by their characters; booleans by truth; durations by
   length; dates and datetimes as instants, a date being the start of that day (section 5.8); lists item by
   item, unknown when an unknown item decides and false when any pair differs.
4. Any other pair of kinds is the error `expr_type`.

5.6.4. **Order** (`<`, `<=`, `>`, `>=`) of known values: false when either is absent; otherwise numbers,
strings (by Unicode code point, never by UTF-16 code unit), durations, dates and datetimes (dates as the
start of their day) compare; any other kind is `expr_type`.

5.6.5. **`in`** takes a list on its right: false when either side is absent; true when the left equals an
item; otherwise unknown when some item is unknown or compares unknown, and false. A present right side that
is not a list is `expr_type`.

5.6.6. **`and`, `or`, `not`** take true, false or unknown; any other value, an absent one included, is
`expr_type`. They follow three-valued logic: `and` is false when any operand is false, true when all are
true, and unknown otherwise; `or` is true when any operand is true, false when all are false, and unknown
otherwise; `not` swaps true and false and keeps unknown. When the result is unknown it is `known_defect` if
any unknown operand carries it, `unobserved` otherwise. Operands are evaluated left to right, and the first
that decides the result stops the evaluation: `false and x` never evaluates `x`, so it never fails on it.

5.6.7. **`+` and `-`**, after section 5.6.2; when either operand is absent, the result is absent (without a
name):

| Left | Operator | Right | Result |
|---|---|---|---|
| number | `+` `-` | number | number |
| duration | `+` `-` | duration | duration |
| datetime or date | `+` `-` | duration | datetime |
| duration | `+` | datetime or date | datetime |
| date | `+` `-` | business days | date |
| business days | `+` | date | date |
| datetime or date | `-` | datetime or date | duration |

Any other combination is `expr_type`. A datetime outside the years 1 to 9999 is `expr_type`.

### 5.7 Functions

| Function | Arguments | Result |
|---|---|---|
| `now()` | none | The datetime of the evaluation. |
| `age(n)` | a name of a slot or input | The time since the slot was observed at its source, never negative; `unobserved` when that time is not known. |
| `observer(n)` | a name of a slot or input | The observer of the slot's value (`machine`, `human`, `source:<name>`), or absent. |
| `changed(n)` | a name of a slot or input | True when the slot has a previous value and it is not identical to the current one (another logical value, kind or value); false otherwise. |
| `was(e)` | an expression | `e` over the previous values: each slot or input reads its value before its latest change, `unobserved` when none is known, and an attribute reads absent. Context names, configuration, quotes and `now()` read as outside. `was`, `changed`, `age` and `observer` are not allowed inside it. |
| `count(e)` | an expression | The number of items of a list; 0 for an absence; unknown for an unknown; `expr_type` for anything else. |
| `business_days(e, c)` | a count and a calendar's name | Business days on calendar `c`, to add to or subtract from a date (section 5.8). |
| `quote(s)` | a source's name | The reader's quote from source `s` for the current context; `.<field>` reads its fields; `unobserved` when there is none. |
| `presented_in_top(n)` | a whole number from 1 | True when the object was presented at a position at most `n` in the current conversation. |
| `sha256(e, ...)` | one to eight expressions | `sha256:` and the lowercase hexadecimal SHA-256 of the arguments' UTF-8 text joined by U+001F: a string as it is, a whole number in decimal (at most 2^53 - 1 in size; any other number is `expr_type`), a boolean as `true` or `false`, a duration in milliseconds, a date as `YYYY-MM-DD` and a datetime as in section 6.4. Unknown when an argument is unknown, absent when one is absent; a list is `expr_type`. |

The name of an unknown function, a wrong number of arguments or an argument of the wrong form (a number
given to `age`, a string given to `quote`) is `expr_invalid`. A whole expression whose value is business
days or a quote is `expr_type`.

### 5.8 Time

5.8.1. An evaluation has a current instant and a UTC offset (`+00:00` unless given). Where an instant is
needed, a date means the start of that day at that offset.

5.8.2. A calendar is a list of holidays (dates) and the ISO weekdays that are never business days (`weekend`,
Saturday and Sunday unless given; a calendar with no business day is `expr_type`). `business_days(e, c)`
takes a whole count from 0 to 1,000: `date + business_days(n, c)` is the n-th business day after the date,
`date - business_days(n, c)` the n-th before it, and a count of 0 leaves the date as it is. The count is
evaluated first: an unknown count gives that unknown and an absent one an absence; a count that is not a
whole number from 0, or an unknown calendar, is `expr_type`; a count over 1,000 is `expr_limit`.

5.8.3. The deadlines of a court, a contract or a product are the company's rules (section 3.5): the
language only counts simple business days.

### 5.9 Errors

| Code | When |
|---|---|
| `expr_invalid` | The text is not an expression of this grammar, or breaks a rule of sections 5.2, 5.3 or 5.7, or (against a type) of section 5.12. |
| `expr_type` | A value of the wrong kind, or out of its domain, met while evaluating. |
| `expr_limit` | A bound of section 5.10 was exceeded. |

The text is checked in this order, and the first failure is the result: its length; then, left to right,
its tokens (a lexical error, or the token over the bound); then its nesting and its grammar; then the rules
of section 5.3 and 5.7. Evaluation errors come from the operations in the order section 5.6 evaluates them.

### 5.10 Bounds

An expression has at most 1,024 characters, 256 tokens and 16 nested groups (parentheses, brackets and
argument lists), a list literal at most 64 items, a string at most 256 characters, a name at most 64 and a
duration at most six digits. A list read from a slot has at most 1,000 items, and a business-day count is at
most 1,000. With these bounds and no loops, every evaluation ends after a
number of steps proportional to its text and its lists.

### 5.11 Evaluation environment

An evaluation reads an environment: the current instant and UTC offset, the object's slots (each a value
with, when known, the time it was observed at its source, its observer, its previous value and its
completeness), the declared inputs, the absence names, the configuration values, the reader's quotes, the
calendars, the lifecycle state, the derived status, the watch count, the read's purpose and the object's
best presented position in the conversation. Section 6.2 gives its JSON form.

### 5.12 Resolution against a type

When a type is validated, each expression is also resolved against it, and these are `expr_invalid`, named
with the entry's path:

1. a bare name that is a state (`state == open`: states are strings, `'open'`), a source (only `quote()`
   takes one), or `config` without a key;
2. a dotted name that is not a declared input, `config.<key>` or `<field>.completeness` of a field that
   declares completeness;
3. `age`, `observer` or `changed` of a context name, a state, a source or an absence name;
4. `quote()` of an undeclared source or of one whose kind is not `quote`;
5. a comparison, an arithmetic operation or a function argument whose kinds, known from the type, cannot
   meet (`age(available) > 5` compares a duration with a number);
6. a condition that gives something other than true or false, and a timer's `due` that gives something
   other than a date or a datetime.

## 6. Test vectors

### 6.1 The file

[`vectors/niadra-expr.v0.json`](../vectors/niadra-expr.v0.json) follows the vector layout shared by the
Niadra specifications: `{"vectors": "niadra-expr", "version": "v0", "spec": "spec/object-type.md",
"cases": [...]}`. Each case has exactly `id` (unique), `expr`, `input` (section 6.2) and `expect`: either
`{"value": <result>}` (section 6.4) or `{"error": "<code>"}`. A consumer MUST fail on a member it does not
know, never skip it.

The cases cover the four logical values, missing fields, the time axes and every operator, function and
error of this document, and the expressions of the three example types.

### 6.2 The input

| Member | Meaning |
|---|---|
| `now` | The current instant, RFC 3339 with an offset. REQUIRED. |
| `utc_offset` | `±HH:MM`, `+00:00` by default. |
| `fields` | The object's slots by name: fields, computed values and time axes. |
| `inputs` | The declared inputs, by dotted path, as slots. |
| `absent_names` | The declared absence names. |
| `config` | Configuration values by key: strings, numbers, booleans or `null` (absent). |
| `quotes` | By source name, the quote's fields as slots. |
| `calendars` | By name: `holidays` (dates) and `weekend` (ISO weekdays). |
| `state`, `derived_status` | Strings, or absent. |
| `watch_count` | A number, 0 by default. |
| `purpose` | A string, `display` by default. |
| `presented_rank` | The object's best position presented in the conversation, or absent. |

### 6.3 A slot

| Member | Meaning |
|---|---|
| `type` | A field type of section 3.4; when absent, `bool`, `number`, `string` or `list` from the JSON value. `date` and `datetime` values are always typed. |
| `v` | The value: a date as `YYYY-MM-DD`, a datetime in RFC 3339 with an offset (digits below the millisecond are dropped), a duration in whole milliseconds, a list as an array of scalars. |
| `logic` | The logical value; by default `yes` with a value (`no` for `false`) and `unobserved` without one. `no` without a value is an absence; for a boolean it is false. |
| `absent` | The name of the absence, with `logic: no`. |
| `at` | When a source observed the value, for `age()`. |
| `observer` | Who observed it, for `observer()`. |
| `was` | The previous value, with `type`, `v`, `logic` and `absent` only, for `was()` and `changed()`. |
| `completeness` | The completeness level, for `<field>.completeness`. |

### 6.4 The result

A result has `logic` and, when known, the kind and the value:

- unknown: `{"logic": "unobserved"}` or `{"logic": "known_defect"}`;
- absent: `{"logic": "no"}`, with `"absent": "<name>"` when named;
- boolean: `{"type": "bool", "logic": "yes", "v": true}` or `{"type": "bool", "logic": "no", "v": false}`;
- present: `{"type": "<kind>", "logic": "yes", "v": ...}`, where a duration is whole milliseconds, a date
  `YYYY-MM-DD`, a datetime `YYYY-MM-DDTHH:MM:SS.sssZ` in UTC, and a list an array of results.

Numbers compare by value: `42` and `42.0` are the same result.

## 7. Examples

[`examples/object-type/`](../examples/object-type/) holds three types that three sectors wrote as the test
of this format, each fitting it without a special case: an item variant of a fashion retailer (shared, with
a live source, a snapshot that may be shown and never claimed, and a checkout quote), a health plan quote
(derived from the lead's inputs, with a source whose enrollment fee is a known defect) and a court notice with
a deadline (mirrored by introspection, five time axes, observers, and a deadline computed by the company's
rule with its declared gaps). A network hospital and an assistant's working state complete them.

## 8. Derivation by introspection

A company that keeps its state in PostgreSQL already wrote much of a type: the columns, the values a CHECK
allows, the enumerations, the foreign keys. A **deriving tool**, usually an SDK command (`niadra types
derive`), reads that catalog inside the company's boundary and proposes the type; a person reviews the
proposal and submits it as a configuration change. Later the tool reads the catalog again and says whether
it drifted. The catalog, the proposal and the review never leave the company through the tool: only the
fingerprint and the counts of section 8.8 do.

### 8.1 What the tool reads

8.1.1. The tool reads one table's catalog, never a row, in a read-only transaction whose `search_path` is
`pg_catalog` alone, so that every name outside it comes schema-qualified whatever the connection's own path:

| Member | From |
|---|---|
| `table` | The table, as `format('%I.%I', schema, name)` writes it. |
| `columns` | Each column that is not dropped: `name`, `type` as `format_type` writes it, `not_null`, and `enum`, the enumeration's `format('%I.%I', schema, name)`, when the column's type is an enumeration. |
| `primary_key` | The primary key's column names, in key order; empty when there is none. |
| `checks` | Each CHECK constraint, as `pg_get_constraintdef` writes it. |
| `enums` | Each enumeration a column uses, once: `name` as above and `labels` in `enumsortorder`. |
| `foreign_keys` | Each foreign key: `columns` in key order, `references` (the referenced table, as `table`) and `referenced_columns`. |
| `triggers` | Each trigger that is not internal: `name`, `definition` as `pg_get_triggerdef` writes it, and `enabled` (false only when it is disabled). |

8.1.2. The catalog is a JSON object with those members and `"catalog": "postgresql"`. Other databases may
come later, each with its own value of `catalog`.

### 8.2 The normalized catalog

The tool sorts what it read, so the same schema gives the same fingerprint however the catalog listed it:
`columns`, `enums` and `triggers` by `name`; `checks` as text; `foreign_keys` by the canonical JSON (8.3)
of each entry. Every order is by UTF-16 code units, the order of RFC 8785 keys. A column carries `enum`
only when its type is an enumeration. A catalog whose `catalog` is not `postgresql` is refused as
`unknown_catalog`, one whose `table` is not `<schema>.<name>` as `invalid_catalog`, and one with no column
as `no_columns`.

### 8.3 The fingerprint

The fingerprint is `sha256:` followed by the 64 lowercase hex digits of SHA-256 over the RFC 8785 canonical
JSON of the normalized catalog: the digest form of the [turn record spec](turn-record.md), section 6.2.1.
It changes when a column, its type or its nullability, a CHECK, an enumeration's labels or their order, the
primary key, a foreign key or a trigger changes, and for nothing else.

### 8.4 The proposal

From the normalized catalog and three options (`type`, `system`, `ownership`), the proposal is a type that
validates against `object-type.v0.json`:

| Member | Value |
|---|---|
| `type` | The option, or the table's name after the schema turned into a name (8.5) of at most 40 characters. A name that is not a type name is refused as `invalid_name`. |
| `version` | `"1"`. |
| `ownership` | The option, `subject` by default, or `shared`. An agent's working state is never derived: `agent` is refused as `ownership_not_derivable`. |
| `mirror_of` | `system` (the option, `postgresql` by default; refused as `invalid_name` when it is not a system name), `derived_by: introspection`, the `fingerprint`, and `drift: alert`. |
| `key` | `{"natural": [...]}`: the primary key's field names, when the table has a primary key of at most 8 columns and each of them became a field. Absent otherwise. |
| `fields` | One per column, in the order of 8.2, named by 8.5, typed by 8.6. A column 8.6 cannot type, and every column past the 200th field, becomes no field. |
| `states` | The values of the first of the columns named `status` and `state` that became a field, when they come from its enumeration or from a list CHECK (8.6): kept in order, each once, when there are 1 to 50 of them and each is a name (`^[a-z][a-z0-9_]{0,63}$`). Absent otherwise. |
| `relations` | For each foreign key of one column that became a field, up to 20: the role is the field's name without a trailing `_id` (the field's own name when what remains is not a name, or is reserved), with `_2`, `_3` ... after a role already taken; its `type` is the referenced table's name after the schema turned into a name of at most 40 characters, and `via` is the field. Absent when there is none. |

A member with no value is absent. The proposal declares no claim, freshness, source, lifecycle or purpose: a
person adds what the company's rules say.

### 8.5 Names

A catalog name becomes a name of the type this way: each letter `A` to `Z` becomes its lowercase letter, and
each other code point outside `[a-z0-9_]` becomes one `_`; a result that does not start with a letter gets
`c_` in front; it is cut at the limit (64 characters, 40 for a type); and a word the language reserves
(section 5.5) gets a `_` after it. Columns are named in the order of 8.2, and a column whose name an earlier one
took gets `_2`, `_3` ... after it, cut so the whole fits the limit. `Delivery Window` becomes
`delivery_window`, `state` becomes `state_`, and `2fa` becomes `c_2fa`.

### 8.6 Field types

The first rule that applies types the column:

| Column | Field type |
|---|---|
| In a foreign key of one column | `ref` |
| Of an enumeration, or named by a list CHECK | `enum` |
| An array (its type ends in `[]`) | `list` |
| `text`, `character varying`, `character`, `citext`, `uuid` | `string` |
| `smallint`, `integer`, `bigint`, `numeric`, `real`, `double precision` | `number` |
| `money` | `money` |
| `boolean` | `bool` |
| `date` | `date` |
| `timestamp`, with or without time zone | `datetime` |
| `interval` | `duration` |
| Anything else (`json`, `jsonb`, `bytea`, `time`, a geometric or network type...) | no field |

A type's modifier does not count: `character varying(20)` is `character varying` and `numeric(12,2)` is
`numeric`.

A **list CHECK** holds one column to a list of constants. After `CHECK ` and an optional trailing
` NOT VALID`, and without the parentheses that enclose the whole, its text is `<column> = ANY (<array>)` or
`<column> = <constant>`, where:
- `<column>` is a column name, bare (`[a-z_][a-z0-9_$]*`) or double-quoted (`""` for a quote inside), maybe
  in parentheses and cast (`(size)::text`);
- `<array>` is `ARRAY[<constant>, ...]`, maybe in parentheses and cast (`(ARRAY[...])::text[]`);
- a `<constant>` is a string literal (`''` for a quote inside) or a number (`-?[0-9]+(\.[0-9]+)?`), maybe in
  parentheses and cast. A string literal cast to `smallint`, `integer`, `bigint`, `numeric`, `real` or
  `double precision` whose text is a number is that number: PostgreSQL writes a negative constant that way.

Parentheses, brackets and `::` count only outside string literals and double-quoted names. A CHECK in any
other form, such as a comparison or two conditions, is not read: it goes to the review.

### 8.7 The review

With the proposal, the tool lists what a person must look at before submitting it, in this order:
1. `{"kind": "column", "column", "type"}` for each column that became no field;
2. `{"kind": "check", "definition"}` for each CHECK that is not a list CHECK of a column of the table;
3. `{"kind": "states", "column"}` when the column the states come from (8.4) has values that cannot be states;
4. `{"kind": "foreign_key", "columns", "references"}` for each foreign key that became no relation;
5. `{"kind": "trigger", "name", "enabled"}` for each trigger. A trigger is code: the transitions it
   enforces are the company's to declare in `lifecycle`, and the tool never guesses them.

The review stays with the company.

### 8.8 Drift

8.8.1. Checking a declared type, the tool reads the table's catalog again and computes its fingerprint. The
type has **drifted** when the fingerprint differs from its `mirror_of.fingerprint`.

8.8.2. The **changes** compare the declaration the tool holds with the proposal the current catalog makes
under the declaration's own `type`, `system` and `ownership`:

| Member | Value |
|---|---|
| `fields_added` | Fields of the proposal the declaration does not have. |
| `fields_removed` | Fields of the declaration the proposal does not have. |
| `fields_retyped` | Fields both have, with a different `type`. |
| `states_added`, `states_removed` | States of one and not the other. |
| `relations_added`, `relations_removed` | Relation roles of one and not the other. |
| `key_changed` | Whether the natural keys differ (an absent key is an empty one). |
| `fields` | The declaration's fields removed or retyped and, when the states differ, its `status` (or else `state_`) field when it has one; sorted, at most 50. |

The first seven are counts. The changes never carry a new name, a value or a definition. A type that
drifted with no change counted drifted in a part the type does not show: a CHECK of another column, a
trigger, a column type that maps to the same field type.

8.8.3. The tool reports a check with `POST /v1/types/fingerprint` (a key with the `push` scope):
`{type, fingerprint, changes?}`. The producer compares the fingerprint with the declared type's and answers
`{drift, issue_id}`:
- the same fingerprint: `drift: false` and `issue_id: null`, and nothing opens;
- another one, with `drift: alert`: `drift: true`, and a data issue of kind `drift` for the type opens, or
  the one already open counts one more occurrence; it names the field when `changes.fields` names exactly
  one, and emits the `type.drift` webhook once for the issue, when it opens. `issue_id` names it;
- another one, with `drift: ignore`: `drift: true` and `issue_id: null`.

A type the space does not declare answers 404; a declared type with no `mirror_of.fingerprint` answers 422
`no_fingerprint`.

8.8.4. The producer also points out drift without the tool, by observation (section 3.2.2).

### 8.9 Vectors

[`vectors/type-derive.v0.json`](../vectors/type-derive.v0.json) follows the vector layout of section 6.1.
A case has `id`, `description` and `op`:
- `derive`: `catalog` (as the tool read it, in any order), `options` (`type`, `system`, `ownership`, each
  optional) and `expect`: `{"fingerprint", "type", "review"}`, or `{"error": "<code>"}`;
- `changes`: `declared` and `live` (two types) and `expect`: `{"changes": {...}}`.

An implementation MUST reproduce every expected member exactly, the `type` as a JSON value (the order of
its members does not count) and the `review` in order.

## 9. Tool bindings

A space binds its agents' tools to the fields of its types, so that a memory can speak a tool's language: which
argument carries which field, where a result carries objects, and what the tool can do. The bindings are one
document of the space's configuration, `tool-bindings`, with `bindings`, a list of at most 200. One binding
([`schemas/tool-binding.v0.json`](../schemas/tool-binding.v0.json)):

| Member | Meaning |
|---|---|
| `tool` | The tool's name, as the agent's framework calls it (`^[A-Za-z0-9_][A-Za-z0-9_.:/-]{0,63}$`). REQUIRED. |
| `args` | The arguments that carry fields, at most 50: each with `attr` (the field, `type.field`), `param` (the argument), and optionally `transform` (`lower` or `upper`, the case text is sent in), `negation.param` (the argument that takes the values the field must not have) and `ops` (the comparisons the argument takes, among `in`, `not_in`, `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `between`). |
| `results` | Where a result carries objects, at most 10: `path` (a small path into the result: `$` the result, `a.b` a key, `a[*]` each item of a list), `type`, `namespace`, `id` (the key of an item that holds the object's id, `id` by default) and `fields` (`{field: key}`, which key of an item holds which field). |
| `capabilities` | `overfetch` (the tool returns more than asked, and the reader filters what no argument carried), `relax_flag` (a path in the result that is `true` when the tool relaxed what it was asked), `dry_run_param` (the argument that makes a call change nothing) and `mask_output` (the reader masks in the tool's output the fields its access may not read). |

In the document, a binding MAY also name `sources`, the ids of the sources whose agents call the tool; none
means every source.

9.1. An argument is bound once: no two `param`s of one binding, a `negation.param` included, are the same. One
field MAY have several arguments, such as a lower and an upper bound.

9.2. A tool is bound once for any one source: two bindings of one tool MUST name disjoint, non-empty `sources`.

9.3. A producer serves each source the bindings of its own tools in the SDK's profile (`GET /v1/sdk/profile`,
`tool_bindings`), without `sources`. A reader takes a tool's binding only from the profile: the document is
the one place a binding is declared, so the space's tools, its readers and the producer never disagree on it.

9.4. The constraints block is rendered through a binding as [`constraints.md`](constraints.md), section 6,
says. `POST /v1/constraints` with `tool` answers the block with `rendered` in advisory mode, through the
binding of that tool for the caller's source, since the producer sees no call; a tool the space does not bind
for that source answers 422. The producer's MCP tool `get_constraints` answers the same block for the customer
its connection is bound to, rendered when the model names the tool it is about to call, and reports an unbound
tool as a tool error the model can read.
