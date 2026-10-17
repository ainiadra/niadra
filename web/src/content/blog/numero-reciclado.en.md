---
id: numero-reciclado
lang: en
slug: when-a-phone-number-changes-hands
title: When a phone number changes hands, what should the AI agent know?
description: A recycled phone number reaches the AI agent carrying someone else's history. See how a provisional profile hands over only what the new holder said, and what brings the number back.
order: 13
kind: explainer
date: 2026-09-25T09:00:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Identity
icon: user
cover: The number is back. The person is not.
keywords: recycled phone number, recycled number AI customer service, provisional profile, customer identity by phone, AI agent reads another person's data, identity verification AI agent, shared number PBX, GDPR phone number
---

A phone number that went months without showing up calls again. For an AI agent that uses the phone as the key, it is the same customer as ever: it greets her by name, remembers the disputed bill and offers a copy. Except the carrier reassigned the number, and whoever is on the line was never a customer. The agent just read a stranger someone else's life.

This article explains what a shared memory does at that moment: it suspends the old link, opens a provisional profile for whoever holds the number now, and returns the number to its previous holder only with a proof the new holder cannot give.

## Why is the phone not enough as a key?

The phone is the most common identifier in customer service, and the most fragile. The calling number can arrive hidden or spoofed, belong to a PBX that dozens of people use, or have changed hands. A memory that treats the number as the person leaks data in the first case and merges different people in the second.

That is why each identifier carries a weight, and the phone alone is a hint: it recognizes the likely person, and sensitive data waits for stronger proof. The [article on identity across channels](/en/blog/how-to-recognize-the-same-customer-across-channels) explains the weights. Here the subject is what happens when the hint points at the wrong person.

## How does the memory notice that the number changed hands?

Two signals, neither of them a model's guess:

1. **A long silence.** A number that goes more than 180 days without showing up and comes back in an event may have been reassigned. If the same event carries no record id of the same customer (a system id, an app login, a checked ID number), the number is put in doubt.
2. **A break with the record.** A recent number the company's systems tie to another customer's record id, while its profile holds a different one, is put in doubt too. That is the carrier reporting the change before the customer calls.

In both cases the decision comes from the event that arrived and the space's rules, and it is recorded with its origin.

## What is a provisional profile?

The old link is not deleted: it stays suspended in the old profile, with all of that person's memory. The number comes back as a new identifier, and what whoever holds the number now says builds a profile of their own.

When the agent reads the context by that number, it gets only what that person said since. Nothing of the previous holder shows: not the name, not the open items, not the history. For the agent it is a first contact, with an empty context and a receipt of the read, never an error. If the new person becomes a customer, their memory grows in the provisional profile like anyone else's.

## What brings the number back to its previous holder?

Only a proof the new holder cannot give: a record id of the previous profile, from the company's systems, a login or an ordinary event of a system of record. When the CRM sends an event with the number and the former customer's record id, or she signs in to the app with login and password, the number returns to her profile and the full context returns with it.

Two things never bring the number back:

- **A code sent to the number itself.** Whoever holds the phone today would pass it. The OTP proves the person has the phone, not that they are the customer.
- **An e-mail typed in the conversation.** Anyone types an e-mail. Without a system of record or a login behind it, it is a statement, not a proof.

The question "is this Marina?" does not work either, for a different reason: it gives the name away. The agent asks for the data the real owner would type without effort, and the memory checks it without revealing anything.

## And the number many people use?

The store tablet, the PBX extension and the counter's WhatsApp are the opposite problem: one number, many people. A handle joined to three different ids of the same type within 24 hours, or to five within 30 days, is marked shared. It stops merging profiles and serving reads, and the same step undoes what it had joined: the people the number bridged before it looked shared split apart, merge by merge, each with their own history. A person with two or three e-mails over a month is still one person, and keeps the number.

## What does this change for whoever runs the service?

- The agent never greets the new holder by the old holder's name, because it never receives the name.
- The previous holder's history stays whole, waiting for the right proof, and leaves the memory only at their request.
- Every read by a number in doubt leaves a receipt: who read, what they received (nothing) and why.
- In the Console the team sees the weight of each identifier and the state of each number (active, in doubt, shared, blocked), and can merge or split with actor and reason.

## How Niadra solves it

Niadra applies these rules in the shared memory of every agent in the company: the 180 days of silence, the break with the record, the provisional profile, the return only through a record id, a login or a system of record, and the marking of a shared number with the undoing of what it joined. The agent reads the context through the SDK or MCP as always; the decision about who the person is stays in the memory, recorded with its origin, and never in the prompt.

Simulating the number that comes back after 180 days is test 4 of [ten tests to run before AI agents from several vendors go live](/en/blog/ten-tests-before-ai-agents-from-several-vendors-go-live); [Identity](/en/produtos/identidade) describes the weight of each identifier and the verification levels.

## Frequently asked questions

### Does a recycled number erase the former customer's history?

No. The link is suspended, not deleted. The history stays in the old profile, out of the context of whoever holds the number now, and comes back whole when a record id, a login or a system-of-record event ties the number to that profile again.

### Does the number's new holder become a customer?

They do, if they talk. What they say builds a provisional profile of their own, with no line from the previous holder. If a company system later ties that number to a record, the profile gets the id and stops being provisional.

### Is an SMS code not enough to confirm who it is?

To prove the person has the phone, it is. To prove they are the customer from before, it is not: the number's new holder receives the code all the same. That is why an OTP raises the conversation's verification level but never returns a number in doubt to its previous profile.

### How does the agent know it is talking to a first contact?

It receives an empty context, with the conversation's verification level and the count of withheld items, and a receipt is recorded. The agent serves as it would a new customer and asks for the record data when the conversation needs it.
