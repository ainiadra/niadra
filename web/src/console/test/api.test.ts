import { describe, expect, it, vi } from 'vitest'
import { cellClient, controlClient } from '../api/clients'
import { ApiError } from '../api/http'
import { dataURLFor, parseConfig } from '../config'
import { readClaims } from '../session'
import { API, CONTROL, jwt, mockApi, problem, SP_PROD } from './harness'

const transport = (baseURL: string, onUnauthenticated = vi.fn(), token: string | null = 'jwt.x') => ({ baseURL, token: () => token, onUnauthenticated })

describe('API client', () => {
  it('sends the Bearer JWT and parses problem+json into ApiError', async () => {
    const api = mockApi({ [`GET ${API}/v1/profiles/p1`]: problem(403, 'forbidden', 'the role cannot do this', 'req_abc') })
    const err = await cellClient(transport(API)).profile('p1').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ kind: 'problem', status: 403, code: 'forbidden', detail: 'the role cannot do this', requestId: 'req_abc' })
    expect(api.calls[0]!.headers.get('Authorization')).toBe('Bearer jwt.x')
  })

  it('falls back to the status code when the body is not problem+json, and reads Retry-After', async () => {
    mockApi({ [`POST ${API}/v1/receipts/search`]: new Response('busy', { status: 429, headers: { 'Retry-After': '7' } }) })
    const err = (await cellClient(transport(API)).searchReceipts({}).catch((e: unknown) => e)) as ApiError
    expect(err.code).toBe('rate_limited')
    expect(err.retryAfter).toBe(7)
  })

  it('reports a network failure as kind network', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    const err = (await controlClient(transport(CONTROL)).projects().catch((e: unknown) => e)) as ApiError
    expect(err.kind).toBe('network')
  })

  it('never puts a searched identifier in the URL: it goes in the body with the limit', async () => {
    const canary = '+15550199999'
    const api = mockApi({ [`POST ${API}/v1/profiles/search`]: { items: [] } })
    await cellClient(transport(API)).searchProfiles({ query: canary })
    const call = api.calls[0]!
    expect(call.url.href).not.toContain('5550199999')
    expect(call.body).toEqual({ query: canary, limit: 20 })
  })

  it('sends Idempotency-Key on identity and erasure writes, which the server requires', async () => {
    const op = { op_id: 'op1', status: 'queued', assertion_id: null, link_id: null }
    const api = mockApi({
      [`POST ${API}/v1/identity/merge`]: op,
      [`POST ${API}/v1/identity/handles/block`]: op,
      [`POST ${API}/v1/forget`]: { request_id: 'r1', status: 'pending', target_kind: 'profile', target_id: 'p1', requested_at: '2026-09-22T12:00:00Z', erased: {}, export_run_ids: [] },
    })
    const cell = cellClient(transport(API))
    await cell.merge(['a', 'b'], false, '')
    await cell.blockHandle({ type: 'phone_e164', value: '+15550100000' }, 'loja')
    await cell.forget({ target: 'profile', profile_id: 'p1' })
    for (const c of api.calls) expect(c.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(api.calls[0]!.body).toEqual({ profile_ids: ['a', 'b'], force: false, reason: null })
    expect(api.calls[1]!.url.href).not.toContain('5550100000')
  })

  it('repeats list query parameters, as FastAPI expects for group_by', async () => {
    const api = mockApi({ [`GET ${API}/v1/context-use`]: { since: '', until: '', group_by: [], buckets: [] } })
    await cellClient(transport(API)).contextUse({ since: '2026-09-01', until: '2026-09-22', group_by: ['source_id', 'channel'] })
    expect(api.calls[0]!.url.searchParams.getAll('group_by')).toEqual(['source_id', 'channel'])
    expect(api.calls[0]!.url.searchParams.get('since')).toBe('2026-09-01')
  })

  it('ends the session on a 401 only when a token was sent', async () => {
    mockApi({ [`GET ${CONTROL}/v1/projects`]: problem(401, 'unauthenticated'), [`POST ${CONTROL}/v1/auth/login`]: problem(401, 'unauthenticated', 'invalid credentials') })
    const withToken = vi.fn()
    await controlClient(transport(CONTROL, withToken)).projects().catch(() => {})
    expect(withToken).toHaveBeenCalledOnce()
    const noToken = vi.fn()
    await controlClient(transport(CONTROL, noToken, null)).login({ email: 'a@b.com', password: 'x' }).catch(() => {})
    expect(noToken).not.toHaveBeenCalled()
  })

  it('downloads the export package with the Bearer token', async () => {
    const api = mockApi({ [`GET ${API}/v1/exports/run1/download`]: new Response('zip', { status: 200, headers: { 'Content-Type': 'application/zip' } }) })
    const blob = await cellClient(transport(API)).downloadExport('run1')
    expect(await blob.text()).toBe('zip')
    expect(api.calls[0]!.headers.get('Authorization')).toBe('Bearer jwt.x')
  })
})

describe('runtime config and token claims', () => {
  it('reads controlURL and apiURL, and prefers a cell or region entry for a space', () => {
    const cfg = parseConfig({ controlURL: 'https://control.1-2-3-4.sslip.io/', apiURL: 'https://api.1-2-3-4.sslip.io', cells: { 'eu-west-1': 'https://eu.example.com' } })
    expect(cfg.controlURL).toBe('https://control.1-2-3-4.sslip.io')
    expect(dataURLFor(cfg, { cell_id: 'cell-sa-1', region: 'sa-east-1' })).toBe('https://api.1-2-3-4.sslip.io')
    expect(dataURLFor(cfg, { cell_id: 'cell-eu-1', region: 'eu-west-1' })).toBe('https://eu.example.com')
  })

  it('rejects plain http outside localhost and a config with no data API', () => {
    expect(() => parseConfig({ controlURL: 'http://c.example.com', apiURL: 'https://a' })).toThrow()
    expect(() => parseConfig({ controlURL: 'https://c.example.com' })).toThrow()
    expect(parseConfig({ controlURL: 'http://localhost:8080', apiURL: 'http://127.0.0.1:8000' }).apiURL).toBe('http://127.0.0.1:8000')
  })

  it('reads the space, roles and expiry from the person JWT', () => {
    const c = readClaims(jwt({ roles: ['security', 'review'] }))
    expect(c.space).toBe(SP_PROD)
    expect(c.roles).toEqual(['security', 'review'])
    expect(() => readClaims('not-a-jwt')).toThrow()
  })
})
