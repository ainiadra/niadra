---
id: estado-tipado
lang: en
slug: why-the-agent-quotes-the-wrong-price
title: Why the agent quotes the wrong price, and what typed state in the context changes
description: The same agent, with and without the state of the customer's objects in the turn, measured on 126 answers per arm, from 36.6% to 84.9% right. What the state of now is, why a quote expires by its inputs and what it costs in tokens.
order: 2
kind: measured
date: 2026-09-30T22:30:00-03:00
category: State
icon: sliders
cover: With the state in the turn, 84.9%
keywords: AI agent quotes wrong price, customer state for AI agents, expired quote AI agent, data freshness in the context, constraint declared by the customer, typed memory for agents, typed state benchmark
---

The agent quotes the wrong price because the memory hands it what was said, not what is true now: the price the customer heard last week, the quote that has already expired, the deadline that was revised. When the turn starts carrying the state of now of the customer's objects, with the age, the source and what cannot be claimed from that value, the same agent, with the same model, gets 84.9% of the cases that ask for a number, a deadline or a choice right, against 36.6% without the state. The measure comes from the [run of September 30, 2026](https://github.com/ainiadra/niadra-sdk-python/tree/main/benchmarks/results/typed/2026-09-30-b59a5d), with 126 answers per arm.

## What was measured

The set has 42 cases in seven categories, in Portuguese and English, with three sectors described by role: a store, a litigation law office and the sale of a health plan. Each case asks for a number, a deadline or a choice that only the state or a constraint backs: "what is the price today?", "is the quote still good?", "what changed since I last looked?". Two arms, the same customer and the same conversation: one context read with no block at all and one that asks for the state and constraints blocks. The only thing that changes is what the two blocks add to the turn block.

The same agent and the same judge (low reasoning, fixed seed) on both arms, three repetitions of each case, on a local cell of the harness with Niadra's server image. The interval is Wilson at 95%, and the repetitions of a case are not independent draws, so it is narrower than new cases would give.

| Category | Without the blocks | With the blocks |
|---|---|---|
| Quote expired by an input | 0% | 100% |
| What changed since last seen | 0% | 100% |
| Declared constraint | 0% | 94.4% |
| Revised deadline | 72.2% | 88.9% |
| Price with freshness | 50% | 66.7% |
| "Not checked" never becomes "no" | 100% | 100% |
| Effect exactly once | 33.3% | 44.4% |
| All cases | 36.6% | 84.9%, interval [77, 90] |

Two results deserve a separate reading. "Not checked" never becomes "no" was already at 100% without the blocks: the agent does not invent a refusal when the value was not checked, state or no state. And "effect exactly once" barely moves with the blocks, because it is not a context problem: it is a coordination one. The second attempt at the same effect was refused as already done in 18 of 18 checks, but through a mechanism separate from the context, which the [Coordination](/en/produtos/coordenacao) page describes.

## Why the price comes out wrong without the state

A conversation's memory stores episodes: the customer asked the price, the agent answered R$ 189, the customer asked for a quote with delivery. All of it is true about the past. The next agent reads those episodes and, with nothing else, treats the last value said as the value of now. If the price list changed, it repeats the old price with the conviction of someone who read the memory.

Three things are missing from the episode for it to become state:

- **The age and the source of each field.** The R$ 189 price came from the price list of September 22, read by the orders system. An 8-day-old value may or may not hold; the company's freshness policy says so, field by field.
- **What cannot be claimed from that value.** With an 8-day-old price, the agent may say "it was R$ 189 last week" and may not say "it costs R$ 189". The prohibition goes written in the block, so the model does not have to infer it.
- **The dependency between values.** A quote is computed from price, shipping and lead time. If shipping changed, the quote expired, even if the validity date printed on it has not passed. Typed state keeps the inputs of every derived value and marks it expired when one of them changes: it is the category that went from 0% to 100%.

The declared constraint is the other side: "do not call after 6 pm", "cash only", "I do not want the plan with co-payment". The customer said it once, on another channel, and today's agent has to honor it without her repeating it. Without the block, 0% of the answers honored the constraint; with it, 94.4%.

## What typed state is, by mechanism

The company declares its state types (a sale, a case, a proposal, a claim, an order), or Niadra derives them from the database schema and warns when they change. Each object has four logical values per field: yes, no, not observed (with who observed and the validity) and known defect of the source. "Not checked" never becomes "no". Sources have precedence, freshness is computed per field at read time, and derived values keep their inputs and expire when an input changes. The agent gets the state in the turn block, with the list of what it cannot claim from stale data. The [object types documentation](https://docs.niadra.com/en/concepts/object-types) describes the contract.

What it costs: the block adds 58 tokens to a voice turn and 51 to a chat turn, at the median. The same data delivered as a tool's JSON would cost 1,038 and 777 tokens. A turn that asks for no block costs the same as before, measured on the same customers. In 126 reads without verification, none carried sensitive data in the block: the state obeys the level the conversation has proven, like the rest of the context.

## What the number does not say

The cases are synthetic and the benchmark is Niadra's; the answer is the public harness, to reproduce it. The measure was taken on a local cell, not in the production region. Of the 126 pairs of answers, 81 passed the validity rule (right with the whole history in the prompt and wrong with no memory); the full table, with the valid ones only, is on the [benchmark page](/en/benchmark). And "effect exactly once" shows the limit of the mechanism itself: state in the context fixes what the agent claims, not what it executes twice.

## How Niadra solves it

State is one of the parts of the memory Niadra builds from the events of every channel and system: objects of any type, the customer's or shared, with freshness per field, derived values that expire by input and the constraints the customer declared, delivered in the turn of any agent, from any vendor. The [State](/en/produtos/estado) page shows the artifact; the [Claims](/en/produtos/conferencia) page shows how what the agent claims is checked against what it looked up, by lexicon, role and anchor, with no extra model.

## Frequently asked questions

### Is this the same as a tool that queries the ERP?

No. A tool returns the record when the model decides to call it; typed state arrives in the turn, before the answer, already with the age, the source and what cannot be claimed. The same data as a tool's JSON cost 13 to 18 times more tokens in the measure.

### Why did "effect exactly once" barely improve?

Because it is not a context failure. Keeping the same message from going out twice is coordination: one business fact, one effect. The second attempt was refused as already done in 18 of 18 checks, through a mechanism of its own, outside the context.

### Does the 84.9% hold for my sector?

The measure uses three sectors described by role and synthetic cases. What holds is the mechanism: your state type is declared by you, or derived from your database, and the measure in your case comes out of your operation, not ours.
