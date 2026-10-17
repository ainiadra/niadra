import { describe, expect, it } from 'vitest'
import { CODE } from './content/integrations/code'
import { dicts } from './i18n'
import { INTEGRATIONS, INTEGRATIONS_PATH, INTEGRATION_PATHS, integrationPath } from './integrations'
import { allPaths, metaFor, withLang } from './seo'

const langs = ['pt', 'en'] as const

describe('integration pages', () => {
  it('publishes one page per integration, with its own title, in both languages, plus the index', () => {
    const paths = new Set(allPaths())
    for (const lang of langs) {
      expect(paths.has(withLang(INTEGRATIONS_PATH, lang))).toBe(true)
      const titles = INTEGRATION_PATHS.map((path) => {
        expect(paths.has(withLang(path, lang))).toBe(true)
        const m = metaFor(withLang(path, lang))
        expect(m.noindex).toBe(false)
        expect(m.alternates.pt && m.alternates.en).toBeTruthy()
        return m.title
      })
      expect(new Set(titles).size).toBe(titles.length)
    }
  })

  it('has the full text of each page in both languages, with real questions and the mechanism of every primitive', () => {
    for (const lang of langs) {
      for (const i of INTEGRATIONS) {
        const p = dicts[lang].integrations.pages[i.key]
        for (const field of [p.name, p.title, p.description, p.h1a, p.h1b, p.sub]) expect(field.trim()).not.toBe('')
        expect(p.problem.length).toBeGreaterThanOrEqual(2)
        for (const k of ['context', 'turns', 'tools', 'verify', 'handoff'] as const) expect(p.how[k].length).toBeGreaterThan(20)
        expect(p.limits.length).toBeGreaterThanOrEqual(3)
        expect(p.faq.length).toBeGreaterThanOrEqual(3)
        for (const f of p.faq) expect(f.q.endsWith('?')).toBe(true)
      }
    }
  })

  it('carries the FAQ of each page as structured data, the same questions the page shows', () => {
    for (const lang of langs) {
      for (const i of INTEGRATIONS) {
        const m = metaFor(withLang(integrationPath(i.slug), lang))
        const faq = m.jsonLd.find((n) => (n as { '@type': string })['@type'] === 'FAQPage') as { mainEntity: { name: string }[] } | undefined
        const t = dicts[lang].integrations
        expect(faq?.mainEntity.map((q) => q.name)).toEqual([...t.pages[i.key].faq, ...t.sharedFaq].map((q) => q.q))
      }
    }
  })

  it('shows the code of every adapter that exists in a published SDK, and only those', () => {
    for (const i of INTEGRATIONS) {
      const sample = CODE[i.key]
      expect(Boolean(sample.python)).toBe(i.python !== null)
      expect(Boolean(sample.typescript)).toBe(i.ts !== null)
      if (i.python) expect(sample.install.python).toContain(`niadra[${i.python}]`)
      if (i.ts) expect(sample.typescript).toContain(`@niadra/sdk/${i.ts}`)
      // The snippet is the docs' example, which imports the real adapter module.
      if (i.python) expect(sample.python).toMatch(/from niadra(\.integrations\.[a-z_]+)? import/)
    }
    // The n8n node has no snippet, so its page says how to install it instead.
    for (const lang of langs) expect(dicts[lang].integrations.pages.n8n.install).toContain('n8n-nodes-niadra')
  })

  it('links the index from the Resources menu and the footer, in both languages', () => {
    for (const lang of langs) {
      expect(dicts[lang].nav.resourceItems.some((r) => r.to === INTEGRATIONS_PATH)).toBe(true)
      expect(dicts[lang].footer.groups[1].links.some((l) => l.to === INTEGRATIONS_PATH)).toBe(true)
    }
  })
})
