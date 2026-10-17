# Niadra Exposure Token, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schema:** none: the token is a string, defined here
- **Vectors:** [`vectors/exposure-token.v0.json`](../vectors/exposure-token.v0.json)
- **License:** Apache 2.0

## 1. Purpose

An agent shows a list of cards; the customer later buys one of them, in the store's own checkout. To tie
that order line to the exact card the agent showed, the card carries a short token and the store's app
copies it, as an opaque string, into a property of the cart line. The order then arrives with the token,
and the memory attributes the line to the exposure and the position, deterministically, without the app
knowing anything else about it.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

The **exposure** is one presentation of a list to a customer, with its id, a UUIDv7 minted where the list
was presented (the SDK, at the interface bridge). `base64url` is the URL-safe alphabet of RFC 4648,
section 5, without padding.

## 3. Format

```
nx1.<id>.<position>.<verifier>
```

| Segment | Meaning |
|---|---|
| `nx1` | This version. |
| `<id>` | The exposure id's 16 bytes, base64url: 22 characters. |
| `<position>` | The card's 1-based position in the list, in decimal without leading zeros, from 1 to 9999. |
| `<verifier>` | The first 4 characters of the base64url of SHA-256 over the ASCII bytes of `nx1.<id>.<position>`. |

The token of the first card of a list is 33 characters long, and at most 36.

3.1. The token carries no personal data: an exposure id and a position, nothing about the customer, the
item or the price.

3.2. The verifier catches a token copied wrong, cut short or edited by hand. It is not a signature: the
memory accepts a token only for an exposure its space recorded, at a position that exposure had.

3.3. The token is an opaque string to the store: the app copies it and MUST NOT parse or change it.

## 4. Reading a token

A reader checks, in this order, and refuses with the first code that applies:

| Step | Refused with |
|---|---|
| 1. Exactly four segments. | `malformed` |
| 2. The first segment is `nx1`. | `unsupported_version` |
| 3. `<id>` is 22 base64url characters that decode to 16 bytes and encode back to the same 22 characters. | `malformed` |
| 4. `<verifier>` is 4 base64url characters. | `malformed` |
| 5. `<position>` matches `^[1-9][0-9]{0,3}$`. | `bad_position` |
| 6. `<verifier>` is the one section 3 computes. | `bad_verifier` |

The reader then has the exposure id, written as a UUID, and the position.

## 5. Where the token goes

5.1. The SDK mints the token when the list is presented (`exposure.token(pos=...)`), and the interface
puts it on the card, for example as a data attribute.

5.2. The store's app copies it into a property of the cart line, and the order event carries that
property. The memory reads the token from the order line, checks it (section 4), and finds the exposure;
a line whose token does not check is attributed by the other methods, never by this one.

## 6. Test vectors

[`vectors/exposure-token.v0.json`](../vectors/exposure-token.v0.json) holds two kinds of case, told apart by
`op`:

| Field | In | Meaning |
|---|---|---|
| `id` | both | The case, unique and dotted. |
| `op` | both | `build` or `parse`. |
| `description` | both | What the case shows. |
| `exposure_id` | `build` | The exposure's UUID. |
| `position` | `build` | The card's position. |
| `token` | `parse` | The token read. |
| `expect` | both | `{"token"}` for `build`; `{"exposure_id", "position"}` or `{"error": "<code>"}` for `parse`. |

An implementation MUST fail, not skip, on a case field it does not know.
