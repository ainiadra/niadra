---
id: memoria-tinha-a-resposta
lang: en
slug: did-the-memory-have-the-answer-memory-error-or-model-error
title: "Did the memory have the answer? How to tell a memory error from a model error"
description: When an AI agent gets a customer question wrong, the error can be in memory, which did not deliver the information, or in the model, which misread it. How to measure the two separately, why a strict automatic judge misleads, and what to do about each kind of error.
order: 5
kind: explainer
date: 2026-10-08T09:00:00-03:00
category: Benchmark
icon: target
cover: Memory or model?
keywords: how to evaluate AI agent memory, did the memory have the answer, model error or memory error, evaluating AI customer service agents, strict LLM as a judge, context has the answer metric, test agent memory before production
---

To know whether an AI agent's memory works, first measure whether the context handed to the agent had the answer, and only then whether the agent answered correctly. The first number is about memory. The second mixes memory with the model that read the context. When both go into a single score, a model that reads badly looks like bad memory, and the team spends weeks fixing the wrong part.

This post shows how to separate the two errors with real customer questions, and what to do about each.

## Why does a single score mislead?

The usual test is to write twenty questions a customer would ask, with the right answer next to each, let the agent answer and count the hits. The number comes out, but it does not say what to fix:

- **The agent said "I don't know".** Was the information missing from the context, or was it there and the agent missed it?
- **The agent got the date wrong.** Did memory deliver the wrong date, or deliver two dates and the agent picked the one on the line above?
- **The agent was right and marked wrong.** It answered the truth with an extra detail, and the judge thought the detail was made up.

The third case is more common than it seems. An automatic judge comparing the answer with a short answer key punishes any detail the key does not have, even a true one. "The fee was due 13/10 and paid 05/10" becomes an error when the key only says "13/10".

## How do you measure memory on its own?

For each question, keep the context block exactly as the agent received it, and ask a judge one thing only: does the block, on its own, contain what is needed to answer? Three possible answers: yes, partly, no.

That gives you the memory measurement: in how many questions the context had the answer. It does not depend on the model answering, the agent's prompt, or the judge's mood about extra details.

Two precautions:

- **Wait for memory to finish.** If the test runs right after the conversations, memory is still learning, and the block comes out without what was said minutes earlier. Check that the conversations have become memory before asking.
- **Ask about what happened.** The answer key has to reflect the state after the conversations, not what was planned before. If the agent already told the customer, "the lawyer will let you know" is no longer the right answer.

## How do you measure the model?

With the same block, swap the model that answers. If three different models get the same question wrong with the same block, the problem is probably how the information was written, not the model. If only the smaller model gets it wrong, the problem is the model.

And fix the judge: an extra detail is an error only when nothing backs it, neither the answer key nor the block the agent received. It is the same rule as values without a source: a number, date or code the agent stated has to come from what it read.

## What do you do about each error?

| What the measurement shows | Where the problem is | What to do |
|---|---|---|
| The block did not have the answer | Memory | Check whether the information was extracted, tied to the right object and delivered to whoever asked |
| The block had it, every model gets it wrong | How memory writes it | Change the wording: the due date as a due date, done apart from pending, the rule saying who it applies to |
| The block had it, one model gets it wrong | The model | Change the model or adjust the agent's prompt |
| The judge marked an error, the detail has a source | The judge | Count it as correct |

The second row is the most useful and the least obvious. Often memory has the answer but its form invites the error: a promise's date next to the case deadline, "done" right after an open item, "overdue" without the date. Changing the wording fixed more errors than changing the model.

## How Niadra solves it

Niadra applies this separation in its own [benchmark](/en/benchmark): the same agent and the same judge answer with each system's memory, and values without a source are counted with the same rule for every system, from what the memory block contained. Day to day, [measurement](/en/produtos/medicao) shows, by agent and by vendor, what was delivered and used, and the [turn record](/en/produtos/registro) keeps the exact block each agent received, so the question can be replayed later. The post on [whether the agent used the context](/en/blog/how-to-know-whether-your-ai-agent-used-the-context) covers the other half: what the agent did with what it received.

## Frequently asked questions

### How many questions do I need for a reliable test?

Variety matters more than count: questions about facts, deadlines, promises, restrictions and things that changed during the week. Run each question more than once; a model's answer varies between runs.

### Can I use the same model as agent and as judge?

You can, but the judge has to see both the answer key and the block, and judge an extra detail by what the block backs. Without that, it punishes true answers.

### Why not just measure customer satisfaction?

Because it arrives late and does not tell you the cause. The memory measurement tells you, before the customer complains, whether the information was there.
