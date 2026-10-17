# Niadra Object State, version 1

- **Status:** draft
- **Date:** 2026-09-29
- **Schema:** [`schemas/object-state.v1.json`](../schemas/object-state.v1.json) (one object as a read serves it)
- **License:** Apache 2.0

## 1. Purpose

The object state says where an object stands and, for an object of a declared type
([`object-type.md`](object-type.md)), how far each value can be trusted at the moment of the read: its
logical value (not observed is never false), who observed it and when, the source used, how fresh it is
against the declaration in force, whether it may be claimed, what the rule behind a computed value does not
know, and what may not be said with data this old.

This document also fixes how a system of record pushes state (section 10), how a third party's content is
scanned before a model reads it (section 11), and the view of a subject that the context read carries
(section 12).

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

The terms are those of [`object-type.md`](object-type.md), section 2. A **read** names objects, optionally
the fields and readings it wants, and its **purpose**: `display`, `claim` or `decide` (`display` when it
names none).

## 3. The object state

| Member | Meaning |
|---|---|
| `ref` | The object: `type`, `namespace`, `id`, and `variant` when the type's key has one. |
| `type_version` | The version of the declaration the read was computed against. |
| `as_of` | The time of the latest change the state reflects. |
| `state` | The lifecycle state. |
| `latches` | Each latch state reached, with the time it was first reached (section 5.3). |
| `outcome` | `state` (a state of the type, or `expired_without_outcome`), `final` and `at` (section 5.3). |
| `derived_status` | For a derived object: `current` or `expired_by_input` (section 5.2). |
| `expired_by` | The inputs whose change expired it, as the type names them (`lead.city`). |
| `axes` | Each time axis of the type whose value is known: a date as `YYYY-MM-DD`, an instant in UTC. |
| `fields` | The fields served, by name (section 4). |
| `values` | The computed values served, by name (section 5). |
| `derived` | The type's derived fields, by name, computed at the read from the related objects (section 5.5). |
| `timers` | The timers: `name`, `due_at` (computed at the read), `state` (`armed`, `fired`, `cancelled`), `fired_at` and `supersedes_firing_id` (section 5.4). |
| `readings` | The readings asked for, by name (section 6). |
| `prohibitions` | What may not be claimed with the data served (section 7). |
| `declared_gaps` | The gaps of every value served, which a claim of those values MUST state. |
| `withheld` | Fields left out for this reader, each with why: `access` (the field's access denies the reader, or a derived field reads a related field it denies or masks), `licence` (only a source whose licence bars the reader observed it) or `scan` (section 11). |
| `refetch` | Why the object should be fetched again, if it should: `reason`, `priority`, and whether the budget `admitted` a request. |
| `refusal` | For a `decide` read of a type that asks for it: `reason` and `action` (section 7.3). |
| `blocked` | What the fields whose value is not known block now, as the type declares it (section 7.4). |

A read's answer lists the objects and says `degraded` when it was served from a fallback (section 9).

## 4. A field

| Member | Meaning |
|---|---|
| `v` | The value, when known and not masked. |
| `logic` | `yes`, `no`, `unobserved` or `known_defect` ([`object-type.md`](object-type.md), section 5.4). |
| `status` | `fresh`, `stale` or `expired`, computed at the read (section 4.3). |
| `age_s` | Seconds since `observed_at`. |
| `observed_at` | When the source observed the value. |
| `src` | The source whose value is served. |
| `observer` | Who observed it: `machine`, `human` or `source:<name>`. |
| `valid_at` | When the value became true in the world, on the axis the type declares for it. |
| `known_at` | When the producer first knew of it. It never moves, even when a revision changes the value. |
| `role` | The field's role, for claims. |
| `claim_safe` | Whether the value may be claimed now (section 4.4). |
| `completeness` | The completeness level of a content field. |
| `masked` | The reader's access masks the value; `v` is left out. |
| `was` | The previous value (`v`, `logic`), for a field that tracks changes. |
| `content` | For a content field: `mode` (`full`, `pointer`, `hash_only`), `sha256` of the text, the `pointer` the reader's SDK resolves inside the company's boundary, and `scan` (`pending`, `clean`, `flagged`). |

### 4.1 The logical value

4.1.1. A field is `yes` when a source or an observer affirmed it, `no` when a source affirmed the negative
(or an authoritative source whose `empty_means` is `absent` returned nothing), `unobserved` when nobody
observed it or the valid observation ended (an observer's `validity` passed), and `known_defect` when the
source used is declared wrong for that field. A value a person observed outranks a machine's when the type
says so (`overrides`).

4.1.3. The observer of a value is the one its report names; otherwise `human` for a source of kind `human`;
otherwise `machine` when the field lists `machine` among its observers and not the source; otherwise
`source:<name>`. When the field's observers declare a `validity`, an observation older than its observer's
validity no longer stands, and the field reads `unobserved` if no other does.

4.1.4. The producer keeps each source's latest observation of a field apart. A read serves the one the type's
`union` picks (`first_by_precedence` when the type names none; `latest` for a type the registry does not
declare), leaving out a source whose `active_when` does not hold and a list shorter than the source's
`sanity.min_items`. When the source picked has a known defect for the field, the read serves the value of the
defect's `use_source`, or `known_defect` while that source has not reported.

4.1.5. Which declared source a report belongs to is decided at the read, so a declaration that names a source
later applies to what was stored before it. A value a system reports itself (a system event, a push) belongs to
the declared source named as the key's source (lowercase, with any other character as `_`), or else to the
type's one source of kind `push`; what a tool's result showed belongs to the declared source named after the
tool, or else to the type's first source of kind `tool_observation`. A source the type does not declare comes
after every declared one.

4.1.2. The producer's own outage is never `unobserved`: a reader that cannot reach it serves its last copy
and says `degraded` (section 9).

### 4.2 The stamps

4.2.1. The producer keeps, per field and source, stamps and never an expiry: `valid_at`, `observed_at`,
`known_at`, the source's version, the observer and the provenance. A value is applied in the order it arrives;
a late value moves a field only when its `valid_at` is newer than the field's, and still enters the object's
history. At the same `valid_at`, it moves the field when the source's version is greater (as numbers when both
are), or, for a type that allows revisions, when it arrives later.

4.2.2. A revision of the same observation (the same key with new content, when the type allows revisions, or a
new version at the same `valid_at`) changes the fields and keeps `known_at`: what is new stays new.

### 4.3 Freshness

The status is computed at every read against the declaration in force, so a new declaration applies to
every object at once:

- `expired`: nothing valid stands: the field was never observed, its observation's validity ended, or the
  derived object is `expired_by_input`;
- `stale`: older than the field's `max_age` (the class's default when not declared);
- `fresh`: otherwise, and always for a field of class `none` or with no freshness declared.

A stale value is served with its age, never hidden: the read never refuses.

### 4.4 When a value may be claimed

`claim_safe` is true only when all of these hold:

1. the type allows claims of the field;
2. its logical value is `yes` or `no`;
3. its status is `fresh` and its age is at most `claim_max_age`;
4. it came from a `live` observation, or from its source's own report (a system event, a push), never from a
   snapshot or a cache;
5. it is not masked, and its content (if any) is `clean`;
6. the object is not `expired_by_input`;
7. no field whose value is not known blocks `claim` (section 7.4).

## 5. A computed value

| Member | Meaning |
|---|---|
| `v` | The value, when present. |
| `logic` | `yes`, or `no` when the rule found none. |
| `absent_as` | The name of that absence, as the type declares it. |
| `version` | The value's version, from 1 (section 5.1). |
| `rule` | The rule reference that computed it. |
| `computed_at` | When the company computed it. |
| `status` | `fresh` while valid; `expired` when its `validity` duration has passed, or its derived object expired by an input. |
| `supersedes_version` | The version it replaced, after a new rule, value or input. |
| `declared_gaps`, `gap_effect` | What the rule does not know, and which way that errs. |
| `claim_safe` | Whether it may be claimed now: present or named absent, and fresh. |

A value is served once a source reported it. A null, or the name of its absence, is that absence;
`computed_at` is when the value became true. A time axis the type derives by a rule is kept the same way.

### 5.1 A value is a fact with versions

5.1.1. The company computes; the producer keeps what it computed as a fact. A report that moves a value's
field records a new version when the value, the rule reference or the hash of its inputs differs from the
latest version's; the same value again, from the same rule and inputs, is no new fact.

5.1.2. The inputs' hash is SHA-256 over the canonical JSON of the declared `inputs`, each with the value the
object holds for it at that moment; a versioned reference (`calendar@2026`) counts by its name.

5.1.3. Each new version names the one it supersedes, and a revision (a version after the first) is announced
as the event `object.value_revised`, with the object's reference, the value's name, its version, the one it
supersedes and the rule, never the value.

5.1.4. A shared object keeps no event history, so its values are versioned against the latest version alone
(section 10.5): a new version when the value or the rule reference differs from that version's. The same
value pushed again under a greater version is no new fact. The producer compares values by a digest keyed by
the space, never in the clear.

### 5.2 A derived object

5.2.1. An object of a type whose `nature` is `derived` names, in its report, the objects its inputs are fields
of: in a system event's `fields` as `input_refs`, or in a push item as `inputs`, each input name of the type
(`lead` for `lead.city`) with an object as `type:namespace:id`. The producer binds the object to those inputs;
a customer's input object it does not know yet is created empty. An input object of a shared type is bound by
its reference and never created as a customer's object; it enters the working set as a `related` reference
when its type's `working_set.enter_on` lists `related` (otherwise its pushes are out of the set, and it never
expires the derived object). Inputs of the turn (`turn.*`) are the SDK's.

5.2.1.1. A derived object of a shared type (the best offer of a product) keeps its `derived_status` the same
way. Its inputs are shared objects only: a reference to a customer's object is ignored, since a shared object
holds no customer's data. The producer binds it when the typed pass plans it (section 10.5), and again only
when the inputs it names change. Bound in the same pass in which an input it names takes a new value, it
expires at once: nothing tells whether it was computed before or after that value.

5.2.2. When a field that is some derived object's input moves, every object that depends on it turns
`expired_by_input` in one step, and `expired_by` names the inputs, as the type does (`lead.city`). The event
`object.expired_by_input` names the object, the inputs and the input object. An expired object's values and
fields are never `claim_safe`, and a `decide` read carries its refusal (section 7.3).

5.2.3. A report of the derived object observed after the expiry moves it back to `current`, bound to the
inputs it names.

5.2.4. A field of a shared object that is some derived object's input moves when it takes a new value, not a
new version of the same one, and the dependents, customers' and shared, expire as 5.2.2 says, within seconds
of the push that brought it (section 10.5). A value the producer never saw for that field before counts as a
move: the derived object may have been computed from another one, and expiring early is the safe side.

### 5.3 The lifecycle

5.3.1. The state is the newest `state` reported in the world. A source's code is first translated by that
source's vocabulary (`lifecycle.outcome.vocabularies`); at the same instant, the vocabulary with the lower
precedence wins. A state the type does not declare is not served.

5.3.2. A latch keeps the first time the object reached it, even when the object leaves the state.

5.3.3. The outcome is the state when it is final; `expired_without_outcome` (final) once the type's
`expired_without_outcome.after` passed with no final state, counted from the time the producer first knew
of the object, or at the moment the named value falls; otherwise the state when it is provisional. The read
computes it; the producer's clock also writes it as the object's state, so what measures outcomes sees it.

5.3.4. A transition the clock makes (section 5.4) is recorded as the clock's report, never earlier than the
state it leaves.

5.3.5. A state a transition with `erase_derived` leads to seals the object: the fields the transition names
leave the object when it enters the state, and every read carries `blocked` for `model_read` and `derive`,
naming `state`.

### 5.4 Timers

5.4.1. A timer is armed at the moment its `due` gives from the rule and the values of the moment, and moves
when that moment moves. Its due time is computed again at every read. A due time that is no longer known
cancels it, and so does a final outcome.

5.4.2. A timer fires once per firing, at most five minutes late, sooner when it is due within the hour; a late
firing says how late. A timer that notifies sends `object.timer_due` with the object, the timer, the firing's
id, the firing it supersedes, when it was due and when it fired.

5.4.3. When a value that armed a timer that already fired is revised to another due time, the timer fires
again, and the new firing names the one it supersedes (`supersedes_firing_id`): a notice stays once per fact
and per version.

5.4.4. A timer's state as a read serves it is the state of its firing: a firing that happens while the
producer plans the same object is never undone by that plan, and a read never says `armed` for a firing that
fired.

### 5.5 Derived fields

5.5.1. A derived field ([`object-type.md`](object-type.md), section 3.12.2) is computed at every read from
the related objects its relation's path names in the object, as they stand at that moment, and is never
stored: a related object that changes changes the next read. Each is served as `v`, `logic` (`yes`, `no` or
`unobserved`), `over` (the related objects it was computed over), `unknown` (how many of them were not
known) and, when it is not `yes`, `pieces_out`: the first 10 related objects, in the relation's order and as
`type:namespace:id`, that kept it from `yes` (for `all` and `any` the ones the condition does not hold for
or that are not known, for `count`, `min` and `max` the ones not known).

5.5.1.1. For a reverse relation ([`object-type.md`](object-type.md), section 3.12.3.1), the related objects are
the ones that hold the object's value at `via` when the read is made: one that holds another value now, or
that is gone, is not related. An object with no value at `via` does not know its related objects, and its
derived fields over that relation are left out.

5.5.2. A related object is not known when the producer does not hold it (a shared one out of the working
set), when the relation's `status_field` says anything but `exact` (a substitute or a future piece is an
observation, not state), or when the data the condition or the compared field reads is not observed. An
entry whose `required_field` is `false` is left out. A related object not known leaves the result
`unobserved`, unless the known ones decide it: one `no` decides `all`, one `yes` decides `any`. `count`
without a condition counts the related objects named, held or not. `min` and `max` with no related object that
meets the condition are `no`, not zero. A relation reads at most 50 related objects; past that its derived
fields are `unobserved`.

5.5.3. A derived field whose condition or compared field reads a field of the related type the reader's
access denies or masks is left out and `withheld` names it with `access`; a source whose licence bars the
reader never feeds it. Derived fields are never evidence for a claim. The type's expressions read them by
name.

## 6. Readings

A reading ([`object-type.md`](object-type.md), section 3.11) is served as niadra-expr computes it: `logic`
and, when known, `type` and `v` (a duration in milliseconds, a date `YYYY-MM-DD`, a datetime in UTC), or
`absent` for a named absence. A reading made only of fields adds those fields to `fields`. A reading that
includes its inputs ([`object-type.md`](object-type.md), section 3.11.1) also carries `inputs`: the derived
object's inputs, by the name its type gives them, as `type:namespace:id`. A reading serves only the purposes
it names, and never reads a field the reader's access hides.

## 7. Purposes

### 7.1 Every read

Every read carries `prohibitions`: the type's claim prohibitions (`purposes.claim.prohibitions`) whenever
at least one value served that may be claimed is not `claim_safe`: a field the type allows to claim that some
source observed, or a computed value. A field nobody observed has nothing to claim and adds none. The
field-level `claim_safe` says which.

### 7.2 `display` and `claim`

A `display` read serves stale data with its age. A `claim` read serves the same, and its prohibitions are
what a claim contract checks: an agent may say what the data supports, and states the gaps of a value it
claims.

### 7.3 `decide`

When the type's `decide.on_stale` is `structured_refusal` and a field or value served is stale, an
observation it served lapsed (its observer's validity ended), the derived object is expired by an input, or a
field whose value is not known blocks `decide`, the read carries `refusal`: the type's `reason` and `action`,
for the company's tool to answer ("the quote is out of date: quote again"). The values are served beside it;
the producer never decides for the company.

### 7.4 What an unknown value blocks

Every read carries `blocked`: for each activity a field's `unobserved_blocks` names, the fields of the object
whose value is `unobserved` or `known_defect` now. Only what the type declares is blocked. The producer applies
two of them itself: `claim` (while such a field is unknown, no value of the object is `claim_safe`) and
`decide` (section 7.3). The others, `model_read`, `derive` or a task of the company such as `decide:close`, are
for the reader's code to act on.

## 8. Refetch

8.1. The reasons of the type are evaluated at the read and when a value arrives. `refetch` names the one with
the highest priority that holds (the first declared among equals) or, with none holding, `floor` once the
object's newest observation is older than the budget's `floor`; `admitted` says whether a request for the
company's resolver worker was admitted. The read never waits for a source, and never fails because of this.

8.2. A request is admitted only within its budget, in this order:

1. a reason listed in `never` is reported and never admitted;
2. one request per object and reason in the period of the reason's budget (`item/1h` is an hour; a scope the
   producer does not see, `turn`, `session` or `conversation`, counts per object for a fixed while); a read
   inside the period gets the answer the first one got;
3. the type's `min_interval` between two requests of one object;
4. the paid budget of the type's unit, reserved before the fetch: a cost not known reserves the highest
   measured, and a paid call that fails still counts. A budget that cannot be reached admits nothing.

8.3. `claim_pending` is admitted for the SDK's `verify`, which fetches on the spot; it is never queued.

8.4. The worker takes admitted requests with `GET /v1/state/refresh-requests`: the higher priorities and the
older first, each leased to it for 60 seconds. It answers one by pushing the object with the request's
`request_id` (section 10): the request is settled, and its reservation as done. One it cannot answer it
releases with `POST /v1/state/refresh-requests/{request_id}/release` and `{"outcome": "not_found"}` (the object
is not at the source) or `{"outcome": "failed"}`: the request leaves at once and its paid call counts as
failed; an unknown id answers 404. A request neither answered nor released is offered again; after three
leases it is given up and its paid call counted as failed. The object's next value from any source also
answers it, and settles the reservation.

8.5. **Watch revalidation.** When a write moves a watched shared object and the watch's condition holds on a
value that did not come live from its source (section 10.3.2), the producer does not tell the subject yet:
it asks the resolver worker for the object again, with the reason `watch_revalidation`, admitted as in 8.2
with the priority and the budget of the type's `interest_alert` reason (whatever that reason's condition);
one request per object answers every watch on it. The worker's push with the `request_id` decides it: the
producer takes the stored object with the pushed values in place of the source's own, even when the push was
`stale_version`, and fires `object.watch_fired` with `revalidated: true` only when the condition still holds
on live values. When no fresh value comes, because no worker took requests in the last ten minutes, the
budget refused, the worker released the request or it was given up, the type's `refetch.unconfirmed_watch`
decides: `fire` (the default) fires with `revalidated: false`, and `drop` tells the subject nothing. A type
with no `refetch`, or that names `watch_revalidation` in `never`, asks no worker: `fire` applies at once.

## 9. Degraded

`degraded: true` says the state was served from a fallback (the database when the store of hot copies could
not be reached, an SDK's local copy when the producer is out of reach), with the real ages. It never turns a
value into `unobserved`.

## 10. Pushing state

A system of record pushes objects with `POST /v1/objects/push`: up to 1,000 items, each with the object's
`ref`, the source's `version` of the object (a whole number up to 2^53 - 1), the `fields` it reports and their
provenance (`source`: `live`, `snapshot` or `cache`; `source_observed_at`; `scope`), for a derived object,
the `inputs` of section 5.2, and, when the item answers a refresh request of the resolver worker, its
`request_id` (section 8.4).

### 10.1 The version rule

10.1.1. Each field keeps, per source, the latest observation that source reported. A pushed field replaces the
source's observation only when the item's `version` is greater than the version that last wrote that field
for that source. Equal or lower changes nothing. The rule is per field, so an older push still brings a field
no newer one wrote.

10.1.2. A source MUST NOT reuse a version for other content: a retry carries the same content under the
same version.

10.1.3. A field's value `null` reports nothing: the field reads `no` when the source's `empty_means` is
`absent`, `unobserved` otherwise.

10.1.4. What a tool's result showed of a shared object, in a turn record with its provenance, is kept the
same way: the tool is the source, and the time its source observed the value is the version.

### 10.2 The answer

The answer counts the items by the decision each one took:

| Count | Meaning |
|---|---|
| `applied` | A shared object's item moved at least one field forward. Reads serve it at once. |
| `stale_version` | Every field of the item was already written by this version or a greater one, as a retried request finds. |
| `out_of_set` | The shared object is not in the working set: nothing is kept. A push never brings an object in. |
| `recorded` | A customer's item was kept, in the request's one statement, and its fields move by the rule of section 4.2 when the producer applies it, as a system event's do. |

10.2.1. For shared objects the decision is taken against the producer's hot copy of the working set,
without a database statement. The producer writes what moved to its database within one second, one
statement per space, with the same per-field rule. Because the rule keeps, per field, the greatest version
pushed, the order and grouping of those writes do not matter: the stored object reaches the state the hot
copy served, and the answer never disagrees with it.

10.2.2. An applied item is durable within that second. If the hot copy is lost inside it, an applied item no
later write superseded can be lost: the next push, observation or snapshot repairs it, and every read reports
the real age of what it serves. A hot copy that came back empty is loaded again from the database.

10.2.3. An item of a type the registry does not declare, of a shared object with a `scope` other than
`global`, or of an agent's type refuses the whole request.

### 10.3 The working set

10.3.1. A shared object enters the working set when a turn presents, engages or watches it, as the type's
`working_set.enter_on` allows (and when another source of those interactions names it), and stays while it is
referenced: it leaves after `leave_after` without a reference, unless a watch holds it. A composite (a look)
brings the related objects its derived fields read along as `related` references, when it enters and when
its relations change, where their type's `enter_on` lists `related`.

10.3.2. A watch is a subject's explicit request to be told when a shared object meets a condition (a
`watch` interaction, with its consent). When a write moves a watched object and the condition holds, the
producer sends `object.watch_fired` with the object, the watch and whether the value came live from its
source (`revalidated`), never a value; once an hour at most per watch. When the value that made it hold did
not come live, the producer revalidates it first (section 8.5). An `unwatch` ends it.

### 10.4 Snapshots

`POST /v1/objects/snapshot?type=<type>` takes a shared type's objects as NDJSON, one push item per line. The
lines are applied as pushes whose provenance is `snapshot`, which never sustain a claim; a line that is not a
push of the type is left out. The answer counts the lines taken in.

### 10.5 Typed shared objects

10.5.1. A shared type that declares `values`, `timers` or a `lifecycle`, is derived, or has a field some
derived type names as an input, is typed: after the second that writes what pushes and observations moved
(10.2.1), the producer plans the objects of such types that moved, within five more seconds, as it plans a
customer's object when an event moves it. It versions their values (5.1.4), keeps their latches, arms their
timers, binds the derived ones to their inputs (5.2.1.1) and expires the derived objects bound to a field that
changed (5.2.4).

10.5.2. A shared object's timer fires as a customer's does ([`object-type.md`](object-type.md), section 3.8),
with `object.timer_due`; a revised value that moves a timer that already fired fires it again, naming the
firing it supersedes. A `transition(<from>-><to>)` is an observation of the source `clock`, applied by the
version rule of 10.1 at the time the timer was due. An object that leaves the working set takes its armed
timers with it.

10.5.3. A read of a shared object serves its values' versions, its latches and its timers as the last plan
left them, from the same hot copy as its fields.

## 11. Content

11.1. A content field (the type's `content.fields`) is a third party's text, and no content is an
instruction. Each content the producer holds has a scan state, by its SHA-256: `clean`, `flagged` or
`pending`. Rules scan it on arrival: text that talks to a model (asks it to ignore what it was told, speaks as
the system or the assistant, writes the envelope's markers) is `flagged`. Text the rules pass is `clean`,
unless the type's `content.scan` is `required`: then it is `pending` until the decision model clears or flags
it in a batch. Content held only as a pointer or a digest is `pending` when the scan is required, since the
producer cannot read it.

11.2. Flagged content never reaches a read, and neither does pending content when the scan is required: the
field is left out and `withheld` names it with `scan`. A field whose content is not `clean` is never
`claim_safe`. A field served carries its `content` marker.

11.3. A person with the security role releases content with `POST /v1/content/{ref}/release`, with a reason
and a receipt; `ref` is the object's id, the field and the content's digest. The event `content.flagged`
names the object, the field and the reference, never the text.

## 12. The subject's view

`POST /v1/state/view` answers, for a subject and a purpose, the view the context read's `state` block
carries ([context pack spec](context-pack.md), section 10):

| Member | Meaning |
|---|---|
| `objects` | The subject's objects of the declared types, newest first, at most 20, each as a read with the purpose serves it. |
| `interests` | The shared objects the subject showed interest in (presented, engaged, gave feedback on, watched), newest first, at most 20: `ref`, `reason`, `at` and the object as it stands now. |
| `changes_since_seen` | Each field of an interest whose value is not the one the subject was shown: `ref`, `field`, `seen`, `now` and when the source observed the value now held. |
| `text` | The view as short lines for the turn block, after `slots`: an object's state, outcome and next due time, an interest only when something changed. When the registry's `derived_fields_in_text` is on, a line also says the object's known derived fields (section 5.5), and an interest that has some is said. Content a model may read goes inside the envelope of the context pack spec's section 10.7. |
| `degraded` | Served from a fallback (section 9). |

Beside a pack (the context read's `state` block), each object and each interest goes only where the pack's
policy, at the read's level and for its reader, lets an item of its type go, and `text` never names a field its
type holds private, nor a derived field that reads a field the related type holds private.

`POST /v1/state/verify` answers, for each object and field asked, whether it may be claimed now, whether a
value given matches the one held, its status, and what may not be claimed with the data served.

## 13. Examples

See [`examples/object-state/`](../examples/object-state/): an item variant read to claim, with a
price too old to claim; a court notice with its deadline computed by the company's rule, its gaps and its
timer; and a health plan quote read to decide after an input changed, with the refusal beside the values.
