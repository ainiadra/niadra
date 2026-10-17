/**
 * The SDK call, for the endpoints the SDK covers. It stays out of the OpenAPI on
 * purpose: that file is rewritten by the server exporter, and these examples
 * belong to the documentation. Endpoints without an entry here show the HTTP call.
 * Written against the `niadra` (Python) and `@niadra/sdk` (TypeScript) SDKs.
 */
type SdkSample = { python: string; typescript: string }

export const SDK_SAMPLES: Record<string, SdkSample> = {
  'post /v1/batch': {
    python: `from niadra import Niadra, phone, system_id

niadra = Niadra()  # reads NIADRA_API_KEY

# Queued and sent in the background, in batches
niadra.track({
    "channel": "whatsapp",
    "conversation_id": "wa-8812",
    "idempotency_key": message["id"],  # the provider message id
    "handles": [phone("+14155550123")],
    "speaker": {"role": "customer"},
    "content": {"text": "The technician never showed up. I am calling you."},
})

niadra.action(
    "credit",
    subject=system_id("crm", "48213"),
    object="invoice:erp:0823",
    result="$40 credit on the August bill",
    closes={"object": {"type": "invoice", "namespace": "erp", "id": "0823"}, "operation": "dispute"},
    channel="erp",
    task_id="billing-7741",
)

niadra.flush()  # waits for the queue, for example before a worker exits`,
    typescript: `import { Niadra, handles } from "@niadra/sdk";

const niadra = new Niadra(); // reads NIADRA_API_KEY

// Queued and sent in the background, in batches
niadra.track({
  channel: "whatsapp",
  conversation_id: "wa-8812",
  idempotency_key: message.id, // the provider message id
  handles: [handles.phone("+14155550123")],
  speaker: "customer",
  text: "The technician never showed up. I am calling you.",
});

niadra.action({
  channel: "erp",
  task_id: "billing-7741",
  handles: [handles.systemId("48213", "crm")],
  object_refs: ["invoice:erp:0823"],
  operation: "credit",
  result: "$40 credit on the August bill",
  closes: { object: { type: "invoice", namespace: "erp", id: "0823" }, operation: "dispute" },
});

await niadra.flush(); // waits for the queue, for example before a worker exits`,
  },
  'post /v1/context': {
    python: `from niadra import Niadra, phone

niadra = Niadra()

ctx = niadra.context(
    subject=phone("+14155550123"),
    view="voice",
    verification="V1",
    conversation_id="call-4471",
)

system_prompt = f"{AGENT_INSTRUCTIONS}\\n\\n{ctx.text}"`,
    typescript: `import { Niadra, handles } from "@niadra/sdk";

const niadra = new Niadra();

const ctx = await niadra.context({
  subject: handles.phone("+14155550123"),
  view: "voice",
  verification: "V1",
  conversation_id: "call-4471",
});

const systemPrompt = \`\${AGENT_INSTRUCTIONS}\\n\\n\${ctx.text}\`;`,
  },
  'post /v1/subject-tokens': {
    python: `token = niadra.subject_token(
    phone("+14155550123"),
    verification="V1",
    conversation_id="call-4471",
)
# Open the MCP connection to niadra.mcp_url with the source key
# and token.headers ({"Niadra-Subject-Token": ...})`,
    typescript: `const { data: token } = await niadra.subjectToken({
  subject: handles.phone("+14155550123"),
  verification: "V1",
  conversation_id: "call-4471",
});
// Open the MCP connection with the source key and the
// "Niadra-Subject-Token" header set to token.token`,
  },
  'post /v1/history/search': {
    python: `result = niadra.search(
    phone("+14155550123"),
    "credit for missed technician visit",
    max_tokens=300,
    conversation_id="call-4471",
    voice=True,
)

if result.recurrence:
    print(result.recurrence.occurrences, result.recurrence.last_resolution)`,
    typescript: `const { data } = await niadra.search({
  subject: handles.phone("+14155550123"),
  query: "credit for missed technician visit",
  max_tokens: 300,
  conversation_id: "call-4471",
});

if (data?.recurrence) {
  console.log(data.recurrence.occurrences, data.recurrence.last_resolution);
}`,
  },
  'post /v1/history/timeline': {
    python: `page = niadra.timeline(
    phone("+14155550123"),
    filters={"since": "2026-01-01T00:00:00Z"},
    limit=20,
)
for item in page.items:
    print(item.at, item.kind, item.text)`,
    typescript: `const { data: page } = await niadra.timeline({
  subject: handles.phone("+14155550123"),
  filters: { since: "2026-01-01T00:00:00Z" },
  limit: 20,
});
for (const item of page?.items ?? []) console.log(item.at, item.kind, item.text);`,
  },
  'post /v1/history/open': {
    python: `item = niadra.open("0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d34", conversation_id="call-4471")
if item:
    print(item.summary, item.resolution)`,
    typescript: `const { data: item } = await niadra.open("0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d34", { conversation_id: "call-4471" });
if (item) console.log(item.summary, item.resolution);`,
  },
  'get /v1/history/tools': {
    python: `# The customer is bound here, outside the model's reach
kit = niadra.tools(phone("+14155550123"), conversation_id="call-4471")

reply = llm.chat.completions.create(
    model=MODEL,
    messages=messages,
    tools=kit.definitions,
)
for call in reply.choices[0].message.tool_calls or []:
    output = kit.call(call.function.name, call.function.arguments)
    messages.append({"role": "tool", "tool_call_id": call.id, "content": output})`,
    typescript: `// The customer is bound here, outside the model's reach
const kit = niadra.tools(handles.phone("+14155550123"), { conversation_id: "call-4471" });

const reply = await llm.chat.completions.create({ model: MODEL, messages, tools: kit.definitions });
for (const call of reply.choices[0].message.tool_calls ?? []) {
  const output = await kit.call(call.function.name, call.function.arguments);
  messages.push({ role: "tool", tool_call_id: call.id, content: output });
}`,
  },
  'post /v1/feedback': {
    python: `niadra.feedback(
    "resolve_open_item",
    phone("+14155550123"),
    open_item_id="0192f7a2-8c41-7e3a-9b5d-2c4e6f8a0b13",
    reason="Visit rescheduled and confirmed by the customer",
)`,
    typescript: `await niadra.feedback({
  action: "resolve_open_item",
  subject: handles.phone("+14155550123"),
  open_item_id: "0192f7a2-8c41-7e3a-9b5d-2c4e6f8a0b13",
  reason: "Visit rescheduled and confirmed by the customer",
});`,
  },
  'post /v1/media/uploads': {
    python: `# Reserves the upload, sends the bytes with the exact upload_headers, returns the reference
media = niadra.upload_media(audio_bytes, "audio/ogg", subject=phone("+14155550123"))

niadra.track({
    "channel": "whatsapp",
    "conversation_id": "wa-8812",
    "handles": [phone("+14155550123")],
    "speaker": {"role": "customer"},
    "content": {"type": "audio", "media_ref": media.media_ref, "media_sha256": media.media_sha256},
})`,
    typescript: `// Reserves the upload, sends the bytes with the exact upload_headers, returns the reference
const { data: media } = await niadra.uploadMedia({
  data: audioBytes,
  content_type: "audio/ogg",
  subject: handles.phone("+14155550123"),
});

niadra.track({
  channel: "whatsapp",
  conversation_id: "wa-8812",
  handles: [handles.phone("+14155550123")],
  speaker: "customer",
  content: { type: "audio", media_ref: media!.media_ref, media_sha256: media!.media_sha256 },
});`,
  },
  'get /v1/objects/{object_type}/{namespace}/{external_id}': {
    python: `invoice = niadra.object_state("invoice:erp:0823")
if invoice:
    print({name: f.v for name, f in invoice.fields.items()}, invoice.as_of)`,
    typescript: `const { data: invoice } = await niadra.objectState("invoice:erp:0823");
if (invoice) console.log(invoice.fields, invoice.as_of);`,
  },
  'get /v1/objects/{object_type}/{namespace}/{external_id}/timeline': {
    python: `page = niadra.object_timeline("invoice:erp:0823", limit=20)
if page:
    for item in page.items:
        print(item.at, item.kind, item.text)`,
    typescript: `const { data: page } = await niadra.objectTimeline("invoice:erp:0823", { limit: 20 });
for (const item of page?.items ?? []) console.log(item.at, item.kind, item.text);`,
  },
  'get /v1/agent-memory/block': {
    python: `# After your instructions, before the customer's context; cached and revalidated by ETag
notes = niadra.agent_memory(max_tokens=300, tags=["scheduling"])
system_prompt = "\\n\\n".join(part for part in (AGENT_INSTRUCTIONS, notes.text, ctx.system_block) if part)`,
    typescript: `// After your instructions, before the customer's context; cached and revalidated by ETag
const notes = await niadra.agentMemory({ max_tokens: 300, tags: ["scheduling"] });
const system = [AGENT_INSTRUCTIONS, notes.text, ctx.text].filter(Boolean).join("\\n\\n");`,
  },
  'post /v1/agent-memory/search': {
    python: `for note in niadra.search_agent_memory("reschedule a technician visit", tags=["scheduling"]):
    print(note.kind, note.title, note.body)`,
    typescript: `const { data } = await niadra.searchAgentMemory({ query: "reschedule a technician visit", tags: ["scheduling"] });
for (const note of data?.notes ?? []) console.log(note.kind, note.title, note.body);`,
  },
  'get /v1/agent-memory/tools': {
    python: `# The history kit plus search_agent_memory (and remember, with write_agent_memory=True)
kit = conversation.tools(agent_memory=True, write_agent_memory=True)
reply = llm.chat.completions.create(model=MODEL, messages=messages, tools=kit.definitions)`,
    typescript: `// The history kit plus search_agent_memory (and remember, with writeAgentMemory: true)
const kit = conv.tools({ agentMemory: true, writeAgentMemory: true });
const reply = await llm.chat.completions.create({ model: MODEL, messages, tools: kit.definitions });`,
  },
  'post /v1/agent-memory/notes': {
    python: `# Needs the agent_memory:write scope; a note with personal data comes back refused
saved = niadra.remember(
    "pitfall",
    "Scheduling API",
    "Dates without a time zone are refused; send the offset the customer's region uses.",
    tags=["scheduling"],
)
if saved.error == "personal_data_in_agent_memory":
    ...  # rewrite the note without the customer's data`,
    typescript: `// Needs the agent_memory:write scope; a note with personal data comes back refused
const { data, error } = await niadra.remember({
  kind: "pitfall",
  title: "Scheduling API",
  body: "Dates without a time zone are refused; send the offset the customer's region uses.",
  tags: ["scheduling"],
});
if (error?.code === "personal_data_in_agent_memory") {
  // rewrite the note without the customer's data
}`,
  },
  // Turn records and replay
  'post /v1/turns': {
    python: `# Inside a conversation, one turn from the customer's message to the agent's answer.
# The record leaves in the background, in the space's content mode; nothing here waits for Niadra.
@Niadra.tool("search_products", provenance=lambda r: [{"ref": f"product:store:{r['sku']}", "fields": r["prices"]}])
def search_products(sku: str) -> dict: ...

with niadra.conversation("wa-8812", subject=customer, agent_id="store") as conversation:
    conversation.customer(text)
    with conversation.turn(build=Niadra.build(prompts={"store": "v3"}, model="gpt-4.1-mini")):
        context = conversation.context(include=["constraints"])
        product = search_products("PX-4471")  # recorded in the turn, with the objects it showed
        reply = model(prompt_with(context, product))
        conversation.agent(reply)`,
    typescript: `// Inside a conversation, one turn from the customer's message to the agent's answer.
// The record leaves in the background, in the space's content mode; nothing here waits for Niadra.
const searchProducts = Niadra.tool("search_products", async (sku: string) => catalog.find(sku), {
  provenance: (r) => [{ ref: \`product:store:\${r.sku}\`, fields: r.prices }],
});

convo.customer(text, { idempotency_key: inbound.id });
await convo.turn({ build: Niadra.build({ prompts: { store: "v3" }, model: "gpt-4.1-mini" }) }, async () => {
  const ctx = await convo.context({ include: ["constraints"] });
  const product = await searchProducts("PX-4471"); // recorded in the turn, with the objects it showed
  convo.agent(await model(promptWith(ctx, product)));
});`,
  },
  'post /v1/turns/promote': {
    python: `from niadra.models.turns import PromoteRequest

# A complaint arrives days later: keep the conversation's turns for the kept tier's retention
niadra.api.promote_turns(PromoteRequest(conversation_id="wa-8812", reason="complaint"))`,
    typescript: `// A complaint arrives days later: keep the conversation's turns for the kept tier's retention
await niadra.api.promoteTurns({ conversation_id: "wa-8812", reason: "complaint" });`,
  },
  'post /v1/replay/cases': {
    python: `from niadra.replay import Replayer

# Runs each turn of the scenario 5 times inside your CI; tools answer from the record
run = Replayer(niadra, build_agent, build=BUILD).run(["sc_01J9..."], runs=5)
if run.regressed:
    raise SystemExit(1)`,
    typescript: `import { Replayer } from "@niadra/sdk";

// Runs each turn of the scenario 5 times inside your CI; tools answer from the record
const run = await new Replayer(niadra, buildAgent, { build: BUILD }).run(["sc_01J9..."], { runs: 5 });
if (run.verdict === "regression") process.exit(1);`,
  },
  'post /v1/scenario-runs': {
    python: `# The Replayer reports the run for you and returns Niadra's verdict
run = Replayer(niadra, build_agent, build=BUILD).run(["sc_01J9..."], runs=5, vary=["prompts"])
print(run.verdict)  # pass, flaky, regression, pin_mismatch or infrastructure_error`,
    typescript: `// The Replayer reports the run for you and resolves with Niadra's verdict
const run = await new Replayer(niadra, buildAgent, { build: BUILD }).run(["sc_01J9..."], { runs: 5, vary: ["prompts"] });
console.log(run.verdict); // pass, flaky, regression, pin_mismatch or infrastructure_error`,
  },
  'get /v1/notifications': {
    python: `# Pull instead of receiving webhooks: keep the cursor between polls
page = niadra.api.notifications(cursor=cursor, limit=100)
for event in page.items:
    handle(event.type, event.data)  # ids and pointers, never content
cursor = page.next_cursor`,
    typescript: `// Pull instead of receiving webhooks: keep the cursor between polls
const page = await niadra.api.notifications({ cursor, limit: 100 });
for (const event of page.items) handle(event.type, event.data); // ids and pointers, never content
cursor = page.next_cursor;`,
  },
  // Typed state
  'get /v1/sdk/profile': {
    python: `# Read once at start and again when valid_for_s runs out; the last profile stays in use when Niadra is down
profile = niadra.profile()
if profile and "turns" in profile.features:
    ...`,
    typescript: `// Read once at start and again when valid_for_s runs out; the last profile stays in use when Niadra is down
const profile = await niadra.profile();
if (profile?.features.includes("turns")) {
  // ...
}`,
  },
  'post /v1/objects/push': {
    python: `from niadra.models.state import ObjectPush, ObjectPushRequest, Provenance, StateRef

# The system of record pushes what it knows; a field moves only under a greater version
niadra.api.push_objects(ObjectPushRequest(objects=[
    ObjectPush(
        ref=StateRef(type="item_variant", namespace="store", id="991"),
        version=1042,
        fields={"available": True, "price_sale": 199.9},
        provenance=Provenance(source="live", source_observed_at=now),
    ),
]))`,
    typescript: `// The system of record pushes what it knows; a field moves only under a greater version
await niadra.api.pushObjects({
  objects: [
    {
      ref: { type: "item_variant", namespace: "store", id: "991" },
      version: 1042,
      fields: { available: true, price_sale: 199.9 },
      provenance: { source: "live", source_observed_at: now },
    },
  ],
});`,
  },
  'post /v1/state/verify': {
    python: `# Whether the agent may state this price now; a value not safe to claim is read again by your resolver
verdict = conversation.verify_claim("item_variant:store:991", "price_sale", 199.9)
if not verdict.claim_safe:
    ...  # say the gap, or quote again`,
    typescript: `// Whether the agent may state this price now; a value not safe to claim is read again by your resolver
const verdict = await convo.verifyClaim("item_variant:store:991", "price_sale", 199.9);
if (!verdict.claimSafe) {
  // say the gap, or quote again
}`,
  },
  'get /v1/state/refresh-requests': {
    python: `# niadra resolver-worker --resolvers app.resolvers:register
# Or in your own process:
from niadra.cli.worker import ResolverWorker

niadra.resolvers.register("item_variant", read_variant)  # reads the object fresh, inside your boundary
ResolverWorker(niadra).run_once()  # leases the waiting requests, resolves them and pushes what it read`,
    typescript: `// niadra resolver-worker --resolvers ./resolvers.js:register
// Or in your own process:
import { ResolverWorker } from "@niadra/sdk";

niadra.resolvers.register("item_variant", readVariant); // reads the object fresh, inside your boundary
await new ResolverWorker(niadra).runOnce(); // leases the waiting requests, resolves them and pushes what it read`,
  },
  // Working memory
  'put /v1/agent-state': {
    python: `# Written by the agent's code, never by a model; merge by key keeps the other fields
written = conversation.agent_state.put({"offer": {"plan": "familia-plus", "status": "shown"}})
if not written.stored:
    print(written.reason)  # over_cap or not_declared_field, never an exception`,
    typescript: `// Written by the agent's code, never by a model; merge by key keeps the other fields
const written = await convo.agentState.put({ offer: { plan: "familia-plus", status: "shown" } });
if (!written.stored) console.log(written.reason); // over_cap or not_declared_field, never a rejection`,
  },
  'post /v1/agent-state/read': {
    python: `state = conversation.agent_state.get()  # version 0 and an empty body before the first write
step = state.body.get("step")`,
    typescript: `const state = await convo.agentState.get(); // version 0 and an empty body before the first write
const step = state.body.step;`,
  },
  // Signals
  'post /v1/constraints': {
    python: `# The block comes with the pack, in the same round trip
context = conversation.context(include=["constraints"])
block = context.constraints  # hard, soft, attributes, exclude, ask`,
    typescript: `// The block comes with the pack, in the same round trip
const ctx = await convo.context({ include: ["constraints"] });
const block = ctx.constraints; // hard, soft, attributes, exclude, ask`,
  },
  // Coordination
  'post /v1/coordination/check': {
    python: `# Ask before an outbound contact; with Niadra out of reach the purpose decides (marketing waits)
decision = conversation.check("proposal_followup", purpose="marketing", channel="whatsapp")
if decision.decision == "allow":
    gateway.send(message, token=decision.contact_token)
    conversation.declare.contact_made(decision, purpose="marketing", channel="whatsapp")`,
    typescript: `// Ask before an outbound contact; with Niadra out of reach the purpose decides (marketing waits)
const decision = await convo.check("proposal_followup", { purpose: "marketing", channel: "whatsapp" });
if (decision.decision === "allow") {
  await gateway.send(message, { token: decision.contact_token });
  convo.declare.contactMade(decision, { purpose: "marketing", channel: "whatsapp" });
}`,
  },
  'post /v1/coordination/declare': {
    python: `# One farewell per conversation: the check reserved the effect, the declaration settles it
key = f"farewell:{conversation.conversation_id}"
decision = conversation.check("farewell", purpose="service", effect_key=key)
if decision.decision == "allow" and decision.effect and decision.effect.state == "none":
    try:
        send(farewell)
        conversation.declare.effect(key, "done")
    except TimeoutError:
        conversation.declare.effect(key, "unknown_outcome")  # never sent again on its own`,
    typescript: `// One farewell per conversation: the check reserved the effect, the declaration settles it
const key = \`farewell:\${convo.id}\`;
const decision = await convo.check("farewell", { purpose: "service", effectKey: key });
if (decision.decision === "allow" && decision.effect?.state === "none") {
  try {
    await send(farewell);
    convo.declare.effect(key, "done");
  } catch {
    convo.declare.effect(key, "unknown_outcome"); // never sent again on its own
  }
}`,
  },
  'post /v1/coordination/claims': {
    python: `# Hold the customer while this agent works the case; another holder's stricter claim wins
claimed = conversation.claim(kind="case", lease_s=600, intents=["proposal_followup"])
if not claimed.held:
    print(claimed.error)  # lease_held, task_locked, or Niadra did not answer`,
    typescript: `// Hold the customer while this agent works the case; another holder's stricter claim wins
const claimed = await convo.claim({ kind: "case", leaseS: 600, intents: ["proposal_followup"] });
if (!claimed.held) console.log(claimed.error); // lease_held, task_locked, or Niadra did not answer`,
  },
  'get /v1/suppressions': {
    python: `# The local copy of the list, read by cursor and refreshed once a minute; it applies with Niadra down
if niadra.may_contact(customer, "marketing", channel="whatsapp"):
    send(offer)`,
    typescript: `// The local copy of the list, read by cursor and refreshed once a minute; it applies with Niadra down
if (await niadra.mayContact(customer, "marketing", { channel: "whatsapp" })) await send(offer);`,
  },
  'get /.well-known/niadra-contact-keys.json': {
    python: `# At your gateway: check the token offline, with the space's public keys kept in memory
gateway = niadra.contact_gateway("wa_gateway", space=SPACE_ID, key=GATEWAY_KEY)
claims = gateway.verify(token, channel="whatsapp", destination="phone:+5511987654321")  # raises ContactTokenError`,
    typescript: `// At your gateway: check the token offline, with the space's public keys kept in memory
const gateway = niadra.contactGateway("wa_gateway", { space: SPACE_ID, key: GATEWAY_KEY });
const claims = await gateway.verify(token, { channel: "whatsapp", destination: "phone:+5511987654321" }); // rejects with NiadraContactTokenError`,
  },
  // Measurement
  'post /v1/measure/counterfactual-runs': {
    python: `# niadra counterfactual --tools app.tools:TOOLS --tool search_products --element hard --scenario sc_01J9...
# Runs each recorded call live, with and without the element, and reports positions and overlaps only`,
    typescript: `// The Python SDK's \`niadra counterfactual\` command runs the cases; the report is read here
const report = await niadra.api.counterfactualRunRead(runId);
console.log(report.effect, report.limits);`,
  },
}
