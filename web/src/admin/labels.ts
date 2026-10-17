/** The panel's words for the codes the API sends. */
import type { AccessClass } from '../../server/access/classify'

export const INTERESTS: Record<string, string> = {
  conversa: 'Entender se serve',
  producao: 'Plano Produção',
  regulado: 'Plano Regulado',
  docs: 'Documentação',
  seguranca: 'Segurança',
}

/** Which form sent the request. */
export const FORMS: Record<string, string> = {
  full: 'Formulário completo (/enterprise)',
  quick: 'Formulário curto',
}

/**
 * The visit's origin, as the panel names it: the utm_source or the referring host the API saved
 * (server/admin/routes.ts, `source`). AI assistants and search engines are named so the founder reads
 * "ChatGPT" and not "chatgpt.com"; anything else keeps the host.
 */
const SOURCE_NAMES: [RegExp, string][] = [
  [/^(chatgpt\.com|chat\.openai\.com|openai\.com)$/, 'ChatGPT'],
  [/^(perplexity\.ai|www\.perplexity\.ai)$/, 'Perplexity'],
  [/^(claude\.ai|anthropic\.com)$/, 'Claude'],
  [/^(gemini\.google\.com|bard\.google\.com)$/, 'Gemini'],
  [/^copilot\.microsoft\.com$/, 'Copilot'],
  [/^(www\.)?google\.[a-z.]+$/, 'Google'],
  [/^(www\.)?bing\.com$/, 'Bing'],
  [/^(duckduckgo\.com|search\.brave\.com)$/, 'Outro buscador'],
  [/^((www|br|pt)\.)?linkedin\.com$|^lnkd\.in$/, 'LinkedIn'],
  [/^(t\.co|x\.com|twitter\.com)$/, 'X'],
  [/^(github\.com|www\.github\.com)$/, 'GitHub'],
  [/^(news\.ycombinator\.com)$/, 'Hacker News'],
  [/^((www|old)\.)?reddit\.com$/, 'Reddit'],
  [/^(www\.)?tabnews\.com\.br$/, 'TabNews'],
  [/^(www\.)?youtube\.com$|^youtu\.be$/, 'YouTube'],
  [/^docs\.niadra\.com$/, 'Documentação'],
  [/^(www\.)?pypi\.org$/, 'PyPI'],
  [/^(www\.)?npmjs\.com$/, 'npm'],
]

export function sourceName(source: string | null | undefined): string {
  if (!source || source === 'direto') return 'Direto'
  const s = source.toLowerCase()
  for (const [re, name] of SOURCE_NAMES) if (re.test(s)) return name
  return source
}

export const HEARD: Record<string, string> = {
  ia: 'Resposta de IA',
  busca: 'Busca',
  linkedin: 'LinkedIn',
  indicacao: 'Indicação',
  evento: 'Evento',
  outro: 'Outro',
}

/** Classes in the order the panel shows them: people first, then who reads for an AI, then the rest. */
export const CLASS_ORDER: AccessClass[] = ['human', 'ai', 'search', 'scanner', 'bot']

export const CLASS_LABEL: Record<AccessClass, string> = {
  human: 'Pessoas',
  ai: 'IAs',
  search: 'Buscadores',
  scanner: 'Scanners',
  bot: 'Outros robôs',
}

/** What each class holds, under its total. */
export const CLASS_HINT: Record<AccessClass, string> = {
  human: 'Navegadores de verdade',
  ai: 'Robôs e buscas de IA, por nome',
  search: 'Google, Bing e outros buscadores',
  scanner: 'Procuram falhas: /.env, /.git',
  bot: 'Prévias de link, SEO, scripts',
}

/** Lower case, inside a sentence: "requisições de pessoas por dia". */
export const CLASS_OF: Record<AccessClass, string> = {
  human: 'pessoas',
  ai: 'IAs',
  search: 'buscadores',
  scanner: 'scanners',
  bot: 'outros robôs',
}

export const PURPOSE: Record<string, string> = {
  training: 'Treino de modelo',
  search: 'Busca com IA',
  user: 'Pedido de uma pessoa',
}
