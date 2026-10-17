---
id: memoria-do-agente
lang: en
slug: agent-memory-procedures-not-customer-data
title: Agent memory, what the agent learned about the work, never about the customer
description: Customer memory is one thing; agent memory is another, procedures, how tools behave and pitfalls, which today live in the prompt and go stale. How it enters the prompt, why it refuses personal data and why no note is born without review.
order: 4
kind: explainer
date: 2026-09-30T22:30:00-03:00
category: Agents
icon: doc
cover: What the agent learned about the work
keywords: procedural memory for AI agents, agent memory, agent working notes, procedures in the agent prompt, agent learns from execution, memory of tools and processes, agent memory without personal data
---

Agent memory is what the agent learned about its own work, not about the customer: "in the ERP, the credit only shows on the invoice after `post_credit` and `refresh_invoice`, in that order"; "the scheduling API refuses dates without a time zone"; "when the customer asks for a duplicate invoice, the procedure is this". It is what the team writes into the prompt by hand and forgets to update. In Niadra it lives in a table of its own, with its own policy, receipts and deletion, enters the prompt as a block separate from the customer's context, refuses any personal data instead of masking it, and no note is born from a conversation without a person approving it. The [documentation](https://docs.niadra.com/en/concepts/agent-memory) has the whole contract; this post gives the why of each rule.

## Why separate agent memory from customer memory

The two kinds of memory answer different questions, and mixing them is expensive in two ways.

The first is privacy. Customer memory is personal data: it has a data subject, a purpose, a retention period and a right to erasure. A procedure ("the duplicate invoice comes out of endpoint X") has no data subject. If the two live in the same table, erasing a customer has to sweep through the procedures, and a procedure that cites an example customer becomes personal data without anyone noticing. Kept apart, erasing a data subject never touches agent memory, by construction: it cannot hold personal data.

The second is caching. The customer's context changes with every customer; the procedure is the same for everyone. If the block of procedures comes before the customer's context and has the same bytes for everyone, it sits in the prompt prefix the AI provider caches, and the customer pays a fraction of the price for it. That is why the order is fixed: the agent's instructions, then the agent's notes, then the customer's context.

## What a note is, and what it never holds

Every note has a kind: `procedure` (how to do something), `tool_note` (how a tool behaves), `process_note` (how a company process works) or `pitfall` (what goes wrong). It has a title of up to 120 characters, a body of up to 2,000, up to 8 lowercase tags (`invoice`, `credit`, `erp`: the space's object types, operations and systems), a visibility and an evidence.

The evidence is the `conversation_id` or the `task_id` the note came from. Only the id, never the text. The visibility is `source` (the owning agent only, the default), `vendor` (every agent of the same vendor) or `space` (every agent in the space), and the same neutrality as customer memory applies: vendor A's agent never reads what vendor B's agent learned, unless the company opens it.

A note with a phone number, an e-mail, an ID number or a customer's name is refused, not masked: the write goes through the personal data detectors of the models server, inside the region, and through a local layer that recognizes e-mail, phone and two ID formats without depending on it. The refusal is `422 personal_data_in_agent_memory`, with the kinds of data found and without the value. Organizations and places do not count as personal data: a procedure cites systems, companies and branches all the time.

## How it enters the prompt

Three paths, all optional, and the feature stays off until a person switches it on in the Console.

**The block.** `GET /v1/agent-memory/block` returns the active notes the agent may read as a ready text, between `<agent_notes>` and `</agent_notes>`, opened with "From the agent itself (procedures and working notes, not customer data)". The notes whose tags match the view or the task come first, then the most revised, then the oldest, always in the same order. The budget goes from 50 to 2,000 tokens, 300 by default. Every [integration](/en/integracoes) takes `agent_memory=True` in Python or `agentMemory: true` in TypeScript and places the block on its own.

**The tools.** `search_agent_memory` (read) and `remember` (write) join the kit and the MCP server. Each one's description tells the model when not to use it: "it holds nothing about customers; for the customer's history use `search_customer_history`", and "never write anything about a customer here". `remember` is only offered to a key with the write scope, and a refused note comes back to the model as an error, for it to rewrite without the data.

**The task section.** A task view can append up to 200 tokens of notes with the tags of the task's object types to the end of the context, for whoever can only change the agent's context, not the prompt.

## Why no note is born on its own

What other memories call procedural memory is usually an AI-written summary of the execution, written without review. In Niadra, learning from a conversation is an explicit request: `POST /v1/agent-memory/distill` takes a `conversation_id` or a `task_id`, reads the whole conversation, already masked, asks the model for a note in the fixed schema and runs the result through the redactor. What comes out is a proposal, and nobody writes anything until a person approves it, with or without edits.

A distillation that yields no note says why: the conversation had no reusable procedure, the proposal carried personal data and was discarded, the conversation spoke only through an unconfirmed identity link, or there are no turns. In a space configured for human-only writes, even an agent's `remember` becomes a proposal.

The limits protect the rest: an agent writes up to 20 notes an hour and keeps up to the space's ceiling, 500 active notes by default. An agent edits and retires only its own notes; every edit creates a new version, and the previous one stays readable. No model reorders the notes by recent use: the order is by tags, version and age, the same for every customer, because that is what keeps the block cached.

## What gets recorded

Every read of the block and every search leaves a receipt in the same chain as the others, with the ids of the notes delivered; every write, edit, retirement, distillation, approval and deletion leaves an admin receipt. "Why did the agent do that?" stays answerable afterwards. Every note and every proposal created emits a webhook notice, with ids, kind and origin, never the text, for whoever wants to review the notes from outside. The export returns everything for portability, and erasing an agent erases all of its notes and proposals, in every version, with a receipt.

## How Niadra solves it

Agent memory is a table next to customer memory, never inside it: procedures, tool notes, process notes and pitfalls, with tags, visibility per agent, vendor or space, versions and evidence by id. It enters the prompt between the instructions and the customer's context, with the same bytes for every customer, and each adapter places it on its own. Personal data is refused at write time; learning from a conversation produces a proposal, which a person approves in the Console. The post on [memory for internal AI agents](/en/blog/memory-for-internal-ai-agents) shows where procedures weigh most: in the CRM, the ERP and billing.

## Frequently asked questions

### Is this procedural memory?

It is what procedural memory promises, with two differences: no note is written without a person approving it, and no note may hold personal data. Distillation proposes; the Console approves.

### Can the agent write during the conversation?

It can, with `remember`, if its key has the write scope and the space allows agent writes. The note goes through the personal data detectors before it enters, and a space can require every agent write to become a proposal.

### Does one vendor's agent memory leak to another?

No. The default visibility is the owning agent only; `vendor` opens it to the agents of the same vendor and `space` to every agent of the company, and the company decides, note by note.
