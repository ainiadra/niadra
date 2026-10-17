/**
 * Turns Caddy's access log entries into counts: day x path x class x agent name, plus, for people, the
 * set of distinct visitors of each day. Nothing per request is kept: the aggregator holds counters
 * until the next flush (server/access/store.ts, once a minute), and a visitor is a salted hash.
 *
 * A visitor is SHA-256 over the day's salt, the address as Caddy logged it (already masked to /24 or
 * /32 when written) and the user agent, cut to 16 bytes. The salt is random per day and deleted two
 * days later, after which no hash can be tied to an address again.
 */
import { createHash } from 'node:crypto'
import { classify, normalizePath, type AccessClass } from './classify'

export interface AccessEvent {
  day: string
  path: string
  cls: AccessClass
  agent: string
  /** Only for people: the day's salted hash of address and user agent, in hex. */
  visitor?: string
  /** The country of the masked address (geo.ts), when the table knows it. */
  country?: string
  /** The campaign the link carried (utm_source, utm_medium, utm_campaign), when it carried one. */
  utm?: Utm
}

export interface Utm {
  source: string
  medium: string
  campaign: string
}

interface CountRow {
  day: string
  path: string
  cls: AccessClass
  agent: string
  hits: number
}

interface CountryRow {
  day: string
  cls: AccessClass
  country: string
  hits: number
}

interface CampaignRow extends Utm {
  day: string
  cls: AccessClass
  hits: number
}

export interface Batch {
  rows: CountRow[]
  visitors: { day: string; visitor: string }[]
  countries: CountryRow[]
  campaigns: CampaignRow[]
}

/** The calendar the panel uses: the founder's day, in São Paulo time. */
const TIME_ZONE = 'America/Sao_Paulo'
const dayFormat = new Intl.DateTimeFormat('en-CA', { timeZone: TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' })
export const dayOf = (ms: number) => dayFormat.format(new Date(ms))

/** What an access log line carries that the count needs. Anything else in the line is ignored. */
export interface LogLine {
  ms: number
  host: string
  path: string
  ua: string
  ip: string
  /** The campaign fields of the query string, when utm_source is there. */
  utm?: Utm
}

const UTM_MAX = 60
/** utm_source, utm_medium and utm_campaign of a request's query string, lower case and cut; nothing without a source. */
export function utmOf(uri: string): Utm | undefined {
  const q = uri.indexOf('?')
  if (q < 0) return undefined
  let params: URLSearchParams
  try {
    params = new URLSearchParams(uri.slice(q + 1).split('#')[0])
  } catch {
    return undefined
  }
  const clean = (k: string) => (params.get(k) ?? '').trim().toLowerCase().replace(/\s+/g, ' ').slice(0, UTM_MAX)
  const source = clean('utm_source')
  if (!source) return undefined
  return { source, medium: clean('utm_medium'), campaign: clean('utm_campaign') }
}

/** One JSON entry of Caddy's access log (deploy/Caddyfile), or null when it is not one. */
export function parseLine(line: string): LogLine | null {
  let e: unknown
  try {
    e = JSON.parse(line)
  } catch {
    return null
  }
  if (!e || typeof e !== 'object') return null
  const entry = e as { logger?: unknown; ts?: unknown; request?: { host?: unknown; uri?: unknown; client_ip?: unknown; headers?: Record<string, unknown> } }
  if (typeof entry.logger !== 'string' || !entry.logger.startsWith('http.log.access')) return null
  const req = entry.request
  if (!req || typeof req.uri !== 'string' || typeof entry.ts !== 'number') return null
  const agents = req.headers?.['User-Agent']
  const parsed: LogLine = {
    ms: Math.round(entry.ts * 1000),
    host: typeof req.host === 'string' ? req.host.toLowerCase().replace(/:\d+$/, '') : '',
    path: normalizePath(req.uri),
    ua: Array.isArray(agents) && typeof agents[0] === 'string' ? agents[0].slice(0, 512) : '',
    ip: typeof req.client_ip === 'string' ? req.client_ip : '',
  }
  const utm = utmOf(req.uri)
  if (utm) parsed.utm = utm
  return parsed
}

export function visitorId(salt: Buffer, ip: string, ua: string): string {
  return createHash('sha256').update(salt).update(ip).update('\n').update(ua).digest().subarray(0, 16).toString('hex')
}

/**
 * The event a log line counts as, or null when it does not count: another host (the admin panel, www,
 * which only redirects), our own probes and health watch, static files. `salt` gives the day's salt;
 * without one (the minute after a start, before the salts load) the person still counts, without a
 * visitor.
 */
export function toEvent(
  line: LogLine,
  siteHost: string,
  salt: (day: string) => Buffer | undefined,
  country: (ip: string) => string | null = () => null,
): AccessEvent | null {
  if (line.host !== siteHost) return null
  const verdict = classify({ path: line.path, ua: line.ua })
  if (!verdict.counted) return null
  const day = dayOf(line.ms)
  const event: AccessEvent = { day, path: line.path, cls: verdict.cls, agent: verdict.agent }
  if (verdict.cls === 'human' && line.ip) {
    const s = salt(day)
    if (s) event.visitor = visitorId(s, line.ip, line.ua)
  }
  if (line.ip) {
    const c = country(line.ip)
    if (c) event.country = c
  }
  if (line.utm) event.utm = line.utm
  return event
}

/** Distinct keys one flush may hold; past it, new paths of a day fold into OVERFLOW_PATH. */
export const MAX_KEYS = 5000
export const OVERFLOW_PATH = '(outras)'
const MAX_VISITORS = 50_000

/** Distinct countries and campaigns one flush may hold; a campaign past it folds into OVERFLOW_PATH. */
const MAX_CAMPAIGNS = 2000

export class Aggregator {
  private counts = new Map<string, number>()
  private visitors = new Set<string>()
  private countries = new Map<string, number>()
  private campaigns = new Map<string, number>()

  add(e: AccessEvent) {
    this.bump(e.day, e.path, e.cls, e.agent, 1)
    if (e.visitor && this.visitors.size < MAX_VISITORS) this.visitors.add(`${e.day}\t${e.visitor}`)
    if (e.country) this.bumpCountry(e.day, e.cls, e.country, 1)
    if (e.utm) this.bumpCampaign(e.day, e.cls, e.utm, 1)
  }

  private bumpCountry(day: string, cls: AccessClass, country: string, n: number) {
    const key = [day, cls, country].join('\t')
    this.countries.set(key, (this.countries.get(key) ?? 0) + n)
  }

  private bumpCampaign(day: string, cls: AccessClass, utm: Utm, n: number) {
    let key = [day, cls, utm.source, utm.medium, utm.campaign].join('\t')
    if (!this.campaigns.has(key) && this.campaigns.size >= MAX_CAMPAIGNS) key = [day, cls, OVERFLOW_PATH, '', ''].join('\t')
    this.campaigns.set(key, (this.campaigns.get(key) ?? 0) + n)
  }

  private bump(day: string, path: string, cls: AccessClass, agent: string, n: number) {
    let key = [day, path, cls, agent].join('\t')
    if (!this.counts.has(key) && this.counts.size >= MAX_KEYS) key = [day, OVERFLOW_PATH, cls, agent].join('\t')
    this.counts.set(key, (this.counts.get(key) ?? 0) + n)
  }

  get size() {
    return this.counts.size
  }

  /** Everything counted since the last drain, and an empty aggregator. */
  drain(): Batch {
    const rows: CountRow[] = [...this.counts].map(([key, hits]) => {
      const [day, path, cls, agent] = key.split('\t') as [string, string, AccessClass, string]
      return { day, path, cls, agent, hits }
    })
    const visitors = [...this.visitors].map((v) => {
      const [day, visitor] = v.split('\t') as [string, string]
      return { day, visitor }
    })
    const countries: CountryRow[] = [...this.countries].map(([key, hits]) => {
      const [day, cls, country] = key.split('\t') as [string, AccessClass, string]
      return { day, cls, country, hits }
    })
    const campaigns: CampaignRow[] = [...this.campaigns].map(([key, hits]) => {
      const [day, cls, source, medium, campaign] = key.split('\t') as [string, AccessClass, string, string, string]
      return { day, cls, source, medium, campaign, hits }
    })
    this.counts = new Map()
    this.visitors = new Set()
    this.countries = new Map()
    this.campaigns = new Map()
    return { rows, visitors, countries, campaigns }
  }

  /** Puts back a batch the database did not take, so the next flush carries it. */
  restore(batch: Batch) {
    for (const r of batch.rows) this.bump(r.day, r.path, r.cls, r.agent, r.hits)
    for (const v of batch.visitors) if (this.visitors.size < MAX_VISITORS) this.visitors.add(`${v.day}\t${v.visitor}`)
    for (const c of batch.countries ?? []) this.bumpCountry(c.day, c.cls, c.country, c.hits)
    for (const c of batch.campaigns ?? []) this.bumpCampaign(c.day, c.cls, c, c.hits)
  }
}
