/**
 * The integration pages (/en/integracoes), in English. Typed against the Portuguese file: a key missing
 * here breaks the build. Every mechanism is the one the docs page documents, in the site's words.
 */
import type { IntegrationsDict } from './integrations.pt'

export const integrationsEn: IntegrationsDict = {
  title: 'Integrations: omnichannel memory for the agents you already have',
  description:
    "Niadra's memory in LiveKit, Pipecat, Vapi, Retell and ElevenLabs voice agents, in Twilio and WhatsApp channels, in the OpenAI Agents SDK, LangGraph, LangChain, CrewAI and Vercel AI SDK frameworks and in n8n: the context before every answer, the turns recorded and the history as a tool, with the real SDK code.",
  h1a: 'Memory for the agents',
  h1b: 'you already have.',
  sub: 'Connect the agents you already have, without changing model, prompt or vendor. Each adapter delivers the context before the answer, records the turns and opens the history as a tool. None fails the agent when Niadra is slow. Below, one page per platform, with the SDK code.',
  groups: { voice: 'Voice platforms', channels: 'Channels', frameworks: 'Agent frameworks', automation: 'Automation' },
  allDocs: 'All 36 integrations, in the documentation',
  allDocsNote: 'Agno, Mastra, Google ADK, Strands, Pydantic AI, LlamaIndex, Semantic Kernel, Bedrock, Anthropic, Google GenAI, LiteLLM, Dify, Flowise, Langflow and the others are in the documentation, with the same design.',
  crumb: 'Integrations',
  cardCta: 'See how it wires in',

  tag: 'Integration',
  docsCta: "The adapter's documentation",
  installH: 'Install',
  codeH: 'The minimal example, as the documentation has it',
  codeNote: "The same code is in {files} in the SDK repositories, where it runs in CI against the framework's real types and Niadra's emulator. To try it without Niadra's cloud, niadra-mock and NIADRA_BASE_URL=http://127.0.0.1:8765.",
  copy: 'Copy',
  copied: 'Copied',
  langs: { python: 'Python', typescript: 'TypeScript' },
  howH: 'How the adapter wires in',
  howSub: "The five primitives of every Niadra integration, in this framework's extension points.",
  prim: { context: 'Context', turns: 'Turns', tools: 'Tools', verify: 'Verification', handoff: 'Handoff' },
  receivesH: 'What the agent receives',
  receivesSub: 'The context is built when the memory changes and served ready, with no AI on the read. What another channel said during the conversation arrives at the end of the prompt, as news.',
  receives: [
    'Who the customer is, by what the conversation has proven: the verification level decides what goes in',
    'Facts, open items and promises, with the date and the channel they came from',
    'What other agents did inside the company, confirmed by the system of record',
    'Patterns computed by rule, with the evidence and the expiry',
    'The three history tools: search, timeline and open an item, bound to the customer in your code',
    'A receipt of every read, chained by SHA-256',
  ],
  receivesLink: 'See the context from the inside',
  limitsH: 'What the adapter does not do',
  faqH: 'Frequently asked questions',
  othersH: 'Other integrations',
  sharedFaq: [
    {
      q: 'Do I have to change my model, my prompt or my vendor?',
      a: "No. The adapter places the context after your instructions and the delta at the end of the prompt, in the extension points the framework already has. Your model, your prompt and your vendor stay the same, and switching any of them later does not erase the memory.",
    },
    {
      q: 'Where does the data live, and what does it cost?',
      a: 'The data stays in a single region, stated in the contract, encrypted with AES-256-GCM under a key exclusive to your company and protected in a FIPS 140-3 HSM. The price is per conversation or task in which an agent read the memory: US$ 2 to 3 per thousand, by volume, with reads, searches and system events included. Niadra is opening to companies by request, before the public launch.',
    },
  ],

  pages: {
    livekit: {
      name: 'LiveKit Agents',
      title: 'Memory for LiveKit voice agents: context before the first word',
      description:
        'How to give customer memory to a LiveKit Agents voice agent, in Python and Node: the first read starts when the caller joins the room, every turn gets the context before the model call, and the history tools go bound to the caller. SDK code, limits and what the agent receives.',
      h1a: 'Memory for voice agents',
      h1b: 'on LiveKit.',
      sub: "The LiveKit agent starts the call knowing who is on the line. The first read starts when the caller joins the room and is waited for up to 1.5 s, while the phone rings. On every turn, the context comes from the SDK's memory at once, and what the partial transcript anticipated arrives within 200 ms. None of it fails a turn.",
      problem: [
        "The LiveKit session knows everything that happened in this call and nothing that happened before it: yesterday's complaint on WhatsApp, the credit the billing agent posted in the ERP, the promise made by another vendor's agent. The chat context's history dies with the room.",
        "Niadra is the memory outside the room. The adapter reads the customer's context before each model call, records every final utterance as a turn and hands over the three history tools bound to the SIP number. What this call produces becomes memory for tomorrow's WhatsApp agent, from any vendor.",
      ],
      how: {
        context: "The first read starts when the caller joins the room (begin()) and the first model call waits for it within 1.5 s (ready()). In Python, every model call after that gets the context in llm_node, on a copy of the chat context, so nothing piles up in the agent's history and LiveKit's preemptive generation still matches; a realtime model does it in on_user_turn_completed. In Node, in onUserTurnCompleted. Each user_input_transcribed, interim or final, sends the turn so far with prefetch(), in the background.",
        turns: "The session's conversation_item_added event records each final customer transcript, with the STT confidence, and each agent answer, with the usage the model reported. The session's close ends the conversation.",
        tools: "The three history tools, as function tools with the kit's schemas, bound to the caller. history_tools=False leaves them out.",
        verify: "attestation= (Python) or verify: attestationProof(...) (Node) with the carrier's STIR/SHAKEN level: A proves V2, B and C prove V1. LiveKit does not read that header itself: map the SIP header to a participant attribute and pass it.",
        handoff: 'When the session moves to another agent, handoff("agent"), and the next NiadraAgent gets the same conversation. transferred_to_human() on a SIP or warm transfer to a person.',
      },
      limits: [
        'Nothing here fails a turn: slots that do not arrive within 200 ms are left out and the turn goes out with the pinned body; a failure to record is logged without content.',
        "The STIR/SHAKEN attestation is not among LiveKit's sip.* attributes. Without the header mapping, the read is at V0 and carries only what the policy releases at that level.",
        'In Python, the livekit and openai-agents extras pin incompatible versions of a shared dependency: install one per environment.',
        "Tested against livekit-agents 1.8.3 and @livekit/agents 1.9.0, with the LLM, STT and TTS replaced by fakes and Niadra on the emulator, in each SDK's CI.",
      ],
      faq: [
        {
          q: "Does the context delay the agent's first word?",
          a: "The first read starts the moment the caller joins the room, before the agent speaks, and is waited for up to 1.5 s, the time of a cold connection plus the compilation. If it does not arrive, the greeting goes out without the context, and it enters on the next turn. In a conversation already open, the pinned body comes from the SDK's memory at once.",
        },
        {
          q: 'What if Niadra gets slow in the middle of the call?',
          a: 'The turn goes out with the pinned body and without the slots that did not arrive within 200 ms. The read goes on in the background, revalidates the body and leaves the delta for the next turn. The call never waits for Niadra.',
        },
        {
          q: 'Does it work with a realtime model, audio to audio?',
          a: 'It does. With a realtime model, the adapter delivers the context in on_user_turn_completed, the hook LiveKit itself documents for RAG. With an STT, LLM and TTS pipeline, in llm_node.',
        },
      ],
    },

    pipecat: {
      name: 'Pipecat',
      title: 'Memory for Pipecat voice agents: one processor between the user and the LLM',
      description:
        'How to give customer memory to a Pipecat agent: a FrameProcessor between the user aggregator and the LLM reads the context on every LLMContextFrame, records the turns through the aggregators and hands over the history tools as FunctionSchemas. SDK code, limits and what the agent receives.',
      h1a: 'Memory for voice agents',
      h1b: 'on Pipecat.',
      sub: "A frame processor between aggregators.user() and the LLM. On every LLMContextFrame it places the customer's context where it belongs, with the pinned body from the SDK's memory and the slots the partial transcript anticipated. observe(aggregators) records the turns. The pipeline never stops because of the memory.",
      problem: [
        "Pipecat's LLMContext is this call's context: the messages since the StartFrame. What the customer told the WhatsApp agent yesterday, what billing resolved this morning and the promise the human agent made last week are not in it, and no per-message memory service fixes that, because each one keeps the memory of a single agent.",
        "Niadra comes in as a processor: it reads the customer's compiled context, pinned per call, and places it right after the system messages, with other channels' turns at the end. The blocks of the previous turn are taken out first, so the shared context never piles them up. A speculative inference gets them too, in its provisional copy.",
      ],
      how: {
        context: "The pipeline's StartFrame starts the first read (begin()), and the first inference waits for it within 1.5 s (ready()). On each LLMContextFrame, between the user aggregator and the LLM, context() on the voice view: the pack goes right after the leading system or developer messages and the turn_block at the end. memory.prefetcher(), right after the STT service, sends the turn so far with prefetch() on each InterimTranscriptionFrame and TranscriptionFrame, holding no frame.",
        turns: "observe() subscribes to the aggregators: each user message written to the context (on_user_turn_message_added, final in cascade and realtime modes) is the customer's turn and each finished assistant turn (on_assistant_turn_stopped) is the agent's. EndFrame and CancelFrame end the conversation.",
        tools: "history_tools() gives the three history tools as FunctionSchemas that carry their own handlers, so the LLM service registers them from the context. Their JSON Schemas are the kit's.",
        verify: "attestation= with the carrier's STIR/SHAKEN level (A, B, C, or Twilio's StirVerstat), recorded once before the first context.",
        handoff: 'transferred_to_human() and transferred_to_agent() record the transfer; call them where the pipeline, or Pipecat Flows, hands the call over.',
      },
      limits: [
        'The processor always passes the frame on, even when Niadra fails: the pipeline never stops because of the memory.',
        "It uses LLMContext and LLMContextFrame, Pipecat's universal context since 1.9; OpenAILLMContext and LLMMessagesFrame are no longer in Pipecat's main and are not handled.",
        "Python 3.11 or newer only, because Pipecat asks for it, and Python only: the JavaScript packages of Pipecat and Daily are browser clients, where a Niadra key must never go. The pipecat and crewai extras pin incompatible versions of a shared dependency: install one per environment.",
        'Tested against pipecat-ai 1.11.0 with a real pipeline, a fake LLM and frames pushed by hand, without audio and without network.',
      ],
      faq: [
        {
          q: 'Where does the processor go in the pipeline?',
          a: "Between the user aggregator and the LLM: Pipeline([transport.input(), stt, user, memory, llm, tts, transport.output(), assistant]). The prefetcher goes right after the STT, so the customer's memory is open when the utterance ends.",
        },
        {
          q: 'Is it a search per message, like a vector memory service?',
          a: "No. The reading is Niadra's: one pack pinned per call, compiled when the memory changes, served from the SDK's memory on every turn, with slots for the turn's words when they ask for them. The history search stays a tool, for the model to call when the context does not answer.",
        },
        {
          q: 'What if Niadra takes longer than the turn can bear?',
          a: 'The frame goes on with the pinned body and without the slots that did not arrive within 200 ms; the read goes on in the background and the delta enters on the next turn. With Niadra down, the frame goes on without context, never with an error.',
        },
      ],
    },

    vapi: {
      name: 'Vapi',
      title: 'Memory for Vapi assistants: context on the assistant-request, through one server URL',
      description:
        'How to give customer memory to a Vapi assistant: one server URL answers the assistant-request with the context, runs the history tools on tool-calls, records the transfer and reads the end-of-call-report as turns. SDK code, limits and what the assistant receives.',
      h1a: 'Memory for assistants',
      h1b: 'on Vapi.',
      sub: 'Vapi posts every server message to one URL, and one handler answers the ones that matter: the context on the assistant-request, while the assistant is not answering yet; the history tools on tool-calls; the transfer destination; and the end-of-call-report as turns. No Vapi package.',
      problem: [
        "The Vapi assistant gets the caller's number and a prompt. What that person told another vendor on WhatsApp yesterday, the credit the billing agent posted and the rebooked visit stayed in other systems. The assistant's knowledge base is about the company, not about this customer.",
        "Niadra answers the assistant-request with the customer's context, read while the phone rings: with a transient assistant, inside model.messages, right after the system messages; with a saved assistant, in the niadra_context variable, for a prompt that says {{niadra_context}} after the instructions. What the call produces becomes memory for any other agent.",
      ],
      how: {
        context: "On the assistant-request, the adapter starts the caller's first read (begin(), after the attestation when you pass one), waits for it within 1.5 s and answers the assistant. With a transient assistant (assistant=), the pack goes into model.messages right after its system messages; with a saved assistant (assistant_id=), it goes in assistantOverrides.variableValues.niadra_context.",
        turns: 'The end-of-call-report records each user and assistant message as a turn at its moment and ends the conversation. Idempotency keys come from the call, so a redelivered report records nothing twice.',
        tools: 'tool_definitions(url) (Python) and vapiTools() (TypeScript) produce the history tools as Vapi function tools, word for word as the kit; tool-calls runs each one for the caller of the call, never for an argument of the model.',
        verify: "The assistant-request calls verify() when you pass the carrier's attestation.",
        handoff: 'transfer-destination-request and handoff-destination-request record the transfer, to a person or to another assistant of a squad, and answer the destination your destination= (Python) or transfer (TypeScript) function returns; a forwarded call in the final report also becomes a handoff.',
      },
      limits: [
        'Requests without the server secret (x-vapi-secret, or Authorization: Bearer in TypeScript) answer 401: they would read customer data.',
        'Niadra slow or down never fails the call: the assistant starts without the context and a tool answers that the history is unavailable.',
        "The carrier's attestation does not come in Vapi's message; without it, the read is at V0 and carries only what the policy releases at that level.",
        'Tested with recorded payloads in the public format and a test secret; no Vapi account is needed.',
      ],
      faq: [
        {
          q: 'Saved assistant or transient assistant?',
          a: "Both work. With the assistant saved in Vapi's dashboard, the context arrives in the niadra_context variable, and your prompt says {{niadra_context}} after the instructions. With the transient assistant built on the server, the context goes into model.messages. The rest is the same.",
        },
        {
          q: 'Can the history tool read the wrong customer?',
          a: 'No. The handler binds the kit to the caller of the call the tool-calls message carries, never to an argument of the model. The model picks the query; who the customer is comes from Vapi.',
        },
        {
          q: 'What happens if Niadra is down when the call comes in?',
          a: 'The assistant-request is answered all the same, without the context, and the assistant takes the call as it does today. A history tool answers that the history is unavailable. The end-of-call-report is recorded when Niadra is back, through the idempotency keys of the call.',
        },
      ],
    },

    retell: {
      name: 'Retell AI',
      title: 'Memory for Retell voice agents: context as a dynamic variable on the inbound webhook',
      description:
        'How to give customer memory to a Retell voice agent: the inbound call webhook answers niadra_context as a dynamic variable, the custom functions run the history tools, call_ended becomes turns and, in TypeScript, the custom LLM websocket gets the context. SDK code and limits.',
      h1a: 'Memory for voice agents',
      h1b: 'on Retell.',
      sub: "Retell reaches your server three ways, and the adapter answers each: the inbound call webhook with the context as a dynamic variable, the Retell LLM's custom functions with the history tools and the agent webhook with the call's events. Every request carries x-retell-signature; without a valid one, 401.",
      problem: [
        "The Retell agent gets the dynamic variables your inbound webhook returns. Without a memory outside it, those variables are what your CRM knows at ring time, not what the customer told the WhatsApp agent yesterday, nor what the billing agent did today.",
        "Niadra answers the inbound webhook with niadra_context, read while the phone rings, and with the agent's own notes. The prompt says {{niadra_context}} after the instructions. On an outbound call, outbound() gives the same variables and a metadata for create_phone_call.",
      ],
      how: {
        context: "The inbound webhook starts the caller's first read (begin(), after the attestation when you pass one), waits for it within 1.5 s and answers the niadra_context variable. TypeScript also answers niadra_turn, with other channels' turns and the delta, and the custom LLM sends the caller's last utterance as query and the utterance so far with prefetch() on each update_only.",
        turns: 'On call_ended, each utterance of transcript_object becomes a turn at its moment in the call, and the conversation ends. Idempotency keys come from the call, so a redelivered event records nothing twice. The other events answer 200.',
        tools: "tool_configs(url) (Python) and handlers.toolConfigs({ url }) (TypeScript) produce the history tools as Retell custom tools, the Retell LLM's general_tools, with the kit's names, descriptions and schemas; the custom function runs each one for the customer of the call the request carries.",
        verify: "attestation= (Python) or verify (TypeScript), a function of the inbound payload (reading custom_sip_headers, for instance), returns the carrier's STIR/SHAKEN level; it goes to verify() before the first context.",
        handoff: 'A transferred call (transfer_started, or call_transfer on call_ended) becomes a handoff to a person, once.',
      },
      limits: [
        "In Python, niadra is a Niadra or an AsyncNiadra: with the sync client, call the *_sync twins. override_agent_id= (Python) or inboundFields(call) (TypeScript) add fields to the inbound webhook's answer, such as the agent that takes the call.",
        "In TypeScript, each call's customer and verification level sit in a store between the webhooks (in memory by default; pass your own for more than one instance), and otherTool(name, args, call) serves the custom functions that are not Niadra's.",
        'Niadra slow or down never fails a call: the inbound webhook answers empty variables, a tool answers that the history is unavailable, and the webhook still answers 200.',
        "Tested with payloads in Retell's public format and signatures computed in the test itself, against the emulator; in TypeScript the handlers also run on Deno, Bun and workerd.",
      ],
      faq: [
        {
          q: 'Does it work on outbound calls?',
          a: "It does. In Python, outbound(to_number) returns the dynamic variables and a metadata for Retell's create_phone_call, with the Niadra conversation id; in TypeScript, call_started keeps the customer of outbound and web calls. The called number is the subject.",
        },
        {
          q: "Can I use my own LLM through Retell's websocket?",
          a: "In TypeScript, yes: the custom LLM gets the context, sends the caller's last utterance as query and anticipates the read with prefetch() on each update_only, so the turn arrives with its slots ready.",
        },
        {
          q: 'What if the customer is identified by something other than the number?',
          a: "Pass subject=, a function of the call, and the subject becomes whatever you return: the app id, the e-mail, the CRM customer. By default, it is the caller's number on an inbound call and the called number on an outbound one.",
        },
      ],
    },

    elevenlabs: {
      name: 'ElevenLabs Agents Platform',
      title: 'Memory for ElevenLabs agents: three webhooks, from call start to post-call',
      description:
        'How to give customer memory to an ElevenLabs Agents Platform phone agent: the initiation webhook hands over the context as a dynamic variable, the server tools run the history kit for whoever is on the line, and the post-call webhook records the transcript turn by turn. SDK code, secrets and limits.',
      h1a: 'Memory for agents',
      h1b: 'on ElevenLabs.',
      sub: 'A call to an ElevenLabs agent reaches your server through three webhooks, and the adapter answers each: the initiation one hands over the context as a dynamic variable, the server tools run the history for whoever is on the line, and the post-call one records the transcript and ends the conversation. On the server side, not the device.',
      problem: [
        "The ElevenLabs agent knows what the prompt says and what the call reveals. What the customer told another channel, and what another agent did inside the company, is outside the prompt, and the agent's knowledge base speaks of the company, not of this person.",
        'The initiation webhook ("Fetch initiation client data from a webhook") brings caller_id, called_number, agent_id, call_sid and conversation_id. Niadra opens the conversation, reads the context while the agent has not spoken yet and answers conversation_initiation_client_data with the niadra_context variable, which the prompt uses after the instructions.',
      ],
      how: {
        context: "The initiation webhook brings the call's identifiers; the adapter opens the conversation, starts the first read (begin(), after the attestation when you pass one), waits for it within 1.5 s and answers the dynamic variable niadra_context. Put {{niadra_context}} in the agent's prompt, after your instructions; in TypeScript, also {{niadra_turn}} where other channels' turns should go.",
        turns: 'The post_call_transcription webhook checks ElevenLabs-Signature (HMAC-SHA256 of "<t>.<body>", 30 minutes of tolerance) and records each item of transcript[] as a turn at its moment in the call, with the model\'s usage; then end(). A redelivered webhook records nothing twice.',
        tools: "tool_configs(url) (Python) and toolConfigs() (TypeScript) produce the three history tools as ElevenLabs webhook tools. The call's identifiers (system__call_sid, system__conversation_id, system__caller_id) are filled by ElevenLabs, never by the model, and the handler binds the kit to that caller.",
        verify: "The initiation webhook calls verify() when you pass the carrier's attestation; without it, the read is at V0.",
        handoff: 'transfer_to_agent and transfer_to_number arrive in the post-call webhook and become a handoff.',
      },
      limits: [
        'The initiation webhook and the server tools return customer data, so they require the X-Niadra-Secret header you configure in ElevenLabs (shared_secret); without it, 401.',
        'Niadra slow or down never fails the call: the initiation answers an empty context, a tool answers that the history is unavailable, and the post-call webhook still answers 200.',
        "The carrier's attestation does not come in ElevenLabs' webhook; without it, the read is at V0.",
        'Tested with recorded payloads in the public format of the three webhooks and signatures computed in the test itself; no ElevenLabs account is needed.',
      ],
      faq: [
        {
          q: 'Does the context arrive before the agent speaks?',
          a: 'It arrives through the initiation webhook, which ElevenLabs calls before the agent says its first word. The adapter waits for the read up to 1.5 s; if it does not arrive, it answers an empty context and the call goes on.',
        },
        {
          q: 'Can anyone call the server tools?',
          a: 'No. They require the X-Niadra-Secret header, configured in the ElevenLabs agent, and answer 401 without it. The call\'s identifiers come from ElevenLabs itself, so the model never picks whose history it is.',
        },
        {
          q: 'Where does the code go: Python or TypeScript, on which server?',
          a: 'The handlers are pure functions of the body and the headers: they serve on FastAPI, Flask, Django, Hono, a Lambda or a Worker. The example shows FastAPI and Hono.',
        },
      ],
    },

    twilio: {
      name: 'Twilio',
      title: 'Memory for agents on Twilio: voice, SMS and WhatsApp, with StirVerstat as proof',
      description:
        "How to give customer memory to an agent that answers through Twilio: the ringing webhook starts the context read, the carrier's StirVerstat proves the caller before the first context, and SMS and WhatsApp messages become turns by MessageSid. SDK code, signature and limits.",
      h1a: 'Memory for agents',
      h1b: 'on Twilio.',
      sub: "The adapter reads the Programmable Voice and Messaging webhooks, SMS and WhatsApp, and in TypeScript the Conversations ones: it checks X-Twilio-Signature, finds the customer, the call or thread id and what the carrier attested, and records the inbound turn. It never answers TwiML: the reply belongs to your agent.",
      problem: [
        "Twilio delivers the call, the number and, with STIR/SHAKEN, an attestation that the number was not spoofed. It does not deliver what that customer said last week on WhatsApp nor what the orders agent did yesterday, and your voice agent starts from zero on every CallSid.",
        'Niadra uses what Twilio proves. The ringing webhook starts the read in the background, call.verify() records the StirVerstat (A proves V2, B and C prove V1) and conversation.ready() waits for the context while the phone rings. On Messaging, every message becomes a turn by MessageSid, and the WhatsApp conversation reads the same memory the call fed.',
      ],
      how: {
        context: "call.conversation() opens the call's conversation (CallSid as id, the caller's number as subject) and, on the ringing webhook (ringing), starts its first read in the background; call.verify() restarts it at the level the attestation proved; conversation.ready() waits for it within 1.5 s, while the phone rings, and your code hands the pack to the agent that takes the call.",
        turns: "parse_message() reads a Messaging webhook: MessageSid is the idempotency key, From (whatsapp:+55... or a phone) and WaId the sender's ids, Body the text; message.record() records the customer's turn. On voice, call.ended() ends the conversation when the status callback says completed (or busy, failed, no-answer, canceled); in TypeScript, recordTwilioInbound() records each recognized sentence.",
        tools: "The kit's, through the conversation: search, timeline and open an item, bound to the customer in your code.",
        verify: "call.verify() (Python) and verifyTwilio() (TypeScript) record Twilio's StirVerstat: TN-Validation-Passed-A proves V2, B and C prove V1; a missing or failed validation proves nothing. A recorded message carries verification_hint V1.",
        handoff: "The conversation's handoff(), where your flow transfers.",
      },
      limits: [
        "A request without the right signature returns None (Python) or no request (TypeScript): answer 403 and record nothing. Both SDKs check the signature the way Twilio computes it: the Base64 HMAC-SHA1, with your auth token, of the full URL Twilio called followed by every POST parameter, sorted by name.",
        'The adapter produces no TwiML and connects no call: connect_your_voice_agent() in the example is your code.',
        'The signature covers the full URL Twilio called; behind a proxy that changes the host or the scheme, pass the public URL.',
        'Tested with recorded payloads and signatures computed in the test; no Twilio account is needed.',
      ],
      faq: [
        {
          q: 'Does it serve ConversationRelay and Media Streams?',
          a: 'The adapter handles the voice webhooks, from the ring to the status callback, and leaves the media to your agent. With ConversationRelay, your code hands the context to the agent that takes the call; with Media Streams, the Pipecat adapter receives the CallSid and the number the same way.',
        },
        {
          q: 'Does the same memory serve the call and WhatsApp through Twilio?',
          a: 'It does. The call comes in by CallSid with the number as subject; the WhatsApp message comes in by MessageSid with the same number and the WaId. Niadra recognizes the same person, and the WhatsApp agent reads what the call produced.',
        },
        {
          q: "Does a spoofed number get the victim's context?",
          a: 'It gets what the policy releases at the level the call proved. Without a valid StirVerstat, the read stays at V0, and the initial policy releases nothing at that level. Sensitive data waits for the proof your policy requires.',
        },
      ],
    },

    whatsapp: {
      name: 'WhatsApp Cloud API',
      title: "Memory for WhatsApp agents on Meta's Cloud API: the webhook becomes turns",
      description:
        "How to give customer memory to a WhatsApp agent on the Cloud API: the adapter checks the signature of Meta's webhook, extracts each message with the wa_id as identifier, records the turn by wamid and records the reply by the id Meta returns. The context is read before answering. SDK code and limits.",
      h1a: 'Memory for',
      h1b: 'WhatsApp agents.',
      sub: "The WhatsApp Cloud API adapter only translates: it checks the signature of Meta's webhook, extracts each message with the customer's identifier and the item ready to record, and records the agent's reply from the send API's answer. It never sends a message: your code talks to the Graph API.",
      problem: [
        "The Cloud API delivers the wa_id of whoever writes and the message. The WhatsApp agent answers with what the prompt knows and, at most, with this window's conversation. Yesterday's call, taken by another vendor's voice agent, and the visit the internal agent rebooked in the system are not here.",
        "With Niadra, each message becomes a turn by wamid, the conversation opens with the wa_id as subject, and chat.context() returns the customer's context before the reply: facts, open items, promises and what other agents did, in chat format. The reply goes in by the id Meta returns.",
      ],
      how: {
        context: "Your code reads context() in the conversation the adapter helps open (wa-<wa_id> as id, the wa_id as subject) and builds the prompt; the adapter does not touch the model. The flush() after record() makes the turn, and the V1 it proves, arrive before the first read.",
        turns: "parse_webhook() (Python) and readWhatsApp() (TypeScript) check X-Hub-Signature-256 (HMAC-SHA256 of the raw body with the app secret) and return the messages of every entry and change, oldest first; message.record() and recordInbound() record the customer's turn with the wamid as idempotency key, because Meta retries webhooks for days. sent() and recordOutbound() record the reply with the wamid the Cloud API returned.",
        tools: "The kit's, through the conversation (chat.tools()); nothing channel-specific.",
        verify: "The recorded turn carries verification_hint V1: it came from that number. It raises the session to V1 for the next read. subscribe() (Python) and whatsAppChallenge() (TypeScript) answer Meta's subscription challenge on the GET.",
        handoff: "The conversation's chat.handoff(), when your flow transfers.",
      },
      limits: [
        'The adapter never sends a message nor calls the Graph API; it translates the webhook and records what your code sent.',
        'Media arrives as a reference (id, MIME type, SHA-256): download the bytes from the Graph API, hand them to upload_media() and pass the result as upload=, with your transcript of a voice note. The text of a PDF, an image or an Office file is read inside the cell only, and only when the space lists the type; the type comes from the bytes, never from the declared MIME type.',
        'Each WhatsAppMessage carries the wa_id and, for a WhatsApp username, the business-scoped user id; statuses and other webhook fields are left out.',
        "Tested with public payloads in Meta's format and signatures computed in the test; no Meta account is needed.",
      ],
      faq: [
        {
          q: 'Does it work with a WhatsApp provider, rather than the Cloud API directly?',
          a: 'Through Twilio, yes, with the Twilio adapter, which reads the WaId on the Messaging webhook. With other providers, use the SDK directly: the conversation opened with the wa_id as subject, customer() and agent() for the turns. The design is the same.',
        },
        {
          q: 'Meta retries the webhook. Does the turn go in twice?',
          a: "No. The wamid is the turn's idempotency key, so a webhook redelivered days later records nothing. The agent's reply uses the wamid the Cloud API returned, by the same rule.",
        },
        {
          q: 'Does the WhatsApp number prove who the person is?',
          a: 'It proves the message came from that number: the turn raises the session to V1. Data your policy reserves for V2 or V3 (a login, a confirmed code) stays withheld until the proof arrives.',
        },
      ],
    },

    openaiAgents: {
      name: 'OpenAI Agents SDK',
      title: 'Memory for the OpenAI Agents SDK: model input filter, hooks and function tools',
      description:
        "How to give customer memory to an OpenAI Agents SDK agent, in Python and JavaScript, through the SDK's own extension points: call_model_input_filter for the context before every model call, RunHooks for the turns, FunctionTool for the history; in JavaScript, NiadraSession and dynamic instructions. SDK code and limits.",
      h1a: 'Memory for agents',
      h1b: 'on the OpenAI Agents SDK.',
      sub: "In Python, NiadraAgentsMemory gives you run_config(), the filter that injects the context before every model call, hooks, which record the turns, and tools. In JavaScript, NiadraSession, niadraInstructions(), niadraTools() and niadraRunHooks(). The SDK's Session stays yours.",
      problem: [
        "The Agents SDK's Session stores this agent's items: what it saw and produced in each run. It is one agent's working memory, keyed by a session of your application. What the same person told another vendor's voice agent, or what the billing agent did in the ERP, is in no session at all.",
        "Niadra keeps the company's derived memory of the customer, not a copy of each item. The call_model_input_filter places the pack after the agent's instructions and the turn_block after the input, on every model call; the hooks record what the customer said and what the agent answered, with the usage reported. A handoff between the run's agents carries the same memory.",
      ],
      how: {
        context: "In Python, run_config() sets call_model_input_filter, which runs right before every model call: the pack goes after the agent's instructions and the turn_block after the input, as a system message; a filter you already had runs first. In JavaScript, niadraInstructions(base, conversation) makes the instructions dynamic: your text, then the agent's notes and the pack, then the suffix.",
        turns: "In Python, hooks (a RunHooks) records the customer's new messages when the model is first called for them, and the agent's answer with the usage reported; customer turns are keyed by position, so the history a Session replays on the next run is never recorded twice. In JavaScript, NiadraSession is a Session for run(agent, input, { session }): it keeps the items in the session you give it and records both sides.",
        tools: "tools: the three history tools as FunctionTools with the kit's names, descriptions and schemas, bound to the customer. In JavaScript, niadraTools(conversation).",
        verify: 'What your app proved (a login, an OTP) goes to conversation.verify() before the run.',
        handoff: 'An SDK handoff between agents records handoff("agent"); give every agent of the run the same memory. In JavaScript, niadraRunHooks(runner, conversation) records the handoffs.',
      },
      limits: [
        "The Python adapter does not implement the SDK's Session: a Session stores the agent's own items, and Niadra keeps derived memory, not a copy of each item. Use any Session next to it. In JavaScript, NiadraSession wraps the session you choose.",
        'Nothing here fails a run: Niadra slow or down leaves the instructions yours alone.',
        'In Python, the openai-agents extra pins versions incompatible with livekit, crewai and litellm; install one per environment.',
        'Tested against openai-agents 0.22.3 and @openai/agents 0.18.0 with the model replaced by a fake and Niadra on the emulator.',
      ],
      faq: [
        {
          q: "Does this replace the SDK's Session?",
          a: "No. The Session is this agent's working memory, and it stays yours, in memory or in SQLite. Niadra is the company's memory of the customer, derived from the events of every channel and agent. The two live in the same run.",
        },
        {
          q: 'With several agents and handoffs, who reads the memory?',
          a: 'Every agent of the run gets the same memory: the filter runs on every model call, from any agent, and the handoff between them is recorded as a transfer to the next agent, with the conversation open.',
        },
        {
          q: "Is the context cached by the provider?",
          a: "The pack goes after the instructions, which remain the prompt's prefix, and comes pinned per conversation, so the start of the prompt repeats call after call and the provider reuses it. What changed enters as a delta, at the end.",
        },
      ],
    },

    langgraph: {
      name: 'LangGraph',
      title: "Memory for LangGraph agents: create_agent middleware, with nothing in the graph's state",
      description:
        "How to give customer memory to a LangGraph agent: NiadraMiddleware wraps every model call of create_agent, puts the context in the system message, records the turns and brings the history tools, without writing anything to the state the checkpointer stores. SDK code and limits.",
      h1a: 'Memory for',
      h1b: 'LangGraph agents.',
      sub: "NiadraMiddleware wraps every model call of the agent built by langchain.agents.create_agent: the pack goes into the system message, the turns are recorded and the history tools come along. Nothing is written to the graph's state: the checkpointer never stores a pack.",
      problem: [
        "LangGraph's checkpointer persists one thread's state: this graph's messages, for this thread. A long-term memory store keeps what this agent decided to remember, in a namespace of your application. Neither knows what the customer told another vendor's voice agent nor what the billing agent did in the ERP.",
        "Niadra stays outside the graph. The middleware appends the customer's context to the system message, with your instructions first, and the turn_block after the messages, on every model call. The state stays yours alone, and the checkpointer stays small.",
      ],
      how: {
        context: 'The pack is appended to the system message, which keeps your instructions first, and the turn_block goes after the messages. Nothing is written to the graph\'s state. For the older create_react_agent, pre_model_hook=pre_model_hook(conversation) gives the model the same messages through llm_input_messages, again without touching state.',
        turns: "The customer's messages are recorded before the call, keyed by their position in the conversation, so the history the checkpointer replays is never recorded twice; the model's answer, with its usage_metadata, after it.",
        tools: 'The middleware brings the history tools, and the agent memory tools, as its own tools, so create_agent offers them without you listing them.',
        verify: 'conversation.verify() before invoking the agent.',
        handoff: 'transferred_to_agent() and transferred_to_human() record the transfer; call them where the graph hands the conversation over.',
      },
      limits: [
        "No BaseStore of its own: Niadra is not the graph's state store.",
        "No separate node for LangGraph.js: withNiadraContext() in @niadra/sdk/langchain already gives the model node its context without writing it into the graph's checkpointed state.",
        'Nothing here fails the agent: with Niadra slow or down, the model call goes on without the pack.',
        'Tested against langgraph 1.2 and langchain 1.4 with a fake chat model and Niadra on the emulator.',
      ],
      faq: [
        {
          q: 'Can I keep the checkpointer and the store I already have?',
          a: "You can, and should. The middleware touches neither the graph's state nor the store: the context enters the model call and leaves with it. What Niadra keeps is the company's memory of the customer, derived from the events of every channel.",
        },
        {
          q: 'And in LangGraph.js?',
          a: 'withNiadraContext() from @niadra/sdk/langchain gives the model node its context right before the call, without writing to state; the NiadraCallbackHandler records the answers. The full example is on the LangChain page.',
        },
        {
          q: "Can every run of the agent become a turn record?",
          a: "It can, with turn records switched on in the space: before_agent opens the turn, wrap_tool_call records each tool call and after_agent closes it with the model calls and their tokens. In a replay in your CI, the graph's tools answer from the record.",
        },
      ],
    },

    langchain: {
      name: 'LangChain',
      title: 'Memory for LangChain and LangGraph.js: a context runnable, a callback and the history tools',
      description:
        'How to give customer memory to a LangChain chain in Python and to a LangGraph.js graph: context_runnable() places the context in the prompt messages, NiadraCallbackHandler records the turns and history_tools() hands over the kit as StructuredTools; in JavaScript, niadraContext(), withNiadraContext() and niadraTools(). SDK code and limits.',
      h1a: 'Memory for',
      h1b: 'LangChain and LangGraph.js.',
      sub: "In Python, with langchain-core only: context_runnable() places the context in the prompt's messages, NiadraCallbackHandler records the turns and history_tools() hands over the kit as StructuredTools. In JavaScript, @niadra/sdk/langchain brings niadraContext() for LCEL chains, withNiadraContext() for LangGraph.js nodes, the callback handler and niadraTools().",
      problem: [
        "LangChain's conversation memory is this chain's history, in a session of your application. It does not cross vendors, does not cross channels and does not know what a system of record changed. Every agent in the company keeps its own version of the customer.",
        "Niadra comes in as a runnable in the middle of the chain: the prompt's messages go in, and come out with the customer's context as a SystemMessage right after the system messages and the turn_block at the end. The callback records what the customer said and what the model answered, with the usage_metadata.",
      ],
      how: {
        context: "In Python, context_runnable() takes the prompt's messages, a list or a PromptValue, and returns them with the pack as a SystemMessage right after the leading system messages and the turn_block as a SystemMessage at the end; with_context() and awith_context() do the same for a list you build. In JavaScript, niadraContext(session) is a runnable for LCEL chains (niadraContext(convo).pipe(model)); withNiadraContext(session, messages) does the same inside a LangGraph.js node, right before the model call, so nothing lands in the graph's state.",
        turns: "NiadraCallbackHandler records the customer's messages when a chat model starts, keyed by position, and the model's answer with its usage_metadata when it ends. In JavaScript, it records the answers with the usage_metadata LangChain standardizes; answers that only call tools record nothing.",
        tools: "history_tools(): StructuredTools with the kit's names, descriptions and schemas, bound to the customer. In JavaScript, niadraTools(session).",
        verify: 'conversation.verify() before the call.',
        handoff: 'conversation.handoff() where the chain transfers.',
      },
      limits: [
        'No BaseChatMessageHistory or BaseStore of its own: Niadra is not the state store of the chain or the graph, and the checkpointer never stores a pack.',
        'Nothing here fails the chain: with Niadra slow or down, the messages go to the model as they came.',
        'Tested against langchain-core 1.6 and @langchain/core 1.2 with a fake chat model and Niadra on the emulator.',
      ],
      faq: [
        {
          q: 'Do I need all of langchain or only the core?',
          a: "In Python, only langchain-core: the runnable, the callback and the tools use the core's interfaces. The example uses langchain-openai for the model, but any chat model works.",
        },
        {
          q: "Does LangChain's conversation memory keep working?",
          a: "It does, and it is yours. The chain's history is what the model saw in this session; Niadra is what the company knows of the person, from every channel and system. The callback records the turns once, by position, so the history replayed on the next call does not go in twice.",
        },
        {
          q: 'And in a chain that calls tools several times?',
          a: "In JavaScript, answers that only call tools record nothing; the agent's turn is the answer with text. The history tools run through the ToolNode, like any other, bound to the customer your code opened.",
        },
      ],
    },

    crewai: {
      name: 'CrewAI',
      title: 'Memory for CrewAI crews: context in the kickoff inputs and the history tools',
      description:
        "How to give customer memory to a CrewAI crew: before_kickoff puts the context in the inputs as niadra_context, after_kickoff records the final answer, and tools hands over the history with the kit's schema. The crew's own memory stays CrewAI's. SDK code and limits.",
      h1a: 'Memory for crews',
      h1b: 'on CrewAI.',
      sub: "NiadraCrew gives you before_kickoff, which puts the pack in the crew's inputs as niadra_context, after_kickoff, which records the final answer, and tools. The crew's short-term, long-term and entity memory stays CrewAI's; Niadra is the company's memory of the customer, shared with every other agent.",
      problem: [
        "CrewAI's memory belongs to the crew: what its agents saw and learned, in the stores it keeps itself. It serves the crew. It does not serve another vendor's voice agent that will take the same customer tomorrow, nor the billing agent acting in the ERP, and it does not receive what those did.",
        "With Niadra, the crew receives at kickoff the context the company has of the customer, through the niadra_context input, which the task's description or the agent's backstory uses after your text. The crew's final answer goes back into the memory, and any agent, from any vendor, reads what it decided.",
      ],
      how: {
        context: "before_kickoff adds niadra_context to the crew's inputs: the agent's notes (with agent_memory=), the pack and the turn_block. Place {niadra_context} in a task's description or an agent's backstory, after your own text.",
        turns: "The input under message (or message_key=) is the customer's turn, recorded before the crew starts; the crew's final answer is the agent's turn, recorded when it ends.",
        tools: "tools: CrewAI tools whose argument schema is the kit's, bound to the customer.",
        verify: 'conversation.verify() before the kickoff.',
        handoff: 'transferred_to_agent() and transferred_to_human() record the transfer.',
      },
      limits: [
        "The crew's own memory (short-term, long-term, entity) stays CrewAI's; Niadra is the company's memory of the customer, shared with every other agent.",
        "Python 3.11 to 3.13 only: CrewAI's vector store depends on onnxruntime, which has no wheels for 3.10. The crewai, pipecat and openai-agents extras pin incompatible versions of a shared dependency: install one per environment.",
        "Only the crew's final answer is recorded as the agent's turn, not each step.",
        'Tested against crewai 1.15 with the model replaced by a fake and Niadra on the emulator.',
      ],
      faq: [
        {
          q: "Should I switch off CrewAI's memory?",
          a: "No need. The crew's memory keeps what its agents learned about the tasks; Niadra keeps what the company knows of each customer, from every channel. One complements the other, and the crew keeps running as it does today.",
        },
        {
          q: "How does the crew's agent find the context?",
          a: "Through the niadra_context input, which before_kickoff adds. The task's description says {niadra_context} after your text, like any interpolated CrewAI input. The history tools go in tools, for the agent to look up what the context does not answer.",
        },
        {
          q: 'Does every step of the crew become a turn?',
          a: "No. Only the crew's final answer is recorded as the agent's turn, and the input under message as the customer's turn. That is what other agents need to know: what the customer asked and what the crew decided.",
        },
      ],
    },

    aiSdk: {
      name: 'Vercel AI SDK',
      title: 'Memory for the Vercel AI SDK: a middleware for wrapLanguageModel, with any provider',
      description:
        'How to give customer memory to a chat route with the Vercel AI SDK: niadraMiddleware is a model middleware (LanguageModelMiddleware) for wrapLanguageModel, which works with any provider; transformParams places the context, wrapGenerate and wrapStream record the answer, and niadraTools hands over the history as AI SDK tools. Code and limits.',
      h1a: 'Memory for',
      h1b: 'the Vercel AI SDK.',
      sub: "niadraMiddleware(session) is a model middleware (LanguageModelMiddleware) for wrapLanguageModel({ model, middleware }): the AI SDK's idiomatic path, which works with every provider without a separate provider package. niadraTools(session) returns the history kit as AI SDK tools. TypeScript only.",
      problem: [
        "The AI SDK hands the interface's messages to the model and returns the stream. The memory, if there is one, is what your route loads from the database for this chat session. What the same person said on WhatsApp to another vendor, or what the orders agent did in the ERP, never reaches the route.",
        "The middleware goes into wrapLanguageModel, once, for any provider: transformParams places the customer's context as a system message right after yours and the suffix, with the deltas and other channels' turns, at the end of the last user message, where every provider accepts it. wrapGenerate and wrapStream record the model's text as the agent's turn, with the usage reported.",
      ],
      how: {
        context: "transformParams puts the pack as a system message right after your system messages and the suffix (deltas and other channels' turns) as a text part at the end of the last user message, where every provider accepts it.",
        turns: "transformParams records the customer's newest message; wrapGenerate and wrapStream record the model's text as the agent's turn, with the usage the provider reported: prompt tokens, cache reads and writes. Tool-only steps record nothing.",
        tools: 'niadraTools(session): the kit as AI SDK tools, bound to the customer; spread them next to your own.',
        verify: 'niadraMiddleware(session, { verify: { method, level } }) records what your app proved before the first read, a login, for instance.',
        handoff: 'conv.handoff() where your route transfers.',
      },
      limits: [
        'Nothing here fails the model call: a context that does not arrive is left out, and a failure to record is logged without content.',
        'The middleware does not wrap the provider: any AI SDK LanguageModel, from any provider, works.',
        'Tested against ai 7, with the types of versions 5 and 6, and a fake model, with Niadra on the emulator, on Node, Deno, Bun, workerd and the Edge Runtime.',
      ],
      faq: [
        {
          q: 'Does it work on the Edge Runtime and on Workers?',
          a: 'It does: the TypeScript SDK runs on Node, Deno, Bun, workerd and the Edge Runtime, and the middleware is tested on all five. The Niadra key stays in the server route, never in the browser.',
        },
        {
          q: 'Who decides which customer it is?',
          a: 'Your code, from the authenticated session: handles.appUserId(userId) opens the conversation. The customer is never something the model or the browser picks. The login proves the level your policy defines, and the middleware records it before the first read.',
        },
        {
          q: 'Can I mix the history tools with my own?',
          a: 'You can: niadraTools(session) returns AI SDK tools, and you spread them next to yours in the same tools object. stopWhen and the rest of the call stay as they are.',
        },
      ],
    },

    n8n: {
      name: 'n8n',
      title: "Memory for n8n's AI Agent: a community node, before and after the agent",
      description:
        "How to give customer memory to an n8n AI Agent without code: the n8n-nodes-niadra community node reads the customer's context before the agent, records the turns after, searches the history, records verification, handoff and the end of the conversation. Fails open by default. Install, operations and limits.",
      h1a: 'Memory for the AI Agent',
      h1b: 'in n8n.',
      sub: 'n8n-nodes-niadra is an n8n community node. Put it before and after your AI Agent node, or give it to the agent as a tool, so the agent starts every conversation knowing what the customer already told your other agents, and so what it says reaches them too. No code.',
      install: "In n8n, Settings, Community Nodes, Install, and type n8n-nodes-niadra. The package lives in packages/n8n-nodes-niadra of the TypeScript SDK repository and is published to npm next to @niadra/sdk. The Niadra API credential is a source key of your space (nia_sk_...): it names the region and the space, so the address comes from it; Base URL only for the local emulator.",
      problem: [
        "The n8n AI Agent's memory is this execution's message window, or a per-session store of your choice. It belongs to the workflow. Another vendor's voice agent, another workflow's WhatsApp agent and the agent acting in the ERP do not read what this workflow knows, and it does not read what they did.",
        "The node reads the customer's context before the agent, by the identifier your trigger brings (the WhatsApp wa_id, the caller's number, your user id), and records what the customer and the agent said after. Every operation fails open by default: with Niadra slow or down, the node returns an empty result with an error field and the workflow goes on.",
      ],
      how: {
        context: "Get Context returns the customer's context for the agent's prompt: text, for after your system message, and suffix, with the live turns from other channels, for the end of the prompt.",
        turns: "Track Turn records what the customer or the agent said, keyed by the provider's message id, so a retried step is recorded once. End records that the conversation ended.",
        tools: 'Search History searches past conversations, promises, actions and business objects; When takes the period in the customer\'s words ("last week", "semana passada"). The node can also be given to the AI Agent as a tool.',
        verify: "Verify records that the customer proved who they are: an OTP, a login, the carrier's attestation.",
        handoff: 'Handoff records a transfer to a person or to another agent.',
      },
      limits: [
        'Every operation fails open by default: when Niadra is slow or down, the node returns an empty result with an error field and the workflow goes on. Turn Fail Open off to stop the workflow instead.',
        "Bind Customer Handle to your trigger's data (the WhatsApp wa_id, the caller's number, your user id), never to a value the model writes: the customer is never something the model chooses.",
        'The node carries neither agent memory nor the timeline; for those, use the SDK or the MCP server.',
        "Built and tested against n8n-workflow 2.16. The layout of the node and its credential follows Mem0's n8n node (MIT), credited in NOTICE.",
      ],
      faq: [
        {
          q: 'How do I install it?',
          a: 'In n8n, Settings, Community Nodes, Install, and type n8n-nodes-niadra. The credential is a source key of your space (nia_sk_...): it names the region and the space, so the address comes from it. Base URL only for the local emulator.',
        },
        {
          q: 'Where does the node go in the workflow?',
          a: "Get Context before the AI Agent, with the Customer Handle coming from the trigger; Track Turn after, with the customer's message and the agent's answer; End when the conversation ends. Search History can go as a tool of the agent.",
        },
        {
          q: 'What if Niadra does not answer in the middle of the workflow?',
          a: 'The node returns an empty result with an error field and the workflow goes on: the agent answers without the context, as it does today. If you would rather the workflow stopped, turn Fail Open off on the node.',
        },
      ],
    },
  },
}
