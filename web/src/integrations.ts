/**
 * The integration pages: one page per voice platform, channel, agent framework and automation tool at
 * /integracoes/<slug> (English under /en), each answering the question a builder asks a search engine
 * or an AI ("memory for LiveKit agents", "memória para agente de WhatsApp") with the real adapter: the
 * SDK snippet of the docs, how the adapter wires the five primitives, its limits and the early-access
 * request. Only adapters that exist in the published SDKs are listed (niadra 0.9.0 on PyPI,
 * @niadra/sdk 0.9.0 on npm, the n8n node), with the docs page each one comes from.
 *
 * The text lives in `t.integrations.pages[key]` (src/i18n); the code in src/content/integrations/code.ts.
 */
import { DOCS } from './i18n'
import type { VendorKey } from './components/VendorLogo'

export type IntegrationGroup = 'voice' | 'channels' | 'frameworks' | 'automation'

/** Each integration shows the platform's own mark (src/components/VendorLogo.tsx), never a generic glyph. */
export const INTEGRATIONS = [
  { slug: 'livekit', key: 'livekit', group: 'voice', logo: 'livekit', docs: '/integrations/livekit', python: 'livekit', ts: 'livekit', examples: 'examples/livekit_agent.py, examples/livekit.ts' },
  { slug: 'pipecat', key: 'pipecat', group: 'voice', logo: 'pipecat', docs: '/integrations/pipecat', python: 'pipecat', ts: null, examples: 'examples/pipecat_bot.py' },
  { slug: 'vapi', key: 'vapi', group: 'voice', logo: 'vapi', docs: '/integrations/vapi', python: 'vapi', ts: 'vapi', examples: 'examples/vapi_server.py, examples/vapi-hono.ts' },
  { slug: 'retell', key: 'retell', group: 'voice', logo: 'retell', docs: '/integrations/retell', python: 'retell', ts: 'retell', examples: 'examples/retell_server.py, examples/retell-hono.ts' },
  { slug: 'elevenlabs', key: 'elevenlabs', group: 'voice', logo: 'elevenlabs', docs: '/integrations/elevenlabs', python: 'elevenlabs', ts: 'elevenlabs', examples: 'examples/elevenlabs_server.py, examples/elevenlabs-hono.ts' },
  { slug: 'twilio', key: 'twilio', group: 'channels', logo: 'twilio', docs: '/integrations/twilio', python: 'twilio', ts: 'twilio', examples: 'examples/twilio_voice.py, examples/twilio-voice.ts' },
  { slug: 'whatsapp', key: 'whatsapp', group: 'channels', logo: 'whatsapp', docs: '/integrations/whatsapp', python: 'whatsapp', ts: 'whatsapp', examples: 'examples/whatsapp_webhook.py, examples/whatsapp-cloud.ts' },
  { slug: 'openai-agents', key: 'openaiAgents', group: 'frameworks', logo: 'openai', docs: '/integrations/openai-agents', python: 'openai-agents', ts: 'openai-agents', examples: 'examples/openai_agents_run.py, examples/openai-agents.ts' },
  { slug: 'langgraph', key: 'langgraph', group: 'frameworks', logo: 'langchain', docs: '/integrations/langgraph', python: 'langgraph', ts: null, examples: 'examples/langgraph_agent.py' },
  { slug: 'langchain', key: 'langchain', group: 'frameworks', logo: 'langchain', docs: '/integrations/langchain', python: 'langchain', ts: 'langchain', examples: 'examples/langchain_chain.py, examples/langgraph-js.ts' },
  { slug: 'crewai', key: 'crewai', group: 'frameworks', logo: 'crewai', docs: '/integrations/crewai', python: 'crewai', ts: null, examples: 'examples/crewai_crew.py' },
  { slug: 'ai-sdk', key: 'aiSdk', group: 'frameworks', logo: 'vercel', docs: '/integrations/ai-sdk', python: null, ts: 'ai-sdk', examples: 'examples/ai-sdk.ts' },
  { slug: 'n8n', key: 'n8n', group: 'automation', logo: 'n8n', docs: '/integrations/n8n', python: null, ts: null, examples: 'packages/n8n-nodes-niadra' },
] as const satisfies readonly {
  slug: string
  key: string
  group: IntegrationGroup
  logo: VendorKey
  docs: string
  /** The extra of the Python package (`pip install 'niadra[<extra>]'`), when the adapter exists in Python. */
  python: string | null
  /** The subpath of the npm package (`@niadra/sdk/<subpath>`), when the adapter exists in TypeScript. */
  ts: string | null
  /** Where the same code runs in the SDK repositories. */
  examples: string
}[]

export type Integration = (typeof INTEGRATIONS)[number]
export type IntegrationSlug = Integration['slug']
export type IntegrationKey = Integration['key']

export const GROUP_ORDER: readonly IntegrationGroup[] = ['voice', 'channels', 'frameworks', 'automation']

export const INTEGRATIONS_PATH = '/integracoes'
export const integrationPath = (slug: IntegrationSlug) => `${INTEGRATIONS_PATH}/${slug}`
export const INTEGRATION_PATHS = INTEGRATIONS.map((i) => integrationPath(i.slug))
export const integrationBySlug = (slug: string | undefined): Integration | undefined => INTEGRATIONS.find((i) => i.slug === slug)
/** The docs page of the adapter, in the Portuguese layout (the site's Link adds /en). */
export const integrationDocs = (i: Integration) => `${DOCS}${i.docs}`
export const INTEGRATIONS_DOCS = `${DOCS}/integrations/overview`
