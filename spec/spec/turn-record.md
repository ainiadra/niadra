# Niadra Turn Record, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schema:** [`schemas/turn-record.v0.json`](../schemas/turn-record.v0.json)
- **License:** Apache 2.0

## 1. Purpose

A team that runs an AI agent needs to see why the agent did what it did, turn by turn: what it read,
which tools it called with which arguments, what the tools returned, what it showed the person, what it
claimed, and what it decided. The team also needs to run a real turn again after changing a prompt, a
model or the data.

A turn record is that account of one turn, in one format for any agent framework. It is captured inside
the agent's process and sent later, so it never slows the agent. It pins the build the turn ran on, so a
replay reproduces the conversation that happened and not a different one. The recorded values can stay in
the company's own storage, with only pointers and digests leaving it.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY and
OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and
only when, they appear in all capitals.

- The **producer** captures the turn and emits the record. It is usually the SDK in the agent's process,
  or the memory itself when it builds a record from OpenTelemetry spans (section 7).
- The **recorder** receives and keeps records.
- A **blob** is a large value the record points at: tool arguments, a tool result, the text of a read, a
  document the turn wrote.

## 3. What a turn is

A turn runs from its input to the last thing it emitted to the person or to a document. The input may be:
- a message (`kind: message`);
- an interface action, such as a button, a "show more", a page of results or a form (`kind: action`);
- a system event (`kind: event`);
- a timer that fired (`kind: timer`).

An interface action is a turn of its own, even when no model runs.

A sub-agent, or an agent called as a tool, opens a **sub-turn**. The sub-turn is a turn with its own
`turn_id` and names the turn that opened it in `agent.parent_turn_id`.

## 4. Capture

4.1. The producer MUST capture in the agent's process, at the moment each thing happens, by copying:
- A tool's arguments and result are deep-copied when the call returns.
- When a result exists in two forms, both are recorded: the form the model saw (`result_model`) and the
  form the interface got (`result_ui`).
- A generator's output is recorded once it is fully consumed.

4.2. Capture MUST NOT delay the agent's answer. Sending happens later and apart from the turn. If
recording fails, the turn still goes on, and the record, when it can still be sent, says
`completeness: incomplete`.

4.3. The `turn_id` is minted when the turn starts. It MUST follow the turn across asynchronous tasks,
threads and child sessions, so every call made on the turn's behalf lands in the same record.
- Every call has a `call_id`, unique in the record.
- A call made inside another call names the outer one in `parent_call_id`.
- The producer MUST mint a `call_id` when the provider gives none.

4.4. A producer that retries a call records every attempt, numbered by `attempt`. It marks:
- `synthetic: true` on a call it answered itself, without the real tool;
- `cache_hit: true` on a result that came from a cache inside the tool.

## 5. The record

### 5.1 Top level

| Field | Required | Meaning |
|---|---|---|
| `spec` | no | Always `turn-record.v0`. |
| `turn_id` | yes | 1 to 64 letters, digits, `.`, `_` or `-`, starting with a letter or digit. It is safe in a URL path and in a storage key. A UUIDv7 is RECOMMENDED. |
| `conversation_id` | one of the two | The conversation the turn belongs to. |
| `task_id` | one of the two | The task, for an agent working without a conversation. |
| `agent` | yes | `name` inside the source, an optional free `role`, and `parent_turn_id` for a sub-turn. |
| `kind` | no | `message` (default), `action`, `event` or `timer` (section 3). |
| `started_at`, `ended_at` | `started_at` | When the input arrived, and when the turn emitted its last output. |
| `fidelity` | no | `gold` (default), `silver` or `bronze` (section 7). |
| `completeness` | no | `complete` (default), `partial`, `incomplete` or `unknown` (section 7). |
| `content_mode` | yes | `stored`, `pointer` or `hash_only` (section 6). |
| `build` | no | The build the turn ran on (section 5.2). |
| `reads` | no | What the turn read from the memory (section 5.3). |
| `calls` | no | Tool and model calls (section 5.3). |
| `interactions` | no | What the person was shown or did (section 5.4). |
| `claims` | no | Claim contract verdicts (section 5.4). |
| `coordination`, `effects` | no | Coordination decisions the turn used, and external effects with their state (section 5.4). |
| `output` | no | What the turn emitted (section 5.4). |
| `cost`, `latency_ms` | no | The turn's cost in US dollars and its latency. |
| `flags` | no | Marks set at capture (section 9). |
| `blobs` | no | The record's large values, by key (section 6). |

### 5.2 The build and its pins

`build.pins` holds what must be the same for a replay to reproduce the turn:

| Pin | Meaning |
|---|---|
| `prompts` | Each prompt's name and version, e.g. `core: v16`. |
| `corpus_digest` | A digest of the files the agent consults. The producer computes it and never sends the files. |
| `model` | The exact model the agent called. |
| `assembler` | The version of the company's own context assembler. |
| `tool_schemas` | Each tool's name and the digest of its schema. |
| `niadra` | The context compiler's version and the hash of the pack the turn read. The producer fills these. |

`build.sdk` and `build.adapter` name the producer and the framework adapter, with their versions.

The recorder's configuration says which pins are required. A turn missing one is kept, but marked not
replayable.

### 5.3 Reads, calls and observations

A **read** names what the turn read from the memory by its version:
- `surface` is `pack`, `state`, `constraints`, `coordination`, `budget`, `history`, `agent_memory` or
  `agent_state`;
- `etag` or `version` identifies what was read;
- `blob` points at the content, when the recording keeps it;
- `receipt_id` points at the read's receipt, when the recorder issued one.

A **call** is a tool call (`kind: tool`) or a model call (`kind: model`).
- `args`, `result_model` and `result_ui` are blob keys. `args_hash` is the digest of the normalized
  arguments (section 6.2), and replay matches calls by it.
- `tokens` (`in`, `cached`, `out`) belongs to a model call only.
- `cost_units` counts the call's cost in the company's own units, e.g. `paid_call: 1`, never in money.
- `truncated: true` marks arguments or a result that arrived cut, as OpenTelemetry may deliver them.

An **observation** is state a tool result showed:
- `ref` names the object as `<type>:<namespace>:<id>`;
- `fields` holds the values shown;
- `provenance` holds `source` (`live`, `snapshot` or `cache`), `source_observed_at` and `scope`
  (`global`, `customer` or `context`).

The provenance rules:
- An observation without provenance is for display only and MUST NOT update state.
- An observation whose `scope` is not `global` MUST NOT update state shared between subjects.
- A cache hit inside the tool is not a new observation.
- An object missing from a result is not a state: absence proves nothing.

`applied` reports what the producer measured of the constraints block on the call:
- `hard_sent`, the hard constraints sent;
- `results_checked`, the results checked;
- `violations`, the results that broke one;
- `unverifiable`, the results that lack the attribute;
- `relaxed`, whether the tool relaxed a constraint, as `declared` or `suspected`.

Sent is not applied: a tool may relax a constraint internally.

### 5.4 Interactions, claims, coordination, effects and output

**Interactions.** Each item has a `kind`: `presented`, `seen`, `engaged`, `feedback`, `preference`,
`attribute`, `watch` or `unwatch`. Its other fields are the Interaction spec's, and this spec does not
restrict them.

**Claims.** Each item is the verdict the producer's claim contract reached before the text left for the
person or the document. It holds:
- `category`;
- the value's `class`, such as `money` or `date`;
- the number's `nature`: `computed`, `quoted` or `model`;
- its `role`, the `span` in the output and the `value`;
- the `evidence`, as a call and field, or a text anchor;
- the `verdict`;
- the `action` taken.

The Claim Contract spec fixes the vocabularies of `class`, `verdict` and `action`. Examples:
- verdicts `matched`, `role_ambiguous` and `below_threshold`;
- actions `none`, `warn`, `count` and `block`.

The recorder only receives verdicts and adds them up. It runs no check of its own on the turn.

**Coordination and effects.** `coordination` lists the decisions the turn relied on, by `decision_id` and
`decision`. `effects` lists each external effect, by `key` and `state`: `reserved`, `done`, `failed` or
`unknown_outcome`.

**Output.**
- `event_keys` names the events the producer already sent for what the turn said. The conversation's
  text MUST NOT be repeated in the record.
- `ui` and `document` are blob keys.
- `handoff_id` names a handoff the turn created.

### 5.5 Rules the schema cannot state

A recorder MUST refuse a record that breaks one of these rules:
1. Every blob key the record uses (in `reads`, `calls`, `claims` and `output`) has an entry in `blobs`.
2. No `call_id` repeats.
3. `ended_at` is not before `started_at`.

## 6. Content modes and blobs

### 6.1 Modes

The mode is set by the recorder's configuration, per source, and the producer follows it.

| Mode | A blob carries | Replay |
|---|---|---|
| `stored` | `sha256` and `content`, the value itself. The recorder keeps it encrypted. | Yes. |
| `pointer` | `sha256` and `pointer`, a URI in the company's own storage (e.g. `s3://...`). | Yes, inside the company's boundary. |
| `hash_only` | `sha256` only. | No. For statistics and structural assertions. |

6.1.1. In `pointer` mode, a blob MUST have a `pointer` and MUST NOT have `content`. No recorded value
leaves the company: the producer writes the value to the company's storage, with the company's
credentials, and sends only the pointer and the digest.

6.1.2. In `hash_only` mode, a blob MUST have neither `pointer` nor `content`.

6.1.3. In `stored` mode, a blob MUST NOT have a `pointer`.

### 6.2 Normalization before the hash

6.2.1. A blob's `sha256` is `sha256:` followed by the lowercase hexadecimal SHA-256 of the value's
canonical JSON (RFC 8785, the JSON Canonicalization Scheme), encoded in UTF-8. `size` is the length of
those bytes. The same value therefore always has the same digest, whichever producer computed it.
`vectors/turn-record-digest.v0.json` holds values with their canonical form, digest and size. Each case
has an `id`, a `note`, the `value` and an `expect` with `canonical`, `sha256` and `size`. A producer MUST
compute all of them exactly; the cases cover key order by UTF-16 code units, escapes and the ECMAScript
form of numbers.

6.2.2. Before hashing, the producer MAY apply the normalizers the recorder's configuration declares, for
instance dropping a request id or a timestamp that changes on every call. It MUST then hash, and in
`stored` mode send, the normalized value, so `args_hash` matches across replays.

6.2.3. In `stored` mode, the recorder masks the text leaves of a value with the rules it applies to
events, before it keeps the value. When a mask changes a value, the recorder replaces the blob's `sha256`
and `size` with those of the masked value. The call's `args_hash` stays the producer's, so a replay still
matches the call.

## 7. Fidelity and completeness

| Fidelity | Produced by | What it supports |
|---|---|---|
| `bronze` | The recorder, from OpenTelemetry `gen_ai` spans (see [OpenTelemetry conventions](otel-conventions.md)). | Metrics and regression by indicator. Never replay or attribution. |
| `silver` | The recorder, from spans that also carry the exposure attributes. | Also what the person was shown. |
| `gold` | The producer intercepting the agent's tools and model calls in its process. | Everything, including replay. |

`completeness` says how much of the turn is in the record:
- `complete`: the whole turn.
- `partial`: the producer dropped blobs to protect its queue. The frame stays, and the blobs' keys
  may point at nothing the recorder holds.
- `incomplete`: the recording failed during the turn.
- `unknown`: a bronze record, whose producer could not know.

## 8. Sending

8.1. A producer sends records in batches of up to 50, in a body of up to 4 MB compressed with zstd or
gzip. It sends them in their own queue, apart from events.

8.2. The recorder answers in one of two ways:
- with success when it took every record;
- with a multi-status answer that names each refused record by its index, with the rule it broke.

A record the recorder already holds, identified by `turn_id`, counts as a duplicate and is not recorded
again: the same turn sent twice is one turn.

8.2.1. A recorder SHOULD take `pointer` and `hash_only` records even when its own storage is unavailable,
since their frame is all of them, and tell a duplicate apart once its storage is back. While it cannot
tell, it counts such a record as taken; it still records the turn once.

8.2.2. A bronze or silver record built from spans that arrive in more than one export is merged, not
duplicated ([OpenTelemetry conventions](otel-conventions.md), 5.1.1).

8.3. When its queue is full, a producer drops the blobs of unflagged turns first, marking those records
`partial`. Only then does it drop the oldest frames, and it counts both.

## 9. Tiers and promotion

9.1. The recorder keeps every turn in a short tier. No tier expires on its own: a turn is kept until an
erasure removes it or until the retention the company sets (`kept_days`, at least 7 days, no maximum), which
applies to every tier.

9.2. A turn is also copied to the kept tier, the one search and replay read, for one of three reasons:
- **by a flag set at capture:** `error`, `guard_acted`, `handoff`, `assertion_failed`, `synthetic`,
  `incomplete` or `negative_feedback`;
- **by a later request**, naming a conversation or turn ids with a reason: `complaint`, `bug_report`,
  `review` or `other`;
- **by a deterministic sample**, 5% by default.

9.3. Past the retention the company set, nothing of a turn remains except the day's totals, which name no
person. A replay scenario's turns follow `scenario_days` after its last run, and are kept when it is unset.

9.4. **The kept tier's index and the company's audit chain.** The recorder keeps no second audit chain: it
sends its own to the company's. Each kept row has an entry: `turn_id`, `source_id`, `agent`, `kind`,
`started_at`, `content_mode`, `build_hash` (hex, or null) and `kept_at`. Its digest is SHA-256 over the entry's
RFC 8785 form. The rows kept on one UTC day, in `turn_id` order, fold into a Merkle root: each leaf is
SHA-256 over `0x00` and the digest; each pair is SHA-256 over `0x01` and the two nodes; an odd node is
carried up; a day with no row has the SHA-256 of nothing. The daily `audit.root` event carries that root
as `turns.root`, with `turns.count`, next to the receipts' root. `GET /v1/turns/index/{day}` lists the day's
rows with their digests, so the company recomputes it. A row that expires or is erased later leaves the
anchored root as it was.

## 10. Replay

A replay case gives the runner the record, its pointers and digests, its pins, and the pack and view of
the time. The runner runs inside the company's boundary. [Replay](replay.md) describes scenarios, cases,
assertions and the statistical verdict.

10.1. The runner MUST fetch each blob and check its digest.

10.2. The runner MUST check each required pin against the build it runs. A pin that does not match
stops the case as `pin_mismatch`.

10.3. A tool call whose normalized arguments match a recorded call's `args_hash` answers with the
recorded result. A call that matches none runs live in dry-run mode, or answers empty and is marked
divergent.

## 11. Privacy

- The record repeats no conversation text: `output.event_keys` points at the events.
- In `pointer` mode no recorded value reaches the recorder (6.1.1). In `hash_only` mode no value is
  recorded at all.
- In `stored` mode, the space may ask (`pii_model` in the recording document) for a second pass after the
  rules: a PII model reads the text leaves of the tools' arguments, and a record where it finds something is
  rewritten with placeholders. Each blob then takes the digest of what it keeps (6.2.3).
- The recorder never places a conversation id in a URL or in a storage key in clear. It stores turns
  under a keyed hash of the conversation, and it erases a person's turns by that prefix.
- Records serve the purpose `quality`, and their retention is the tier's (section 9).

## 12. Examples

`examples/turn-record/` holds one record per situation:
- a quote in a health plan sale, stored;
- a litigation draft whose values stay in the firm's bucket, by pointer;
- a "show me more" action in a store, as hashes only;
- a bronze record built from OpenTelemetry;
- a timer's follow-up whose farewell came back with an unknown outcome.

`examples/invalid/turn-*.json` each break one rule of this document. `vectors/turn-record-digest.v0.json` holds the digest
vectors of section 6.2.1.

## 13. Versioning

This is a draft. A new field means a new schema file, and a published file never changes meaning.
