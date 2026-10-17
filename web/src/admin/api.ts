/**
 * The admin API (server/admin/routes.ts), same origin, with the session in an HttpOnly cookie the
 * page never sees. Every call can end the session: a 401 sends the panel back to the sign-in.
 */
import type { AccessReport, Range } from '../../server/access/store'
import type { Contact } from '../../server/admin/routes'

export type { AccessReport, Contact, Range }

export class AdminError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly retryAfter?: number,
  ) {
    super(`${status} ${code}`)
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...init })
  } catch {
    throw new AdminError(0, 'network')
  }
  const body = (await res.json().catch(() => ({}))) as { error?: string; retryAfter?: number }
  if (!res.ok) throw new AdminError(res.status, body.error ?? 'unknown', body.retryAfter)
  return body as T
}

const post = (body?: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body ?? {}),
})

export interface Session {
  email: string
  expiresAt: string
}

export const api = {
  login: (email: string, password: string) => call<Session>('/api/admin/login', post({ email, password })),
  logout: () => call<{ ok: true }>('/api/admin/logout', post()),
  session: () => call<Session>('/api/admin/session'),
  contacts: () => call<{ contacts: Contact[] }>('/api/admin/contacts').then((r) => r.contacts),
  access: (range: Range) => call<AccessReport>(`/api/admin/access?range=${range}`),
}

/** What the panel says about a failed call, in Portuguese. */
export function describe(err: unknown): { title: string; detail: string } {
  const e = err instanceof AdminError ? err : new AdminError(0, 'unknown')
  if (e.status === 0) return { title: 'Sem conexão com o servidor.', detail: 'Confira a internet e tente de novo.' }
  if (e.status >= 500) return { title: 'O servidor falhou ao responder.', detail: 'Nada foi perdido. Tente de novo em alguns segundos.' }
  return { title: 'A consulta não deu certo.', detail: `O servidor respondeu ${e.status} (${e.code}).` }
}
