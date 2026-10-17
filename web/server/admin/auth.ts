/**
 * Sign-in of the admin panel (admin.niadra.com): one account from the environment (seed.ts), a scrypt hash
 * of its password and never the password, opaque sessions of 12 hours in an HttpOnly cookie.
 *
 * - The password check is scrypt with the parameters of the server (N=2^14, r=8, p=1, 32 bytes), in
 *   the server's format, compared in constant time. The e-mail is compared in constant time too, and a
 *   wrong e-mail still runs scrypt, so the answer takes the same time whichever half was wrong.
 * - One scrypt runs at a time (16 MiB each): a burst of attempts waits in line instead of adding memory.
 * - Failed attempts are limited per address, and too many failures from anywhere lock every sign-in for
 *   a while, the right password included. Both live in memory: a restart clears them, which an attacker
 *   cannot cause from outside.
 * - The session token is 32 random bytes; the store keeps only its SHA-256.
 */
import { createHash, randomBytes, scrypt as scryptCb, timingSafeEqual, type ScryptOptions } from 'node:crypto'

export const SESSION_MS = 12 * 60 * 60_000
export const COOKIE = '__Host-niadra_admin'
const MAX_PASSWORD = 1024

const scrypt = (password: string, salt: Buffer, keylen: number, options: ScryptOptions) =>
  new Promise<Buffer>((resolve, reject) => scryptCb(password, salt, keylen, options, (err, key) => (err ? reject(err) : resolve(key))))

const unb64 = (text: string) => Buffer.from(text, 'base64url')

/** Checks a password against `scrypt$N$r$p$salt$digest` (the server's format). False on any malformed hash. */
export async function verifyPassword(password: string, encoded: string): Promise<boolean> {
  const parts = encoded.split('$')
  if (parts.length !== 6 || parts[0] !== 'scrypt') return false
  const [N, r, p] = parts.slice(1, 4).map(Number) as [number, number, number]
  const salt = unb64(parts[4]!)
  const expected = unb64(parts[5]!)
  if (!N || !r || !p || !salt.length || !expected.length) return false
  const actual = await scrypt(password, salt, expected.length, { N, r, p, maxmem: 256 * N * r })
  return timingSafeEqual(actual, expected)
}

const sha256 = (text: string | Buffer) => createHash('sha256').update(text).digest()

/** Constant-time equality of two strings of any length. */
const sameText = (a: string, b: string) => timingSafeEqual(sha256(a), sha256(b))

export interface GuardOptions {
  /** Failures one address may have in the window before it is refused. */
  perAddress: number
  /** Failures from all addresses in the window that lock every sign-in. */
  global: number
  windowMs: number
  lockMs: number
}

const GUARD_DEFAULTS: GuardOptions = { perAddress: 5, global: 20, windowMs: 15 * 60_000, lockMs: 15 * 60_000 }

export type GuardVerdict = { ok: true } | { ok: false; reason: 'address' | 'locked'; retryAfter: number }

/** The sign-in rate limits: per address, and a global lock after repeated failures. */
export class LoginGuard {
  private failures = new Map<string, number[]>()
  private all: number[] = []
  private lockedUntil = 0

  constructor(
    private readonly opts: GuardOptions = GUARD_DEFAULTS,
    private readonly now: () => number = Date.now,
  ) {}

  check(address: string): GuardVerdict {
    const now = this.now()
    if (now < this.lockedUntil) return { ok: false, reason: 'locked', retryAfter: Math.ceil((this.lockedUntil - now) / 1000) }
    const recent = this.recent(address, now)
    if (recent.length >= this.opts.perAddress) {
      return { ok: false, reason: 'address', retryAfter: Math.ceil((recent[0]! + this.opts.windowMs - now) / 1000) }
    }
    return { ok: true }
  }

  fail(address: string) {
    const now = this.now()
    const recent = this.recent(address, now)
    recent.push(now)
    this.failures.set(address, recent)
    this.all = this.all.filter((t) => now - t < this.opts.windowMs)
    this.all.push(now)
    if (this.all.length >= this.opts.global) {
      this.lockedUntil = now + this.opts.lockMs
      this.all = []
    }
    if (this.failures.size > 10_000) {
      for (const [k, v] of this.failures) if (now - v[v.length - 1]! >= this.opts.windowMs) this.failures.delete(k)
    }
  }

  succeed(address: string) {
    this.failures.delete(address)
  }

  private recent(address: string, now: number) {
    return (this.failures.get(address) ?? []).filter((t) => now - t < this.opts.windowMs)
  }
}

export interface Session {
  email: string
  expiresAt: Date
}

/** Where sessions live. The token never reaches it, only its SHA-256. */
export interface SessionStore {
  create(tokenHash: Buffer, session: Session): Promise<void>
  find(tokenHash: Buffer, now: Date): Promise<Session | null>
  remove(tokenHash: Buffer): Promise<void>
}

export class MemorySessions implements SessionStore {
  private rows = new Map<string, Session>()
  async create(tokenHash: Buffer, session: Session) {
    this.rows.set(tokenHash.toString('hex'), session)
  }
  async find(tokenHash: Buffer, now: Date) {
    const s = this.rows.get(tokenHash.toString('hex'))
    return s && s.expiresAt > now ? s : null
  }
  async remove(tokenHash: Buffer) {
    this.rows.delete(tokenHash.toString('hex'))
  }
}

export type LoginResult =
  | { ok: true; token: string; session: Session }
  | { ok: false; status: 401 }
  | { ok: false; status: 429; reason: 'address' | 'locked'; retryAfter: number }

export class Auth {
  private queue: Promise<unknown> = Promise.resolve()

  constructor(
    private readonly account: { email: string; passwordHash: string },
    private readonly sessions: SessionStore,
    private readonly guard: LoginGuard = new LoginGuard(),
    private readonly now: () => number = Date.now,
  ) {}

  async login(email: string, password: string, address: string): Promise<LoginResult> {
    const verdict = this.guard.check(address)
    if (!verdict.ok) return { ok: false, status: 429, reason: verdict.reason, retryAfter: verdict.retryAfter }
    const emailOk = sameText(email.trim().toLowerCase(), this.account.email)
    // Both halves are always checked, one scrypt at a time.
    const passwordOk = password.length <= MAX_PASSWORD && (await this.serial(() => verifyPassword(password, this.account.passwordHash)))
    if (!(emailOk && passwordOk)) {
      this.guard.fail(address)
      return { ok: false, status: 401 }
    }
    this.guard.succeed(address)
    const token = randomBytes(32).toString('base64url')
    const session = { email: this.account.email, expiresAt: new Date(this.now() + SESSION_MS) }
    await this.sessions.create(sha256(token), session)
    return { ok: true, token, session }
  }

  /** The session a request's cookie names, or null when there is none or it expired. */
  async session(cookieHeader: string | undefined): Promise<Session | null> {
    const token = readCookie(cookieHeader)
    if (!token) return null
    return this.sessions.find(sha256(token), new Date(this.now()))
  }

  async logout(cookieHeader: string | undefined) {
    const token = readCookie(cookieHeader)
    if (token) await this.sessions.remove(sha256(token))
  }

  private serial<T>(fn: () => Promise<T>): Promise<T> {
    const run = this.queue.then(fn, fn)
    this.queue = run.catch(() => {})
    return run
  }
}

export function readCookie(header: string | undefined): string | null {
  if (!header) return null
  for (const part of header.split(';')) {
    const [name, ...rest] = part.trim().split('=')
    if (name === COOKIE) {
      const value = rest.join('=')
      return /^[A-Za-z0-9_-]{43}$/.test(value) ? value : null
    }
  }
  return null
}

export const sessionCookie = (token: string) => `${COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${SESSION_MS / 1000}`
export const clearCookie = () => `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`
