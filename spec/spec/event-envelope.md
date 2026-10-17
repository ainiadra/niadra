# Niadra Event Envelope, version 0

- **Status:** draft, published with the first SDKs
- **Date:** 2026-09-23
- **Schema:** [`schemas/event-envelope.v0.json`](../schemas/event-envelope.v0.json)
- **License:** Apache 2.0

## 1. Purpose

The event envelope records one thing that happened between a company and a subject: a message
in a conversation, a state change in a system of record, or an action an agent took. It is the
same shape whatever the channel (WhatsApp, voice, e-mail, app, ticket) and whatever produced it
(an AI agent, a human attendant, an ERP), so any memory or agent can consume events from all of
them.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

An envelope is a JSON object (RFC 8259). Timestamps are RFC 3339 date-times and SHOULD carry an
explicit offset. The **producer** is the software that emits an envelope; the **consumer** is the
software that stores or reads it.

The schema is normative for structure. It also encodes the cross-field rules of section 5, so a
document that validates against it satisfies every rule a schema can express. The rules that no
schema can check, such as idempotency, are stated in prose below.

## 3. Fields

| Field | Required | Meaning |
|---|---|---|
| `type` | no | Always `event`. Present so envelopes can share a batch with other item types. |
| `kind` | no | `message` (default), `system_event` or `action`. |
| `idempotency_key` | yes | The producer's unique id for this event: the provider's message id, or a UUIDv7. |
| `channel` | yes | Where it happened: `whatsapp`, `voice`, `email`, `app`, `billing`, `erp`... |
| `conversation_id` | no | The producer's id of the conversation thread. |
| `conversation_aliases` | no | Other ids of the same conversation, such as the trunk's call id. At most 8. |
| `task_id` | no | The id of an internal agent's unit of work. |
| `handles` | no | Identifiers of one person: phone, e-mail, WhatsApp ids, system ids. At most 16. |
| `subjects` | no | Several subjects with a `kind` (`person`, `account`, `partner`, `staff`), a `role` and handles. At most 8. A `staff` subject is someone who works for the company, named only by a system id; a conversation with staff is internal. |
| `object_refs` | no | Business objects the event is about, as `{type, namespace, id}`. At most 16. |
| `speaker` | yes | `{role, id?}` with `role` one of `customer`, `ai_agent`, `human_agent`, `system`. |
| `direction` | no | `inbound` (from the subject) or `outbound` (to the subject). |
| `content` | see 5.2 | `{type, text?, transcript?, media_ref?, media_sha256?, stt_confidence?}`. |
| `occurred_at` | yes | When it happened, by the producer's clock. |
| `visibility` | no | `public` (default) or `internal`, for notes the subject must never see. |
| `verification_hint` | no | The lowest verification level at which this content may be shown. |
| `canonical_type` | see 5.3 | The system event's type, such as `invoice.credited`. |
| `fields` | no | Structured data, mainly of system events. |
| `action` | see 5.4 | `{operation, result?, purpose?, closes?, corrects_action_id?}`. |
| `corrects_event_id` | no | The event this one corrects. |
| `voice` | no | Call data: ANI, DNIS, trunk, network attestation, answer and end times, turn offset. |
| `context_stamp` | no | On an AI agent's turn or action: `{etag?, injected_at}`, the context its prompt carried (section 9). |
| `usage` | no | On an AI agent's message: what the model provider reported for the call behind it (`provider`, `model`, `prompt_tokens`, `cached_tokens`, `cache_write_tokens`). |
| `valid_until` | no | Until when what this event states holds, such as an offer valid until a date. |
| `backing` | no | On an agent's message: `{checked, unbacked_values, guard_violations}`, what a backing check found in it (section 10). |

## 4. Identity

4.1. An envelope MUST name at least one of: a handle, a subject or a business object.

4.2. The top-level `handles` list describes one person. Producers SHOULD include every handle
they know for that person in the same event (for example the phone number and the WhatsApp
business-scoped id), because handles that arrive together are evidence that they belong together.

4.3. When an event concerns more than one subject (a driver and the carrier, a buyer and the
company they buy for), the producer MUST use `subjects`, one entry per subject, each with its
`kind`. Consumers MUST NOT merge the identities of different subjects of one event; they MAY
record a link between them.

4.4. Handle values MUST be normalized: `phone_e164` in E.164 with the leading `+`; `email`
trimmed and lowercased; `wa_id` as digits only. Scoped handle types MUST carry `scope`:
`wa_bsuid` (the WhatsApp business account), `system_id` (the system's namespace),
`gov_id_hmac` and `org_registry_hmac` (the issuing country). A document handle (`gov_id_hmac`,
`org_registry_hmac`) carries the document number itself, over TLS, and producers MUST NOT hash
it: the consumer validates it by the check digits of the country's scheme, which a hash cannot
pass, and rejects it otherwise. The consumer MUST keep a document only as a keyed hash (an HMAC
with a secret of the space) and an encrypted copy, never in clear at rest, and MUST show it
masked.

4.5. `subject_kind` on a handle defaults to `person`, except for handle types that only name
organizations (`org_registry_hmac`, `email_domain`).

## 5. Rules by kind

5.1. `kind` defaults to `message`. The `action` block MUST be present when `kind` is `action`
and MUST be absent or null otherwise.

5.2. A `message` MUST carry `content` with a non-empty `text`, a non-empty `transcript` or a
`media_ref`. Media MUST NOT travel inside the envelope: the producer uploads it separately, as
[`media-upload.md`](media-upload.md) describes, and sends the reference and its SHA-256 digest.
Speech-to-text output SHOULD include `stt_confidence`.

5.3. A `system_event` MUST carry `canonical_type`, written as `<object>.<past participle>` in
lowercase ASCII (`order.created`, `invoice.credited`, `ticket.reopened`). It SHOULD carry the
affected `object_refs`, and its structured data goes in `fields`. Free text produced inside a
system (a ticket description, an e-mail body) is not a system event: it is a `message` on a
channel of its own.

5.4. An `action` records what an agent did in a system of record. `operation` is a canonical
verb (`credit`, `reschedule`, `refund`). `closes`, when present, names the open item or promise
the action fulfils, either by `item_id` (the item's id, a UUID) or by `object` and `operation`,
and MUST NOT use both forms. Consumers MUST treat an action as declared, not confirmed, until an
event from the system of record confirms it.

## 6. Idempotency and order

6.1. `idempotency_key` MUST be unique per producer for a given event, and every retry of that
event MUST reuse it. When the channel provider assigns a message id, producers SHOULD use it.

6.2. A consumer MUST treat a second envelope with a known `idempotency_key` from the same
producer as a duplicate and MUST NOT compare contents to detect duplicates: short legitimate
messages look alike.

6.3. Consumers MUST order events by `occurred_at`, never by arrival: channels resend old
messages when they resynchronize. A consumer MAY correct an `occurred_at` that is implausibly far
from the time it received the event, and MUST record that it did.

## 7. Immutability

7.1. An envelope is never updated. A correction is a new envelope with `corrects_event_id`; a
corrected action is a new action with `action.corrects_action_id`. Late data about a
conversation (a post-call transcript, an analysis) is a new event.

7.2. A correction of what a memory derived from events (a wrong fact, an open item already
resolved) is feedback, recorded as a `feedback.<action>` system event; see
[`feedback.md`](feedback.md).

## 8. Privacy

8.1. Handles and content are personal data. Producers MUST send envelopes only in request bodies
and MUST NOT place handles, content or queries in URLs, headers or log lines.

8.2. `visibility: internal` marks content that MUST NOT reach an agent that talks to the subject.

8.3. `verification_hint` states the lowest level at which the content may be shown. It MUST NOT
raise the verification level of a conversation; only a verification assertion does that (see
[`verification-assertion.md`](verification-assertion.md)).

## 9. Context stamp

9.1. A producer that puts a context pack in front of an AI agent's model SHOULD set
`context_stamp` on the agent's turns and actions that follow: `etag`, the entity tag of the pack
the prompt carried, and `injected_at`, when the pack went into the prompt. It MUST NOT set it on
the turns of customers, human attendants or systems.

9.2. `injected_at` is the moment of injection, not of the request for the pack: a pack fetched
early and injected late is late. A consumer compares it with the agent's turns to tell a context
that arrived after the agent spoke from one the agent had and did not use.

9.3. When the prompt carried no pack (the request failed, or the subject was unknown), the
producer MAY still send `injected_at` without `etag`, so the consumer can tell "built without
context" from "never asked". A pack that is empty on purpose, as in a control group, carries its
`etag`.

9.4. Each prompt the agent builds is a new injection: the stamp names the latest one before the
turn, not the first of the conversation.

## 10. Backing

10.1. A producer that checks an agent's answer before or after sending it MAY report what it found
as `backing` on the agent's message: `checked`, how many numbers, dates, codes and amounts the
answer states; `unbacked_values`, one `{kind}` per value no source the agent had backs (the
context pack, the system records, the customer's own words, the results of the tools it called),
with `kind` one of `amount`, `date`, `number` (three or more digits) or `code`; and
`guard_violations`, the short ids of the guard lines (Context Pack, section 4.9) the answer went
against.

10.2. The report MUST NOT carry a value: the answer's text already holds it, under the consumer's
masking and retention. `unbacked_values` MUST NOT outnumber `checked`. It rides only on a message of
an `ai_agent` or a `human_agent`.

10.3. A consumer MAY warn a supervisor at once of a guard the answer went against, count unbacked
values per agent, and send the conversation to review; the report is the producer's word, and a
consumer MAY check the answer's text again by its own rule.

## 11. Extensibility

The envelope is closed: producers MUST NOT add top-level fields that this version does not
define, and a consumer following the schema rejects them. Data outside this specification goes
in `fields`. New optional fields arrive in a new version of this document and of the schema.

## 12. Examples

See [`examples/event-envelope/`](../examples/event-envelope/) for a WhatsApp message, a voice
turn with a context stamp and a media reference, an agent's answer with its backing report, an
agent action that closes an open item, and a system event about two subjects; [`examples/invalid/`](../examples/invalid/) shows one violation per file.
