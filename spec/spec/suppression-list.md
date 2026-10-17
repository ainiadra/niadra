# Niadra Suppression List, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schema:** [`schemas/suppression-list.v0.json`](../schemas/suppression-list.v0.json)
- **Vectors:** [`vectors/suppression-key.v0.json`](../vectors/suppression-key.v0.json),
  [`vectors/contact-window.v0.json`](../vectors/contact-window.v0.json)
- **License:** Apache 2.0

## 1. Purpose

When a customer asks not to be contacted, every vendor that could contact them must know, including the
ones that never call the memory before they send. The suppression list is the floor any vendor can
honor: the subjects not to be contacted, per purpose and channel, keyed so that a vendor can match the
destination it is about to contact and learns nothing about anyone else. A vendor keeps a local copy, and
the copy keeps applying when the memory is out of reach.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

The **producer** keeps the suppressions. A **reader** is a source (a vendor's agent, gateway or
middleware) with its own key. `base64url` is the URL-safe alphabet of RFC 4648, section 5, without
padding.

## 3. The canonical destination

The list covers the handles a contact goes to: phone numbers, WhatsApp ids and e-mail addresses. Each has
one canonical form, written `<kind>:<value>`, and the same form feeds the contact token's `rcpt`
([`contact-token.md`](contact-token.md), section 4.1).

3.1. **Phone numbers** (`phone_e164`, `wa_id`, `wa_jid`) become `phone:+<digits>`, in E.164, so the number a
call shows and the id WhatsApp gives reach one key:

1. A `wa_jid` keeps its user part: what comes before `@`, and before `:` when a device suffix follows.
   A `wa_id` and a `wa_jid` user part are international numbers without the `+`.
2. The value is folded to Unicode NFKC (full-width digits become ASCII), and format characters (general
   category `Cf`, such as the direction marks WhatsApp puts around a copied number) are removed. Then the
   value is trimmed, and a leading `tel:` (any case) is removed.
3. A leading `+` or `00` marks an international number. In an international number, `(0)` is removed: the
   national trunk zero is never dialed (`+44 (0) 20 ...`).
4. Spaces, parentheses, dots, hyphens and slashes are removed. What follows the `+` or `00` MUST be ASCII
   digits.
5. A number without either is a Brazilian national number, and gains `55`, when what is left after its
   leading zeros is one: a Brazilian area code (section 3.4) followed by 8 digits that start with 2 to 9,
   or by 9 digits that start with `9`. A number that started with a zero may also carry a two-digit carrier
   code before the area code (`0 21 11 98765-4321`): when 12 or 13 digits are left after the leading zeros
   and the last 10 or 11 are a Brazilian national number, the first two, the carrier code, are removed. Any other number without `+` or `00` keeps its digits as they are (`14155550100` is
   `+14155550100`).
6. The result has 8 to 15 digits and does not start with `0`.
7. A Brazilian number (`55`) whose national part has 10 digits, an area code of section 3.4 and a subscriber
   that starts with 6, 7, 8 or 9 is a mobile number from before the ninth digit and gains a `9` after the
   area code. A landline (2 to 5) never does.
8. A number of 13 digits that starts with `521` or `549` loses its third digit: the `1` that Mexico dropped
   from its mobile numbers in 2019, and the `9` Argentina dials before a mobile number, which WhatsApp ids
   keep. The ten digits after the country code already name one line.

3.2. **E-mail addresses** (`email`) become `email:<address>`, trimmed and lowercased, with no other change
(no folding of dots or tags). An address has one `@`, a local part and a domain with at least one dot, and
no spaces.

3.3. A value that fails these rules is not a handle of its kind (`invalid_handle`); any other handle type is
not in the list (`unsupported_type`).

3.4. The Brazilian area codes are 11 to 19, 21, 22, 24, 27, 28, 31 to 35, 37, 38, 41 to 49, 51, 53, 54, 55,
61 to 69, 71, 73, 74, 75, 77, 79, 81 to 89 and 91 to 99.

## 4. The key per reader

Each reader has its own salt, 32 random bytes, from `GET /v1/suppressions/salt`:
`{"salt_id", "salt", "valid_from"}`, with the salt in base64url. An entry's key is

```
key = base64url(HMAC-SHA256(salt, UTF-8(canonical destination)))
```

43 characters. Two readers of one space get different keys for the same person, so they cannot join their
copies, and a reader that does not already know a phone number or an address learns nothing from the
list.

4.1. The reader MUST keep the salt as a secret and MUST NOT send it or the keys anywhere else.

4.2. The producer MAY rotate a reader's salt. The next page then carries the new `salt_id` with `reset`
(section 5.3).

## 5. Reading the list

`GET /v1/suppressions?cursor=<cursor>&limit=<n>` returns a page of changes, oldest first, described by
`suppression-list.v0.json`:

| Field | Meaning |
|---|---|
| `salt_id` | The salt the keys of this page were computed with. |
| `reset` | When true, the reader drops its copy before applying `items`. |
| `items` | The entries that changed. |
| `next_cursor` | The cursor to read from next. It is on the last page too, as the point the next read starts from, and absent only while the list has had no change. A page with fewer items than `limit` is the end of the changes for now. |

Each entry:

| Field | Meaning |
|---|---|
| `id` | The entry, opaque. A later change of the same entry has the same `id`. |
| `key` | The destination, keyed (section 4). |
| `purpose` | The purpose it may not be contacted for, or `any` for every purpose. |
| `channel` | The channel, or null for every channel. |
| `since` | When the suppression began. |
| `until` | When it lapses on its own, or null when only a person lifts it. |
| `window` | The local hours it holds in (section 6.4), or null for every hour. |
| `removed` | When true, the entry was lifted: the reader deletes it. |

5.1. A reader starts without a cursor, which returns every entry in force, and keeps the last
`next_cursor` it got. It reads pages until one has fewer items than `limit`, and the next read starts from
that page's cursor. It SHOULD read again every 60 seconds, or when a `suppression.changed` webhook
arrives.

5.2. Cursors are opaque, and an entry may appear again in a later page with its latest state: the reader
applies pages in order and keeps, per `id`, the latest.

5.3. With `reset`, the salt changed or the cursor is older than what the producer keeps: the reader
replaces its copy with the pages that follow, from that page on.

5.4. The list never carries a handle, a name or the reason for the suppression: a reader learns that it
may not contact, never why.

5.5. A source adds an entry by declaring `suppression.added` for a subject named by a phone number or an
e-mail address, and lifts only the entries it added ([`coordination.md`](coordination.md), section 5).

## 6. Applying the list

6.1. Before an outbound contact of a purpose on a channel to a destination at time `now`, the reader
computes the destination's key and MUST NOT contact when its copy has an entry with that key, that
purpose or `any`, that channel or a null channel, `since <= now`, `until` null or after `now`, and a null
`window` or one that `now` falls in (section 6.4).

6.2. The reader MUST keep applying its last copy when the list cannot be read, however old the copy is: an
opt-out is a legal obligation that does not wait for the producer.

6.3. Messages the customer sent are never suppressed. A suppression of one purpose does not stop another;
one of `any` purpose stops every purpose. No producer declares a purpose named `any`.

6.4. **Contact windows.** An entry with a `window` holds only in those local hours, as when a customer says
"don't call before 9 am" (a window from 00:00 to 09:00 on the voice channel): outside them the contact may
go. A window is:

| Field | Meaning |
|---|---|
| `from` | When it starts, local `HH:MM`, inclusive. |
| `to` | When it ends, local `HH:MM`, exclusive: at exactly `to` it is over. |
| `tz` | An IANA time zone name (`America/Sao_Paulo`). The producer picks the subject's own zone when it knows it, else the account's or the space's. |
| `days` | ISO 8601 weekdays (1 is Monday, 7 is Sunday) it starts on, or null for every day. |

`now` falls in a window when, with `local` the time of `now` in `tz` by the zone's rules (the IANA time zone
database, daylight saving included):

1. When `from` is before `to`: `from <= local < to`, and the window started on `local`'s date.
2. When `to` is not after `from`, the window crosses midnight: `local >= from` (started on `local`'s date) or
   `local < to` (started on the day before). A window from 20:00 to 00:00 ends at the next midnight.
3. When `days` is not null, the date the window started on is one of `days`: a Friday window from 22:00 to
   06:00 still holds at 01:00 on Saturday, and the window that starts on Saturday is not a Friday one.

A window whose `from` equals its `to`, whose `tz` is not an IANA name, or whose `days` hold a value outside 1
to 7 is not a window, and the producer refuses it. A window ends on the `to` of the date the window started
on (or of the next day, across midnight): the producer's coordination decision defers the contact to that
moment (`contact_window`, [`coordination.md`](coordination.md), section 4). A window never makes an entry hold
at every hour.

6.5. A reader keeps the window with its entry and weighs it at the moment of each contact, with its own copy
of the IANA time zone database. A reader that cannot resolve an entry's zone keeps applying the entries it
can and asks again for the list; it never treats a window as every hour.

## 7. Test vectors

[`vectors/contact-window.v0.json`](../vectors/contact-window.v0.json): each case gives a window and an
instant `at`, and expects `{"applies", "until"}` (whether the instant falls in the window, and when that
window ends), or `{"error": "invalid_window"}`.

[`vectors/suppression-key.v0.json`](../vectors/suppression-key.v0.json): each case gives a reader's salt
and a handle as it comes, and expects its canonical form and key, or an error.

| Field | Meaning |
|---|---|
| `id` | The case, unique and dotted. |
| `description` | What the case shows. |
| `salt` | The reader's salt, base64url. |
| `type` | The handle's type: `phone_e164`, `wa_id`, `wa_jid`, `email`, or another. |
| `value` | The handle's value, as it comes. |
| `expect` | `{"canonical", "key"}`, or `{"error": "invalid_handle" \| "unsupported_type"}`. |

An implementation MUST fail, not skip, on a case field it does not know.
