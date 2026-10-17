# Niadra Media Upload, version 0

- **Status:** draft, published with the first SDKs
- **Date:** 2026-09-23
- **Schemas:** [`schemas/media-upload-request.v0.json`](../schemas/media-upload-request.v0.json),
  [`schemas/media-upload-response.v0.json`](../schemas/media-upload-response.v0.json)
- **License:** Apache 2.0

## 1. Purpose

An event never carries media: a call recording or a photo would make every envelope heavy, and
would put the file wherever the envelope goes. The producer hands the file to storage first and
the envelope carries only a reference and a digest (event envelope, rule 5.2). This document
defines that handshake.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

The **producer** has the file and emits the event. The **consumer** stores both.

## 3. The request

| Field | Required | Meaning |
|---|---|---|
| `content_type` | yes | The media type of the file, such as `audio/wav` or `image/png`. |
| `size_bytes` | yes | Its exact size, from 1 byte to 500 MiB. |
| `sha256` | yes | Its SHA-256 digest, as 64 lowercase hexadecimal characters. |
| `subject` | no | The handle of the person the file belongs to. |

3.1. The producer MUST compute `size_bytes` and `sha256` over the exact bytes it will send.

3.2. The producer SHOULD send `subject` whenever it knows whose file it is. The consumer then
stores the file under that person, so erasing the person erases the file, even when no event
ever references it. A file reserved without a subject is erased only through the events that
reference it.

## 4. The response

| Field | Meaning |
|---|---|
| `media_ref` | The reference the event carries in `content.media_ref`. Opaque to the producer. |
| `upload_url` | A signed URL that accepts the bytes. |
| `upload_headers` | The headers the `PUT` carries, such as `Content-Type` and `x-amz-checksum-sha256`. |
| `expires_at` | When `upload_url` stops accepting them. |

4.1. The producer MUST send the bytes to `upload_url` with an HTTP `PUT`, before `expires_at`,
carrying exactly the headers in `upload_headers`. The signature covers them and the declared size,
and the store recomputes the digest, so a body that differs from the reservation is refused.
When `upload_headers` is empty, the `PUT` carries only `Content-Type`, set to the reserved
`content_type`.

4.2. `upload_url` is itself the credential. The producer MUST NOT add its own credentials or any
other header to the `PUT`, and MUST NOT log the URL.

4.3. The producer MUST NOT send the bytes over plain HTTP, except to a local emulator that serves
the API itself over plain HTTP.

4.4. A `PUT` that fails with a server error or a network error MAY be retried until `expires_at`:
storing the same bytes under the same reference twice is harmless. After that, the producer
reserves again.

4.5. When `upload_url` is empty, the consumer already holds the bytes and the producer sends
nothing.

## 5. The event

5.1. The event that carries the file MUST set `content.media_ref` to the reserved reference and
`content.media_sha256` to the digest from the request, and SHOULD set `content.type` to `audio`,
`image` or `file`. A transcript of audio goes in `content.transcript` of the same event.

5.2. A consumer SHOULD reject a file whose bytes do not match the digest the event declares.

## 6. Examples

See [`examples/media-upload-request/`](../examples/media-upload-request/) and
[`examples/media-upload-response/`](../examples/media-upload-response/). The voice turn in
[`examples/event-envelope/`](../examples/event-envelope/voice-turn.json) carries the reference
reserved there.
