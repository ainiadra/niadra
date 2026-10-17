---
id: vou-enviar-nao-e-enviei
lang: en
slug: when-to-mark-a-pending-item-as-done
title: 'When to mark a pending item as done: why "I will send it" is not "I sent it"'
description: An AI agent that closes the item when it says "I'm sending it now" erases the promise before it is kept. What has to happen for an item to close, who may close it, and how to avoid both mistakes, closing too early and never closing.
order: 3
kind: explainer
date: 2026-10-08T09:00:00-03:00
category: Governance
icon: check
cover: Close only what was done
keywords: when to close an AI agent pending item, AI agent marks task done without doing it, agent says it sent and did not, AI agent follow up, close a promise to the customer, check the AI agent delivered, item closed by a system event
---

A pending item should close when what was promised happened: the document reached the customer, the notice was given, the system recorded the payment. It should not close when the agent says it will do it, nor when it says it did without doing it. "I will send the copy" keeps the item open; "here is the copy of the ruling", with the attachment, closes it.

It sounds obvious, but this is exactly where AI agents go wrong, because to a language model the two sentences look alike.

## Why is closing too early worse than not closing?

One extra open item is a nuisance: it shows up for the next agent, who asks or checks. An item closed too early disappears: no agent sees it again, and the customer waits for something everyone thinks has been done.

The two cases we saw most:

- **The agent announces and records.** The customer asks for the copy of the ruling, the agent answers "I'm sending it to you now" and records a delivery action. No document was attached. If the action closes the item, the lawyer's promise vanishes.
- **The agent repeats someone else's promise.** "The lawyer will send you the copy" repeats the promise; it does not keep it.

## What has to happen for it to close?

An item closes in three ways, each with its own check:

1. **A system event.** The document was attached to the court file, the payment cleared, the order went out for delivery. The system of record is the strongest source, and the event closes the item that was waiting for that operation.
2. **An agent action that delivered.** An agent talking to the customer can close an item when it records the action and its reply, in the same conversation, delivered what was promised: gave the result, attached the document, asked for what it needed to ask. "I'm sending it now" with no attachment delivers nothing.
3. **A person or agent saying it was done.** "I already told the client about the hearing" closes the item to tell them. A conversation about the same topic that does not say it was done does not close it.

In all three, the item only closes if it is the same one: the same object, its words, the operation it was waiting for or a related one. When the words are not enough ("inform of the judgment" for "communicate the judgment"), a decision model checks whether the action keeps the promise, and it only closes with high confidence.

## What about the opposite mistake, never closing?

A check that is too strict leaves everything open, and the list of items loses its value. Three adjustments prevent that:

- **Synonyms.** "Notify", "inform" and "let them know" do the same thing. The check compares what was done, not the exact word.
- **Related operations.** "Attach a document" and "deliver a document" both keep the promise to send a copy.
- **Items with no operation.** A promise made before memory knew any operation still closes through its words and the model's check.

## How Niadra solves it

With Niadra, an item closes on what was done: an event from the [system of record](/en/produtos/sistemas), an agent action whose reply delivered the promised content, or a statement saying it was done. A customer-facing agent's action goes through a delivery gate before it closes anything, and the action is recorded even when it closes nothing. Every close shows in the [history](/en/produtos/historico) with its reason, and [claim checking](/en/produtos/conferencia) shows when an agent stated something with no source. The documentation describes [how items close](https://docs.niadra.com/en/concepts/object-memory) and [coordination between agents](https://docs.niadra.com/en/concepts/coordination).

## Frequently asked questions

### Can the agent talking to the customer close a promise the lawyer made?

Yes, when its reply to the customer delivered what the lawyer promised. "The judgment dismissed the claim" closes the promise to communicate the judgment. "The lawyer will talk to you" does not.

### What if the agent records an action it did not perform?

The action is recorded, but it does not close the item without the delivery in the reply. The promise keeps showing to the next agents until it is kept.

### Can an item reopen?

A closed item does not reopen on its own. If the customer says they never received it, that becomes a new dispute on the same object, visible to every agent working on it.
