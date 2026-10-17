/**
 * The admin panel's API, under /api/admin/*. It answers only on the admin host (ADMIN_HOST,
 * admin.niadra.com): on any other host every path here is a 404, as if it did not exist. Besides the
 * sign-in, every route needs a valid session. Every answer carries `Cache-Control: no-store` and
 * `X-Robots-Tag: noindex`.
 *
 *   POST /api/admin/login     {email, password} -> 200 and the session cookie, 401, or 429 with Retry-After
 *   POST /api/admin/logout
 *   GET  /api/admin/session   who is signed in, until when
 *   GET  /api/admin/contacts  the contact form's requests, newest first
 *   GET  /api/admin/access?range=today|7d|30d   the access counts (server/access/store.ts)
 */
import type { IncomingMessage, OutgoingHttpHeaders, ServerResponse } from 'node:http'
import type { AccessReport, Range } from '../access/store'
import { clientIp, readBody, send } from '../http'
import { clearCookie, sessionCookie, type Auth } from './auth'

export interface Contact {
  id: number
  created_at: string
  name: string
  email: string
  company: string
  interest: string
  message: string
  lang: string
  email_domain: string | null
  domain_has_mx: boolean | null
  source: string | null
  landing_page: string | null
  heard_from: string | null
  /** The page the form was sent from (/benchmark, /integracoes/livekit, a post), which says what converts. */
  page: string | null
  /** 'full' is /enterprise; 'quick' is the short block at the end of the high-intent pages. */
  form: string
}

export interface AdminDeps {
  auth: Auth
  /** The admin host, as the Host header carries it: admin.niadra.com, or localhost:5174 in development. */
  adminHost: string
  contacts: () => Promise<Contact[]>
  access: (range: Range) => Promise<AccessReport>
}

const PRIVATE: OutgoingHttpHeaders = { 'X-Robots-Tag': 'noindex, nofollow', 'Referrer-Policy': 'no-referrer' }
const RANGES = new Set<Range>(['today', '7d', '30d'])

/** Handles /api/admin/*; false for any other path, which the caller serves. */
export async function handleAdmin(req: IncomingMessage, res: ServerResponse, deps: AdminDeps): Promise<boolean> {
  const [path, query = ''] = (req.url ?? '/').split('?', 2) as [string, string?]
  if (path !== '/api/admin' && !path.startsWith('/api/admin/')) return false
  const reply = (status: number, body: object, headers: OutgoingHttpHeaders = {}) => send(res, status, body, { ...PRIVATE, ...headers })

  const host = String(req.headers.host ?? '').toLowerCase()
  if (host !== deps.adminHost) {
    reply(404, { error: 'not_found' })
    return true
  }

  if (req.method === 'POST') {
    // Same origin only. SameSite=Strict already keeps the cookie off cross-site requests, and a JSON
    // body forces a preflight nobody answers; this is the third lock.
    const origin = req.headers.origin
    if (origin && origin !== `https://${deps.adminHost}` && origin !== `http://${deps.adminHost}`) {
      reply(403, { error: 'origin' })
      return true
    }
  }

  const route = `${req.method} ${path}`
  if (route === 'POST /api/admin/login') {
    await login(req, reply, deps)
    return true
  }
  if (route === 'POST /api/admin/logout') {
    await deps.auth.logout(req.headers.cookie)
    reply(200, { ok: true }, { 'Set-Cookie': clearCookie() })
    return true
  }

  const session = await deps.auth.session(req.headers.cookie)
  if (!session) {
    reply(401, { error: 'session' })
    return true
  }
  if (route === 'GET /api/admin/session') {
    reply(200, { email: session.email, expiresAt: session.expiresAt.toISOString() })
  } else if (route === 'GET /api/admin/contacts') {
    reply(200, { contacts: await deps.contacts() })
  } else if (route === 'GET /api/admin/access') {
    const range = (new URLSearchParams(query).get('range') ?? '7d') as Range
    if (!RANGES.has(range)) reply(400, { error: 'range' })
    else reply(200, await deps.access(range))
  } else {
    reply(404, { error: 'not_found' })
  }
  return true
}

async function login(req: IncomingMessage, reply: (status: number, body: object, headers?: OutgoingHttpHeaders) => void, deps: AdminDeps) {
  if (!String(req.headers['content-type'] ?? '').startsWith('application/json')) return reply(415, { error: 'content_type' })
  let input: { email?: unknown; password?: unknown }
  try {
    input = JSON.parse(await readBody(req, 4096)) as typeof input
  } catch (err) {
    return reply((err as Error).message === 'too_large' ? 413 : 400, { error: 'body' })
  }
  if (!input || typeof input.email !== 'string' || typeof input.password !== 'string') return reply(400, { error: 'body' })
  const result = await deps.auth.login(input.email, input.password, clientIp(req))
  if (result.ok) {
    return reply(200, { email: result.session.email, expiresAt: result.session.expiresAt.toISOString() }, { 'Set-Cookie': sessionCookie(result.token) })
  }
  if (result.status === 429) {
    return reply(429, { error: result.reason === 'locked' ? 'locked' : 'rate', retryAfter: result.retryAfter }, { 'Retry-After': String(result.retryAfter) })
  }
  console.log('admin: sign-in refused')
  return reply(401, { error: 'credentials' })
}

/** The contact requests, newest first; the source is the UTM source, else the referring site, else "direct". */
export const CONTACTS_SQL = `
  select id, created_at, name, email, company, interest, message, lang, email_domain, domain_has_mx,
         coalesce(nullif(utm_source, ''), nullif(split_part(split_part(referrer, '://', 2), '/', 1), ''), 'direto') as source,
         landing_page, heard_from, page, coalesce(form, 'full') as form
  from contact_requests order by id desc limit 5000`
