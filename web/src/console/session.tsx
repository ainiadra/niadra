/**
 * The Console session. The token lives only in this module's React state and
 * refs: never localStorage, sessionStorage, cookies or the URL (architecture
 * 5.22). Reloading the tab ends the session on purpose.
 *
 * The control plane issues one person JWT whose audience is a space; the same
 * token is verified offline by the space's data API. `POST /v1/auth/token`
 * switches the space and renews the token, re-reading the person's roles.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { cellClient, controlClient, type CellClient, type ControlClient } from './api/clients'
import type { ApiError } from './api/http'
import type { MeOut, Role, Space, TokenClaims, TokenOut } from './api/types'
import { dataURLFor, type ConsoleConfig } from './config'

const EXPIRED_NOTICE = 'Sua sessão terminou. Entre de novo para continuar.'

interface Auth {
  token: string
  expiresAt: number
  claims: TokenClaims
  /** As typed at the login; the token carries no e-mail. */
  email: string
}

export interface ConsoleSession {
  config: ConsoleConfig
  auth: Auth | null
  /** GET /v1/users/me: the real e-mail and the spaces the person can open. Null while it loads. */
  me: MeOut | null
  /** The space the person entered in this tab; its data API is `cell`. */
  space: Space | null
  notice: string | null
  control: ControlClient
  cell: CellClient | null
  signIn: (res: TokenOut, email: string) => void
  enterSpace: (space: Space) => Promise<void>
  signOut: (notice?: string) => void
  hasRole: (...roles: Role[]) => boolean
}

const Ctx = createContext<ConsoleSession | null>(null)

export function useConsole(): ConsoleSession {
  const v = useContext(Ctx)
  if (!v) throw new Error('useConsole outside ConsoleProvider')
  return v
}

/** For screens under a space: the data API client is guaranteed there. */
export function useSpace() {
  const s = useConsole()
  if (!s.space || !s.cell || !s.auth) throw new Error('useSpace outside a space')
  return { space: s.space, cell: s.cell, control: s.control, hasRole: s.hasRole, config: s.config, auth: s.auth }
}

export class RegionNotConfigured extends Error {
  constructor(readonly region: string) {
    super(`no data API for region ${region} in config.json`)
  }
}

class InvalidToken extends Error {}

/** Reads the JWT payload for display and timers. Trust stays with the servers, which verify the signature. */
export function readClaims(token: string): TokenClaims {
  const part = token.split('.')[1]
  if (!part) throw new InvalidToken('not a JWT')
  try {
    const bin = atob(part.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(part.length / 4) * 4, '='))
    const c = JSON.parse(new TextDecoder().decode(Uint8Array.from(bin, (ch) => ch.charCodeAt(0)))) as Partial<TokenClaims>
    if (typeof c.space !== 'string' || !Array.isArray(c.roles)) throw new InvalidToken('missing claims')
    return c as TokenClaims
  } catch (e) {
    throw e instanceof InvalidToken ? e : new InvalidToken('unreadable payload')
  }
}

// Renew this long before the token expires (tokens live 15 minutes).
const RENEW_BEFORE_MS = 60_000

// setTimeout fires at once when the delay passes 2^31-1 ms (about 24.8 days).
const MAX_TIMER_MS = 2_147_483_647
function later(fn: () => void, ms: number): number | undefined {
  const wait = Math.max(0, ms)
  return wait > MAX_TIMER_MS ? undefined : window.setTimeout(fn, wait)
}

function toAuth(res: TokenOut, email: string): Auth {
  const claims = readClaims(res.access_token)
  const fromClaims = typeof claims.exp === 'number' ? claims.exp * 1000 : NaN
  const expiresAt = Number.isFinite(fromClaims) ? fromClaims : Date.now() + res.expires_in * 1000
  return { token: res.access_token, expiresAt, claims, email }
}

export function ConsoleProvider({ config, children }: { config: ConsoleConfig; children: ReactNode }) {
  const [auth, setAuth] = useState<Auth | null>(null)
  const [space, setSpace] = useState<Space | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [me, setMe] = useState<MeOut | null>(null)

  // Clients read the token through a ref, so a renewed token applies to the next call without rebuilding them.
  const authRef = useRef<Auth | null>(null)
  authRef.current = auth

  const signOut = useCallback((n?: string) => {
    authRef.current = null
    setAuth(null)
    setSpace(null)
    setNotice(n ?? null)
  }, [])

  const onUnauthenticated = useCallback((_: ApiError) => signOut(EXPIRED_NOTICE), [signOut])
  const token = useCallback(() => authRef.current?.token ?? null, [])

  const control = useMemo(() => controlClient({ baseURL: config.controlURL, token, onUnauthenticated }), [config.controlURL, token, onUnauthenticated])

  const dataURL = space ? dataURLFor(config, space) : null
  const cell = useMemo(() => (dataURL ? cellClient({ baseURL: dataURL, token, onUnauthenticated }) : null), [dataURL, token, onUnauthenticated])

  const replace = useCallback((next: Auth) => {
    authRef.current = next
    setAuth(next)
  }, [])

  const signIn = useCallback(
    (res: TokenOut, email: string) => {
      replace(toAuth(res, email))
      setSpace(null)
      setNotice(null)
    },
    [replace],
  )

  const enterSpace = useCallback(
    async (target: Space) => {
      if (!dataURLFor(config, target)) throw new RegionNotConfigured(target.region)
      const current = authRef.current
      if (!current) throw new InvalidToken('no session')
      if (current.claims.space !== target.space_id) replace(toAuth(await control.token(target.space_id), current.email))
      setSpace(target)
    },
    [config, control, replace],
  )

  // Silent renewal: a fresh token for the same space shortly before this one expires.
  // A failed renewal changes nothing; the expiry below still ends the session on time.
  useEffect(() => {
    if (!auth) return
    const renew = later(() => {
      control.token(auth.claims.space).then(
        (res) => replace(toAuth(res, auth.email)),
        () => {},
      )
    }, auth.expiresAt - Date.now() - RENEW_BEFORE_MS)
    const expire = later(() => signOut(EXPIRED_NOTICE), auth.expiresAt - Date.now())
    return () => {
      window.clearTimeout(renew)
      window.clearTimeout(expire)
    }
  }, [auth, control, replace, signOut])

  // Who the person is, once per sign-in (a renewal keeps the same person).
  const person = auth?.claims.sub
  useEffect(() => {
    if (!person) {
      setMe(null)
      return
    }
    const ac = new AbortController()
    control.me({ signal: ac.signal }).then(setMe, () => {})
    return () => ac.abort()
  }, [person, control])

  // The server has the last word on roles; admins pass every role check there too.
  const hasRole = useCallback((...roles: Role[]) => Boolean(auth && (auth.claims.roles.includes('admin') || auth.claims.roles.some((r) => roles.includes(r)))), [auth])

  const value = useMemo<ConsoleSession>(
    () => ({ config, auth, me, space, notice, control, cell, signIn, enterSpace, signOut, hasRole }),
    [config, auth, me, space, notice, control, cell, signIn, enterSpace, signOut, hasRole],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
