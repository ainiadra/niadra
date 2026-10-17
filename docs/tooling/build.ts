/**
 * Builds the API reference from the contract (tooling/openapi/cell.json and
 * control.json, exported from the server) and from the tables in this folder:
 *
 *   openapi/<pt|en>/<cell|control>.json   the OpenAPI of each language, with the translated
 *                                         descriptions, the examples, the SDK call and
 *                                         who can call each route
 *   api/**.mdx and en/api/**.mdx          one page per endpoint, which Mintlify
 *                                         fills in from the OpenAPI
 *   docs.json                             the API reference tab of each language
 *
 * The generated files go into the repository (Mintlify publishes what is in it).
 * Run after changing the contract or the tables: `npm run build`.
 */
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { API_GROUPS } from './api-pages'
import { accessSection } from './openapi/access-text'
import { ACCESS, accessKey, type Access } from './openapi/access'
import type { Json, Spec } from './openapi/model'
import { GENERIC_RESPONSES } from './openapi/model'
import { PT } from './openapi/pt'
import { SDK_SAMPLES } from './openapi/samples'
import { EN, EXAMPLES } from './openapi/tables'
import type { ApiPage, Lang, SpecId } from './types'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const LANGS: Lang[] = ['pt', 'en']
const SPECS: SpecId[] = ['cell', 'control']
const METHODS = ['get', 'post', 'put', 'patch', 'delete'] as const

export const readContract = (spec: SpecId): Spec => JSON.parse(readFileSync(join(ROOT, 'tooling', 'openapi', `${spec}.json`), 'utf8'))

/** Control API routes carry the `control:` prefix in the tables: the two APIs have routes with the same path. */
const prefixed = (spec: SpecId, key: string) => (spec === 'control' ? `control:${key}` : key)

/** The translation for one language: that language's table, then the English descriptions of the documentation. */
function translator(spec: SpecId, lang: Lang) {
  return (k: string): string | undefined => {
    const key = prefixed(spec, k)
    const generic = k.startsWith('res:*:') ? k : undefined
    if (lang === 'pt') return PT[key] ?? (generic && PT[generic]) ?? EN[key]
    return EN[key]
  }
}

const INFO: Record<SpecId, Record<Lang, { title: string; description: string }>> = {
  cell: {
    pt: { title: 'API de dados da Niadra', description: 'Escrita, contexto, histórico, objetos, identidade, privacidade e governança de um espaço. Cada espaço tem um endereço estável, com o espaço e a região no nome.' },
    en: { title: 'Niadra data API', description: 'Writing, context, history, objects, identity, privacy and governance of one space. Every space has a stable address, with the space and the region in its name.' },
  },
  control: {
    pt: { title: 'API de controle da Niadra', description: 'Tenants, projetos, fontes, chaves, pessoas, configuração e uso. Nunca recebe conteúdo de cliente.' },
    en: { title: 'Niadra control API', description: 'Tenants, projects, sources, keys, people, configuration and usage. It never receives customer content.' },
  },
}

function servers(spec: SpecId, lang: Lang): Json[] {
  if (spec === 'control') return [{ url: 'https://control.api.niadra.com' }]
  const pt = lang === 'pt'
  return [
    {
      url: 'https://{space}.{region}.api.niadra.com',
      variables: {
        space: { default: 'acme-prod', description: pt ? 'O espaço, que vem na chave de fonte.' : 'The space, which comes in the source key.' },
        region: { default: 'us-east-2', description: pt ? 'A região do espaço, que também vem na chave.' : 'The region of the space, which also comes in the key.' },
      },
    },
  ]
}

const SCHEMES: Record<SpecId, Json> = {
  cell: {
    sourceKey: { type: 'http', scheme: 'bearer', description: 'nia_sk_...' },
    personToken: { type: 'http', scheme: 'bearer', bearerFormat: 'JWT' },
    subjectToken: { type: 'apiKey', in: 'header', name: 'Niadra-Subject-Token' },
    cellToken: { type: 'apiKey', in: 'header', name: 'X-Niadra-Cell-Token' },
    operatorToken: { type: 'apiKey', in: 'header', name: 'X-Niadra-Operator-Token' },
  },
  control: {
    personToken: { type: 'http', scheme: 'bearer', bearerFormat: 'JWT' },
    cellToken: { type: 'apiKey', in: 'header', name: 'X-Niadra-Cell-Token' },
    operatorToken: { type: 'apiKey', in: 'header', name: 'X-Niadra-Operator-Token' },
  },
}

/** What the playground asks for to call the route, based on who can call it. */
function security(access: Access | undefined): Json[] {
  switch (access?.k) {
    case 'source':
    case 'analytics':
      return [{ sourceKey: [] }]
    case 'governance':
    case 'sourceOrPerson':
      return [{ sourceKey: [] }, { personToken: [] }]
    case 'person':
    case 'controlPerson':
      return [{ personToken: [] }]
    case 'mcp':
      return [{ sourceKey: [], subjectToken: [] }]
    case 'cell':
      return [{ cellToken: [] }]
    case 'operator':
      return [{ operatorToken: [] }]
    case 'cellOrOperator':
      return [{ cellToken: [] }, { operatorToken: [] }]
    default:
      return []
  }
}

/**
 * Pydantic puts `$defs` inside some schemas and points to them with
 * `#/$defs/<name>`, which does not resolve outside the schema. The `$defs` move up to
 * `components.schemas` (under the same name, where identical copies already exist) and
 * the references are rewritten to point to them.
 */
function hoistDefs(doc: Spec) {
  const schemas: Record<string, Json> = (doc.components ??= {}).schemas ??= {}
  const walk = (v: Json) => {
    if (Array.isArray(v)) return v.forEach(walk)
    if (!v || typeof v !== 'object') return
    if (typeof v.$ref === 'string' && v.$ref.startsWith('#/$defs/')) v.$ref = `#/components/schemas/${v.$ref.slice(8)}`
    // The discriminator also points by reference: the `type` field of each batch item.
    if (v.discriminator?.mapping) {
      for (const [k, ref] of Object.entries<Json>(v.discriminator.mapping))
        if (typeof ref === 'string' && ref.startsWith('#/$defs/')) v.discriminator.mapping[k] = `#/components/schemas/${ref.slice(8)}`
    }
    if (v.$defs) {
      const defs: Record<string, Json> = v.$defs
      delete v.$defs
      for (const [name, s] of Object.entries(defs)) {
        walk(s)
        schemas[name] ??= s
      }
    }
    for (const x of Object.values(v)) walk(x)
  }
  walk(doc.paths)
  for (const s of Object.values(schemas)) walk(s)
}

/** Translates the description of every named schema (those in components and the scattered $defs) and of its fields. */
function translateSchemas(doc: Spec, tr: (k: string) => string | undefined) {
  const named = (name: string, s: Json) => {
    if (!s || typeof s !== 'object') return
    const own = tr(name)
    if (own) s.description = own
    for (const [f, v] of Object.entries<Json>(s.properties ?? {})) {
      const d = tr(`${name}.${f}`)
      if (d && v && typeof v === 'object') v.description = d
    }
  }
  for (const [name, s] of Object.entries<Json>(doc.components?.schemas ?? {})) named(name, s)
  const walk = (v: Json) => {
    if (!v || typeof v !== 'object') return
    if (v.$defs) for (const [name, s] of Object.entries<Json>(v.$defs)) named(name, s)
    for (const x of Object.values(v)) walk(x)
  }
  walk(doc.paths)
  walk(doc.components)
}

export function localizedSpec(spec: SpecId, lang: Lang, pages: Map<string, ApiPage>): Spec {
  const doc: Spec = structuredClone(readContract(spec))
  const tr = translator(spec, lang)
  doc.info = { ...doc.info, ...INFO[spec][lang] }
  doc.servers = servers(spec, lang)
  doc.components = { ...doc.components, securitySchemes: SCHEMES[spec] }
  hoistDefs(doc)
  translateSchemas(doc, tr)

  for (const [path, item] of Object.entries<Json>(doc.paths ?? {})) {
    for (const method of METHODS) {
      const o = item?.[method]
      if (!o) continue
      const key = `${method} ${path}`
      const extra = EXAMPLES[prefixed(spec, key)] ?? {}
      const page = pages.get(`${spec} ${key}`)
      if (page) o.summary = page.title[lang]
      o.description = [tr(`op:${key}`) ?? o.description, accessSection(spec, method, path, lang)].filter(Boolean).join('\n\n')
      o.security = security(ACCESS[accessKey(spec, method, path)])

      for (const p of o.parameters ?? []) {
        p.description = tr(`param:${key}:${p.name}`) ?? tr(`param:${p.name}`) ?? p.description
        if (extra.params?.[p.name] !== undefined) p.example = extra.params[p.name]
      }
      const body = o.requestBody?.content
      if (body && extra.request !== undefined) {
        const ct = Object.keys(body)[0]
        body[ct].example = extra.request
      }
      for (const [status, r] of Object.entries<Json>(o.responses ?? {})) {
        const generic = GENERIC_RESPONSES.has(r.description)
        r.description = tr(`res:${key}:${status}`) ?? (generic ? tr(`res:*:${r.description}`) : undefined) ?? r.description
        const ex = extra.responses?.[status]
        if (ex !== undefined && r.content) {
          const ct = Object.keys(r.content)[0]
          r.content[ct].example = ex
        }
      }
      const sample = spec === 'cell' ? SDK_SAMPLES[key] : undefined
      if (sample) {
        o['x-codeSamples'] = [
          { lang: 'python', label: 'Python', source: sample.python },
          { lang: 'typescript', label: 'TypeScript', source: sample.typescript },
        ]
      }
    }
  }
  return doc
}

const pagePath = (slug: string, lang: Lang) => (lang === 'en' ? `en/${slug}` : slug)
const yamlString = (s: string) => JSON.stringify(s)

function endpointPage(p: ApiPage, lang: Lang): string {
  return [
    '---',
    `title: ${yamlString(p.title[lang])}`,
    `description: ${yamlString(p.description[lang])}`,
    `openapi: ${yamlString(`/openapi/${lang}/${p.spec}.json ${p.method.toUpperCase()} ${p.path}`)}`,
    '---',
    '',
  ].join('\n')
}

/** The reference tab in each language: the overview and the groups of API_GROUPS, in the same order. */
function apiTab(lang: Lang): Json {
  return {
    tab: lang === 'pt' ? 'Referência da API' : 'API reference',
    groups: [
      { group: lang === 'pt' ? 'Visão geral' : 'Overview', pages: [pagePath('api', lang)] },
      ...API_GROUPS.map((g) => ({ group: g.label[lang], pages: g.pages.map((p) => pagePath(p.slug, lang)) })),
    ],
  }
}

/** The Context Pack examples, from the server's compiler (tooling/pack/generate.py, run by `npm run sync`). */
type PackExamples = { server_commit: string; compiler_version: string; packs: Record<string, Record<string, { text: string; tokens: number; withheld: number }>> }
export const readPackExamples = (): PackExamples => JSON.parse(readFileSync(join(ROOT, 'tooling', 'pack', 'examples.json'), 'utf8'))

/**
 * The guide pages that show the voice agent's pack. Both languages show the English space's pack, as the
 * code samples beside it are the same in both.
 */
export const PACK_PAGES = ['index', 'concepts/context', 'en/index', 'en/concepts/context']
const PACK_MARK = '{/* The pack below is the server compiler output for a synthetic customer (tooling/pack): `npm run sync` writes it. */}'

/** The page with the code block right after PACK_MARK holding `text`; throws when the page has no marker. */
export function withPack(src: string, text: string, page: string): string {
  const at = src.indexOf(PACK_MARK)
  if (at < 0) throw new Error(`${page}: no pack example marker`)
  const open = src.indexOf('```', at + PACK_MARK.length)
  const body = src.indexOf('\n', open) + 1
  const close = src.indexOf('\n```', body - 1)
  if (open < 0 || body <= 0 || close < 0) throw new Error(`${page}: no code block after the pack example marker`)
  return src.slice(0, body) + text + src.slice(close)
}

function writePackExamples() {
  const pack = readPackExamples().packs.en!.voice!
  for (const page of PACK_PAGES) {
    const file = join(ROOT, `${page}.mdx`)
    const src = readFileSync(file, 'utf8')
    const next = withPack(src, pack.text, page)
    if (next !== src) writeFileSync(file, next)
  }
}

export function build() {
  writePackExamples()
  const pages = new Map(API_GROUPS.flatMap((g) => g.pages).map((p) => [`${p.spec} ${p.method} ${p.path}`, p]))
  for (const lang of LANGS) {
    for (const spec of SPECS) {
      const file = join(ROOT, 'openapi', lang, `${spec}.json`)
      mkdirSync(dirname(file), { recursive: true })
      writeFileSync(file, JSON.stringify(localizedSpec(spec, lang, pages), null, 2) + '\n')
    }
    // Endpoint pages are all generated: the folder is rebuilt from scratch, so the page of a removed route disappears.
    const dir = join(ROOT, lang === 'en' ? 'en/api' : 'api')
    rmSync(dir, { recursive: true, force: true })
    for (const p of pages.values()) {
      const file = join(ROOT, `${pagePath(p.slug, lang)}.mdx`)
      mkdirSync(dirname(file), { recursive: true })
      writeFileSync(file, endpointPage(p, lang))
    }
  }
  const docsFile = join(ROOT, 'docs.json')
  const docs = JSON.parse(readFileSync(docsFile, 'utf8'))
  for (const entry of docs.navigation.languages) {
    const lang: Lang = entry.language === 'en' ? 'en' : 'pt'
    // The reference tab is rebuilt in the same place in the menu (before the FAQ);
    // in a docs.json without it, it goes at the end.
    const at = entry.tabs.findIndex((t: Json) => t.tab === apiTab(lang).tab)
    if (at >= 0) entry.tabs[at] = apiTab(lang)
    else entry.tabs.push(apiTab(lang))
  }
  writeFileSync(docsFile, JSON.stringify(docs, null, 2) + '\n')
  console.log(`${pages.size} endpoints, ${LANGS.length} languages: openapi/, api/, en/api/ and docs.json updated`)
}

if (process.argv[1] === fileURLToPath(import.meta.url)) build()
