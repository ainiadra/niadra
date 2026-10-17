---
id: agente-afirma-sem-consultar
lang: en
slug: the-ai-agent-stated-what-it-never-checked-two-public-cases
title: The AI agent stated what it never checked: two public cases and the mechanism behind the failure
description: A Canadian tribunal ordered Air Canada to pay for what its website agent said about bereavement fares. A Cursor support agent invented a one-device policy and customers cancelled. Why it happens, step by step, and what would have changed the outcome.
order: 2
kind: case
date: 2026-10-01T09:00:00-03:00
category: Cases
icon: eye
cover: Stated without checking. The company paid
keywords: AI agent gave wrong information who pays, Moffatt v. Air Canada, Cursor support agent invented policy, liability for AI agent answers, AI agent invents policy, hallucination in customer service, how to stop an agent stating what it did not check, claim contract for AI agents
---

On February 14, 2024, the Civil Resolution Tribunal of British Columbia ordered Air Canada to pay a passenger CAN$ 812.02 because the AI agent on the airline's website said one thing about bereavement fares and the policy page said another ([Moffatt v. Air Canada, 2024 BCCRT 149, Dentons summary](https://www.dentonsdata.com/airline-ordered-to-compensate-a-b-c-man-because-its-chatbot-provided-inaccurate-information/)). In April 2025, Cursor's support agent, which signed as "Sam", told a developer that being logged out when switching machines was expected behavior under a one-device-per-subscription policy. No such policy existed; customers cancelled in public and the cofounder apologized ([The Register, April 18, 2025](https://www.theregister.com/2025/04/18/cursor_ai_support_bot_lies/)). The two cases share one mechanism: the agent confidently stated something it had never checked, and nobody saw it before the customer did.

This post is for the people accountable for the agents that talk to customers. It tells both cases in the sources' own words, separates the stages of the failure and says what would have changed the outcome at each one. Every source was read on October 1, 2026.

## The Air Canada case, in the parties' words

In November 2022, Jake Moffatt needed to fly from Vancouver to Toronto for his grandmother's funeral; she died on November 11. Before buying, he asked the AI agent on Air Canada's website about bereavement fares. The answer, as reproduced in the decision: "If you need to travel immediately or have already travelled and would like to submit your ticket for a reduced bereavement rate, kindly do so within 90 days of the date your ticket was issued by completing our Ticket Refund Application form" ([BD&P, February 28, 2024](https://www.bdplaw.com/insights/ai-conversations-and-chatbot-accountability-under-scrutiny-the-case-of-the-too-helpful-chatbot)). He bought tickets on November 12 and 18 and applied within the window, with the death certificate. Air Canada refused: its "Bereavement travel" page said the policy did not apply to requests made after travel.

At the tribunal, the airline argued that the AI agent was "a separate legal entity that is responsible for its own actions" ([Dentons](https://www.dentonsdata.com/airline-ordered-to-compensate-a-b-c-man-because-its-chatbot-provided-inaccurate-information/)). Tribunal member Christopher C. Rivers rejected it: "It should be obvious to Air Canada that it is responsible for all the information on its website", whether it comes from a static page or from the interactive agent. He added that "there is no reason why Mr. Moffatt should know that one section of Air Canada's webpage is accurate, and another is not" ([BD&P](https://www.bdplaw.com/insights/ai-conversations-and-chatbot-accountability-under-scrutiny-the-case-of-the-too-helpful-chatbot)). Air Canada, he wrote, had not taken reasonable care to ensure its agent was accurate. The bill: CAN$ 650.88 for the fare difference, CAN$ 36.14 in interest and CAN$ 125 in tribunal fees ([Dentons](https://www.dentonsdata.com/airline-ordered-to-compensate-a-b-c-man-because-its-chatbot-provided-inaccurate-information/)).

The amount is small. The precedent is not: a company answers for what its AI agent says the way it answers for any page on its site.

## The Cursor case, in the parties' words

In April 2025, users of Cursor, Anysphere's AI code editor, started getting logged out when they opened the program on a second machine. One of them wrote to support and received a reply signed "Sam" saying this was expected behavior under a new one-device-per-subscription policy, for security. The developer posted the exchange on Reddit and Hacker News; the April 15, 2025 thread is titled "Cursor IDE support hallucinates lockout policy, causes user cancellations" ([Hacker News](https://news.ycombinator.com/item?id=43683012)). Users announced cancellations in the same thread.

There was no policy. Cofounder Michael Truell replied: "We have no such policy. You're of course free to use Cursor on multiple machines", and explained that the answer had come from a front-line AI support agent and was incorrect; the logouts came from a bug in session handling ([The Register, April 18, 2025](https://www.theregister.com/2025/04/18/cursor_ai_support_bot_lies/); [Fortune, April 19, 2025](https://www.yahoo.com/news/customer-support-ai-went-rogue-120000474.html)). The company refunded the developer and announced that "any AI responses used for email support are now clearly labeled as such". The case is recorded as incident 1039 in the [AI Incident Database](https://incidentdatabase.ai/cite/1039/).

Here too the direct cost was small. The real cost was cancellations made in public and a week of headlines about a policy that never existed.

## The mechanism, step by step

Both agents failed the same way, in four steps. None of them is specific to airlines or code editors.

1. **The agent answered from text, not from the authoritative source.** At Air Canada, the authoritative source was the policy page; the agent generated a coherent sentence that contradicted it. At Cursor, there was no policy at all; the model filled the gap with a plausible explanation for what was actually a bug. An AI model produces the most likely sentence, and "we have a security policy" is more likely than "I don't know why you were logged out".
2. **Nothing demanded evidence before the statement went out.** A 90-day deadline and a one-device policy are statements of fact. Neither system required a statement of that kind to point at the page, the record or the tool that backed it. The sentence went out because nothing held it.
3. **Nobody could see, afterwards, what the agent had consulted.** Air Canada learned of the problem when the customer produced a screenshot. Cursor learned from Reddit. In neither case did the company hold the record of the turn: what the agent read, what it called, what it stated.
4. **The person did not know they were talking to an AI, or did not know what weight to give it.** The Canadian tribunal was direct: it is not the customer's job to know which part of the site is reliable. Cursor started labeling AI replies after the case. Since August 2, 2026, in the European Union, that label is a legal duty ([what 2026 regulation asks](/en/blog/what-2026-regulation-asks-of-an-ai-agent-that-talks-to-customers)).

There is a fifth factor the two cases do not show, but which appears in any company with agents from several vendors: one agent's wrong statement becomes the next agent's memory. The WhatsApp agent promises a deadline that does not exist; hours later the voice agent reads "deadline promised" and confirms it. One vendor's error ends up spoken by another, with more conviction at every handoff.

## What would have changed the outcome

In terms of mechanism, not product:

- **A rule about what the agent may state and with what proof.** Deadline, price, policy, promise of action: each category requires evidence in the turn itself, a value with provenance, a tool that was called, a passage of a document. The sentence about 90 days would have gone out flagged as unsupported; so would the one-device policy. The check is lexical and runs before the customer reads; it does not need a second model to judge the first.
- **The record of the turn.** What the agent read, called and stated, with the prompt version and the model. Cursor would have found the problem in its own records instead of on Reddit, and Air Canada would have seen that the agent never consulted the policy page.
- **The gap said out loud.** "I could not find the policy on this; let me check with a person" is an acceptable answer, and would have cost less than the two that went out.
- **The label.** The person needs to know they are talking to an AI and on whose behalf. It does not prevent the error; it changes what the person does with the answer.

## What we don't know

- The cases are from 2024 and 2025. We do not know how Air Canada and Cursor run their agents today; Cursor announced the labeling at the time, and we found no later public update.
- The Canadian decision comes from a small-claims tribunal, for a small amount. It is cited as a liability precedent, but we know of no higher-court decision confirming or contradicting it.
- We do not know how many subscriptions Cursor lost. The sources speak of cancellations announced in public, without a number.
- We do not know what Cursor's system consulted before answering. "Filled the gap" is the sources' reading and ours; the company only spoke of an "incorrect response".

## How Niadra solves it

In Niadra, the memory tells the agent what it may state and checks what it stated. The [claim contract](/en/produtos/conferencia) declares, per category (price, deadline, policy, promise of action), the evidence a statement needs in the turn: a value with provenance, a tool that was called, a passage anchored in a document. The check runs in the agent's process, lexically, with no extra model, at the last point before the customer; whatever does not hold is annotated, counted or, in an output that may be changed and only when the correction is unequivocal, rewritten to the value with provenance. Niadra never blocks with a generic message and never rewrites a signed document: it checks and counts, and the company decides the action. The [turn record](/en/produtos/registro) keeps what the agent read, called, showed, stated and decided, with the prompt and the model pinned, and lets you replay a real conversation in your CI without the content leaving your company.

In the [benchmark of September 30, 2026](/en/benchmark), the same rule the SDK applies was applied by the script to every system's answers: Niadra had zero values without a source in 1,068 answers, and the agent reading Niadra's context answered 98.8% of the 338 cross-channel continuity cases correctly, with the same agent and the same judge for every system. The post on [why the agent quotes the wrong price](/en/blog/why-the-agent-quotes-the-wrong-price) measures what changes when the turn carries the current state and not only what was said.

## Frequently asked questions

### Is the company liable for what its AI agent tells a customer?

In the Canadian case, yes: the tribunal treated the agent as part of the website and rejected the separate-entity argument. Each jurisdiction decides its own, but the practical rule is the same: whoever publishes the agent answers for what it states.

### Does a second model reviewing the first solve it?

It reduces it, and costs latency and money on every turn. The contract check is different: lexical, deterministic, model-free, and tied to the evidence of the turn itself (what the tool returned, what the context read served). It does not judge whether the sentence sounds right; it checks whether the number and the promise have a source.

### How do I find out how many unsupported statements my agents make today?

In count mode: the contract runs without changing the output and every statement enters the turn record with its verdict. Within a week, the company has the number per agent and per vendor, before deciding on any action.
