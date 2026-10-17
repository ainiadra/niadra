// Sign-in of the admin panel: the scrypt check, the rate limits, the session and its expiry.
import { describe, expect, it } from 'vitest'
import { Auth, COOKIE, LoginGuard, MemorySessions, readCookie, SESSION_MS, verifyPassword } from './auth'
import { adminAccount } from './seed'
import { hashPassword } from './testing'

const PASSWORD = 'uma frase longa de teste 42'
const account = { email: 'founder@example.com', passwordHash: hashPassword(PASSWORD) }

function clock(start = Date.parse('2026-09-28T12:00:00Z')) {
  let t = start
  return { now: () => t, advance: (ms: number) => (t += ms) }
}

const cookieOf = (token: string) => `other=1; ${COOKIE}=${token}`

describe('password hash', () => {
  it('accepts the right password and refuses a wrong one', async () => {
    expect(await verifyPassword(PASSWORD, account.passwordHash)).toBe(true)
    expect(await verifyPassword(`${PASSWORD} `, account.passwordHash)).toBe(false)
    expect(await verifyPassword('', account.passwordHash)).toBe(false)
  })

  it('refuses a malformed hash instead of throwing', async () => {
    expect(await verifyPassword(PASSWORD, 'plain')).toBe(false)
    expect(await verifyPassword(PASSWORD, 'bcrypt$1$2$3$4$5')).toBe(false)
  })

  it('reads a hash made by the server (Python hashlib.scrypt, the same format)', async () => {
    // hash_password('senha-de-teste') from niadra.domain.control.credentials.
    const fromPython = 'scrypt$16384$8$1$AAECAwQFBgcICQoLDA0ODw$vcHJd-vuMutXcCkkrMLWjYrexwSDt8kxAdriYuL47M0'
    expect(await verifyPassword('senha-de-teste', fromPython)).toBe(true)
  })

  it('reads the admin account from the environment, and none without both values', () => {
    const passwordHash = hashPassword(PASSWORD)
    expect(adminAccount({ ADMIN_EMAIL: ' Founder@Example.com ', ADMIN_PASSWORD_HASH: passwordHash })).toEqual({ email: 'founder@example.com', passwordHash })
    expect(adminAccount({ ADMIN_EMAIL: 'founder@example.com' })).toBeNull()
    expect(adminAccount({ ADMIN_PASSWORD_HASH: passwordHash })).toBeNull()
    expect(adminAccount({})).toBeNull()
  })

  it('refuses every sign-in when no account is configured', async () => {
    const auth = new Auth({ email: '', passwordHash: '' }, new MemorySessions())
    expect(await auth.login('', '', '203.0.113.1')).toEqual({ ok: false, status: 401 })
    expect(await auth.login('founder@example.com', PASSWORD, '203.0.113.1')).toEqual({ ok: false, status: 401 })
  })
})

describe('sign-in', () => {
  it('opens a 12 hour session with the right e-mail and password', async () => {
    const c = clock()
    const auth = new Auth(account, new MemorySessions(), new LoginGuard(undefined, c.now), c.now)
    const r = await auth.login('  Founder@Example.com ', PASSWORD, '203.0.113.1')
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.token).toMatch(/^[A-Za-z0-9_-]{43}$/)
    expect(r.session.expiresAt.getTime()).toBe(c.now() + SESSION_MS)
    expect(await auth.session(cookieOf(r.token))).toMatchObject({ email: account.email })
  })

  it('refuses a wrong password and a wrong e-mail the same way', async () => {
    const auth = new Auth(account, new MemorySessions())
    expect(await auth.login(account.email, 'errada', '203.0.113.1')).toEqual({ ok: false, status: 401 })
    expect(await auth.login('other@example.com', PASSWORD, '203.0.113.1')).toEqual({ ok: false, status: 401 })
  })

  it('stops an address after five failures for fifteen minutes, even with the right password', async () => {
    const c = clock()
    const auth = new Auth(account, new MemorySessions(), new LoginGuard(undefined, c.now), c.now)
    for (let i = 0; i < 5; i++) expect(await auth.login(account.email, `errada-${i}`, '203.0.113.1')).toMatchObject({ status: 401 })
    const blocked = await auth.login(account.email, PASSWORD, '203.0.113.1')
    expect(blocked).toMatchObject({ ok: false, status: 429, reason: 'address' })
    expect(blocked.ok === false && blocked.status === 429 && blocked.retryAfter).toBe(15 * 60)
    // Another address still signs in.
    expect((await auth.login(account.email, PASSWORD, '198.51.100.1')).ok).toBe(true)
    c.advance(15 * 60_000)
    expect((await auth.login(account.email, PASSWORD, '203.0.113.1')).ok).toBe(true)
  })

  it('locks every sign-in after twenty failures from anywhere', async () => {
    const c = clock()
    const auth = new Auth(account, new MemorySessions(), new LoginGuard(undefined, c.now), c.now)
    for (let i = 0; i < 20; i++) await auth.login(account.email, 'errada', `203.0.113.${i}`)
    expect(await auth.login(account.email, PASSWORD, '198.51.100.7')).toMatchObject({ ok: false, status: 429, reason: 'locked', retryAfter: 900 })
    c.advance(14 * 60_000)
    expect(await auth.login(account.email, PASSWORD, '198.51.100.7')).toMatchObject({ status: 429, reason: 'locked', retryAfter: 60 })
    c.advance(60_000)
    expect((await auth.login(account.email, PASSWORD, '198.51.100.7')).ok).toBe(true)
  })

  it('forgets an address that signed in', async () => {
    const guard = new LoginGuard()
    for (let i = 0; i < 4; i++) guard.fail('203.0.113.1')
    guard.succeed('203.0.113.1')
    for (let i = 0; i < 4; i++) guard.fail('203.0.113.1')
    expect(guard.check('203.0.113.1')).toEqual({ ok: true })
  })

  it('ends the session after 12 hours and on sign-out', async () => {
    const c = clock()
    const auth = new Auth(account, new MemorySessions(), new LoginGuard(undefined, c.now), c.now)
    const r = await auth.login(account.email, PASSWORD, '203.0.113.1')
    if (!r.ok) throw new Error('sign-in failed')
    c.advance(SESSION_MS - 1000)
    expect(await auth.session(cookieOf(r.token))).not.toBeNull()
    c.advance(1000)
    expect(await auth.session(cookieOf(r.token))).toBeNull()

    const again = await auth.login(account.email, PASSWORD, '203.0.113.1')
    if (!again.ok) throw new Error('sign-in failed')
    await auth.logout(cookieOf(again.token))
    expect(await auth.session(cookieOf(again.token))).toBeNull()
  })

  it('reads only a well-formed session cookie', () => {
    const token = 'a'.repeat(43)
    expect(readCookie(`${COOKIE}=${token}`)).toBe(token)
    expect(readCookie(`x=1;${COOKIE}=${token}; y=2`)).toBe(token)
    expect(readCookie(`${COOKIE}=short`)).toBeNull()
    expect(readCookie(`${COOKIE}=${token}'--`)).toBeNull()
    expect(readCookie(undefined)).toBeNull()
  })
})
