import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { dicts, type Lang } from './i18n'

const LANGS: Lang[] = ['pt', 'en']
const copy = (lang: Lang) => JSON.stringify(dicts[lang])

/**
 * The rules of the brand book that a test can hold: no dash as punctuation, never
 * "bot", no internal codename or planning id, and none of the promises the site never makes.
 */
describe('the copy of both languages', () => {
  it.each(LANGS)('has no em dash or en dash (%s)', (lang) => {
    expect(copy(lang)).not.toMatch(/[–—]/)
  })

  it.each(LANGS)('never says bot or chatbot (%s)', (lang) => {
    // Letter lookarounds instead of \b: JavaScript's word boundary is ASCII-only, and "botão" is a button.
    expect(copy(lang)).not.toMatch(/(?<!\p{L})(chat)?bots?(?!\p{L})/iu)
  })

  it.each(LANGS)('names no internal codename, planning id or phase (%s)', (lang) => {
    const text = copy(lang)
    expect(text).not.toMatch(/\b(EST|AFI|COO|REG|MED|PRV|OPS)-\d{2}\b/)
    expect(text).not.toMatch(/\bL(1[89]|2[0-7])\b/)
    expect(text).not.toMatch(/\b(fase|phase) [0-4]\b/i)
    expect(text).not.toMatch(/\b(Régua|Traço|Trama|Meada|Crivo|Tear|Tecido)\b/)
  })

  it.each(LANGS)('makes none of the promises the brand book forbids (%s)', (lang) => {
    const text = copy(lang)
    expect(text).not.toMatch(/sem alucina|hallucination-free|no hallucination/i)
    expect(text).not.toMatch(/100% (de )?(precis|accura)/i)
    expect(text).not.toMatch(/tempo real|real[- ]time/i)
    expect(text).not.toMatch(/a Niadra (impede|bloqueia|garante)|Niadra (prevents|blocks|guarantees)/i)
    expect(text).not.toMatch(/em breve|coming soon|\(meta\)|rascunho/i)
  })
})

/**
 * Since 02/10/2026 the site never says which models or which model provider Niadra uses, and it has no
 * subprocessors list: that is contract material. The check covers the dictionaries, the posts and the
 * benchmark results shipped in the bundle.
 */
describe('what the site never names', () => {
  const FORBIDDEN_SHA256 = new Set([
    '9e66a118b9a0fb8cada5eb0f357806a21cc067fa4b9d9f76eb9773a24e022438',
    '41418a61d982be103ecc30f2e00ca90bb4c4d1b2e107b9bca93f52ea0d10c6b3',
    '970ec274ca867815174ebe4eff19282000f9495a6c7254e94991d1fb4dc3df30',
    '7f871cbf905f6e0cd598b11609f33f609e60a61892ac6b37e80cf1de282ee367',
    'bc74d4c225d5b7d9149b370ab12c1f4aba41322dcb527d7ea1fa941028f176e8',
  ])
  const SUBPROCESSOR = /(?<![\p{L}\d])subprocess\w*(?![\p{L}\d])/iu
  const sha256 = (text: string) => createHash('sha256').update(text).digest('hex')

  function forbiddenNames(text: string): string[] {
    const found = new Set<string>()
    const words = new Set(text.toLowerCase().match(/[\p{L}\d]+(?:-[\p{L}\d]+)*/gu) ?? [])
    for (const word of words) {
      const parts = word.split('-')
      parts.forEach((part, i) => {
        const pair = i + 1 < parts.length ? `${part}-${parts[i + 1]}` : ''
        for (const token of [part, pair]) if (token && FORBIDDEN_SHA256.has(sha256(token))) found.add(token)
      })
    }
    if (SUBPROCESSOR.test(text)) found.add('subprocessor')
    return [...found]
  }

  it('recognizes a forbidden name by its hash, alone or inside a model id', () => {
    expect(forbiddenNames('a subprocessor list')).toEqual(['subprocessor'])
    expect(forbiddenNames('the extraction model, the decision model')).toEqual([])
    expect(forbiddenNames('lunar jevons, a type-safe api')).toEqual([])
  })

  it.each(LANGS)('names no model, no model provider and no subprocessor in the copy (%s)', (lang) => {
    expect(forbiddenNames(copy(lang))).toEqual([])
  })

  it('names no model in the posts or in the published benchmark results', async () => {
    const { readdirSync, readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const blog = join(__dirname, 'content', 'blog')
    for (const f of readdirSync(blog)) expect(forbiddenNames(readFileSync(join(blog, f), 'utf8')), f).toEqual([])
    for (const dir of ['runs', 'typed']) {
      const at = join(__dirname, 'content', 'benchmark', dir)
      for (const f of readdirSync(at)) expect(forbiddenNames(readFileSync(join(at, f), 'utf8')), f).toEqual([])
    }
  })
})
