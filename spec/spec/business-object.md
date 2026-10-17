# Niadra Business Object, version 0

- **Status:** draft
- **Date:** 2026-09-23
- **Schema:** [`schemas/object-timeline.v0.json`](../schemas/object-timeline.v0.json)
- **License:** Apache 2.0

## 1. Purpose

Internal agents work on things, not only on people: an invoice, an order, a ticket, a technical
visit. Events name them in `object_refs` (event envelope, section 3). The **object state** says where
the object stands now ([Object State](object-state.md)); the **object timeline**, this document, says what
happened to it.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

An object is identified by `ref`: `{type, namespace, id}`, such as `invoice` / `erp` / `0823`,
where `namespace` names the system of record. Its short form is `type:namespace:id`.

## 3. Object timeline

| Field | Meaning |
|---|---|
| `ref` | The object. |
| `items` | System events and agent actions about it, newest first, one line each. |
| `next_cursor` | Present when there are more items; the reader sends it back for the next page. |
| `as_of` | The time of the latest event the timeline reflects. |

3.1. The timeline MUST NOT carry conversation content: what customers and agents said about the
object belongs to the subject's history, under that subject's verification and policy.

3.2. Cursors are opaque to the reader.

## 4. Addressing

4.1. A reader addresses an object by its three parts. They are system record ids, never personal
data, and MAY appear in a URL; each part MUST be percent-encoded there.

4.2. A part that contains `/` cannot be addressed by path in this version.

## 5. Examples

See [`examples/object-timeline/`](../examples/object-timeline/).
