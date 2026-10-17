// The aggregator: Caddy's log entries in, counts per day x path x class x agent out, people as salted
// hashes, nothing per request kept.
import { describe, expect, it } from 'vitest'
import { existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { Aggregator, dayOf, MAX_KEYS, OVERFLOW_PATH, parseLine, toEvent, utmOf, visitorId } from './aggregate'
import { loadGeo } from './geo'

const CHROME = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36'
const GPT = 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.4; +https://openai.com/gptbot)'
// 28/09/2026 15:00 in São Paulo (UTC-3).
const TS = Date.parse('2026-09-28T18:00:00Z') / 1000

/** An entry as the Caddyfile's `counter` log writes it. */
const entry = (over: { uri?: string; ua?: string; host?: string; ts?: number; ip?: string; logger?: string } = {}) =>
  JSON.stringify({
    level: 'info',
    ts: over.ts ?? TS,
    logger: over.logger ?? 'http.log.access.site',
    msg: 'handled request',
    request: {
      client_ip: over.ip ?? '203.0.113.0',
      proto: 'HTTP/1.1',
      method: 'GET',
      host: over.host ?? 'niadra.com',
      uri: over.uri ?? '/',
      headers: { 'User-Agent': [over.ua ?? CHROME], Accept: ['*/*'] },
    },
    status: 200,
  })

const salt = Buffer.alloc(32, 7)
const salts = (day: string) => (day === '2026-09-28' ? salt : undefined)
const event = (over: Parameters<typeof entry>[0] = {}) => toEvent(parseLine(entry(over))!, 'niadra.com', salts)

describe('access log parsing', () => {
  it('reads what the count needs from an entry', () => {
    expect(parseLine(entry({ uri: '/blog/x?utm_source=chatgpt.com', host: 'NIADRA.com:443' }))).toEqual({
      ms: TS * 1000,
      host: 'niadra.com',
      path: '/blog/x',
      ua: CHROME,
      ip: '203.0.113.0',
      utm: { source: 'chatgpt.com', medium: '', campaign: '' },
    })
  })

  it('reads the campaign of the query string, lower case and cut, and nothing without a source', () => {
    expect(utmOf('/benchmark?utm_source=LinkedIn&utm_medium=Ads&utm_campaign=UAE%20Test&x=1#top')).toEqual({ source: 'linkedin', medium: 'ads', campaign: 'uae test' })
    expect(utmOf('/benchmark?utm_medium=ads')).toBeUndefined()
    expect(utmOf('/benchmark')).toBeUndefined()
    expect(utmOf(`/?utm_source=${'a'.repeat(100)}`)!.source).toHaveLength(60)
    expect(parseLine(entry({ uri: '/preco' }))!.utm).toBeUndefined()
  })

  it('ignores lines that are not access log entries', () => {
    expect(parseLine('not json')).toBeNull()
    expect(parseLine('[ERROR] net log writer failed to connect')).toBeNull()
    expect(parseLine(entry({ logger: 'http.handlers.reverse_proxy' }))).toBeNull()
    expect(parseLine(JSON.stringify({ logger: 'http.log.access.site', ts: TS }))).toBeNull()
  })

  it('uses the São Paulo calendar for the day', () => {
    expect(dayOf(Date.parse('2026-09-29T02:59:00Z'))).toBe('2026-09-28')
    expect(dayOf(Date.parse('2026-09-29T03:00:00Z'))).toBe('2026-09-29')
  })
})

describe('events', () => {
  it('counts a person with a visitor hash and a crawler by name, without one', () => {
    expect(event({ uri: '/preco' })).toEqual({ day: '2026-09-28', path: '/preco', cls: 'human', agent: '', visitor: visitorId(salt, '203.0.113.0', CHROME) })
    expect(event({ uri: '/llms.txt', ua: GPT })).toEqual({ day: '2026-09-28', path: '/llms.txt', cls: 'ai', agent: 'GPTBot' })
  })

  it('does not count other hosts, probes, the health watch or static files', () => {
    expect(event({ host: 'admin.niadra.com' })).toBeNull()
    expect(event({ host: 'www.niadra.com' })).toBeNull()
    expect(event({ uri: '/healthz', ua: 'kube-probe/1.33' })).toBeNull()
    expect(event({ ua: 'curl/8.7.1' })).toBeNull()
    expect(event({ uri: '/assets/index-Dj3GcIQF.js' })).toBeNull()
  })

  it('carries the country of the masked address and the campaign of the link', () => {
    const country = (ip: string) => (ip === '203.0.113.0' ? 'AE' : null)
    expect(toEvent(parseLine(entry({ uri: '/en/benchmark?utm_source=linkedin&utm_campaign=uae' }))!, 'niadra.com', salts, country)).toMatchObject({
      cls: 'human',
      path: '/en/benchmark',
      country: 'AE',
      utm: { source: 'linkedin', medium: '', campaign: 'uae' },
    })
    expect(toEvent(parseLine(entry({ ip: '198.51.100.0' }))!, 'niadra.com', salts, country)).not.toHaveProperty('country')
    expect(event({ uri: '/preco' })).not.toHaveProperty('utm')
  })

  it('counts a person without a visitor when the day has no salt yet', () => {
    expect(event({ ts: TS + 86_400 })).toEqual({ day: '2026-09-29', path: '/', cls: 'human', agent: '' })
  })

  it('makes the visitor hash depend on the salt, the address and the user agent', () => {
    const a = visitorId(salt, '203.0.113.0', CHROME)
    expect(a).toMatch(/^[0-9a-f]{32}$/)
    expect(visitorId(Buffer.alloc(32, 8), '203.0.113.0', CHROME)).not.toBe(a)
    expect(visitorId(salt, '198.51.100.0', CHROME)).not.toBe(a)
    expect(visitorId(salt, '203.0.113.0', `${CHROME} x`)).not.toBe(a)
  })
})

describe('aggregator', () => {
  it('adds requests up per day, path, class and agent, and counts each visitor once', () => {
    const agg = new Aggregator()
    for (let i = 0; i < 3; i++) agg.add(event({ uri: '/preco' })!)
    agg.add(event({ uri: '/preco', ip: '198.51.100.0' })!)
    agg.add(event({ uri: '/preco', ua: GPT })!)
    agg.add(event({ uri: '/.env' })!)
    const batch = agg.drain()
    expect(batch.rows).toEqual(
      expect.arrayContaining([
        { day: '2026-09-28', path: '/preco', cls: 'human', agent: '', hits: 4 },
        { day: '2026-09-28', path: '/preco', cls: 'ai', agent: 'GPTBot', hits: 1 },
        { day: '2026-09-28', path: '/.env', cls: 'scanner', agent: '', hits: 1 },
      ]),
    )
    expect(batch.rows).toHaveLength(3)
    expect(batch.visitors).toHaveLength(2)
    expect(agg.drain()).toEqual({ rows: [], visitors: [], countries: [], campaigns: [] })
  })

  it('keeps no address or user agent in what it hands to the database', () => {
    const agg = new Aggregator()
    agg.add(event({ uri: '/' })!)
    const text = JSON.stringify(agg.drain())
    expect(text).not.toContain('203.0.113')
    expect(text).not.toContain('Mozilla')
  })

  it('folds new paths into one row once a minute holds too many', () => {
    const agg = new Aggregator()
    for (let i = 0; i < MAX_KEYS + 10; i++) agg.add({ day: '2026-09-28', path: `/scan-${i}`, cls: 'scanner', agent: '' })
    agg.add({ day: '2026-09-28', path: '/scan-0', cls: 'scanner', agent: '' })
    const rows = agg.drain().rows
    expect(rows).toHaveLength(MAX_KEYS + 1)
    expect(rows.find((r) => r.path === OVERFLOW_PATH)?.hits).toBe(10)
    expect(rows.find((r) => r.path === '/scan-0')?.hits).toBe(2)
  })

  it('puts a batch the database refused back, added to what came meanwhile', () => {
    const agg = new Aggregator()
    agg.add(event({ uri: '/preco' })!)
    agg.add(event({ uri: '/preco' })!)
    const failed = agg.drain()
    agg.add(event({ uri: '/preco' })!)
    agg.restore(failed)
    const batch = agg.drain()
    expect(batch.rows).toEqual([{ day: '2026-09-28', path: '/preco', cls: 'human', agent: '', hits: 3 }])
    expect(batch.visitors).toHaveLength(1)
  })
})

describe('countries and campaigns', () => {
  it('counts per day, class and country, and per campaign, and restores a refused batch', () => {
    const agg = new Aggregator()
    const base = { day: '2026-09-28', path: '/en/benchmark', cls: 'human' as const, agent: '' }
    agg.add({ ...base, country: 'AE', utm: { source: 'linkedin', medium: 'ads', campaign: 'uae-test' } })
    agg.add({ ...base, country: 'AE' })
    agg.add({ ...base, path: '/', country: 'BR', utm: { source: 'linkedin', medium: 'ads', campaign: 'uae-test' } })
    agg.add({ ...base, cls: 'ai', agent: 'GPTBot', country: 'US' })
    const batch = agg.drain()
    expect(batch.countries).toEqual([
      { day: '2026-09-28', cls: 'human', country: 'AE', hits: 2 },
      { day: '2026-09-28', cls: 'human', country: 'BR', hits: 1 },
      { day: '2026-09-28', cls: 'ai', country: 'US', hits: 1 },
    ])
    expect(batch.campaigns).toEqual([{ day: '2026-09-28', cls: 'human', source: 'linkedin', medium: 'ads', campaign: 'uae-test', hits: 2 }])
    agg.restore(batch)
    agg.add({ ...base, country: 'AE' })
    const again = agg.drain()
    expect(again.countries.find((c) => c.country === 'AE')?.hits).toBe(3)
    expect(again.campaigns[0]?.hits).toBe(2)
  })

  // The packed table exists after `npm run build:api`; CI only builds the site, so this check skips there.
  const packed = join(process.cwd(), 'dist-server', 'geo-country.bin')
  it.skipIf(!existsSync(packed))('places real masked addresses with the packed table', () => {
    const geo = loadGeo()!
    expect(geo.size).toBeGreaterThan(500_000)
    // The first range of each country in the dataset itself, so the check follows the data.
    const require = createRequire(import.meta.url)
    const dir = dirname(require.resolve('@ip-location-db/geo-whois-asn-country/package.json'))
    const csv = require('node:fs').readFileSync(join(dir, 'geo-whois-asn-country-ipv4-num.csv'), 'utf8') as string
    for (const cc of ['AE', 'BR', 'US']) {
      const start = Number(new RegExp(`^(\\d+),\\d+,${cc}$`, 'm').exec(csv)![1])
      const ip = [start >>> 24, (start >>> 16) & 255, (start >>> 8) & 255, 0].join('.')
      expect(geo.countryOf(ip)).toBe(cc)
    }
    expect(geo.countryOf('10.0.0.0')).toBeNull()
  })
})
