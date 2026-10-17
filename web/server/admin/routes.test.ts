// The admin API over real HTTP: only the admin host, only with a session, never cached or indexed.
import { createServer, request, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { AccessReport } from '../access/store'
import { Auth, COOKIE, LoginGuard, MemorySessions, SESSION_MS } from './auth'
import { handleAdmin, type AdminDeps, type Contact } from './routes'
import { hashPassword } from './testing'

const PASSWORD = 'uma frase longa de teste 42'
const EMAIL = 'founder@example.com'
const ADMIN = 'admin.example.com'
const passwordHash = hashPassword(PASSWORD)

const contact: Contact = {
  id: 1, created_at: '2026-09-28T12:00:00Z', name: 'Marina Souza', email: 'marina@acme.com', company: 'Acme', interest: 'producao',
  message: 'Queremos a memória.', lang: 'pt', email_domain: 'acme.com', domain_has_mx: true, source: 'direto', landing_page: '/', heard_from: null, page: '/enterprise', form: 'full',
}

let server: Server
let base: string
let t: number

beforeEach(async () => {
  t = Date.parse('2026-09-28T12:00:00Z')
  const now = () => t
  const deps: AdminDeps = {
    auth: new Auth({ email: EMAIL, passwordHash }, new MemorySessions(), new LoginGuard(undefined, now), now),
    adminHost: ADMIN,
    contacts: async () => [contact],
    access: async (range) => ({ range }) as unknown as AccessReport,
  }
  server = createServer(async (req, res) => {
    if (!(await handleAdmin(req, res, deps))) {
      res.writeHead(418)
      res.end()
    }
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
})

afterEach(() => new Promise<void>((resolve) => server.close(() => resolve())))

/** A response with the parts the tests read. fetch cannot set Host, which is what the admin API checks. */
interface Answer {
  status: number
  headers: { get: (name: string) => string | null }
  json: () => Promise<unknown>
}

function raw(path: string, method: string, headers: Record<string, string>, body?: string): Promise<Answer> {
  return new Promise((resolve, reject) => {
    const url = new URL(path, base)
    const req = request({ host: url.hostname, port: url.port, path: url.pathname + url.search, method, headers }, (res) => {
      const chunks: Buffer[] = []
      res.on('data', (c: Buffer) => chunks.push(c))
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8')
        resolve({
          status: res.statusCode ?? 0,
          headers: { get: (n) => { const v = res.headers[n.toLowerCase()]; return v === undefined ? null : Array.isArray(v) ? v.join(', ') : String(v) } },
          json: async () => JSON.parse(text),
        })
      })
    })
    req.on('error', reject)
    req.end(body)
  })
}

const call = (path: string, init: { method?: string; host?: string; cookie?: string; body?: unknown; origin?: string; ip?: string } = {}) =>
  raw(path, init.method ?? 'GET', {
    host: init.host ?? ADMIN,
    ...(init.cookie ? { cookie: init.cookie } : {}),
    ...(init.body !== undefined ? { 'content-type': 'application/json' } : {}),
    ...(init.origin ? { origin: init.origin } : {}),
    ...(init.ip ? { 'x-forwarded-for': init.ip } : {}),
  }, init.body !== undefined ? JSON.stringify(init.body) : undefined)

async function signIn(ip = '203.0.113.1') {
  const res = await call('/api/admin/login', { method: 'POST', body: { email: EMAIL, password: PASSWORD }, origin: `https://${ADMIN}`, ip })
  expect(res.status).toBe(200)
  const setCookie = res.headers.get('set-cookie')!
  expect(setCookie).toMatch(new RegExp(`^${COOKIE}=[A-Za-z0-9_-]{43}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=43200$`))
  return setCookie.split(';')[0]!
}

describe('admin API', () => {
  it('signs in and reads contacts and access counts with the session cookie', async () => {
    const cookie = await signIn()
    const contacts = await call('/api/admin/contacts', { cookie })
    expect(contacts.status).toBe(200)
    expect(await contacts.json()).toEqual({ contacts: [contact] })
    const access = await call('/api/admin/access?range=30d', { cookie })
    expect(await access.json()).toEqual({ range: '30d' })
    expect((await call('/api/admin/access?range=1y', { cookie })).status).toBe(400)
    expect(await (await call('/api/admin/session', { cookie })).json()).toEqual({ email: EMAIL, expiresAt: new Date(t + SESSION_MS).toISOString() })
  })

  it('answers 404 on any other host, with or without a session', async () => {
    const cookie = await signIn()
    for (const host of ['niadra.example.com', 'www.example.com', `${ADMIN}.evil.com`, '']) {
      for (const path of ['/api/admin/contacts', '/api/admin/access', '/api/admin/session']) {
        const res = await call(path, { host, cookie })
        expect(res.status, `${host} ${path}`).toBe(404)
      }
      const login = await call('/api/admin/login', { method: 'POST', host, body: { email: EMAIL, password: PASSWORD } })
      expect(login.status).toBe(404)
      expect(login.headers.get('set-cookie')).toBeNull()
    }
  })

  it('answers 401 without a session, with a forged one and with an expired one', async () => {
    expect((await call('/api/admin/contacts')).status).toBe(401)
    expect((await call('/api/admin/access', { cookie: `${COOKIE}=${'a'.repeat(43)}` })).status).toBe(401)
    const cookie = await signIn()
    t += SESSION_MS
    expect((await call('/api/admin/contacts', { cookie })).status).toBe(401)
  })

  it('refuses a wrong password, then limits the address with Retry-After', async () => {
    for (let i = 0; i < 5; i++) {
      const res = await call('/api/admin/login', { method: 'POST', body: { email: EMAIL, password: 'errada' }, ip: '198.51.100.9' })
      expect(res.status).toBe(401)
      expect(res.headers.get('set-cookie')).toBeNull()
    }
    const limited = await call('/api/admin/login', { method: 'POST', body: { email: EMAIL, password: PASSWORD }, ip: '198.51.100.9' })
    expect(limited.status).toBe(429)
    expect(limited.headers.get('retry-after')).toBe('900')
    expect(await limited.json()).toEqual({ error: 'rate', retryAfter: 900 })
  })

  it('refuses a sign-in posted from another origin and a body that is not JSON', async () => {
    const cross = await call('/api/admin/login', { method: 'POST', body: { email: EMAIL, password: PASSWORD }, origin: 'https://evil.example' })
    expect(cross.status).toBe(403)
    const form = await raw('/api/admin/login', 'POST', { host: ADMIN, 'content-type': 'application/x-www-form-urlencoded' }, 'email=x')
    expect(form.status).toBe(415)
  })

  it('signs out: the cookie is cleared and the session no longer works', async () => {
    const cookie = await signIn()
    const out = await call('/api/admin/logout', { method: 'POST', cookie, origin: `https://${ADMIN}` })
    expect(out.headers.get('set-cookie')).toMatch(new RegExp(`^${COOKIE}=; .*Max-Age=0$`))
    expect((await call('/api/admin/contacts', { cookie })).status).toBe(401)
  })

  it('marks every answer noindex and no-store, errors included', async () => {
    const cookie = await signIn()
    const answers = await Promise.all([
      call('/api/admin/contacts', { cookie }),
      call('/api/admin/contacts'),
      call('/api/admin/nothing', { cookie }),
      call('/api/admin/contacts', { host: 'niadra.example.com' }),
      call('/api/admin/login', { method: 'POST', body: { email: EMAIL, password: 'errada' } }),
    ])
    for (const res of answers) {
      expect(res.headers.get('x-robots-tag')).toBe('noindex, nofollow')
      expect(res.headers.get('cache-control')).toBe('no-store')
    }
  })

  it('leaves every other path to the rest of the API', async () => {
    expect((await call('/api/contato', { method: 'POST', body: {} })).status).toBe(418)
    expect((await call('/api/administrador')).status).toBe(418)
  })
})
