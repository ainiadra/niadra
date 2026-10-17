# Niadra Verification Assertion, version 0

- **Status:** draft, published with the first SDKs
- **Date:** 2026-09-23
- **Schema:** [`schemas/verification-assertion.v0.json`](../schemas/verification-assertion.v0.json)
- **License:** Apache 2.0

## 1. Purpose

Agents disagree about what "verified" means. This assertion states, in one shared vocabulary,
that one conversation or task reached a verification level, by which method and until when. A
memory uses it to decide what an agent may be told: what a caller who only stated their name may
hear is less than what a customer who passed a one-time code may hear.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

The **asserting party** is the source that performed the verification and emits the assertion.
The **relying party** stores it and applies it to later reads.

## 3. Levels

| Level | Name | Meaning |
|---|---|---|
| `V0` | Self-declared | The person said who they are. Nothing was checked. |
| `V1` | Plausible by channel | The contact came from the subject's number or WhatsApp account, with weak or no network attestation. |
| `V2` | Attested by channel | The channel vouched for the sender: network attestation level A (STIR/SHAKEN or an equivalent), or an authenticated WhatsApp sender. |
| `V3` | Challenged | The person passed a challenge: a one-time code on the same channel, or a login to the company's service. |
| `V4` | Documentary or attendant | A human attendant or a system of record matched the person against documents on file. |
| `no_customer` | No customer present | An internal task with nobody on the line. It is outside the V0 to V4 scale, not below or above it. |

Levels V0 to V4 are ordered. `no_customer` MUST NOT be compared with them or combined with them.

## 4. The assertion

| Field | Required | Meaning |
|---|---|---|
| `type` | no | Always `verify`. |
| `idempotency_key` | yes | Unique per asserting party; retries reuse it. |
| `method` | yes | `otp_whatsapp`, `otp_sms`, `login`, `kba`, `network_attestation` or `human_agent`. |
| `level` | yes | The level reached. |
| `conversation_id` | no | The conversation the level applies to. |
| `task_id` | no | The task the level applies to. |
| `handle` | yes | The handle that was verified. |
| `valid_until` | no | When the level lapses. |
| `occurred_at` | yes | When the verification happened. |

## 5. Rules

5.1. A level MUST only be raised by an assertion. A relying party MUST NOT infer a level from the
content of a conversation, from an event's `verification_hint`, or from the absence of doubt.

5.2. An assertion applies to the conversation or task it names, and only to it. It SHOULD name
one of them. A later conversation starts again at the level its channel gives it.

5.3. The effective level of a read is the lowest of: the level the reader requests, the ceiling
configured for the reading source, and the highest level proven in that conversation or task by
assertions still valid. A relying party MUST NOT let an assertion raise a source above its
ceiling, and SHOULD reject such an assertion with a per-item error.

5.4. After `valid_until`, the assertion no longer counts towards the proven level.

5.5. Methods map to levels as follows. An assertion whose level exceeds what its method can
establish SHOULD be rejected.

| Method | Highest level |
|---|---|
| `network_attestation` | `V2` (attestation A); `V1` for B or C |
| `otp_whatsapp`, `otp_sms`, `login`, `kba` | `V3` |
| `human_agent` | `V4` |

5.6. A one-time code sent to a handle and answered proves control of that handle. The relying
party MAY record that as evidence linking the handle to the subject of the conversation.

5.7. Idempotency follows the event envelope: a repeated `idempotency_key` from the same asserting
party is a duplicate and MUST NOT be applied twice.

5.8. Assertions are never updated. A mistaken assertion is withdrawn by the relying party's
governance process, which MUST lower the proven level of the affected conversation from then on.

## 6. Effect on reads

A context pack reports the outcome as `verification: {requested, effective, reason}` and counts
what the level kept out in `withheld` (see [`context-pack.md`](context-pack.md), section 6). An
agent that needs more SHOULD verify the person and then request a new pack; it MUST NOT expect
the pinned pack to change on its own.

## 7. Examples

See [`examples/verification-assertion/`](../examples/verification-assertion/): a WhatsApp
one-time code, a network attestation on a call, and an attendant's documentary check.
