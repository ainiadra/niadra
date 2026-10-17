import { createContext, useContext } from 'react'
import { pt } from './pt'
import { en } from './en'

/** Portuguese defines the shape. A key missing in English breaks the build. */
export type Dict = typeof pt
export type Lang = 'pt' | 'en'

export const dicts: Record<Lang, Dict> = { pt, en }

/**
 * The language lives in the URL: Portuguese at the root, English under /en. Each page
 * exists at both addresses, prerendered, so search engines and AI read both
 * versions without running JavaScript.
 */
export const langOfPath = (pathname: string): Lang =>
  pathname === '/en' || pathname.startsWith('/en/') ? 'en' : 'pt'

/** The path without the language prefix: '/en/preco' becomes '/preco', '/en' becomes '/'. */
export const stripLang = (pathname: string): string =>
  langOfPath(pathname) === 'en' ? pathname.slice(3) || '/' : pathname

/**
 * The docs live outside the site, on Mintlify, with the same language layout:
 * Portuguese at the root, English under /en. The texts keep the Portuguese address.
 */
export const DOCS = 'https://docs.niadra.com'

/** Adds the language prefix to an internal link ('/preco#x', '/enterprise?y'). */
export function localize(to: string, lang: Lang): string {
  if (to === DOCS || to.startsWith(`${DOCS}/`)) return lang === 'en' ? `${DOCS}/en${to.slice(DOCS.length)}` : to
  if (!to.startsWith('/') || to.startsWith('//')) return to
  if (lang === 'pt') return to
  if (langOfPath(to.split(/[?#]/)[0]) === 'en') return to
  return to === '/' ? '/en' : to.startsWith('/?') || to.startsWith('/#') ? `/en${to.slice(1)}` : `/en${to}`
}

const KEY = 'niadra.lang'

/** Portuguese-speaking time zones: Brazil, Portugal and PALOP. */
const PT_TZ = new Set([
  'America/Sao_Paulo', 'America/Bahia', 'America/Fortaleza', 'America/Recife',
  'America/Belem', 'America/Maceio', 'America/Araguaina', 'America/Campo_Grande',
  'America/Cuiaba', 'America/Manaus', 'America/Boa_Vista', 'America/Porto_Velho',
  'America/Rio_Branco', 'America/Eirunepe', 'America/Santarem', 'America/Noronha',
  'Europe/Lisbon', 'Atlantic/Azores', 'Atlantic/Madeira', 'Africa/Luanda',
  'Africa/Maputo', 'Africa/Bissau', 'Africa/Sao_Tome', 'Atlantic/Cape_Verde',
])

export function savedLang(): Lang | null {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'pt' || v === 'en') return v
  } catch { /* private mode */ }
  return null
}

/**
 * Saved choice, then device language, then time zone. The time zone counts because a
 * phone set to English in Brazil still belongs, almost always, to someone who
 * reads Portuguese.
 */
export function detectLang(): Lang {
  const saved = savedLang()
  if (saved) return saved

  try {
    for (const l of navigator.languages?.length ? navigator.languages : [navigator.language]) {
      const tag = l?.toLowerCase() ?? ''
      if (tag.startsWith('pt')) return 'pt'
      if (tag.startsWith('en')) break
    }
  } catch { /* no navigator */ }

  try {
    if (PT_TZ.has(Intl.DateTimeFormat().resolvedOptions().timeZone)) return 'pt'
  } catch { /* no Intl */ }

  return 'en'
}

export function persistLang(l: Lang) {
  try { localStorage.setItem(KEY, l) } catch { /* holds for this visit only */ }
}

/**
 * Search and AI crawlers are never redirected: they need to index the Portuguese
 * version at the root, even if their browser says "en-US".
 */
export const isCrawler = (ua: string) =>
  /bot|crawl|spider|slurp|preview|headless|lighthouse|facebookexternalhit|embedly|quora|whatsapp|telegram/i.test(ua)

export const I18n = createContext<{ lang: Lang; t: Dict; setLang: (l: Lang) => void }>({
  lang: 'pt', t: pt, setLang: () => {},
})

export const useT = () => useContext(I18n)
