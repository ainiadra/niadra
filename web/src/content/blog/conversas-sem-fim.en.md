---
id: conversas-sem-fim
lang: en
slug: conversations-nobody-ends-when-agent-memory-should-learn
title: "Conversations nobody ends: when an AI agent's memory should learn"
description: On WhatsApp, in the app chat and in the staff assistant, almost nobody ends the conversation. If memory only learns when the conversation closes, the next agent does not know what was said ten minutes ago. When to learn, how to handle people who come back, and what to do when a call ends.
order: 6
kind: explainer
date: 2026-10-08T09:00:00-03:00
category: Architecture
icon: layers
cover: Memory does not wait for the end
keywords: when does AI agent memory learn, WhatsApp conversation that never ends, AI agent conversation session, delayed memory between agents, conversation inactivity, end conversation in the SDK, AI agent does not see what was just said
---

An AI agent's memory should learn a conversation a few minutes after it goes quiet, without waiting for someone to end it. On WhatsApp, in the app chat and in the assistant staff use, a conversation almost never ends with a "goodbye": the person stops replying. If memory only learns at a formal end, it learns late, and the next agent answers without knowing what was said ten minutes ago.

With Niadra, a conversation nobody ended becomes memory after 3 minutes without turns. If the person speaks again within the channel's window, the conversation reopens as the same one and is learned again with what came after.

## Why does waiting for the end delay everything?

Picture a lawyer who, at 3pm, tells the firm's assistant: "Note: in this case, do not propose a settlement, the client's board decided to go to trial". They close the laptop. At 3:10pm, the client messages on WhatsApp asking whether there will be a settlement.

If memory waits for the lawyer's conversation to end, and the channel's rule is to close after half an hour of inactivity, the WhatsApp agent answers at 3:10pm without the instruction. Worse, it may say a settlement is being considered. The information existed, was told to the company, and arrived late.

The same goes for the inside. The billing agent in the ERP does not learn that the customer disputed the invoice in the chat until the conversation "ends".

## Why not learn on every message?

Learning on every turn looks faster, but it has two costs:

- **Partial reading.** A sentence on its own often says nothing. "Friday works" only makes sense with the question before it. Learning on every turn produces half-facts that later need correcting.
- **Cost.** Every extraction pass costs. One per message multiplies the cost of a long conversation without improving the result.

A short silence is a good sign that a stretch of conversation is over: the person said what they wanted and is waiting, or went to do something else.

## What happens when the person speaks again?

The conversation reopens as the same one, as long as the return falls within the channel's inactivity window (about 20 minutes on WhatsApp, longer for e-mail, where a reply two hours later is still the same topic). Memory learns again from the new stretch, and the new version replaces the previous one. A return much later opens a new conversation, which sits next to the old one.

This keeps two things right at once: what was said reaches the other agents quickly, and the topic stays one for whoever reads the history.

## What about a call that ends?

On voice, the end is clear: the call hangs up. The agent sends the end of the conversation with the real time, and memory learns right away, without waiting for silence. The end time also decides which side a late transcript falls on: before the end, it joins the same conversation; after, it opens another.

## How do you know it has become memory?

Ask. For each conversation or task, Niadra answers whether it is still open, being processed or ready. When it is ready, a context read made at that moment already sees what was said. In tests and critical flows, check the state before asking the next agent.

## How Niadra solves it

With Niadra, each conversation is split into stretches of activity. A stretch nobody ended becomes memory after 3 minutes of silence, a call becomes memory when it hangs up, and an internal agent's task closes when it finishes. What was learned enters the [context](/en/produtos/contexto) of every agent serving the same customer or object, and the [history](/en/produtos/historico) keeps each version. The documentation describes [events and sessions](https://docs.niadra.com/en/concepts/events), with each channel's windows and each conversation's state.

## Frequently asked questions

### Do I need to send the end of the conversation from the SDK?

It is not required: memory learns from silence. On voice, send the end when the call hangs up; it is more precise than silence and decides where a late transcript falls.

### Does learning again when the person comes back duplicate facts and promises?

No. The new version of the conversation replaces the previous one, and a repeated promise is recognized as the same, as the post on [stopping your agent from promising and forgetting](/en/blog/how-to-stop-your-ai-agent-promising-and-forgetting) explains.

### Does this raise the cost per conversation?

A conversation with long pauses is learned once per pause. The price does not change: it is charged per conversation, not per pass, as the [pricing](/en/preco) page shows.
