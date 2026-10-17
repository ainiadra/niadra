import { describe, expect, it } from 'vitest'
import { PRICE, ROWS, SYSTEMS } from './comparison'
import { dicts } from '../i18n'
import { allPaths, metaFor, withLang } from '../seo'

const MARKS = new Set(['yes', 'no', 'partial', 'undocumented'])
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/

/**
 * The rules of the comparison page: every cell is one of the four marks, cites a page on the vendor's
 * own site and says when it was read; nothing is invented, and the price is quoted, never converted.
 */
describe('the feature comparison', () => {
  it('has every system on every row, each cell with a mark, a source and the date it was read', () => {
    for (const row of ROWS) {
      for (const s of SYSTEMS) {
        const cell = row.cells[s.key]
        expect(cell, `${row.key}/${s.key}`).toBeDefined()
        expect(MARKS.has(cell.v)).toBe(true)
        expect(cell.src).toMatch(/^https:\/\//)
        expect(cell.read).toMatch(ISO_DAY)
        if (cell.v === 'partial') expect(cell.note, `${row.key}/${s.key} needs a note`).toBeDefined()
      }
    }
    for (const s of SYSTEMS) {
      expect(PRICE[s.key].src).toMatch(/^https:\/\//)
      expect(PRICE[s.key].read).toMatch(ISO_DAY)
    }
  })

  it('says what Niadra lacks: the open source row reads no', () => {
    expect(ROWS.find((r) => r.key === 'oss')?.cells.niadra.v).toBe('no')
  })

  it('never uses a dash as punctuation in the notes', () => {
    expect(JSON.stringify(ROWS) + JSON.stringify(PRICE)).not.toMatch(/[–—]/)
  })

  it('is published in both languages, linked from the resources menu and the footer', () => {
    const paths = new Set(allPaths())
    for (const lang of ['pt', 'en'] as const) {
      expect(paths.has(withLang('/comparativo', lang))).toBe(true)
      const m = metaFor(withLang('/comparativo', lang))
      expect(m.noindex).toBe(false)
      expect(m.title).toContain(lang === 'pt' ? 'Comparativo' : 'Comparison')
      expect(dicts[lang].nav.resourceItems.some((i) => i.to === '/comparativo')).toBe(true)
      expect(dicts[lang].footer.groups.some((g) => g.links.some((l) => l.to === '/comparativo'))).toBe(true)
    }
  })
})
