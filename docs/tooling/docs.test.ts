/**
 * What must hold before each commit: the contract and the reference match, the
 * tables cover the contract, the generated files are up to date and the pages follow
 * the brand rules. `npm run check` runs this, `mint validate` and `mint broken-links`.
 */
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { API_GROUPS } from './api-pages'
import { localizedSpec, PACK_PAGES, readContract, readPackExamples, withPack } from './build'
import { MCP_TOOLS, SDK_VERSIONS } from './facts'
import { ACCESS, accessKey } from './openapi/access'
import { brokenRefs, exampleProblem, operationKeys, translationKeys } from './openapi/model'
import { PT } from './openapi/pt'
import { EN, EXAMPLES } from './openapi/tables'
import type { Lang, SpecId } from './types'

const ROOT = join(__dirname, '..')
const PAGES = API_GROUPS.flatMap((g) => g.pages)
const SPECS: SpecId[] = ['cell', 'control']
const LANGS: Lang[] = ['pt', 'en']
const contract = { cell: readContract('cell'), control: readContract('control') }
const docs = JSON.parse(readFileSync(join(ROOT, 'docs.json'), 'utf8'))

describe('contract and reference', () => {
  it('has no broken $ref, and every contract route has a page, and vice versa', () => {
    for (const spec of SPECS) {
      expect(brokenRefs(contract[spec])).toEqual([])
      const inContract = new Set(operationKeys(contract[spec]))
      const inPages = new Set(PAGES.filter((p) => p.spec === spec).map((p) => `${p.method} ${p.path}`))
      expect([...inContract].sort()).toEqual([...inPages].sort())
    }
  })

  it('knows who can call each route', () => {
    for (const p of PAGES) expect(ACCESS[accessKey(p.spec, p.method, p.path)], `${p.spec} ${p.method} ${p.path}`).toBeDefined()
    expect(Object.keys(ACCESS)).toHaveLength(PAGES.length)
  })

  it('has handwritten examples that fit the contract', () => {
    for (const [key, extra] of Object.entries(EXAMPLES)) {
      const spec = key.startsWith('control:') ? contract.control : contract.cell
      const [method, path] = key.replace(/^control:/, '').split(' ')
      const o = spec.paths[path]?.[method]
      expect(o, key).toBeDefined()
      if (extra.request !== undefined) expect(exampleProblem(spec, o.requestBody.content['application/json'].schema, extra.request), `${key} request`).toBeUndefined()
      for (const [status, ex] of Object.entries(extra.responses ?? {})) {
        const schema = o.responses[status]?.content?.['application/json']?.schema
        expect(schema, `${key} ${status}`).toBeDefined()
        expect(exampleProblem(spec, schema, ex), `${key} ${status}`).toBeUndefined()
      }
    }
  })

  it('adds English descriptions only where the contract has none', () => {
    for (const spec of SPECS) {
      const own = new Set(translationKeys(contract[spec]).map((k) => (spec === 'control' ? `control:${k}` : k)))
      for (const k of Object.keys(EN)) if (k.startsWith('control:') === (spec === 'control')) expect(own.has(k), `redundant: ${k}`).toBe(false)
    }
  })

  it('has the Portuguese text of every description', () => {
    const missing: string[] = []
    for (const spec of SPECS) {
      for (const k of translationKeys(contract[spec])) {
        const key = k.startsWith('res:*:') ? k : spec === 'control' ? `control:${k}` : k
        if (!(key in PT)) missing.push(key)
      }
    }
    for (const k of Object.keys(EN)) if (!(k in PT)) missing.push(k)
    expect(missing).toEqual([])
  })
})

describe('generated files', () => {
  const pages = new Map(PAGES.map((p) => [`${p.spec} ${p.method} ${p.path}`, p]))

  it('are up to date with the contract and the tables (run `npm run build`)', () => {
    for (const lang of LANGS) {
      for (const spec of SPECS) {
        const file = join(ROOT, 'openapi', lang, `${spec}.json`)
        expect(JSON.parse(readFileSync(file, 'utf8')), file).toEqual(localizedSpec(spec, lang, pages))
      }
    }
  })

  it('leave no $defs for Mintlify to resolve', () => {
    for (const lang of LANGS) for (const spec of SPECS) expect(readFileSync(join(ROOT, 'openapi', lang, `${spec}.json`), 'utf8')).not.toContain('$defs')
  })
})

describe('pack examples', () => {
  it('show the pack the server compiled for the synthetic customer (run `npm run sync`)', () => {
    const examples = readPackExamples()
    const pack = examples.packs.en!.voice!
    expect(examples.compiler_version).toMatch(/^compiler-/)
    for (const page of PACK_PAGES) {
      const src = readFileSync(join(ROOT, `${page}.mdx`), 'utf8')
      expect(withPack(src, pack.text, page), page).toBe(src)
    }
  })
})

// The words and characters the brand does not use.
const BANNED = [/\bchatbot\b/i, /\bbot\b/i, /\bBrasil\b/, /\bCPF\b/, /\bCNPJ\b/, /\brascunho\b/i, /\bem breve\b/i, /\bcoming soon\b/i, /\brecibo/i, /\bTrama\b/, /\bRégua\b/, /\bCrivo\b/, /\bTecido\b/, /\bMeada\b/, /\bTear\b/, /[—–]/, /região que você escolh/i, /region you choos/i, /niadra-spec/]

const navPages = (node: unknown): string[] => {
  if (typeof node === 'string') return [node]
  if (Array.isArray(node)) return node.flatMap(navPages)
  if (node && typeof node === 'object') {
    const o = node as Record<string, unknown>
    return [...navPages(o.tabs ?? []), ...navPages(o.groups ?? []), ...navPages(o.pages ?? [])]
  }
  return []
}

describe('pages', () => {
  for (const entry of docs.navigation.languages) {
    const pages = navPages(entry)
    it(`${entry.language}: every menu page exists and follows the brand rules`, () => {
      expect(pages.length).toBeGreaterThan(0)
      for (const page of pages) {
        const file = join(ROOT, `${page}.mdx`)
        expect(existsSync(file), file).toBe(true)
        const src = readFileSync(file, 'utf8')
        for (const re of BANNED) expect(src, `${page}: ${re}`).not.toMatch(re)
        // The title comes from the frontmatter; a "# " outside code would be a second title.
        expect(/^# /m.test(src.replace(/```[\s\S]*?```/g, '')), page).toBe(false)
      }
    })
  }
})

const menuFiles = (): string[] => [
  ...docs.navigation.languages.flatMap((entry: unknown) => navPages(entry).map((page) => `${page}.mdx`)),
  'llms.txt',
  'skill/niadra/SKILL.md',
]

describe('product facts in one place (tooling/facts.ts)', () => {
  const read = (file: string) => readFileSync(join(ROOT, file), 'utf8')

  it('every page and llms.txt name the published SDK versions', () => {
    const current: [RegExp, string][] = [
      [/pip install niadra\s+# (\d+\.\d+\.\d+)/g, SDK_VERSIONS.python],
      [/npm install @niadra\/sdk\s+# (\d+\.\d+\.\d+)/g, SDK_VERSIONS.typescript],
      [/`niadra` (\d+\.\d+\.\d+) (?:on|no) PyPI/g, SDK_VERSIONS.python],
      [/`@niadra\/sdk` (\d+\.\d+\.\d+) (?:on|no) npm/g, SDK_VERSIONS.typescript],
    ]
    let seen = 0
    for (const file of menuFiles()) {
      const src = read(file)
      for (const [re, version] of current) {
        for (const m of src.matchAll(re)) {
          seen += 1
          expect(m[1], `${file}: ${m[0]}`).toBe(version)
        }
      }
    }
    expect(seen).toBeGreaterThanOrEqual(6)
  })

  it('the MCP pages count the tools the server lists', () => {
    const words: Record<string, number> = {
      one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12,
      um: 1, uma: 1, dois: 2, duas: 2, 'três': 3, quatro: 4, cinco: 5, seis: 6, sete: 7, oito: 8, nove: 9, dez: 10, onze: 11, doze: 12,
    }
    const groups = new Set<number>([MCP_TOOLS.customer.length, MCP_TOOLS.agentMemory.length, MCP_TOOLS.history.length])
    const count = /\b([a-zê]+)\s+(customer\s+tools|tools|ferramentas\s+do\s+cliente|ferramentas|do\s+cliente)\b/gi
    for (const file of menuFiles()) {
      const src = read(file)
      for (const m of src.matchAll(count)) {
        const n = words[m[1]!.toLowerCase()]
        if (n === undefined) continue
        // Only where the text speaks of the MCP server.
        if (!src.slice(Math.max(0, m.index! - 240), m.index!).includes('MCP') && !/\/mcp/.test(file)) continue
        const where = `${file}: ${m[0]}`
        if (/customer|cliente/i.test(m[2]!)) expect(n, where).toBe(MCP_TOOLS.customer.length)
        else expect(groups.has(n), where).toBe(true)
      }
    }
  })

  it('the MCP guide lists exactly the customer tools', () => {
    for (const file of ['guides/mcp.mdx', 'en/guides/mcp.mdx']) {
      const rows = [...read(file).matchAll(/^\| `([a-z_]+)` \|/gm)].map((m) => m[1])
      expect(rows.sort(), file).toEqual([...MCP_TOOLS.customer].sort())
    }
  })
})

