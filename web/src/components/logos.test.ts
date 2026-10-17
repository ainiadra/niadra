import { readdirSync, readFileSync, statSync } from 'node:fs'
import { relative, resolve, sep } from 'node:path'
import { describe, expect, it } from 'vitest'
import { LOGO_DIR as BENCH_DIR, logoOf, SYSTEMS_WITH_LOGO } from './benchmark/logos'
import { LOGO_DIR as VENDOR_DIR, VENDORS, VENDOR_OF_NAME, vendorLogo } from './VendorLogo'
import { SYSTEMS } from '../content/comparison'
import { INTEGRATIONS } from '../integrations'
import { dicts } from '../i18n'

const PUBLIC = resolve(process.cwd(), 'public')

/** The file exists under public/, is not empty, and starts like the image its extension says. */
function expectImage(path: string) {
  const file = resolve(PUBLIC, path.replace(/^\//, ''))
  expect(statSync(file).size, path).toBeGreaterThan(200)
  const head = readFileSync(file).subarray(0, 8)
  if (path.endsWith('.png')) expect(head.toString('hex').startsWith('89504e47'), `${path} is not a PNG`).toBe(true)
  else if (path.endsWith('.svg')) expect(readFileSync(file, 'utf8').includes('<svg'), `${path} is not an SVG`).toBe(true)
  else throw new Error(`${path}: unexpected extension`)
}

/**
 * Every logo the site references is a real file in public/: a wrong path or a missing download would
 * otherwise render as an empty tile (what the founder saw on 01/10/2026 for three lazy-loaded marks).
 */
describe('the logos the site references', () => {
  it('exist for every integration platform, in the directory VendorLogo names', () => {
    expect(VENDOR_DIR).toBe('/integrations/logos')
    for (const key of Object.keys(VENDORS) as (keyof typeof VENDORS)[]) expectImage(vendorLogo(key))
    for (const i of INTEGRATIONS) expect(VENDORS[i.logo], i.slug).toBeDefined()
    for (const key of Object.values(VENDOR_OF_NAME)) expect(VENDORS[key]).toBeDefined()
  })

  it('exist for every system of the benchmark and of the comparison page', () => {
    expect(BENCH_DIR).toBe('/benchmark/logos')
    for (const system of SYSTEMS_WITH_LOGO) expectImage(logoOf(system)!)
    for (const s of SYSTEMS) expectImage(s.logo)
  })

  it('cover every platform the home diagram names with a mark the site has', () => {
    // The chips that carry a mark are the ones in VENDOR_OF_NAME; a name outside it stays as text on purpose.
    const named = new Set(Object.keys(VENDOR_OF_NAME))
    for (const lang of ['pt', 'en'] as const) {
      const items = dicts[lang].home.flow.groups.flatMap((g) => g.items)
      for (const name of ['LiveKit', 'Pipecat', 'Twilio', 'Vapi', 'Retell', 'ElevenLabs', 'LangGraph', 'CrewAI', 'n8n']) {
        expect(items).toContain(name)
        expect(named.has(name)).toBe(true)
      }
    }
  })
})

/** Every file under public/, as the address the browser asks for. */
function publicFiles(dir = PUBLIC): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = resolve(dir, e.name)
    return e.isDirectory() ? publicFiles(full) : ['/' + relative(PUBLIC, full).split(sep).join('/')]
  })
}

/**
 * The redirects of deploy/Caddyfile, read the way Caddy applies them: a `redir <path>` matches that
 * exact path, and a `redir @name` matches the path_regexp of @name minus its `not path` globs.
 */
function caddyRedirects(): ((path: string) => boolean)[] {
  const caddy = readFileSync(resolve(process.cwd(), 'deploy/Caddyfile'), 'utf8')
  const matchers = new Map<string, { re: RegExp; not: RegExp[] }>()
  for (const m of caddy.matchAll(/@(\w+)\s+(?:\{([^}]*)\}|path_regexp\s+\w+\s+(\S+))/g)) {
    const [, name, block, inline] = m
    const re = inline ?? block?.match(/path_regexp\s+\w+\s+(\S+)/)?.[1]
    if (!re) continue
    const not = [...(block ?? '').matchAll(/not path\s+(.+)/g)].flatMap((n) =>
      n[1].trim().split(/\s+/).map((g) => new RegExp('^' + g.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*') + '$')),
    )
    matchers.set(name, { re: new RegExp(re), not })
  }
  return [...caddy.matchAll(/^\s*redir\s+(\S+)\s/gm)].map(([, target]) => {
    if (!target.startsWith('@')) return (path: string) => path === target
    const m = matchers.get(target.slice(1))
    if (!m) return () => false
    return (path: string) => m.re.test(path) && !m.not.some((n) => n.test(path))
  })
}

// On 02/10/2026 the twelve integration logos answered 301 to /en/integracoes/logos/... (a 404): the
// redirect for old /integrations/<slug> pages also caught the logo directory.
describe('the files in public/', () => {
  it('are served as files, never caught by a redirect', () => {
    const redirects = caddyRedirects()
    expect(redirects.length).toBeGreaterThan(20)
    const caught = publicFiles().filter((f) => redirects.some((r) => r(f)))
    expect(caught).toEqual([])
  })
})
