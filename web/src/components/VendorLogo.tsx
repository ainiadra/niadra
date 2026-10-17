import { useT } from '../i18n'

/**
 * The marks of the platforms Niadra integrates with, shown wherever the site names one (the integration
 * pages, the sources diagram of the home, the menu). A generic glyph in their place read as a placeholder
 * (founder, 01/10/2026: "aquele ícone de raio feio não dá").
 *
 * Every file sits in public/integrations/logos, downloaded once from the owner's official assets (never
 * hotlinked), as the benchmark's logos are (src/components/benchmark/logos.ts): the GitHub organization
 * avatar of each one, 256 px, which is the mark each company publishes for itself, used as published
 * (no recolor, no crop). The marks belong to their owners and only identify the integrations.
 *
 * Where each file came from (read on 01/10/2026):
 * - livekit.png     github.com/livekit, the organization avatar
 * - pipecat.png     github.com/pipecat-ai, the organization avatar
 * - vapi.png        github.com/VapiAI, the organization avatar
 * - retell.png      github.com/RetellAI, the organization avatar
 * - elevenlabs.png  github.com/elevenlabs, the organization avatar
 * - twilio.png      github.com/twilio, the organization avatar
 * - whatsapp.png    github.com/WhatsApp, the organization avatar (Meta's WhatsApp mark)
 * - openai.png      github.com/openai, the organization avatar (OpenAI Agents SDK is OpenAI's)
 * - langchain.png   github.com/langchain-ai, the organization avatar (LangGraph and LangChain are LangChain's)
 * - crewai.png      github.com/crewAIInc, the organization avatar
 * - vercel.png      github.com/vercel, the organization avatar (the AI SDK is Vercel's)
 * - n8n.png         github.com/n8n-io, the organization avatar
 */
export const LOGO_DIR = '/integrations/logos'

export const VENDORS = {
  livekit: { file: 'livekit.png', name: 'LiveKit' },
  pipecat: { file: 'pipecat.png', name: 'Pipecat' },
  vapi: { file: 'vapi.png', name: 'Vapi' },
  retell: { file: 'retell.png', name: 'Retell' },
  elevenlabs: { file: 'elevenlabs.png', name: 'ElevenLabs' },
  twilio: { file: 'twilio.png', name: 'Twilio' },
  whatsapp: { file: 'whatsapp.png', name: 'WhatsApp' },
  openai: { file: 'openai.png', name: 'OpenAI' },
  langchain: { file: 'langchain.png', name: 'LangChain' },
  crewai: { file: 'crewai.png', name: 'CrewAI' },
  vercel: { file: 'vercel.png', name: 'Vercel' },
  n8n: { file: 'n8n.png', name: 'n8n' },
} as const

export type VendorKey = keyof typeof VENDORS

export const vendorLogo = (vendor: VendorKey) => `${LOGO_DIR}/${VENDORS[vendor].file}`

/**
 * The names the home's sources diagram shows as text chips, mapped to the owner of each mark. A name
 * not listed here (Agno, Dify, a system of record) keeps its text chip: never a guessed logo.
 */
export const VENDOR_OF_NAME: Record<string, VendorKey> = {
  LiveKit: 'livekit',
  Pipecat: 'pipecat',
  Twilio: 'twilio',
  Vapi: 'vapi',
  Retell: 'retell',
  ElevenLabs: 'elevenlabs',
  'WhatsApp Cloud API': 'whatsapp',
  WhatsApp: 'whatsapp',
  'OpenAI Agents SDK': 'openai',
  LangGraph: 'langchain',
  LangChain: 'langchain',
  CrewAI: 'crewai',
  'Vercel AI SDK': 'vercel',
  n8n: 'n8n',
}

const SIZES = { sm: 'h-5 w-5 rounded-[5px]', md: 'h-10 w-10 rounded-lg', lg: 'h-12 w-12 rounded-lg' } as const

/**
 * The mark in a neutral tile: a 1px hairline on the page's paper, the logo as published inside it. A
 * mark on a dark field (LiveKit, LangChain, Vercel) keeps its field; a mark on white (ElevenLabs,
 * OpenAI, Retell) sits on the paper tone, so the tile still reads as a tile on a white card. The
 * hairline is what separates every tile from the page, light or dark.
 *
 * Loaded eagerly on purpose: the twelve files weigh about 110 KB together, and a lazy mark below the
 * fold rendered as an empty tile in the first screenshots (01/10/2026).
 */
export function VendorLogo({ vendor, size = 'md', className = '' }: { vendor: VendorKey; size?: keyof typeof SIZES; className?: string }) {
  const { t } = useT()
  const v = VENDORS[vendor]
  return (
    <img
      src={vendorLogo(vendor)}
      alt={t.meta.logoOf.replace('{name}', v.name)}
      width={256}
      height={256}
      loading="eager"
      decoding="async"
      className={`${SIZES[size]} shrink-0 border border-line bg-bg object-cover ${className}`}
    />
  )
}
