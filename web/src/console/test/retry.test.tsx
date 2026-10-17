import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/http'
import { retryDelayMs } from '../lib/useResource'
import { at, baseRoutes, CONTROL, mockApi, signInAt, SP_PROD, SP_SBX, tokenOut } from './harness'

/** A 503 as the control plane and the cells send it: problem+json, with Retry-After when they know. */
const busy = (retryAfter?: number) =>
  new Response(JSON.stringify({ type: 'https://docs.niadra.com/errors/unavailable', title: 'unavailable', status: 503, code: 'unavailable', request_id: 'req_busy' }), {
    status: 503,
    headers: { 'Content-Type': 'application/problem+json', ...(retryAfter ? { 'Retry-After': String(retryAfter) } : {}) },
  })

const RULES = { space_id: SP_PROD, type: 'trait-rules', version: 7, document: [{ rule_id: 'reclamacao.visita', catalog: 'recurring_complaint', version: 'v3', params: {} }] }

describe('A read the server asks to try later', () => {
  it('waits the Retry-After of a 503, says so, and shows the data when the space answers', async () => {
    let calls = 0
    const api = mockApi(baseRoutes({ [`GET ${CONTROL}/v1/config/trait-rules`]: () => (++calls === 1 ? busy(1) : RULES) }))
    await signInAt(at('padroes'))
    const wait = await screen.findByText(/O Console tenta de novo em 1 s\./)
    expect(wait.closest('[role="status"]')).toHaveTextContent('A região deste espaço pediu um instante.')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    // No click: the Console tries again by itself.
    expect(await screen.findByText('reclamacao.visita', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(api.find('GET', '/v1/config/trait-rules').length).toBeGreaterThanOrEqual(2)
  })

  it('lets the person try now instead of waiting', async () => {
    let calls = 0
    mockApi(baseRoutes({ [`GET ${CONTROL}/v1/config/trait-rules`]: () => (++calls === 1 ? busy(30) : RULES) }))
    const { user } = await signInAt(at('padroes'))
    expect(await screen.findByText(/O Console tenta de novo em 30 s\./)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Tentar agora' }))
    expect(await screen.findByText('reclamacao.visita')).toBeInTheDocument()
  })

  it('opens a space that is still being prepared once it answers, saying so meanwhile', async () => {
    let calls = 0
    mockApi(
      baseRoutes({
        // The sign-in lands on production; entering the sandbox asks for a token of that space.
        [`POST ${CONTROL}/v1/auth/token`]: (r: { body: { space_id: string } }) =>
          ++calls === 1 ? busy(1) : tokenOut({ roles: ['admin'], space: r.body.space_id, env: 'sandbox' }),
        [`GET ${CONTROL}/v1/config/trait-rules`]: RULES,
      }),
    )
    await signInAt(`/console/e/${SP_SBX}/padroes`)
    expect(await screen.findByRole('heading', { name: 'Abrindo o espaço' })).toBeInTheDocument()
    expect(screen.getByText(/Ela está ocupada ou ainda preparando o espaço\./)).toBeInTheDocument()
    expect(await screen.findByText('reclamacao.visita', {}, { timeout: 4000 })).toBeInTheDocument()
  })

  it('tries again only what the server asked to try later, never a refusal', () => {
    const problem = (status: number, retryAfter: number | null = null) => new ApiError({ kind: 'problem', status, code: 'x', title: 'x', retryAfter })
    expect(retryDelayMs(problem(503, 2))).toBe(2000)
    // Without Retry-After (a header the browser could not read), 3 s; never more than a minute.
    expect(retryDelayMs(problem(503))).toBe(3000)
    expect(retryDelayMs(problem(503, 600))).toBe(60_000)
    expect(retryDelayMs(problem(429, 7))).toBe(7000)
    for (const status of [400, 401, 403, 404, 409, 422, 500]) expect(retryDelayMs(problem(status, 5))).toBeNull()
    expect(retryDelayMs(new ApiError({ kind: 'network', code: 'network', title: 'x' }))).toBeNull()
    expect(retryDelayMs(new Error('x'))).toBeNull()
  })
})
