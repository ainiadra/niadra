---
id: custo-de-repetir
lang: en
slug: what-it-costs-when-the-customer-tells-the-same-story-on-every-channel
title: What it costs when the customer tells the same story on every channel
description: 56% of customers say they have to repeat or re-explain what they already said; 29% of contacts come back for the same reason; an assisted contact costs US$ 13.50 at the median. The calculation an executive runs with their own numbers, and why AI agents from several vendors make it bigger.
order: 3
kind: case
date: 2026-10-01T09:00:00-03:00
category: Operations
icon: clock
cover: 29% come back. US$ 13.50 each
keywords: cost of customers repeating themselves, repeat contact cost customer service, first call resolution benchmark 71%, Gartner cost per contact, customers repeat information across channels, customer effort omnichannel, AI agents ask the same questions again, shared memory across channels cost
---

More than half of customers say they have to repeat or re-explain what they already said when they speak to someone else at the same company: 56%, in a Salesforce survey of 11,000 consumers and 3,300 business buyers across 25 countries ([State of the Connected Customer, sixth edition](https://www.salesforce.com/resources/research-reports/state-of-the-connected-customer/), fielded May to July 2023). In the contact center, the metric that measures this has a name: first contact resolution sits at 71% on the industry average, which means 29% of customers come back about the same issue ([SQM Group](https://www.sqmgroup.com/resources/library/blog/fcr-metric-operating-philosophy)). And each assisted contact costs US$ 13.50 at the median, against US$ 1.84 for self-service ([Gartner, 2024, cited by Parloa](https://www.parloa.com/knowledge-hub/call-center-cost/)). This post puts those three numbers into a calculation an executive runs with their own data, and explains why AI agents from different vendors, without a common memory, make the bill bigger rather than smaller.

Written for the people accountable for service, collections and sales handled by agents. Every source was read on October 1, 2026; each one carries its sample and date.

## What customers say

The Salesforce survey measures the gap between what customers expect and what they get. In the sixth edition, with 2023 data:

| What the customer says | Share of customers |
|---|---|
| Expects consistent interactions across departments | 79% |
| Expects all company representatives to have the same information about them | 70% |
| Often has to repeat or re-explain information to different representatives | 56% |
| Feels like they are communicating with separate departments, not one company | 55% |

Source: [Salesforce, State of the Connected Customer, sixth edition](https://www.salesforce.com/resources/research-reports/state-of-the-connected-customer/), 14,300 respondents, double-blind survey from May 3 to July 14, 2023.

Zendesk, in CX Trends 2026, finds the same pattern: 74% of consumers find it frustrating to have to tell their story over and over to different agents ([Zendesk CX Trends 2026](https://cxtrends.zendesk.com/)).

## What operations measure

Frustration becomes cost when the customer comes back. The industry benchmark for first contact resolution (FCR) is 71%, according to SQM Group, which has tracked contact centers for decades; the remaining 29% return about the same issue. Among the causes of repeat contact that SQM lists, three are failures of memory, not of skill: the customer comes back to "verify or check the status of an unresolved issue", "the customer's requested action is not completed" and "the customer is redirected to a third party" ([SQM Group](https://www.sqmgroup.com/resources/library/blog/fcr-metric-operating-philosophy)). SQM attributes 49% of the errors that prevent first contact resolution to the organization's policies, processes and procedures, 38% to agent error and 13% to customer miscommunication.

The price of each return comes from Gartner. In 2019, a contact on an assisted channel (phone, chat, email) cost US$ 8.01 on average, and a self-service contact about US$ 0.10 ([Gartner, 2019](https://www.destinationcrm.com/Articles/CRM-Insights/Insight/Gartner-Survey-Finds-Self-Service-Insufficient-135436.aspx)). In 2024, the median assisted contact rose to US$ 13.50, and self-service to US$ 1.84 ([Gartner, Benchmarks to Assess Your Customer Service Costs, 2024](https://www.gartner.com/en/documents/5164231), cited by [Parloa](https://www.parloa.com/knowledge-hub/call-center-cost/)).

## The calculation, with your numbers

No survey figure replaces yours. The calculation has four lines, and the company has three of them in its own systems:

1. **Contacts per month**, across channels (voice, WhatsApp, chat, email, app).
2. **Repeat contact about the same issue within 72 hours**, per customer and across channels. If the company only measures per channel, the call that follows a WhatsApp conversation shows up as a first contact, and the number comes out smaller than it is.
3. **Share of repeat contact caused by missing context**: the customer came back to ask about what they already requested, to repeat what they already told, or because the second agent did not know about the first. This comes from sampled listening, not from the system.
4. **Cost per assisted contact**, yours, not the median.

An example, with the industry numbers and one stated assumption: 100,000 contacts a month, 29% repeat contact (SQM), half of it caused by missing context (assumption; your sampled listening gives the real figure), US$ 13.50 per contact (Gartner, 2024). That is 14,500 contacts a month that exist because the company forgot what the customer said, or US$ 195,750 a month. The example shows the shape of the calculation; the number that matters comes from your four lines.

And the calculation leaves out what does not fit in it: the customer who does not come back. Salesforce measures that 55% already feel they are talking to separate departments. Nobody measures how many gave up the second time they had to repeat an order number.

## Why AI agents from several vendors make the bill bigger

The intuition is that the AI agent fixes this, because it "remembers". It remembers what went through it. The typical 2026 operation has the voice agent on one vendor, the WhatsApp agent on another and the app agent in-house, and each keeps its own memory of the customer inside its own platform ([the 2026 memory launches](/en/blog/twilio-servicenow-aws-and-google-shipped-memory-for-ai-agents)). Three effects:

- **The repetition changes place, not size.** Before, the customer repeated the story to a human agent. Now they repeat to the WhatsApp agent what they told the voice agent, and the agent, polite and fast, asks for the order number again.
- **The repeat contact becomes invisible.** The voice agent measures its own call; the WhatsApp agent, its own conversation. Neither sees they are the same case. The company measures FCR per channel and believes it improved.
- **One agent's promise becomes another's permanent open item.** The voice agent rebooks the visit; the WhatsApp agent, unaware, offers another slot. A credit the billing agent posted in the ERP stays "under review" for the agent serving the customer, and someone posts it again.

What changes the calculation is a memory that sits outside every agent: what the customer said on one channel reaches the agent on the other before the first word, and the repeated question becomes something measured, per agent and per vendor, instead of estimated from a sample ([how to know whether the agent used the context](/en/blog/how-to-know-whether-your-ai-agent-used-the-context)).

## What we don't know

- The Salesforce survey is from 2023 and measures perception, not occurrence. A customer may "feel" they repeat more than they do.
- The 71% FCR is an industry and telephone average. Yours, per channel and per reason, may be quite different, and only your sampled listening reveals the share caused by missing context.
- Gartner's costs are medians, in dollars, from companies that take part in its benchmark. In an operation in Brazil or in the Emirates, the cost per contact is different; the shape of the calculation is the same.
- There is no public number for repetition between AI agents of different vendors. It is the number that context-use measurement produces inside each company, and that no market survey has yet.

## How Niadra solves it

Niadra is the omnichannel memory layer for a company's AI agents: it takes the events from every channel, platform and system, recognizes that they speak of the same customer, and delivers to any agent, from any vendor, the context of the task before the first word ([Context](/en/produtos/contexto)). What the customer told the WhatsApp agent at 2:02 pm reaches the voice agent at 2:07 pm, with its source beside it, and the credit posted in the ERP at 2:06 pm appears as done, not pending. After each conversation, Niadra measures what the agent used, what it asked again and what it contradicted, per agent and per vendor, because it delivered the context and received the next conversation ([Measurement](/en/produtos/medicao)). That is line 3 of your calculation, measured instead of estimated.

In the [benchmark of September 30, 2026](/en/benchmark), with the same agent and the same judge for every system, the agent reading Niadra's context answered 98.8% of the 338 cases where the customer changes channel correctly; the most used open-source memory, given one identifier per channel, answered 26.6%. Rollout is a 90-day mission with repeat contact within 72 hours as the goal written down beforehand and a control group drawn customer by customer ([the 90 days, week by week](/en/blog/the-90-day-rollout-of-shared-memory-week-by-week)). Pricing is per conversation or task, US$ 2 to 3 per thousand ([Pricing](/en/preco)): the memory of one conversation costs US$ 0.003, against an assisted contact at US$ 13.50.

## Frequently asked questions

### How do I measure repeat contact across channels if each vendor measures its own?

By person, not by channel: the call and the message from the same customer, about the same issue, within 72 hours, count as one repeat contact. That requires recognizing the same person by phone number, WhatsApp identifier and app login ([how to recognize the same customer across channels](/en/blog/how-to-recognize-the-same-customer-across-channels)).

### Doesn't a CRM with all the data solve the repetition?

The CRM holds the record and the cases; it does not hand the voice agent, in under a second, what the customer just said on another vendor's WhatsApp, and it does not measure whether the agent used it. The shared memory sits between the CRM and the agents, and points to the CRM as the official source ([memory for internal agents](/en/blog/memory-for-internal-ai-agents)).

### How much of the repeat contact can be eliminated?

We do not promise a number before measuring yours. The 90-day plan starts from the company's three numbers, picks one journey and compares, for twelve weeks, the customers who received the memory with a control group that did not.
