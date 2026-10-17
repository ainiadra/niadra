# Niadra OpenTelemetry conventions, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schema:** none. The conventions are span attributes, and the records they produce follow the
  [Turn Record](turn-record.md).
- **License:** Apache 2.0

## 1. Purpose

Many agents already export OpenTelemetry traces with the `gen_ai.*` attributes. This document says:
- which of those attributes a memory reads;
- the few `niadra.*` attributes that OpenTelemetry has no equivalent for (the customer's channel and
  identifiers, the turn, the build, what the person was shown);
- what record each level of adoption produces.

An agent can start by pointing the OTLP exporter it already has at the memory, and add attributes later.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY and
OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and
only when, they appear in all capitals.

An attribute set on the resource applies to every span of that resource, and one set on a span wins over
the resource's. Traces arrive over OTLP/HTTP, as JSON or protobuf.

## 3. Levels

| Level | What the agent sends | What it produces |
|---|---|---|
| Bronze | `gen_ai` spans of model and tool calls, as the agent's instrumentation emits them (section 4) | A turn record with `fidelity: bronze` and `completeness: unknown`: tools, model, tokens, latency and cost per turn, and regression by indicator. Never replay or attribution. |
| Silver | Bronze, plus `niadra.exposure` and `niadra.pack.hash` (section 5) | Also what the person was shown: a `presented` interaction produced by `otel`. |
| Gold | Not OpenTelemetry: the SDK intercepts the agent's calls in its process | Everything in the Turn Record, including replay. |

## 4. What is read from `gen_ai` spans

The `gen_ai` conventions are still evolving, and a reader SHOULD accept their current and earlier
attribute names.

| Span | Attributes read | Becomes |
|---|---|---|
| A tool call (`gen_ai.operation.name` = `execute_tool`) | `gen_ai.tool.name`; `gen_ai.tool.call.id` (else the span id); the arguments and the result, when the instrumentation captures content | A call with `kind: tool`. Its latency is the span's duration. |
| A model call (any other `gen_ai.operation.name`) | The model (`gen_ai.response.model`, else `gen_ai.request.model`); `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`, and a cached-token count when the instrumentation reports one; the input and output messages | A call with `kind: model`, and the conversation's messages. |

A value the exporter cut, whether by an attribute length limit or by dropped attributes, marks its call
`truncated: true`.

## 5. The `niadra.*` attributes

### 5.1 The turn

| Attribute | Type | Meaning |
|---|---|---|
| `niadra.turn.id` | string | The turn's id (Turn Record 5.1). Every span of the turn carries it. Without it, the spans of one trace are one turn, named `otel-<trace id>`. |

5.1.1. The spans of one turn may arrive in more than one export. The memory merges them into the same
record: a later export adds the calls (by call id) and the exposures the record lacks, and widens its start
and end. An export that adds nothing counts as a duplicate, so sending the same spans again changes
nothing. A call's blobs are named after its span, so two exports never collide.
| `niadra.pack.hash` | string | The hash of the context pack the turn read, as the memory returned it. |

### 5.2 The build

| Attribute | Type | Meaning |
|---|---|---|
| `niadra.build.prompt.<name>` | string | The version of prompt `<name>`, e.g. `niadra.build.prompt.core` = `v16`. |
| `niadra.build.corpus_digest` | string | `sha256:` and the hex digest of the files the agent consults. |
| `niadra.build.model` | string | The exact model. `gen_ai.response.model` is used when this is absent. |
| `niadra.build.assembler` | string | The version of the company's context assembler. |
| `niadra.build.tool_schema.<tool>` | string | `sha256:` and the hex digest of tool `<tool>`'s schema. |

The resource's `service.version` names the agent's code version.

### 5.3 The exposure

A span or span event named `niadra.exposure`, emitted when a list reaches the person (not when the tool
returns it), carries:

| Attribute | Type | Meaning |
|---|---|---|
| `niadra.exposure.id` | string | The exposure's id. |
| `niadra.exposure.list` | string | The list's id; "show more" keeps the id and moves the page. |
| `niadra.exposure.page`, `niadra.exposure.offset` | int | Where in the list this page starts. |
| `niadra.exposure.visible_k` | int | How many items the person could see without scrolling. |
| `niadra.exposure.items` | string | JSON: an array of `{pos, ref, shown}` in the order shown, where `ref` is `<type>:<namespace>:<id>` and `shown` holds the values displayed (a price, a color). |

### 5.4 The customer and the channel

| Attribute | Type | Meaning |
|---|---|---|
| `niadra.channel` | string | The customer's channel, e.g. `whatsapp`. OpenTelemetry has no attribute for it. When absent, the source's default applies. |
| `niadra.handle.<type>` | string | An identifier of the customer, one attribute per handle type, e.g. `niadra.handle.app_user_id`. `enduser.id` is read as an `app_user_id`. |

## 6. Propagation

6.1. An SDK MAY copy `niadra.turn.id` and `niadra.pack.hash` from OpenTelemetry baggage to the spans of the
turn, so they reach spans the agent's code never touches.

6.2. Baggage travels in the headers of every outgoing call, including calls to third parties, so it MUST
carry only those two values. A `niadra.handle.*`, a `niadra.exposure.*` or any value that names a person
MUST NOT go in baggage.

## 7. Privacy

Span attributes reach every destination the collector exports to. Therefore:
- A producer SHOULD send the `niadra.*` attributes only on the pipeline that goes to the memory.
- A producer SHOULD prefer an internal id (`app_user_id`, `enduser.id`) to a phone number or an e-mail.
- Personal data MUST NOT travel in a URL.

A bronze record keeps the digests of what it read, not the values: its mode is `hash_only` unless the
memory's configuration for that source says otherwise.
