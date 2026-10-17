import { describe, expect, it } from 'vitest'
import { dicts } from './i18n'
import { GROUPS, PRODUCT_PATHS, PRODUCTS } from './products'
import { allPaths, metaFor, withLang } from './seo'

const langs = ['pt', 'en'] as const

describe('product catalog', () => {
  it('puts each product in exactly one group', () => {
    const grouped = GROUPS.flatMap((g) => g.slugs as readonly string[])
    expect([...grouped].sort()).toEqual(PRODUCTS.map((p) => p.slug).sort())
  })

  it('has the text of each product in both languages', () => {
    for (const lang of langs) {
      for (const p of PRODUCTS) {
        const c = dicts[lang].products[p.key]
        for (const field of [c.name, c.menu, c.line, c.body, c.title, c.description]) expect(field.trim()).not.toBe('')
      }
    }
  })

  it('publishes one page per product, with its own title, in both languages', () => {
    const paths = new Set(allPaths())
    for (const lang of langs) {
      const titles = PRODUCT_PATHS.map((path) => {
        expect(paths.has(withLang(path, lang))).toBe(true)
        const m = metaFor(withLang(path, lang))
        expect(m.noindex).toBe(false)
        return m.title
      })
      expect(new Set(titles).size).toBe(titles.length)
    }
  })

  it('links the home fronts and the footer only to products that exist', () => {
    const known = new Set<string>(PRODUCTS.map((p) => p.slug))
    for (const lang of langs) {
      for (const s of dicts[lang].home.solutions) for (const slug of s.products) expect(known.has(slug)).toBe(true)
      const footer = dicts[lang].footer.groups[0].links.map((l) => l.to)
      expect([...footer].sort()).toEqual([...PRODUCT_PATHS].sort())
    }
  })

  it('links each product to a docs page at docs.niadra.com', () => {
    for (const p of PRODUCTS) expect(p.docs).toMatch(/^\/[a-z-]+(\/[a-z-]+)*$/)
  })
})
