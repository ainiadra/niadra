---
id: identidade-no-benchmark
lang: en
slug: why-cross-channel-identity-breaks-agent-memory
title: Why cross-channel identity breaks agent memory, from 77.6% to 28.5%
description: In the same benchmark, the same memory gets 77.6% right with a single id and 28.5% when each channel sends its own id. What the number says about agents from different vendors, and why identity has to be resolved before anything is stored.
order: 1
kind: measured
date: 2026-09-30T22:30:00-03:00
category: Benchmark
icon: user
cover: One id per channel, 28.5% right
keywords: customer identity across channels, agent memory with user_id, memory benchmark for AI agents, agents from different vendors, omnichannel identity resolution, per-user memory, mem0 per-channel id
---

A per-user memory needs a ready `user_id`, and in an operation with several channels and vendors that id does not exist: voice hands over a phone number, WhatsApp a `wa_id`, the app a login, the CRM a contact number. When each channel sends its own identifier, the memory stores three people where there is one. The [Niadra benchmark](/en/benchmark) measured that effect: the most used open-source memory gets 77.6% of the cross-channel cases right with a single id and 28.5% with one id per channel, on the same 165 cases, with the same agent and the same judge. Niadra, which resolves identity before storing, gets 98.7%.

## What the benchmark measured

The dataset has 356 synthetic cases in Portuguese and English, each with a customer who speaks on more than one channel and a question whose answer only exists if the memory crossed the channels. Every system runs with the same agent, the same judge and the same models, and the comparison is made on the 165 valid cases all of them ran: a case only counts when the agent gets it right with the whole history in the prompt and wrong with no memory at all. The files of the run of September 30, 2026, with three repetitions, are [published in the harness repository](https://github.com/ainiadra/niadra-sdk-python/tree/main/benchmarks/results/2026-09-30-6e6d07), and the benchmark page shows every measure, including the ones Niadra does not lead.

For the systems that take a ready `user_id`, the harness runs two scenarios:

- **Same id on every channel.** The application has already resolved who the person is, and the memory gets the same id on voice, on WhatsApp and in the app. It is the condition those systems are measured under in their own benchmarks.
- **One id per channel.** Each channel sends the identifier it has: the phone number, the `wa_id`, the login. It is the condition of a company with agents from different vendors, where nobody resolved identity beforehand.

The same memory, the same data, the same cases: 77.6% by the judge in the first scenario, 28.5% in the second. With a result reranker, 76.4% and 27.3%. The references give the scale: the agent with the whole history in the prompt gets 100%, and with no memory at all, 8.5%.

## Why one id per channel brings accuracy down

The mechanism is plain. A per-user memory indexes everything by `user_id`: the facts extracted from the conversation, the vectors, the graph, the entities. When the WhatsApp agent asks "what has this person already said?", the search looks up the WhatsApp id and finds what came in through WhatsApp. Yesterday's call came in through the phone, under an id the memory does not link to that one. To it, they are two people, and no search crosses over.

The single-id scenario hides this, because it assumes someone resolved identity beforehand. In a single-vendor application with a login, that someone is the application itself. In a company with the voice agent at one vendor, the WhatsApp agent at another and the billing agent built in house, there is no such someone: each vendor gets the identifier of its own channel and keeps the memory of its own agent. The "shared" memory becomes three memories that do not talk, and the customer tells the story again.

There is a second effect, which the privacy measure shows. With one id per channel, no sensitive value leaked to a conversation that had not proven who it was: zero. Not through governance, but because nothing crosses; it is the same reason accuracy falls. With the single id, the same memory handed 32 of 32 sensitive values to unverified conversations, because a ready id carries no level of proof: whoever has the id has everything. Niadra handed 0 of 32, with the verification level decided per conversation.

## What changes when identity is resolved before storing

Niadra does not take a `user_id`. It takes the events of each source with the identifiers the source has (`phone_e164`, `wa_id`, `email`, `app_user_id`, the contact id in the CRM, the customer id in the ERP) and resolves whom each event is about before storing it. Three rules make that work in production:

1. **Every identifier has a weight.** The phone number is a hint; the ID number said on the call is declared; the e-mail confirmed by a code is verified; the login with a password is authenticated. Two identifiers become the same customer when there is evidence: a system event saying that login 88213 has that phone number, or a confirmed code. A similar name never merges.
2. **Every merge is reversible.** Each fact stays bound to the identifier and the conversation it came from, and the merge is an assertion with a method, a date and a source. Withdraw the assertion and the facts return to their rightful owner. It is what keeps customer A's invoice out of customer B's call.
3. **The verification level belongs to the conversation, not to the id.** The call starts at the level the carrier attested; a confirmed code raises it; the policy says what each level may read. The context the agent receives changes with what the conversation has proven.

The result on the same 165 cases is 98.7% by the judge, with the context compiled before the conversation and no AI model on the read. The [Identity page](/en/produtos/identidade) shows the mechanism, and the post on [how to recognize the same customer across channels](/en/blog/how-to-recognize-the-same-customer-across-channels) goes through the levels.

## When a per-user memory is the right choice

When the application already owns the identity. An app with a login, one agent, one vendor: the `user_id` exists, it is authenticated, and a per-user memory does what it promises, in the scenario it was measured in. It is simpler to run, and the model spend measured in the benchmark, for whoever hosts it, stays under US$ 2 per thousand conversations on the cheapest option. In that case, Niadra adds a layer the company does not need yet.

The trouble starts at the second channel, or the second vendor. The deciding question is: who resolves identity before the memory stores anything? If the answer is "nobody", the number that counts is 28.5%, not 77.6%.

The benchmark's limits apply both ways. The cases are synthetic, Niadra produced it, and the other systems ran one repetition against Niadra's three. The answer to that is reproduction: the script, the dataset and the configuration are public, and anyone can run it again.

## How Niadra solves it

Niadra is the omnichannel memory layer: it takes the events from every channel, platform and system, recognizes whom each one is about through the identifiers the source has, with weights and reversible merges, and builds one memory of each customer, which any agent, from any vendor, reads in the context of its task and at the level the conversation has proven. The adapters for [LiveKit, Vapi, WhatsApp Cloud API, LangGraph and the others](/en/integracoes) hand over each platform's identifiers the way it has them.

## Frequently asked questions

### What are the "same id on every channel" and "one id per channel" scenarios?

They are the two ways of handing identity to a memory that takes a ready `user_id`. In the first, the application has already resolved who the person is and sends the same id on every channel. In the second, each channel sends the identifier it has, as happens when the agents come from different vendors.

### Is the difference not just configuring a single id?

Configuring a single id requires someone to resolve identity before the memory stores anything, across every channel and vendor, with a weight for each identifier and reversible merges. That work is what Niadra does. Without it, the single id does not exist for a phone number calling for the first time.

### Where are the numbers?

On the [benchmark page](/en/benchmark), with every measure, and in the [files of the run of September 30, 2026](https://github.com/ainiadra/niadra-sdk-python/tree/main/benchmarks/results/2026-09-30-6e6d07), with the script, the dataset and the configuration to reproduce it.
