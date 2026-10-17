---
id: restricoes-por-pessoa
lang: en
slug: dont-discuss-amounts-with-my-wife-per-person-restrictions
title: '"Do not discuss amounts with my wife": per-person restrictions in AI agents'
description: When a customer asks that one contact not receive certain information, the rule has to hold in every agent and every channel, and only for that person. How to store a per-person restriction, how it should reach the agent, and why a contact preference is not a restriction.
order: 4
kind: guide
date: 2026-10-08T09:00:00-03:00
category: Security
icon: lock
cover: The rule holds for whoever it names
keywords: per contact information restriction AI agent, do not discuss amounts with family member, per person privacy in AI customer service, AI agent respects customer request, accounts with several contacts, contact preference AI agent, customer instruction for agents
---

A per-person restriction works when it is stored on the customer's account, says exactly who it applies to and reaches every agent that serves any contact on that account. "Do not discuss amounts with Clara" has to stop every agent, on every channel, from telling Clara an amount, and it has to let the account holder receive their own amounts as usual.

Both halves matter equally. If the rule covers fewer people than it should, information leaks. If it covers more, the agent refuses the account holder what is theirs.

## Where does this request come up?

In any account with more than one contact:

- A plan holder asks that their spouse, also registered, not receive billing amounts.
- A company partner asks that finance receive invoices but not contract details.
- A law firm's client asks that a relative who sometimes calls not be told how the case is going.

The request is usually made to a staff member, once, in a conversation about something else. After that, the account is served by a WhatsApp agent, a voice agent and a human, each from a different vendor.

## What has to be stored?

Three things:

1. **The rule, in the words of whoever asked.** "Do not discuss the amounts of the cases."
2. **Who it applies to.** The named person, stored apart from the text. When the rule names someone, it applies only to them.
3. **What it covers.** The whole account ("the cases", plural) or one object ("this case"). A rule about the account cannot end up tied to the one case that happened to be open on screen when it was said.

The rule also has to survive how it was received. When the request is made to the staff assistant and it answers "I can't record that here", the rule still stands: the person who asked decides, not the assistant's reply.

## How should the rule reach the agent?

Wording changes the outcome. We tried several forms with the same model, in the same conversation:

- "Do not discuss amounts with Clara (with this customer)" made the agent refuse amounts to the account holder, who read "this customer" as themselves.
- "Do not discuss amounts with Clara (applies to whoever it names)" still confused the smaller model.
- "Required if the speaker is Clara: do not discuss amounts. With any other contact, it does not apply" worked both ways: Clara got no amounts, and the account holder did.

The conditional form, which says when the rule applies and when it does not, was the only one the smaller model read correctly.

## Is a contact preference the same thing?

No, and mixing the two causes the same mistake of refusing the account holder. "Reach Marcos only on WhatsApp, after 10am" says how staff should contact Marcos. It does not say what may be told to him. When the preference showed up among the restrictions, as "required", the agent refused to answer Marcos himself. Kept apart, as a contact preference, it is respected when staff reach out to him and does not get in the way of serving him.

## How Niadra solves it

With Niadra, an account instruction is stored with the person it applies to, and each agent's [context](/en/produtos/contexto) shows it conditionally: what holds when that person is speaking, and that it does not apply to any other contact. The restriction covers the whole account on every channel, and contact preferences show separately, as guidance for when staff reach out to someone. [Identity](/en/produtos/identidade) ties each phone and e-mail to the right account, so the rule reaches the person even when they write from a new number. The post on [security and GDPR](/en/blog/security-and-gdpr-for-customer-memory-in-ai) covers the rest of the protections, and the documentation describes [privacy](https://docs.niadra.com/en/concepts/privacy) and [identity](https://docs.niadra.com/en/concepts/identity).

## Frequently asked questions

### What if the restricted person calls from a number memory does not know?

The rule depends on recognizing who is speaking. A new number not yet tied to the account receives nothing from the account. Once the number is tied to the person, the restriction applies to it.

### Can the restriction drop out of the context?

It cannot drop out silently. If memory cannot read some contact's instructions, the context says they were not read, instead of handing over a block without the rule.

### Does the restriction hold for agents from other vendors?

Yes. Memory is neutral: one vendor's WhatsApp agent and another vendor's voice agent read the same account instruction, as the post on [agents from different vendors](/en/blog/how-ai-agents-from-different-vendors-share-customer-history) shows.
