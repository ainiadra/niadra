/**
 * Transport shared by the control and cell clients: Bearer JWT on every call,
 * RFC 9457 problem+json parsed into ApiError, a timeout, and query strings
 * built only from ids. Personal data (handles, free text) travels in bodies,
 * never in the URL.
 */

export type ErrorKind = 'problem' | 'network' | 'timeout' | 'parse'

export class ApiError extends Error {
  readonly kind: ErrorKind
  readonly status: number
  readonly code: string
  readonly title: string
  readonly detail: string | null
  readonly requestId: string | null
  readonly retryAfter: number | null

  constructor(init: {
    kind: ErrorKind
    status?: number
    code: string
    title: string
    detail?: string | null
    requestId?: string | null
    retryAfter?: number | null
  }) {
    super(init.detail ?? init.title)
    this.name = 'ApiError'
    this.kind = init.kind
    this.status = init.status ?? 0
    this.code = init.code
    this.title = init.title
    this.detail = init.detail ?? null
    this.requestId = init.requestId ?? null
    this.retryAfter = init.retryAfter ?? null
  }
}

/** Codes the backend sends (adapters/inbound/http/errors.py), used when a body is not problem+json. */
const CODE_BY_STATUS: Record<number, string> = {
  400: 'invalid_input',
  401: 'unauthenticated',
  403: 'forbidden',
  404: 'not_found',
  409: 'conflict',
  421: 'wrong_cell',
  422: 'invalid_input',
  500: 'internal_error',
  429: 'rate_limited',
  503: 'unavailable',
}

type QueryValue = string | number | boolean | null | undefined
type Query = Record<string, QueryValue | readonly QueryValue[]>

export interface Transport {
  baseURL: string
  token: () => string | null
  /** Called on 401 so the session can end and the person goes back to the login. */
  onUnauthenticated?: (err: ApiError) => void
  timeoutMs?: number
}

export interface CallOptions {
  query?: Query
  body?: unknown
  /** Required by the backend on governance actions (revoke, rotate, forget). */
  idempotencyKey?: string
  signal?: AbortSignal
}

function buildURL(baseURL: string, path: string, query?: Query): string {
  const url = new URL(path.replace(/^\//, ''), baseURL.endsWith('/') ? baseURL : `${baseURL}/`)
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      // Arrays repeat the parameter (FastAPI list query params, e.g. group_by).
      for (const item of Array.isArray(v) ? v : [v]) {
        if (item === undefined || item === null || item === '') continue
        url.searchParams.append(k, String(item))
      }
    }
  }
  return url.toString()
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID()
}

async function readProblem(res: Response): Promise<ApiError> {
  const retry = res.headers.get('Retry-After')
  const retryAfter = retry && /^\d+$/.test(retry) ? Number(retry) : null
  const headerRequestId = res.headers.get('X-Request-Id')
  let body: Record<string, unknown> | null = null
  try {
    const text = await res.text()
    body = text ? (JSON.parse(text) as Record<string, unknown>) : null
  } catch {
    body = null
  }
  const str = (v: unknown) => (typeof v === 'string' && v.length > 0 ? v : null)
  return new ApiError({
    kind: 'problem',
    status: res.status,
    code: str(body?.code) ?? CODE_BY_STATUS[res.status] ?? 'server_error',
    title: str(body?.title) ?? res.statusText ?? 'erro',
    detail: str(body?.detail),
    requestId: str(body?.request_id) ?? headerRequestId,
    retryAfter,
  })
}

async function send(t: Transport, method: string, path: string, opts: CallOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json, application/problem+json' }
  const token = t.token()
  if (token) headers.Authorization = `Bearer ${token}`
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey

  const timeout = AbortSignal.timeout(t.timeoutMs ?? 20_000)
  const signal = opts.signal ? AbortSignal.any([opts.signal, timeout]) : timeout

  let res: Response
  try {
    res = await fetch(buildURL(t.baseURL, path, opts.query), {
      method,
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal,
      credentials: 'omit',
      cache: 'no-store',
      referrerPolicy: 'no-referrer',
    })
  } catch (e) {
    if (opts.signal?.aborted) throw e
    const timedOut = timeout.aborted
    throw new ApiError({
      kind: timedOut ? 'timeout' : 'network',
      code: timedOut ? 'timeout' : 'network',
      title: timedOut ? 'timeout' : 'network',
    })
  }

  if (!res.ok) {
    const err = await readProblem(res)
    // A 401 on a call made with a token means the session ended; a failed login is not that.
    if (res.status === 401 && token) t.onUnauthenticated?.(err)
    throw err
  }
  return res
}

export async function call<T>(t: Transport, method: string, path: string, opts: CallOptions = {}): Promise<T> {
  const res = await send(t, method, path, opts)
  if (res.status === 204) return undefined as T
  try {
    return (await res.json()) as T
  } catch {
    throw new ApiError({ kind: 'parse', status: res.status, code: 'parse', title: 'parse' })
  }
}

/** A download that needs the Bearer token (the export package): fetched as a blob, never a bare link. */
export async function callBlob(t: Transport, path: string): Promise<Blob> {
  const res = await send(t, 'GET', path, {})
  return res.blob()
}
