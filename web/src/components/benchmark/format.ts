/**
 * The only way a number reaches the /benchmark page. Benchmark.test.tsx formats every number of the
 * results file with these same functions and fails if the page shows a number outside that set.
 */
import type { Lang } from '../../i18n'

const LOCALE: Record<Lang, string> = { pt: 'pt-BR', en: 'en-US' }

// One formatter per language and precision: building an Intl.NumberFormat is slow, and a run has thousands of numbers.
const cache = new Map<string, Intl.NumberFormat>()
const intl = (lang: Lang, min: number, max: number) => {
  const key = `${lang}:${min}:${max}`
  let f = cache.get(key)
  if (!f) cache.set(key, (f = new Intl.NumberFormat(LOCALE[lang], { minimumFractionDigits: min, maximumFractionDigits: max })))
  return f
}

const nf = (lang: Lang, digits: number) => intl(lang, 0, digits)

/**
 * Which way a value is rounded to the digits the page shows. The page never rounds in Niadra's favor:
 * Niadra's values lean against it (down where higher is better, up where lower is better) and every
 * other system's lean toward it. `nearest` is for the references and for counts that need no rounding.
 */
export type Lean = 'nearest' | 'up' | 'down'

export function leanTo(v: number, digits: number, lean: Lean) {
  if (lean === 'nearest') return v
  const k = 10 ** digits
  // Strip float noise first (16.1 * 10 is 161.00000000000003), so an exact value stays as it is.
  const x = Math.round(v * k * 1e6) / 1e6
  return (lean === 'up' ? Math.ceil(x) : Math.floor(x)) / k
}

/** Milliseconds: one decimal under 100, whole above (the harness writes one decimal). */
export const fmtMs = (lang: Lang, v: number, lean: Lean = 'nearest') => {
  const digits = Math.abs(v) < 100 ? 1 : 0
  return `${nf(lang, digits).format(leanTo(v, digits, lean))} ms`
}

/** A share (0 to 1) as a percentage with one decimal. */
export const fmtPct = (lang: Lang, v: number, lean: Lean = 'nearest') => `${nf(lang, 1).format(leanTo(v * 100, 1, lean))}%`

/** Dollars with cents; four decimals under one cent so a small cost does not read as zero. */
export const fmtUsd = (lang: Lang, v: number, lean: Lean = 'nearest') => {
  const digits = v !== 0 && Math.abs(v) < 0.01 ? 4 : 2
  const n = intl(lang, 2, digits).format(leanTo(v, digits, lean))
  return lang === 'pt' ? `US$ ${n}` : `$${n}`
}

export const fmtInt = (lang: Lang, v: number, lean: Lean = 'nearest') => nf(lang, 0).format(leanTo(v, 0, lean))

export const fmtNum = (lang: Lang, v: number, lean: Lean = 'nearest') => nf(lang, 2).format(leanTo(v, 2, lean))

/** The run's date (YYYY-MM-DD in the file) as the language writes it. */
export function fmtDate(lang: Lang, iso: string) {
  const [y, m, d] = iso.slice(0, 10).split('-')
  return lang === 'pt' ? `${d}/${m}/${y}` : `${m}/${d}/${y}`
}

const LEANS: Lean[] = ['nearest', 'up', 'down']

/** Every way the page may write a value: each formatter, each rounding direction. */
export const FORMATTERS = [fmtMs, fmtPct, fmtUsd, fmtInt, fmtNum].flatMap((f) => LEANS.map((lean) => (lang: Lang, v: number) => f(lang, v, lean)))

/** Every run of digits in a text, the way the test reads the page. */
export const numbersIn = (text: string) => text.match(/\d+(?:[.,]\d+)*/g) ?? []
