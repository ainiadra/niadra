import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/http'
import type { Page } from '../api/types'

/** Tries of a read that keeps answering 503 or 429 before the screen shows the error and a button. */
const RETRY_LIMIT = 30
const DEFAULT_WAIT_S = 3
const MAX_WAIT_S = 60

/**
 * A read the server asked to try again later: a 503 (the region is busy, or the space is still being
 * prepared) or a 429, with the seconds of its Retry-After. The hook waits and tries again by itself;
 * ErrorState shows it as a wait with a countdown, not as a failure.
 */
export class Retrying extends Error {
  constructor(
    readonly cause: ApiError,
    /** When the next try starts, in milliseconds since the epoch. */
    readonly at: number,
    readonly attempt: number,
  ) {
    super(cause.message)
    this.name = 'Retrying'
  }
}

/** How long to wait before trying a failed read again, in ms, or null when it is not worth trying. */
export function retryDelayMs(error: unknown): number | null {
  if (!(error instanceof ApiError) || !(error.status === 503 || error.status === 429)) return null
  const seconds = Math.min(Math.max(error.retryAfter ?? DEFAULT_WAIT_S, 1), MAX_WAIT_S)
  return seconds * 1000
}

/** Resolves true after `ms`, or false as soon as the signal aborts. */
function sleep(ms: number, signal: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve(false)
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', stop)
      resolve(true)
    }, ms)
    const stop = () => {
      clearTimeout(timer)
      resolve(false)
    }
    signal.addEventListener('abort', stop, { once: true })
  })
}

type Status = 'idle' | 'loading' | 'success' | 'error'

export interface Resource<T> {
  status: Status
  data: T | undefined
  error: unknown
  reload: () => void
}

/**
 * One request per change of `deps`, aborted when the view goes away or the inputs change. A 503 or a 429
 * is tried again after its Retry-After, up to RETRY_LIMIT times, with a `Retrying` error in the meantime.
 */
export function useResource<T>(load: (signal: AbortSignal) => Promise<T>, deps: readonly unknown[], enabled = true): Resource<T> {
  const [state, setState] = useState<{ status: Status; data?: T; error?: unknown }>({ status: enabled ? 'loading' : 'idle' })
  const [nonce, setNonce] = useState(0)
  const loadRef = useRef(load)
  loadRef.current = load

  useEffect(() => {
    if (!enabled) {
      setState({ status: 'idle' })
      return
    }
    const ac = new AbortController()
    setState({ status: 'loading' })
    const run = async () => {
      for (let attempt = 1; ; attempt += 1) {
        try {
          const data = await loadRef.current(ac.signal)
          if (!ac.signal.aborted) setState({ status: 'success', data })
          return
        } catch (error: unknown) {
          if (ac.signal.aborted) return
          const wait = retryDelayMs(error)
          if (wait === null || attempt >= RETRY_LIMIT) {
            setState({ status: 'error', error })
            return
          }
          // The wait shows on the screen; the next try runs behind it, without a loading flash.
          setState({ status: 'error', error: new Retrying(error as ApiError, Date.now() + wait, attempt) })
          if (!(await sleep(wait, ac.signal))) return
        }
      }
    }
    void run()
    return () => ac.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, enabled])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { status: state.status, data: state.data, error: state.error, reload }
}

export interface Paged<T> {
  status: Status
  items: T[]
  error: unknown
  hasMore: boolean
  loadingMore: boolean
  moreError: unknown
  loadMore: () => void
  reload: () => void
}

/** Cursor pagination: the first page loads with the view; "Carregar mais" appends the next. */
export function usePaged<T>(
  load: (cursor: string | null, signal: AbortSignal) => Promise<Page<T>>,
  deps: readonly unknown[],
  enabled = true,
): Paged<T> {
  const first = useResource((signal) => load(null, signal), deps, enabled)
  const [extra, setExtra] = useState<T[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [more, setMore] = useState<{ loading: boolean; error: unknown }>({ loading: false, error: null })
  const loadRef = useRef(load)
  loadRef.current = load

  useEffect(() => {
    setExtra([])
    setCursor(first.data?.next_cursor ?? null)
    setMore({ loading: false, error: null })
  }, [first.data])

  const loadMore = useCallback(() => {
    if (!cursor) return
    setMore({ loading: true, error: null })
    loadRef.current(cursor, new AbortController().signal).then(
      (page) => {
        setExtra((prev) => [...prev, ...page.items])
        setCursor(page.next_cursor ?? null)
        setMore({ loading: false, error: null })
      },
      (error: unknown) => setMore({ loading: false, error }),
    )
  }, [cursor])

  return {
    status: first.status,
    items: [...(first.data?.items ?? []), ...extra],
    error: first.error,
    hasMore: Boolean(cursor),
    loadingMore: more.loading,
    moreError: more.error,
    loadMore,
    reload: first.reload,
  }
}

/** The most pages a deep link walks to find its page's cursor, so a typed `?p=` cannot run on forever. */
const WALK_LIMIT = 50

/** The router state of a numbered list: `cursors[i]` reads page i + 1, so `cursors[0]` is always null. */
interface PagesState {
  cursors: (string | null)[]
}

function cursorsOf(state: unknown): (string | null)[] {
  const c = (state as Partial<PagesState> | null)?.cursors
  if (!Array.isArray(c) || c[0] !== null || !c.every((x) => x === null || typeof x === 'string')) return [null]
  return c
}

/** `?p=` as a page number: a whole number from 1, and 1 for anything else. */
function pageOf(raw: string | null): number {
  const n = Number(raw)
  return Number.isInteger(n) && n >= 1 ? n : 1
}

interface Landed<T> {
  page: number
  items: T[]
  next: string | null
  cursors: (string | null)[]
  /** The history entry the read was made for: right after a move, the page on screen belongs to the last one. */
  entry: string
}

export interface NumberedPages<T> {
  /** 'loading' only before the first page shows; while another page is read, the last one stays, with `busy`. */
  status: Status
  page: number
  items: T[]
  error: unknown
  busy: boolean
  hasPrevious: boolean
  hasNext: boolean
  previous: () => void
  next: () => void
  reload: () => void
}

/**
 * Cursor pagination shown one page at a time, for a long list a person browses (Clientes). The page number
 * lives in the address (`?p=`, absent on the first page), so reloading and going back keep it, and the cursor
 * of every page visited lives in the router state, so "Anterior" reads its page straight away; the cursor never
 * reaches the address. A page opened without its cursor (a reload, which passes through the sign-in, or a link
 * typed by hand) walks from the first page, following each `next_cursor`, and lands on the last page there is
 * when the list ends before it. The other parameters of the address stay as they are.
 */
export function useNumberedPages<T>(
  load: (cursor: string | null, signal: AbortSignal) => Promise<Page<T>>,
  deps: readonly unknown[],
): NumberedPages<T> {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const page = pageOf(params.get('p'))
  const known = cursorsOf(location.state)
  const knownRef = useRef(known)
  knownRef.current = known
  const entryRef = useRef(location.key)
  entryRef.current = location.key
  const loadRef = useRef(load)
  loadRef.current = load

  const read = useResource<Landed<T>>(
    async (signal) => {
      const entry = entryRef.current
      const cursors = [...knownRef.current]
      let at = Math.min(page, cursors.length)
      const from = at
      let res = await loadRef.current(cursors[at - 1] ?? null, signal)
      while (at < page && res.next_cursor && at - from < WALK_LIMIT) {
        cursors[at] = res.next_cursor
        at += 1
        res = await loadRef.current(res.next_cursor, signal)
      }
      return { page: at, items: res.items, next: res.next_cursor ?? null, cursors: cursors.slice(0, at), entry }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [...deps, page],
  )

  // The last page read stays on screen while the next one is read, so the list does not jump.
  const shown = useRef<Landed<T> | null>(null)
  if (read.status === 'success' && read.data) shown.current = read.data
  const landed = shown.current

  const search = useCallback(
    (n: number) => {
      const next = new URLSearchParams(params)
      if (n > 1) next.set('p', String(n))
      else next.delete('p')
      const s = next.toString()
      return s ? `?${s}` : ''
    },
    [params],
  )

  // A walk keeps the cursors it found, and a page past the end of the list becomes the last one.
  useEffect(() => {
    const d = read.status === 'success' ? read.data : undefined
    if (!d || d.entry !== location.key || (d.page === page && d.cursors.length <= known.length)) return
    navigate({ search: search(d.page) }, { replace: true, state: { cursors: d.cursors } satisfies PagesState })
  }, [read.status, read.data, location.key, page, known.length, navigate, search])

  const busy = read.status === 'loading' && landed !== null
  const next = useCallback(() => {
    if (!landed?.next || busy) return
    navigate({ search: search(landed.page + 1) }, { state: { cursors: [...landed.cursors.slice(0, landed.page), landed.next] } satisfies PagesState })
  }, [landed, busy, navigate, search])
  const previous = useCallback(() => {
    if (!landed || landed.page <= 1 || busy) return
    navigate({ search: search(landed.page - 1) }, { state: { cursors: landed.cursors } satisfies PagesState })
  }, [landed, busy, navigate, search])

  return {
    status: read.status === 'error' ? 'error' : landed ? 'success' : read.status,
    page: landed?.page ?? page,
    items: landed?.items ?? [],
    error: read.error,
    busy,
    hasPrevious: (landed?.page ?? 1) > 1,
    hasNext: Boolean(landed?.next),
    previous,
    next,
    reload: read.reload,
  }
}

export interface NextPages<P> {
  pages: P[]
  hasMore: boolean
  loading: boolean
  error: unknown
  loadMore: () => void
}

/**
 * The pages after one already read, by the cursor the read names (`timeline_next`, `objects_next`):
 * "Carregar mais" appends each. Reading the first page again starts over.
 */
export function useNextPages<P>(
  first: unknown,
  next: string | null | undefined,
  load: (cursor: string) => Promise<P>,
  nextOf: (page: P) => string | null | undefined,
): NextPages<P> {
  const [pages, setPages] = useState<P[]>([])
  const [cursor, setCursor] = useState<string | null>(next ?? null)
  const [more, setMore] = useState<{ loading: boolean; error: unknown }>({ loading: false, error: null })
  const loadRef = useRef(load)
  loadRef.current = load
  const nextRef = useRef(nextOf)
  nextRef.current = nextOf

  useEffect(() => {
    setPages([])
    setCursor(next ?? null)
    setMore({ loading: false, error: null })
  }, [first, next])

  const loadMore = useCallback(() => {
    if (!cursor) return
    setMore({ loading: true, error: null })
    loadRef.current(cursor).then(
      (page) => {
        setPages((prev) => [...prev, page])
        setCursor(nextRef.current(page) ?? null)
        setMore({ loading: false, error: null })
      },
      (error: unknown) => setMore({ loading: false, error }),
    )
  }, [cursor])

  return { pages, hasMore: Boolean(cursor), loading: more.loading, error: more.error, loadMore }
}
