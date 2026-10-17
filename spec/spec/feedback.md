# Niadra Feedback, version 0

- **Status:** draft, published with the first SDKs
- **Date:** 2026-09-23
- **Schema:** [`schemas/feedback.v0.json`](../schemas/feedback.v0.json)
- **License:** Apache 2.0

## 1. Purpose

A memory derives facts, open items and outcomes from events, and sometimes gets one wrong. Feedback
is how an agent, or the person reviewing it, corrects what was derived without editing history: the
correction is itself an event, so it can be audited, replayed and undone like any other.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

## 3. Fields

| Field | Required | Meaning |
|---|---|---|
| `idempotency_key` | yes | The producer's unique id for this correction; every retry reuses it. |
| `subject` | yes | A handle of the person or organization the correction is about. |
| `action` | yes | What to correct (section 4). |
| `fact_id` | see 4 | The fact to retract or correct: its id, a UUID, as the history lists it. |
| `open_item_id` | see 4 | The open item or promise to resolve: its id, a UUID, as the history lists it. |
| `conversation_id` | see 4 | The conversation whose outcome is recorded. |
| `value` | see 4 | The right value, up to 2,000 characters. |
| `reason` | no | Why, up to 500 characters. |

## 4. Actions

| `action` | Takes | Effect |
|---|---|---|
| `retract_fact` | `fact_id` | The fact stops being served. |
| `correct_fact` | `fact_id`, `value` | The fact is replaced by `value`. |
| `resolve_open_item` | `open_item_id` | The open item or promise is closed. |
| `conversation_outcome` | `conversation_id`, `value` | The conversation's outcome is set to `value`, such as `resolved`. |

4.1. The producer SHOULD send the fields each action takes; a consumer MAY reject a correction
that lacks them.

## 5. Processing

5.1. A consumer MUST record a correction as a system event about `subject`, with `canonical_type`
`feedback.<action>` and the correction's fields in `fields`, and MUST NOT delete or rewrite the
events the corrected item was derived from.

5.2. A second correction with a known `idempotency_key` MUST be treated as a duplicate.

5.3. A correction applies to derived memory. It does not erase personal data; erasure is a
separate right with its own request.

## 6. Examples

See [`examples/feedback/`](../examples/feedback/).
