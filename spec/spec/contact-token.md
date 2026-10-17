# Niadra Contact Token, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schema:** [`schemas/contact-token.v0.json`](../schemas/contact-token.v0.json) (the payload)
- **Vectors:** [`vectors/contact-token.v0.json`](../vectors/contact-token.v0.json)
- **License:** Apache 2.0

## 1. Purpose

A coordination decision ([`coordination.md`](coordination.md)) is advice until the point that sends the
message enforces it. A contact token lets that point, the company's gateway or middleware, enforce it
without calling anyone: the producer signs, with the space's key, that one outbound contact of one
purpose, on one channel, to one destination, through one gateway, may leave in the next two minutes. The
gateway checks the signature and the fields offline and lets the message out once.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

The **issuer** signs tokens. The **gateway** checks them and dispatches messages. `base64url` is the
URL-safe alphabet of RFC 4648, section 5, without padding.

## 3. Format

```
nct1.<payload>.<signature>
```

- `nct1` names this version.
- `<payload>` is the base64url of the payload, a JSON object (section 4) encoded in UTF-8.
- `<signature>` is the base64url of the 64-byte Ed25519 signature (RFC 8032) of the ASCII bytes of
  `nct1.<payload>`, the first two segments and the dot between them, exactly as they appear in the token.

3.1. The issuer MUST serialize the payload as compact JSON, without whitespace, with its members in the
order of section 4. A token issued this way is the same byte for byte in every implementation, which the
vectors check.

3.2. A gateway MUST verify the signature over the segments as received and MUST NOT decode and re-encode
the payload before it does.

3.3. A token is at most 1,024 characters. It travels as a message header or a metadata field of the
dispatch request, never in a URL.

## 4. Payload

| Member | Type | Meaning |
|---|---|---|
| `kid` | string | The signing key's id, 8 to 64 characters of the base64url alphabet. |
| `space` | UUID | The space the contact belongs to. |
| `jti` | UUID | The token's id, a UUIDv7. It also names the contact in the producer's contact log. |
| `purpose` | name | The purpose of the contact (`marketing`, `retention`, `collection`...). |
| `channel` | name | The channel it leaves on (`whatsapp`, `sms`, `email`...). |
| `rcpt` | string | The destination, keyed with the gateway's key: base64url of HMAC-SHA256 (43 characters). |
| `gateway` | name | The gateway it may leave through. |
| `iat` | integer | When it was issued, in seconds since the Unix epoch. |
| `exp` | integer | When it expires, in the same unit: after `iat`, and at most 120 seconds after it. |

A name matches `^[a-z][a-z0-9_]{0,39}$`. The payload carries no personal data: the destination only as
`rcpt`, which only a holder of the gateway's key can compute.

4.1. **The recipient.** `rcpt` is the base64url of HMAC-SHA256, keyed with the 32-byte key the gateway
shares with the producer, over the UTF-8 bytes of the destination's canonical form, as the suppression
list defines it ([`suppression-list.md`](suppression-list.md), section 3): `phone:+5511987654321`,
`email:ana.souza@example.com`. Only the gateway and the producer hold that key; the producer keeps it in
the space's vault.

4.2. A caller that holds the gateway's key and sends to a destination other than the subject's handle
passes `rcpt` itself in the check, as `destination_hash`.

## 5. Keys

5.1. Each space has its own Ed25519 key pair. The private key stays sealed in the producer's vault; the
public key is published at

```
GET /.well-known/niadra-contact-keys.json?space=<space id>
```

as a key set of JWKs (RFC 8037): `{"keys": [{"kid", "kty": "OKP", "crv": "Ed25519", "x", "space",
"status", "not_after"}]}`. The document lists only the keys of the space asked for.

5.2. A key is `active` or `retiring`. A retiring key signs nothing new and still verifies what it signed,
until `not_after`. The producer rotates a key by publishing the next one as `active` and the previous as
`retiring` for at least the life of a token.

5.3. A gateway SHOULD cache the key set, refresh it at least hourly, and on an unknown `kid` refresh it at
most once a minute before it refuses.

## 6. Verification

A gateway about to send a message of a channel to a destination checks the token in this order, and
refuses with the first code that applies:

| Step | Refused with |
|---|---|
| 1. Exactly three segments, each non-empty. | `malformed` |
| 2. The first segment is `nct1`. | `unsupported_version` |
| 3. Both segments decode as base64url without padding; the signature is 64 bytes; the payload is a JSON object with exactly the members of section 4, of their types. | `malformed` |
| 4. The key set has a key with the payload's `kid` whose `space` is the payload's `space`. | `unknown_key` |
| 5. The signature verifies with that key. | `bad_signature` |
| 6. `space` is the gateway's space. | `wrong_space` |
| 7. `gateway` is this gateway. | `wrong_gateway` |
| 8. `exp` is after `iat`, by at most 120 seconds. | `lifetime_too_long` |
| 9. `iat - 5 <= now`. | `not_yet_valid` |
| 10. `now < exp + 5`. | `expired` |
| 11. `channel` is the channel of the message. | `wrong_channel` |
| 12. `rcpt` equals the gateway's own computation for the message's destination, compared in constant time. | `wrong_recipient` |
| 13. The gateway has not let this `jti` through before. | `replayed` |

6.1. The five seconds in steps 9 and 10 are the only leeway for clock skew. `now` is the gateway's clock,
in seconds.

6.2. After a token passes, the gateway MUST remember its `jti` until `exp + 5` and MUST refuse it again
within that time. A gateway with several processes shares that memory, or routes a token's messages to
one of them.

6.3. The gateway SHOULD tell the producer that the contact left, with the `jti`, through a `contact.made`
declaration (coordination, section 5). Two declarations citing one `jti` show a token used twice.

6.4. A refused token MUST NOT be retried with the same token: the caller asks for a new decision.

## 7. Where tokens apply

A token is issued only with an `allow` of an outbound contact whose purpose the space marks as needing
one (by default `marketing`, `retention` and `collection`). A gateway that enforces tokens refuses an
outbound message of those purposes without a valid token, which is how those purposes fail closed when
the producer is out of reach. Messages the customer sent, transactional messages and service messages
never need one.

## 8. Test vectors

[`vectors/contact-token.v0.json`](../vectors/contact-token.v0.json) holds two kinds of case, told apart by
`op`:

| Field | In | Meaning |
|---|---|---|
| `id` | both | The case, unique and dotted. |
| `op` | both | `issue` or `verify`. |
| `description` | both | What the case shows. |
| `seed` | `issue` | The 32-byte Ed25519 private seed, base64url. A test key, never a real one. |
| `claims` | `issue` | The payload, members in order. |
| `keys` | `verify` | The key set the gateway holds. |
| `gateway` | `verify` | The gateway: `gateway_id`, `space` and its shared `key` (base64url). |
| `token` | `verify` | The token presented. |
| `destination` | `verify` | The canonical form of the message's destination. |
| `channel` | `verify` | The message's channel. |
| `now` | `verify` | The gateway's clock, in seconds. |
| `seen_jti` | `verify` | The `jti` values the gateway already let through and still remembers. |
| `expect` | both | `{"token"}` for `issue`; `{"claims"}` or `{"error": "<code>"}` for `verify`. |

An implementation MUST fail, not skip, on a case field it does not know.
