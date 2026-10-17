/**
 * The Niadra site API, with its own Postgres database (niadra_site):
 *   - the contact requests from the form (/enterprise), saved as they come;
 *   - the access counter: Caddy sends each access log entry over UDP and this counts it by day, page,
 *     class (person, AI, search engine, scanner, other robot) and crawler name (server/access);
 *   - the admin panel's API under /api/admin/*, on the admin host only (server/admin).
 *
 * Runs in the cluster as the `site-api` image (Dockerfile at the root); the
 * ingress hands /api/* to it. No framework: a Node HTTP server and the `pg`
 * driver, bundled into a single file by esbuild.
 *
 * Variables: DATABASE_URL (required), HOST (default 127.0.0.1; 0.0.0.0 in the
 * image), PORT (default 8787), ALLOWED_ORIGINS (extra origins, comma
 * separated: the cluster's temporary address or the local one, in development),
 * SITE_HOST (niadra.com, the host whose requests are counted), ADMIN_HOST
 * (admin.niadra.com), ADMIN_EMAIL and ADMIN_PASSWORD_HASH (the admin panel's
 * account, server/admin/seed.ts) and ACCESS_LOG_PORT (8788, UDP; 0 turns the
 * counter off).
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import pg from 'pg'
import { startCounter, type Counter } from './access/intake'
import { ACCESS_SCHEMA, report } from './access/store'
import { Auth } from './admin/auth'
import { CONTACTS_SQL, handleAdmin, type AdminDeps, type Contact } from './admin/routes'
import { adminAccount } from './admin/seed'
import { PgSessions, SESSIONS_SCHEMA } from './admin/sessions'
import { screen, str } from './contact'
import { clientIp, readBody, send } from './http'

const HOST = process.env.HOST ?? '127.0.0.1'
const PORT = Number(process.env.PORT ?? 8787)
const SITE_HOST = (process.env.SITE_HOST ?? 'niadra.com').toLowerCase()
const ADMIN_HOST = (process.env.ADMIN_HOST ?? `admin.${SITE_HOST}`).toLowerCase()
const ACCESS_LOG_PORT = Number(process.env.ACCESS_LOG_PORT ?? 8788)
const DATABASE_URL = process.env.DATABASE_URL
if (!DATABASE_URL) {
  console.error('DATABASE_URL is not set')
  process.exit(1)
}

const pool = new pg.Pool({ connectionString: DATABASE_URL, max: 4, idleTimeoutMillis: 30_000 })

const SCHEMA = `
  create table if not exists contact_requests (
    id          bigserial primary key,
    created_at  timestamptz not null default now(),
    name        text not null,
    email       text not null,
    company     text not null,
    interest    text not null,
    message     text not null,
    lang        text not null,
    page        text
  );
  create index if not exists contact_requests_created_at on contact_requests (created_at desc);
  alter table contact_requests add column if not exists email_domain text;
  alter table contact_requests add column if not exists domain_has_mx boolean;
  alter table contact_requests add column if not exists referrer text;
  alter table contact_requests add column if not exists landing_page text;
  alter table contact_requests add column if not exists utm_source text;
  alter table contact_requests add column if not exists utm_medium text;
  alter table contact_requests add column if not exists utm_campaign text;
  alter table contact_requests add column if not exists heard_from text;
  alter table contact_requests add column if not exists form text not null default 'full';
`

/**
 * Where the form may be sent from. Any other origin gets 403. In
 * development, ALLOWED_ORIGINS adds the local address.
 */
const ORIGINS = new Set(['https://niadra.com', 'https://www.niadra.com', ...(process.env.ALLOWED_ORIGINS ?? '').split(',').map((o) => o.trim()).filter(Boolean)])
const MAX_BODY = 16 * 1024

// In-memory limit per IP: 5 requests every 10 minutes in each replica. It is lost on
// restart, and that is fine: it is a brake on spam, not access control.
const WINDOW = 10 * 60_000
const PER_IP = 5
const hits = new Map<string, number[]>()
function limited(ip: string): boolean {
  const now = Date.now()
  const recent = (hits.get(ip) ?? []).filter((t) => now - t < WINDOW)
  recent.push(now)
  hits.set(ip, recent)
  if (hits.size > 10_000) for (const [k, v] of hits) if (now - v[v.length - 1] > WINDOW) hits.delete(k)
  return recent.length > PER_IP
}

async function contact(req: IncomingMessage, res: ServerResponse) {
  const origin = req.headers.origin
  if (origin && !ORIGINS.has(origin)) return send(res, 403, { error: 'origin' })
  if (!String(req.headers['content-type'] ?? '').startsWith('application/json')) return send(res, 415, { error: 'content_type' })

  let input: Record<string, unknown>
  try {
    const raw = await readBody(req, MAX_BODY)
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('shape')
    input = parsed as Record<string, unknown>
  } catch (err) {
    return send(res, (err as Error).message === 'too_large' ? 413 : 400, { error: 'body' })
  }

  // Honeypot field filled in: answers as a success and saves nothing.
  if (str(input.website)) return send(res, 201, { ok: true })

  if (limited(clientIp(req))) return send(res, 429, { error: 'rate' })

  const screened = await screen(input)
  if (!screened.ok) return send(res, screened.status, screened.body)

  const { data: d, domain, mx } = screened
  await pool.query(
    `insert into contact_requests (name, email, company, interest, message, lang, page, email_domain, domain_has_mx,
       referrer, landing_page, utm_source, utm_medium, utm_campaign, heard_from, form)
     values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)`,
    [d.name, d.email, d.company, d.interest, d.message, d.lang, d.page || null, domain, mx,
      d.referrer || null, d.landing || null, d.utm_source || null, d.utm_medium || null, d.utm_campaign || null, d.heard || null, d.form],
  )
  console.log(`contact saved: form=${d.form} interest=${d.interest} lang=${d.lang} domain=${domain} mx=${mx} page=${d.page || '-'}`)
  send(res, 201, { ok: true })
}

const ADMIN_ACCOUNT = adminAccount()
if (!ADMIN_ACCOUNT) console.warn('ADMIN_EMAIL or ADMIN_PASSWORD_HASH is not set: the admin panel refuses every sign-in')

const admin: AdminDeps = {
  auth: new Auth(ADMIN_ACCOUNT ?? { email: '', passwordHash: '' }, new PgSessions(pool)),
  adminHost: ADMIN_HOST,
  contacts: async () => (await pool.query<Contact>(CONTACTS_SQL)).rows,
  access: (range) => report(pool, range),
}

const server = createServer(async (req, res) => {
  const url = (req.url ?? '/').split('?')[0]
  try {
    if (await handleAdmin(req, res, admin)) return
    if (url === '/api/contato') {
      if (req.method !== 'POST') return send(res, 405, { error: 'method' })
      return await contact(req, res)
    }
    if (url === '/api/saude' && req.method === 'GET') {
      await pool.query('select 1')
      return send(res, 200, { ok: true })
    }
    send(res, 404, { error: 'not_found' })
  } catch (err) {
    console.error('API error:', (err as Error).message)
    if (!res.headersSent) send(res, 500, { error: 'internal' })
  }
})

// Slow headers and bodies do not hold a connection open.
server.headersTimeout = 10_000
server.requestTimeout = 15_000

let counter: Counter | null = null

async function main() {
  await pool.query(SCHEMA + ACCESS_SCHEMA + SESSIONS_SCHEMA)
  if (ACCESS_LOG_PORT) counter = await startCounter({ pool, port: ACCESS_LOG_PORT, siteHost: SITE_HOST })
  server.listen(PORT, HOST, () => console.log(`niadra-api listening on ${HOST}:${PORT}`))
}

// The minute's counts go to the database before the process ends.
const stop = () => server.close(() => (counter?.stop() ?? Promise.resolve()).finally(() => pool.end().then(() => process.exit(0))))
process.on('SIGTERM', stop)
process.on('SIGINT', stop)

main().catch((err) => {
  console.error('failed to start:', (err as Error).message)
  process.exit(1)
})
