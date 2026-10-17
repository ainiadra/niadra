# Niadra Agent State, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schema:** [`schemas/agent-state.v0.json`](../schemas/agent-state.v0.json) (the write)
- **License:** Apache 2.0

## 1. Purpose

An agent's code keeps a small working state between turns: the step of a form, the offer it showed, the
cart it is building, which sub-agent is on which part. Kept by the agent alone, that state is lost when a
process restarts and invisible to the company's governance. Kept as an opaque blob in a memory, it can be
neither masked nor erased with proof. The agent state is that working memory with a declared schema: the
memory does not interpret what it means, but knows its fields, so it can mask, retain, export and erase
them.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

The **producer** stores the states. A **writer** is the agent's code, through its SDK or the API. Sizes are
in bytes of UTF-8.

## 3. The declared schema and the scope

3.1. An agent's state is a type with `ownership: agent` in the space's type registry
([`object-type.md`](object-type.md)). Its fields declare what any field declares, and in particular `pii`,
`sensitivity` and retention; its `agent_state` section declares:

| Member | Meaning |
|---|---|
| `scope` | What one state belongs to: `conversation`, `task`, `object` or `subject`. |
| `write` | The modes allowed: `cas`, `merge_by_key`, or both. |

3.1.1. The schema of an agent's state is the type with `ownership: agent` named as the `agent`, or, when the
registry declares exactly one type with that ownership, that one. Without either, no field is declared.

3.2. A state is named by its scope and by the source and the agent that write it: `{"kind", "id"}` plus
`agent`. `id` is REQUIRED: the conversation's or the task's id, the object as `type:namespace:id`, or, for
a subject, the canonical form of one of its handles ([`suppression-list.md`](suppression-list.md),
section 3). The scope travels in the body, never in a URL, because a conversation id can look like a phone
number. The producer keeps the scope only as a keyed hash.

3.3. The state is written by code, never by a model: the producer MUST NOT offer it as a model's tool, in
any protocol. It never enters a context pack or an extraction, and a console shows its size, version and
dates, never its content.

## 4. Writing

`PUT /v1/agent-state`, described by `agent-state.v0.json`:

| Field | Required | Meaning |
|---|---|---|
| `scope` | yes | `{"kind", "id"}`, section 3.2. |
| `agent` | yes | The agent whose state it is. |
| `mode` | yes | `cas` or `merge_by_key`. |
| `if_version` | with `cas` | The version the writer read; 0 when the state must not exist yet. |
| `body` | yes | The state's fields, by name. |
| `subject` | no | The customer the state is about, as a handle, when the scope does not name them (a scope of kind `subject` does). |

4.1. **Compare-and-swap** (`cas`). `body` is the whole new state. The write applies only when the stored
version is `if_version`; otherwise the producer answers `412 agent_state_conflict` and changes nothing.
`body` MUST NOT carry a removal marker: a field left out of `body` is gone.

4.2. **Merge by key** (`merge_by_key`). Each top-level field of `body` replaces that field whole, with no
deeper merge; a field whose value is exactly `{"$delete": true}` is removed; the fields `body` does not
name stay as they are. Sub-agents writing different fields in parallel therefore never lose each other's
writes, and a removed field never comes back unless someone writes it again. With `if_version`, the merge
applies only at that version, as in 4.1.

4.3. Every write that is stored adds 1 to the version, which starts at 1 with the first one. The answer is
`{"stored", "version", "reason"}`: `version` is the state's version after the call, the new one when
stored and the unchanged one when not.

4.4. A field the schema does not declare is refused: `stored: false`, `reason: not_declared_field`, and the
state stays as it was.

4.5. Fields the schema marks `pii` are masked at write by the space's masking rules, as any text the memory
stores.

4.6. A mode the type's `agent_state.write` does not allow is refused with `422 invalid_input`.

4.7. The producer keeps the customer of a state as its keyed digest of the `subject` handle, the one it keeps
for the handle itself, and never the handle: erasing the customer and their data package find the state by
it.

## 5. Size

5.1. A state is kept whole. There is no cap on its size: the producer never stores part of a state, and
never drops a write because the state grew.

5.2. A write is bounded only by the request's own size limit, and a body over it is refused with an error
before anything is stored, so the agent knows. The previous state stays as it was.

5.3. The producer checks, in order: the version (412), the declared fields.

## 6. Reading

6.1. `POST /v1/agent-state/read` with `{"scope", "agent"}` answers `{"body", "version", "updated_at"}`. A
state never written reads as an empty `body` at version 0.

6.2. **Read your writes.** Once a write answered `stored: true` with version `N`, every later read of the
same scope and agent returns version `N` or later. The producer MUST keep this across its two stores: the
write is decided in the state store and answered only after the new version is there; the copy that reads
use carries its version, is replaced by the write before the write answers, and is only ever replaced by a
higher version; when the cache cannot be reached, reads go to the state store until it can. A read never
waits for a database.

6.3. The SDK keeps, per scope and agent, the last version it wrote or read, and serves the higher of that
and what it reads. With the producer out of reach it keeps the last version locally and sends the write
again later with the same `if_version`; a compare-and-swap that then conflicts is reported to the code,
never merged in silence.

## 7. Retention and erasure

7.1. A state is kept until its type names a retention: the type's `retention.state` sets how long a state
stays after its last write (any duration; `while_in_working_set` keeps it 30 days after its last write), and
`retention.<field>` a field's own, counted from when the field was last written. A type that names no
retention keeps its states until they are erased. A field past its retention is never served and leaves the
state with the next write, and the producer removes it from what it stores.

7.2. Erasing a subject erases the states of its scope and those whose `subject` it is; erasing a conversation
erases the states of that conversation's scope. The subject's data package carries their states, by declared
field.

## 8. Examples

See [`examples/agent-state/`](../examples/agent-state/): a compare-and-swap that creates a state, and a
merge by key that updates one field and removes another.
