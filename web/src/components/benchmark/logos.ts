/**
 * Each system's logo on /benchmark, in the manner of Artificial Analysis: the company's own mark before
 * the system's name. Every file sits in public/benchmark/logos, downloaded once from the owner's official
 * assets (never hotlinked), an SVG when the owner publishes one, otherwise a 40 px PNG (2x the 20 px it is
 * shown at). The marks belong to their owners and only identify the systems compared; the page says so.
 *
 * Where each file came from:
 * - niadra.svg       our own mark, public/favicon.svg
 * - mem0.svg         mem0.ai, the site's favicon (SVG); viewBox added so it scales
 * - zep.png          github.com/getzep, the organization avatar (Graphiti is Zep's)
 * - hindsight.png    hindsight.vectorize.io, the site's favicon (Hindsight's own mark)
 * - memobase.png     github.com/memodb-io, the organization avatar
 * - supermemory.svg  supermemory.ai, the site's favicon (SVG)
 * - memos.svg        memos.openmem.net, the site's logo (SVG)
 * - redis.png        redis.io, the site's icon
 * - honcho.svg       honcho.dev, the site's favicon (SVG)
 * - langchain.png    github.com/langchain-ai, the organization avatar (LangMem is LangChain's)
 * - cognee.png       cognee.ai, the site's favicon
 * - aws.png          aws.amazon.com, the site's touch icon (AgentCore Memory is AWS's)
 */

export const LOGO_DIR = '/benchmark/logos'

/** The file of each owner's mark, in LOGO_DIR. */
const LOGO_FILES = {
  niadra: 'niadra.svg',
  mem0: 'mem0.svg',
  zep: 'zep.png',
  hindsight: 'hindsight.png',
  memobase: 'memobase.png',
  supermemory: 'supermemory.svg',
  memos: 'memos.svg',
  redis: 'redis.png',
  honcho: 'honcho.svg',
  langchain: 'langchain.png',
  cognee: 'cognee.png',
  aws: 'aws.png',
} as const

type Owner = keyof typeof LOGO_FILES

/**
 * The owner of every system key the harness writes. A key not listed here (a reference, a system that
 * left the benchmark) takes a neutral icon, never a guessed logo.
 */
const OWNER: Record<string, Owner> = {
  niadra: 'niadra',
  mem0_oss: 'mem0',
  mem0_oss_rerank: 'mem0',
  mem0_platform: 'mem0',
  graphiti: 'zep',
  hindsight: 'hindsight',
  hindsight_reflect: 'hindsight',
  memobase: 'memobase',
  supermemory: 'supermemory',
  memos: 'memos',
  redis_agent_memory: 'redis',
  honcho: 'honcho',
  honcho_dialectic: 'honcho',
  langmem: 'langchain',
  cognee: 'cognee',
  agentcore_memory: 'aws',
}

export const SYSTEMS_WITH_LOGO = Object.keys(OWNER)

/** The logo's address, or null for a key with no logo. */
export const logoOf = (system: string): string | null => (OWNER[system] ? `${LOGO_DIR}/${LOGO_FILES[OWNER[system]]}` : null)

/**
 * A variant shares its base system's logo, so a short tag beside the base system's name tells them apart.
 * The tags are the names of the options themselves (a rerank step, Hindsight's reflect and Honcho's
 * dialectic calls), the same in both languages.
 */
export const VARIANTS: Record<string, { base: string; tag: string }> = {
  mem0_oss_rerank: { base: 'mem0_oss', tag: 'rerank' },
  hindsight_reflect: { base: 'hindsight', tag: 'reflect' },
  honcho_dialectic: { base: 'honcho', tag: 'dialectic' },
}
