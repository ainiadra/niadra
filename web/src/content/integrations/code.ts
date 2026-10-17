/**
 * The minimal example of each adapter, the same code as the "Exemplo mínimo" of its page at
 * docs.niadra.com and as the file in `examples/` of the SDK repositories, where it runs in CI against
 * the real framework types and Niadra's emulator. Nothing here is written for the site: when the docs
 * change the example, this file follows. The install line of each adapter comes first.
 */
import type { IntegrationKey } from '../../integrations'

export type CodeSample = { python?: string; typescript?: string; install: { python?: string; typescript?: string } }

export const CODE: Record<IntegrationKey, CodeSample> = {
  livekit: {
    install: { python: "pip install 'niadra[livekit]'   # livekit-agents 1.8.3 or newer, below 2", typescript: 'npm install @niadra/sdk @livekit/agents   # @livekit/agents 1.9, as an optional peer dependency' },
    python: `"""A LiveKit voice agent that starts every call knowing the caller. Run: python livekit_agent.py dev"""

from livekit.agents import AgentServer, AgentSession, JobContext, cli, inference

from niadra import AsyncNiadra
from niadra.integrations.livekit import NiadraAgent, conversation_for

niadra = AsyncNiadra(channel="voice")
server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    caller = await ctx.wait_for_participant()
    conversation = conversation_for(niadra, caller, room=ctx.room)  # the SIP number and call id
    session = AgentSession(
        stt=inference.STT("deepgram/nova-3"),
        llm=inference.LLM("openai/gpt-4.1-mini"),
        tts=inference.TTS("cartesia/sonic-2"),
    )
    agent = NiadraAgent(
        conversation, instructions="You are Acme's support agent. Be brief.", agent_memory=True
    )
    await session.start(agent, room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)`,
    typescript: `import { type JobContext, ServerOptions, cli, defineAgent, voice } from "@livekit/agents";
import { fileURLToPath } from "node:url";
import { Niadra } from "@niadra/sdk";
import { NiadraAgent, NiadraMemory, attestationProof, sipConversationId, sipSubject } from "@niadra/sdk/livekit";

const niadra = new Niadra();

export default defineAgent({
  entry: async (ctx: JobContext) => {
    await ctx.connect();
    const caller = await ctx.waitForParticipant();
    const conversation = niadra.conversation({
      subject: sipSubject(caller),
      channel: "voice",
      conversation_id: sipConversationId(caller, ctx.room.name ?? "room"),
    });
    const memory = new NiadraMemory({
      conversation,
      // Map the carrier's STIR/SHAKEN header to this attribute in your SIP trunk's header settings.
      verify: attestationProof(caller.attributes["sip.h.x-stir-verstat"]),
    });
    const session = new voice.AgentSession({
      stt: "deepgram/nova-3",
      llm: "openai/gpt-4.1-mini",
      tts: "cartesia/sonic-3",
    });
    memory.attach(session);
    await session.start({
      agent: new NiadraAgent({ instructions: "You answer the phone for Acme Energy. Be brief.", memory }),
      room: ctx.room,
    });
  },
});

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  cli.runApp(new ServerOptions({ agent: fileURLToPath(import.meta.url) }));
}`,
  },

  pipecat: {
    install: { python: "pip install 'niadra[pipecat]'   # pipecat-ai 1.11.0 or newer, below 2; Python 3.11 or newer" },
    python: `"""A Pipecat phone agent over Twilio Media Streams with the customer's memory."""

from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair

from niadra import AsyncNiadra
from niadra.integrations.pipecat import NiadraMemoryProcessor, conversation_for_call, history_tools

niadra = AsyncNiadra(channel="voice")


def build(transport, stt, llm, tts, call_sid: str, caller: str, stir_verstat: str | None) -> Pipeline:
    conversation = conversation_for_call(niadra, call_sid, caller)
    memory = NiadraMemoryProcessor(conversation, attestation=stir_verstat)
    context = LLMContext(
        [{"role": "system", "content": "You are Acme's agent."}], tools=history_tools(conversation)
    )
    aggregators = LLMContextAggregatorPair(context)
    memory.observe(aggregators)
    user, assistant = aggregators.user(), aggregators.assistant()
    return Pipeline([transport.input(), stt, user, memory, llm, tts, transport.output(), assistant])`,
  },

  vapi: {
    install: { python: "pip install 'niadra[vapi]'   # no framework dependency", typescript: 'npm install @niadra/sdk   # @niadra/sdk/vapi needs no Vapi package' },
    python: `"""The server URL of a Vapi assistant. Run: uvicorn vapi_server:app"""

import os

from fastapi import FastAPI, Request, Response

from niadra import AsyncNiadra
from niadra.integrations.vapi import VapiServer, tool_definitions

niadra = AsyncNiadra(channel="voice")
vapi = VapiServer(
    niadra, secret=os.environ["VAPI_SERVER_SECRET"], assistant_id=os.environ["VAPI_ASSISTANT_ID"]
)
app = FastAPI()
TOOLS = tool_definitions("https://agent.example.com/vapi")  # add them to the assistant's model.tools


@app.post("/vapi")
async def server(request: Request) -> Response:
    result = await vapi.handle(await request.body(), request.headers)
    return Response(result.text(), result.status, media_type=result.content_type)`,
    typescript: `// Vapi's server URL on Hono. Set the assistant's (or phone number's) server URL to
// https://api.acme.com/vapi with the secret below, and use {{niadra_context}} in the system prompt.
import { Hono } from "hono";
import { Niadra } from "@niadra/sdk";
import { vapi, vapiTools } from "@niadra/sdk/vapi";

const niadra = new Niadra();
const secret = process.env.VAPI_SERVER_SECRET ?? "";

const handle = vapi({
  niadra,
  secret,
  // A saved assistant; the context arrives in its variables.
  assistant: "YOUR_ASSISTANT_ID",
  // A number to transfer to when the assistant asks for a person.
  transfer: () => ({ destination: { type: "number", number: "+551130000000", message: "Transferring you now." } }),
});

export const app = new Hono();

app.post("/vapi", async (c) => {
  const { status, body } = await handle(await c.req.json(), c.req.raw.headers);
  return c.json(body, status as 200);
});

// The tools to add to the assistant in Vapi, with the SDK's descriptions.
if (process.argv.includes("--print-tools")) {
  console.log(JSON.stringify(vapiTools({ url: "https://api.acme.com/vapi", secret }), null, 2));
}

export default app;`,
  },

  retell: {
    install: { python: "pip install 'niadra[retell]'   # no framework dependency", typescript: 'npm install @niadra/sdk   # @niadra/sdk/retell needs only web APIs' },
    python: `"""The server side of a Retell voice agent: inbound webhook, custom functions and call events.

Run: uvicorn retell_server:app. On the Retell phone number, set the inbound webhook to
/retell/inbound; add the tools from tool_configs() to the Retell LLM's general_tools; set the
agent's webhook to /retell/events. Put {{niadra_agent_memory}} and {{niadra_context}} in the
prompt, after your instructions.
"""

import os

from fastapi import FastAPI, Request, Response

from niadra import AsyncNiadra
from niadra.integrations.retell import RetellWebhooks, tool_configs

niadra = AsyncNiadra(channel="voice")
retell = RetellWebhooks(niadra, api_key=os.environ["RETELL_API_KEY"], agent_memory=True)
app = FastAPI()
TOOLS = tool_configs("https://agent.example.com/retell/tools", agent_memory=True)


def answer(result) -> Response:
    return Response(result.text(), result.status, media_type=result.content_type)


@app.post("/retell/inbound")
async def inbound(request: Request) -> Response:
    return answer(await retell.inbound(await request.body(), request.headers))


@app.post("/retell/tools")
async def tools(request: Request) -> Response:
    return answer(await retell.custom_function(await request.body(), request.headers))


@app.post("/retell/events")
async def events(request: Request) -> Response:
    return answer(await retell.webhook(await request.body(), request.headers))


async def call_out(to_number: str) -> dict:
    """What to pass to Retell's create_phone_call for an outbound call to a customer."""
    return await retell.outbound(to_number)`,
    typescript: `// The three Retell endpoints on Hono. Every handler takes the raw body: the signature covers the exact bytes.
import { Hono } from "hono";
import { Niadra } from "@niadra/sdk";
import { retell } from "@niadra/sdk/retell";

const niadra = new Niadra();
const handlers = retell({ niadra, apiKey: process.env.RETELL_API_KEY ?? "" });
const respond = (c, { status, body }) => c.json(body, status);

const app = new Hono();
app.post("/retell/inbound", async (c) => respond(c, await handlers.inbound(await c.req.text(), c.req.raw.headers)));
app.post("/retell/webhook", async (c) => respond(c, await handlers.webhook(await c.req.text(), c.req.raw.headers)));
app.post("/retell/tools", async (c) => respond(c, await handlers.tool(await c.req.text(), c.req.raw.headers)));

// For the Retell LLM's general_tools.
export const TOOLS = handlers.toolConfigs({ url: "https://agent.example.com/retell/tools" });
export default app;`,
  },

  elevenlabs: {
    install: { python: "pip install 'niadra[elevenlabs]'   # no framework dependency: the handlers take the body and the headers", typescript: 'npm install @niadra/sdk   # @niadra/sdk/elevenlabs needs no ElevenLabs package' },
    python: `"""The server side of an ElevenLabs phone agent: initiation, tools and post-call webhooks.

Run: uvicorn elevenlabs_server:app. In the ElevenLabs agent, set the initiation webhook to
/elevenlabs/initiation, add the tools from tool_configs(), and the post-call webhook to
/elevenlabs/post-call. Put {{niadra_agent_memory}} and {{niadra_context}} in the system prompt.
"""

import os

from fastapi import FastAPI, Request, Response

from niadra import AsyncNiadra
from niadra.integrations.elevenlabs import ElevenLabsWebhooks, tool_configs

niadra = AsyncNiadra(channel="voice")
hooks = ElevenLabsWebhooks(
    niadra,
    webhook_secret=os.environ["ELEVENLABS_WEBHOOK_SECRET"],
    shared_secret=os.environ["NIADRA_TOOL_SECRET"],
    agent_memory=True,
)
app = FastAPI()
TOOLS = tool_configs("https://agent.example.com/elevenlabs/tools", secret=os.environ["NIADRA_TOOL_SECRET"])


def answer(result) -> Response:
    return Response(result.text(), result.status, media_type=result.content_type)


@app.post("/elevenlabs/initiation")
async def initiation(request: Request) -> Response:
    return answer(await hooks.conversation_initiation(await request.body(), request.headers))


@app.post("/elevenlabs/tools/{name}")
async def tool(name: str, request: Request) -> Response:
    return answer(await hooks.server_tool(name, await request.body(), request.headers))


@app.post("/elevenlabs/post-call")
async def post_call(request: Request) -> Response:
    return answer(await hooks.post_call(await request.body(), request.headers))`,
    typescript: `// The three ElevenLabs webhooks on Hono, for any runtime Hono runs on (Node, Bun, Deno, Workers).
import { Hono } from "hono";
import { Niadra } from "@niadra/sdk";
import { elevenLabs } from "@niadra/sdk/elevenlabs";

const niadra = new Niadra();
const handlers = elevenLabs({
  niadra,
  secret: process.env.NIADRA_ELEVENLABS_SECRET ?? "",
  webhookSecret: process.env.ELEVENLABS_WEBHOOK_SECRET ?? "",
});

export const app = new Hono();

app.post("/elevenlabs/initiation", async (c) => {
  const { status, body } = await handlers.initiation(await c.req.json(), c.req.raw.headers);
  return c.json(body, status as 200);
});

app.post("/elevenlabs/tools", async (c) => {
  const { status, body } = await handlers.tool(await c.req.json(), c.req.raw.headers);
  return c.json(body, status as 200);
});

app.post("/elevenlabs/post-call", async (c) => {
  // The raw body: the signature covers the exact bytes ElevenLabs sent.
  const { status, body } = await handlers.postCall(await c.req.text(), c.req.raw.headers);
  return c.json(body, status as 200);
});

// The server tool configurations to create in ElevenLabs (API or dashboard), printed once.
if (process.argv.includes("--print-tools")) {
  console.log(JSON.stringify(handlers.toolConfigs({ url: "https://api.acme.com/elevenlabs/tools", secretId: "YOUR_SECRET_ID" }), null, 2));
}

export default app;`,
  },

  twilio: {
    install: { python: "pip install 'niadra[twilio]'   # no framework dependency", typescript: 'npm install @niadra/sdk   # @niadra/sdk/twilio' },
    python: `"""A Twilio voice webhook that verifies the carrier's attestation before the first context."""

import os

from flask import Flask, request

from niadra import Niadra
from niadra.integrations.twilio import parse_call

niadra = Niadra(channel="voice")
app = Flask(__name__)


@app.post("/twilio/voice")
def incoming_call() -> tuple[str, int]:
    call = parse_call(request.get_data(), request.headers, request.url, os.environ["TWILIO_AUTH_TOKEN"])
    if call is None:
        return "", 403
    conversation = call.conversation(niadra)
    call.verify(conversation)  # StirVerstat: A proves V2, B and C prove V1
    context = conversation.ready()  # the first read, started above; the phone rings meanwhile
    return connect_your_voice_agent(call.call_sid, context.system_block), 200


@app.post("/twilio/status")
def status() -> tuple[str, int]:
    call = parse_call(request.get_data(), request.headers, request.url, os.environ["TWILIO_AUTH_TOKEN"])
    if call is not None:
        call.ended(call.conversation(niadra))
    return "", 204`,
    typescript: `// A Twilio Programmable Voice webhook on Hono: the carrier's attestation proves the caller, the
// context is read before the first answer, and each recognized sentence is recorded.
import { Hono } from "hono";
import { Niadra } from "@niadra/sdk";
import { readTwilio, recordTwilioInbound, verifyTwilio } from "@niadra/sdk/twilio";

const niadra = new Niadra();
const authToken = process.env.TWILIO_AUTH_TOKEN ?? "";
const publicUrl = process.env.PUBLIC_URL ?? "";

export const app = new Hono();

app.post("/twilio/voice", async (c) => {
  const { status, request } = await readTwilio(\`\${publicUrl}/twilio/voice\`, await c.req.text(), c.req.raw.headers, { authToken });
  if (!request) return c.body(null, status as 403);
  // The attestation is recorded once, on the call's first webhook; later ones open at the proven level.
  const first = request.params.CallStatus === "ringing";
  const convo = niadra.conversation({
    subject: request.subject,
    channel: "voice",
    conversation_id: request.conversationId ?? "",
    verification: first ? "V0" : (request.proof?.level ?? "V0"),
  });
  if (first) await verifyTwilio(convo, request);
  recordTwilioInbound(convo, request);
  const ctx = await convo.ready(); // the first read, started on the ringing webhook
  convo.markInjected(ctx);
  const reply = await answer(ctx.text, request.text);
  convo.agent(reply);
  const twiml = \`<Response><Gather input="speech" action="/twilio/voice"><Say>\${escape(reply)}</Say></Gather></Response>\`;
  return c.body(twiml, 200, { "content-type": "text/xml" });
});

export default app;`,
  },

  whatsapp: {
    install: { python: "pip install 'niadra[whatsapp]'   # no framework dependency", typescript: 'npm install @niadra/sdk   # @niadra/sdk/whatsapp' },
    python: `"""A WhatsApp Cloud API webhook: each message is the customer's turn, each reply the agent's."""

import os

from fastapi import FastAPI, Request, Response

from niadra import Niadra
from niadra.integrations.whatsapp import parse_webhook, sent, subscribe

niadra = Niadra(channel="whatsapp")
app = FastAPI()


@app.get("/whatsapp")
def challenge(request: Request) -> Response:
    result = subscribe(request.query_params, os.environ["WHATSAPP_VERIFY_TOKEN"])
    return Response(result.text(), result.status, media_type=result.content_type)


@app.post("/whatsapp")
async def inbound(request: Request) -> Response:
    messages = parse_webhook(await request.body(), request.headers, os.environ["META_APP_SECRET"])
    if messages is None:
        return Response(status_code=401)
    for message in messages:
        with niadra.conversation(f"wa-{message.wa_id}", subject=message.subject) as chat:
            message.record(chat)
            niadra.flush()
            context = chat.context()
            reply = your_model(context.system_block, context.turn_block, message.text)
            sent(chat, reply, send_whatsapp(message.wa_id, reply))
    return Response(status_code=200)`,
    typescript: `// A WhatsApp Cloud API webhook on Hono: reads Meta's delivery, gives the agent the customer's
// context, sends the answer through the Graph API and records both turns.
import { Hono } from "hono";
import { Niadra } from "@niadra/sdk";
import { readWhatsApp, recordInbound, recordOutbound, whatsAppChallenge } from "@niadra/sdk/whatsapp";

const niadra = new Niadra();
const env = (name: string): string => process.env[name] ?? "";

export const app = new Hono();

app.get("/whatsapp", (c) => {
  const { status, body } = whatsAppChallenge(new URL(c.req.url).searchParams, env("META_VERIFY_TOKEN"));
  return c.text(body, status as 200);
});

app.post("/whatsapp", async (c) => {
  const { status, messages } = await readWhatsApp(await c.req.text(), c.req.raw.headers, { appSecret: env("META_APP_SECRET") });
  for (const inbound of messages) {
    const convo = niadra.conversation({ subject: inbound.subject, channel: "whatsapp", conversation_id: \`wa:\${inbound.waId}\` });
    recordInbound(convo, inbound);
    const ctx = await convo.context();
    convo.markInjected(ctx);
    const reply = await answer(\`You are Acme's WhatsApp agent.\\n\\n\${ctx.text}\`, ctx.suffix, inbound.text);
    const sent = await fetch(\`https://graph.facebook.com/v23.0/\${inbound.phoneNumberId}/messages\`, {
      method: "POST",
      headers: { authorization: \`Bearer \${env("META_ACCESS_TOKEN")}\`, "content-type": "application/json" },
      body: JSON.stringify({ messaging_product: "whatsapp", to: inbound.waId, type: "text", text: { body: reply } }),
    });
    recordOutbound(convo, reply, await sent.json());
  }
  return c.body(null, status as 200);
});

export default app;`,
  },

  openaiAgents: {
    install: { python: "pip install 'niadra[openai-agents]'   # openai-agents 0.22.3 or newer, below 1", typescript: 'npm install @niadra/sdk @openai/agents   # @openai/agents 0.18, as an optional peer dependency' },
    python: `"""An OpenAI Agents SDK agent with the customer's memory."""

import asyncio

from agents import Agent, Runner

from niadra import AsyncNiadra, phone
from niadra.integrations.openai_agents import NiadraAgentsMemory

niadra = AsyncNiadra(channel="chat")


async def main() -> None:
    async with niadra.conversation("thread-81", subject=phone("+5511912345678")) as conversation:
        memory = NiadraAgentsMemory(conversation, agent_memory=True)
        agent = Agent(
            name="Support", instructions="You are Acme's agent.", model="gpt-4.1", tools=memory.tools
        )
        result = await Runner.run(
            agent, "Where is my replacement lid?", hooks=memory.hooks, run_config=memory.run_config()
        )
        print(result.final_output)
    await niadra.close()


asyncio.run(main())`,
    typescript: `import { Agent, Runner } from "@openai/agents";
import { Niadra, handles } from "@niadra/sdk";
import { NiadraSession, niadraInstructions, niadraRunHooks, niadraTools } from "@niadra/sdk/openai-agents";

const niadra = new Niadra();
const runner = new Runner();

/** One customer message in; \`userId\` comes from your session, never from the model. */
export async function reply(userId: string, chatId: string, text: string): Promise<string> {
  const convo = niadra.conversation({ subject: handles.appUserId(userId), channel: "web_chat", conversation_id: chatId });
  const billing = new Agent({ name: "Billing", instructions: niadraInstructions("You handle invoices and credits.", convo), tools: niadraTools(convo) });
  const support = new Agent({
    name: "Support",
    instructions: niadraInstructions("You are Acme's support agent. Hand billing questions to Billing.", convo),
    tools: niadraTools(convo),
    handoffs: [billing],
  });
  const stop = niadraRunHooks(runner, convo);
  try {
    const result = await runner.run(support, text, { session: new NiadraSession(convo) });
    return String(result.finalOutput ?? "");
  } finally {
    stop();
  }
}`,
  },

  langgraph: {
    install: { python: "pip install 'niadra[langgraph]'   # langgraph 1.2, langchain 1.4 and langchain-core 1.6 or newer, below 2" },
    python: `"""A LangGraph agent (langchain.agents.create_agent) with the customer's memory as middleware."""

import asyncio

from langchain.agents import create_agent

from niadra import AsyncNiadra, phone
from niadra.integrations.langgraph import NiadraMiddleware

niadra = AsyncNiadra(channel="chat")


async def main() -> None:
    async with niadra.conversation("thread-81", subject=phone("+5511912345678")) as conversation:
        agent = create_agent(
            "openai:gpt-4.1",
            system_prompt="You are Acme's agent.",
            middleware=[NiadraMiddleware(conversation)],
        )
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": "Where is my replacement lid?"}]}
        )
        print(result["messages"][-1].content)
    await niadra.close()


asyncio.run(main())`,
  },

  langchain: {
    install: { python: "pip install 'niadra[langchain]'   # langchain-core 1.6 or newer, below 2", typescript: 'npm install @niadra/sdk @langchain/core   # @langchain/core 1.x, as an optional peer dependency' },
    python: `"""An LCEL chain with the customer's context and the history tools."""

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from niadra import Niadra, phone
from niadra.integrations.langchain import NiadraCallbackHandler, context_runnable, history_tools

niadra = Niadra(channel="chat")
prompt = ChatPromptTemplate.from_messages([("system", "You are Acme's agent."), ("human", "{question}")])

with niadra.conversation("thread-81", subject=phone("+5511912345678")) as conversation:
    model = ChatOpenAI(model="gpt-4.1").bind_tools(history_tools(conversation))
    chain = prompt | context_runnable(conversation) | model
    reply = chain.invoke(
        {"question": "Where is my replacement lid?"},
        config={"callbacks": [NiadraCallbackHandler(conversation)]},
    )
    print(reply.content)`,
    typescript: `// A LangGraph.js agent: the context goes into the model call inside the node (never into the
// graph's state), the history tools run through ToolNode, and the callback records the answers.
import { HumanMessage, SystemMessage } from "@langchain/core/messages";
import { END, MessagesAnnotation, START, StateGraph } from "@langchain/langgraph";
import { ToolNode, toolsCondition } from "@langchain/langgraph/prebuilt";
import { ChatOpenAI } from "@langchain/openai";
import { Niadra, handles } from "@niadra/sdk";
import { NiadraCallbackHandler, niadraTools, withNiadraContext } from "@niadra/sdk/langchain";

const niadra = new Niadra();

/** One customer message in; \`userId\` comes from your session, never from the model. */
export async function reply(userId: string, threadId: string, text: string): Promise<string> {
  const convo = niadra.conversation({ subject: handles.appUserId(userId), channel: "web_chat", conversation_id: threadId });
  const tools = niadraTools(convo);
  const model = new ChatOpenAI({ model: "gpt-4.1" }).bindTools(tools);

  const graph = new StateGraph(MessagesAnnotation)
    .addNode("agent", async (state) => ({ messages: [await model.invoke(await withNiadraContext(convo, state.messages))] }))
    .addNode("tools", new ToolNode(tools))
    .addEdge(START, "agent")
    .addConditionalEdges("agent", toolsCondition, ["tools", END])
    .addEdge("tools", "agent")
    .compile();

  const result = await graph.invoke(
    { messages: [new SystemMessage("You are Acme's support agent. Be brief."), new HumanMessage(text)] },
    { callbacks: [new NiadraCallbackHandler(convo)] },
  );
  return result.messages.at(-1)?.text ?? "";
}`,
  },

  crewai: {
    install: { python: "pip install 'niadra[crewai]'   # crewai 1.15 or newer, below 2; Python 3.11 to 3.13" },
    python: `"""A CrewAI crew whose task reads the customer's context."""

from crewai import Agent, Crew, Task

from niadra import Niadra, phone
from niadra.integrations.crewai import NiadraCrew

niadra = Niadra(channel="chat")
memory = NiadraCrew(niadra.conversation("thread-81", subject=phone("+5511912345678")))
support = Agent(role="Support", goal="Help the customer", backstory="You work for Acme.", tools=memory.tools)
answer = Task(
    description="Answer the customer: {message}\\n\\nWhat the company knows:\\n{niadra_context}",
    expected_output="A short, specific answer.",
    agent=support,
)
crew = Crew(
    agents=[support],
    tasks=[answer],
    before_kickoff_callbacks=[memory.before_kickoff],
    after_kickoff_callbacks=[memory.after_kickoff],
)
print(crew.kickoff(inputs={"message": "Where is my replacement lid?"}).raw)`,
  },

  aiSdk: {
    install: { typescript: 'npm install @niadra/sdk ai   # ai 5, 6 or 7, as an optional peer dependency' },
    typescript: `// A chat route with the Vercel AI SDK (Next.js App Router or any fetch handler): the model gets
// the customer's context through a middleware, and the history tools next to your own.
import { openai } from "@ai-sdk/openai";
import {
  type UIMessage,
  convertToModelMessages,
  createUIMessageStreamResponse,
  isStepCount,
  streamText,
  toUIMessageStream,
  wrapLanguageModel,
} from "ai";
import { Niadra, handles } from "@niadra/sdk";
import { niadraMiddleware, niadraTools } from "@niadra/sdk/ai-sdk";

const niadra = new Niadra();

/** \`userId\` comes from your session: the customer is never something the model or the browser picks. */
export async function POST(request: Request, userId: string): Promise<Response> {
  const { id, messages } = (await request.json()) as { id: string; messages: UIMessage[] };
  const convo = niadra.conversation({ subject: handles.appUserId(userId), channel: "web_chat", conversation_id: id });

  const tools = niadraTools(convo);
  const result = streamText({
    model: wrapLanguageModel({
      model: openai("gpt-4.1"),
      // The user signed in, which proves V2 in this space's policy.
      middleware: niadraMiddleware(convo, { verify: { method: "login", level: "V2" } }),
    }),
    system: "You are Acme's support agent. Be brief.",
    messages: await convertToModelMessages(messages),
    tools,
    stopWhen: isStepCount(4),
  });
  return createUIMessageStreamResponse({ stream: toUIMessageStream({ stream: result.stream, tools }) });
}`,
  },

  n8n: {
    install: {},
  },
}
