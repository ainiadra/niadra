---
id: memoria-para-voz
lang: en
slug: memory-for-voice-agents-the-latency-budget
title: Memory for voice agents, the latency budget from the ring to the last turn
description: How long memory may take on a call, where every millisecond goes and what happens when it does not arrive, 1.5 s while the phone rings, 200 ms per turn, and the context measured at 16.8 ms at p95 through the public address.
order: 3
kind: measured
date: 2026-09-30T22:30:00-03:00
category: Voice
icon: bolt
cover: 1.5 s on the ring, 200 ms per turn
keywords: memory latency for voice agents, context before the first word, voice agent with customer memory, voice agent latency budget, memory for LiveKit Pipecat Vapi, context prefetch in voice, context p95
---

On a call, memory has two deadlines: up to 1.5 s while the phone rings, for the first read, and up to 200 ms per turn, for the slots of the words the customer has just said. Whatever does not fit those deadlines stays out of the turn, and the call goes on with what was already pinned. Niadra's context, compiled when the memory changes and with no AI model on the read, was measured at 11.2 ms at the median and 16.8 ms at p95 through the public address, at 10 reads per second, with zero errors in 900 requests ([run of September 30, 2026](https://github.com/ainiadra/niadra-sdk-python/tree/main/benchmarks/results/2026-09-30-6e6d07)). This post describes where every millisecond goes, and what happens when it does not arrive.

## Why voice is the most demanding channel for memory

The caller hears the silence. In a chat, 800 ms more between the message and the answer go unnoticed; on a call, the same gap is a pause the person fills with "hello?". Telephony's classic recommendation for one-way delay is 150 ms ([ITU-T G.114](https://www.itu.int/rec/T-REC-G.114)), and the voice agent already spends most of the turn's budget on transcription, the model and speech synthesis. Memory comes after all of that, and it comes on every turn.

A memory that runs a search per message, with a model deciding what to remember at read time, does not fit that budget. In the same benchmark, the systems that reason on the read took 2 to 14 s per read, and the ones that answer fast answer what the vector search found, without compiling. The [benchmark page](/en/benchmark) has the whole table, including the paths where Niadra trails.

## The budget, deadline by deadline

| Moment | Deadline | What happens when it is blown |
|---|---|---|
| First read, while the phone rings | 1.5 s | The greeting goes out without the context, and it enters on the next turn |
| The turn's slots, from the words the customer said | 200 ms | The turn goes out with the pinned body, without the slots; the delta goes to the next one |
| History search, as a tool | 300 ms, 300 tokens | The tool answers that the history is unavailable |
| Memory down (error 503) | 168 ms at the median | The agent gets an empty context, with no error, within the turn's budget |
| Memory delaying every answer by 2 s | 202 ms at the median | The same empty context; the call never waits for the whole delay |

The last two numbers come from the resilience measure: with the memory answering 503, Niadra delivered the empty context in 168 ms and no error reached the agent; with the memory delaying every answer by 2,000 ms, 202 ms. The other systems measured waited for the whole delay or returned the error to the agent on every attempt. The 1.5 s, 200 ms and 300 ms deadlines are configurable in the SDK; the values are the defaults documented in the [voice agents guide](https://docs.niadra.com/en/guides/voice-agents).

## What runs at each moment

**While the phone rings.** The voice platform announces the call before answering it: Twilio's ringing webhook, Vapi's `assistant-request`, ElevenLabs' initiation webhook, the caller joining the LiveKit room. That is where the first read starts (`begin()`), after the carrier's attestation: STIR/SHAKEN level A proves V2, B and C prove V1, and the read comes out at the level the call has proven. The first model call waits for that read up to 1.5 s (`ready()`), the time of a cold connection (TCP, TLS and the request) plus the server's first compilation.

**On every turn.** The context is pinned per conversation: the same bytes for the whole call, served from the SDK's memory at once, while the SDK revalidates them by ETag in the background. That is what keeps the start of the prompt identical, turn after turn, and the AI provider reuses that prefix. What changes are the slots: while the customer speaks, the SDK sends the partial transcript with `prefetch()`, and when it stops changing for 200 ms, reads the turn with it. The final turn reuses that read when its words begin with the partial's. The [LiveKit](/en/integracoes/livekit), [Pipecat](/en/integracoes/pipecat) and Retell adapters do the prefetch on their own.

**What arrived from another channel during the call.** The WhatsApp message that came in at 2:05 pm, while the customer was already on the line, arrives as a delta at the end of the next turn's prompt. The measured freshness from a write on one channel to its appearance on another channel's read: 62.7 ms at the median and 90.1 ms at p95.

**When the customer remembers something old.** The voice view's context is short on purpose: 88 tokens at the median, 151 at p95, with who is calling, what is open, what other agents have just done and the highlights of the history. The rest is left to the search, which the model calls as a tool, with a 300 ms budget and 300 tokens of answer, cut by value.

```python
from niadra import Niadra, phone

niadra = Niadra(channel="voice")
caller = phone(caller_id)

# While the phone rings: the carrier's attestation proves the level of this call
niadra.verify("network_attestation", "V1", handle=caller, conversation_id=call_id)
conversation = niadra.conversation(call_id, subject=caller, view="voice", verification="V1")
ctx = conversation.ready()  # the first read, within 1.5 s; the greeting waits for it
prompt = f"{YOUR_PROMPT}\n\n{ctx.system_block}"

# During the call: the history as a tool, within the voice budget
kit = niadra.tools(caller, verification="V1", conversation_id=call_id, voice=True)
hits = kit.call("search_customer_history", {"query": "credit for missed technician visit"})
```

## What the p95 number does not say

The 16.8 ms measure is through the public address, at 10 and 25 reads per second, on a cell with the production machine and database. It does not include the network between your voice platform and Niadra's region: a platform on another continent pays the round trip, which is why the first read starts on the ring and the following turns serve from the SDK's memory. On the harness's own machine, at 10 reads per second, Niadra's p95 was 46.3 ms, behind three systems that answer without compiling; the benchmark page says so in full. And "under 100 ms" remains the published target of the read path, not a contractual guarantee: the SLA belongs to the Regulated plan.

## How Niadra solves it

Niadra compiles each customer's context when the memory changes, not when the agent asks, and serves it pinned per conversation, with no AI model on the read. The voice adapters ([LiveKit](/en/integracoes/livekit), [Pipecat](/en/integracoes/pipecat), [Vapi](/en/integracoes/vapi), [Retell](/en/integracoes/retell), [ElevenLabs](/en/integracoes/elevenlabs) and [Twilio](/en/integracoes/twilio)) start the first read on the ring, record the carrier's attestation and prefetch from the customer's words, and none of them fails a turn when the memory does not arrive. The post on [keeping context from the call to WhatsApp](/en/blog/how-to-keep-context-from-voice-call-to-whatsapp) shows what happens after the call ends.

## Frequently asked questions

### What does the agent say if the context does not arrive within 1.5 s?

The greeting it would say without Niadra. The context enters on the next turn, and nothing on the call waits beyond the deadline. The read goes on in the background.

### Does the history search delay the answer?

It only runs when the model calls it, as a tool, and it has a 300 ms budget on voice, with 300 tokens of answer. The "History" line of the context already says whether that has happened before and how it was resolved, so most turns do not search.

### Does the prefetch send the partial transcript outside the voice platform?

It sends the words of the turn so far to Niadra's region, through the same encrypted channel as everything else, so the turn's read finds the memory already open. A prefetch never delays nor fails a turn, and the text goes through the same masking before any model.
