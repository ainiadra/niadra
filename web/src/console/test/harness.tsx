import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter, Routes } from 'react-router-dom'
import { vi } from 'vitest'
import { consoleRoutes } from '../routes'
import type { ProjectOut, Role, SourceOut, TokenClaims, TokenOut } from '../api/types'

export const CONTROL = 'https://control.test'
export const API = 'https://api.test'
const CONFIG = { controlURL: CONTROL, apiURL: API }

export interface Req {
  method: string
  url: URL
  body: any
  headers: Headers
}
type Reply = Response | object | undefined
export type Handler = Reply | ((req: Req) => Reply | Promise<Reply>)

export const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json', ...headers },
  })

/** A problem as the server writes it (adapters/inbound/http/errors.py). */
export const problem = (status: number, code: string, detail?: string, requestId = 'req_test_1') =>
  json(status, { type: `https://docs.niadra.com/errors/${code}`, title: code.replace(/_/g, ' '), status, code, detail, request_id: requestId })

/**
 * Replaces fetch with a router keyed by "METHOD origin+path" (or "METHOD path").
 * Unknown routes answer 404 problem+json, so a missing mock fails loudly.
 */
export function mockApi(routes: Record<string, Handler>) {
  const calls: Req[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://localhost')
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
    const req: Req = { method, url, body, headers: new Headers(init?.headers) }
    calls.push(req)
    const h = routes[`${method} ${url.origin}${url.pathname}`] ?? routes[`${method} ${url.pathname}`]
    if (h === undefined) return problem(404, 'not_found', `no mock for ${method} ${url.href}`)
    const r = typeof h === 'function' ? await h(req) : h
    if (r instanceof Response) return r
    return json(200, r ?? {})
  })
  vi.stubGlobal('fetch', fn)
  const find = (method: string, path: string) => calls.filter((c) => c.method === method && c.url.pathname === path)
  return { calls, find }
}

const TENANT = '0192a000-0000-7000-8000-000000000001'
export const SP_PROD = '0192a000-0000-7000-8000-00000000a001'
export const SP_SBX = '0192a000-0000-7000-8000-00000000a002'
const SP_FAR = '0192a000-0000-7000-8000-00000000a003'
export const SRC_VOICE = '0192a000-0000-7000-8000-00000000b001'
export const SRC_BILLING = '0192a000-0000-7000-8000-00000000b002'

const b64url = (s: string) => btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

/** A person JWT with the claims app/control.py writes; the signature is irrelevant to the browser. */
export function jwt(claims: Partial<TokenClaims> = {}): string {
  const full: TokenClaims = {
    sub: USER_ID,
    tenant: TENANT,
    space: SP_PROD,
    region: 'sa-east-1',
    env: 'production',
    roles: ['admin'],
    sources: null,
    exp: Math.floor(Date.now() / 1000) + 900,
    ...claims,
  }
  return `${b64url(JSON.stringify({ alg: 'EdDSA', typ: 'JWT' }))}.${b64url(JSON.stringify(full))}.c2ln`
}

/** Ten recovery codes as the control plane issues them: Crockford base32, `XXXXX-XXXXX`. */
export const CODES = ['7KQ2M-XA9BD', 'H3VNP-8R2TW', 'C4ZE6-JM7QK', 'P9WXT-2BD5N', 'R8HCV-6KMZ3', 'T2NQA-WV47H', 'Y6DJ9-QX3PC', 'B5MRK-HT8WZ', 'E7GXN-4PQ2V', 'W3ZKC-9NBHD']

export const tokenOut = (claims: Partial<TokenClaims> = {}): TokenOut => ({
  access_token: jwt(claims),
  token_type: 'Bearer',
  expires_in: 900,
  space_id: claims.space ?? SP_PROD,
})

const PROJECTS: ProjectOut[] = [
  {
    project_id: '0192a000-0000-7000-8000-0000000000p1',
    name: 'Atendimento e cobrança',
    slug: 'atendimento',
    region: 'sa-east-1',
    spaces: [
      { space_id: SP_PROD, project_id: '0192a000-0000-7000-8000-0000000000p1', environment: 'production', slug: 'atendimento-prod', region: 'sa-east-1', cell_id: 'cell-sa-1' },
      { space_id: SP_SBX, project_id: '0192a000-0000-7000-8000-0000000000p1', environment: 'sandbox', slug: 'atendimento-sbx', region: 'sa-east-1', cell_id: 'cell-sa-1' },
    ],
  },
  {
    project_id: '0192a000-0000-7000-8000-0000000000p2',
    name: 'Expansão',
    slug: 'expansao',
    region: 'eu-west-1',
    spaces: [{ space_id: SP_FAR, project_id: '0192a000-0000-7000-8000-0000000000p2', environment: 'production', slug: 'expansao-prod', region: 'eu-west-1', cell_id: 'cell-eu-1' }],
  },
]

export const SOURCES: SourceOut[] = [
  {
    source_id: SRC_VOICE,
    space_id: SP_PROD,
    name: 'Agente de voz',
    audience: 'customer_agent',
    vendor: 'Fornecedor A',
    channel: 'voice',
    purposes: ['atendimento'],
    verification_ceiling: 'V2',
    confirms_before_acting: true,
    navigation_enabled: true,
    trusted_action_ops: [],
    critical: false,
    revoked_at: null,
  },
  {
    source_id: SRC_BILLING,
    space_id: SP_PROD,
    name: 'Agente de cobrança',
    audience: 'internal_agent',
    vendor: 'Fornecedor B',
    channel: 'erp',
    purposes: ['cobranca'],
    verification_ceiling: 'no_customer',
    confirms_before_acting: false,
    navigation_enabled: false,
    trusted_action_ops: ['invoice.credit'],
    critical: false,
    revoked_at: null,
  },
]

/** GET /v1/users/me: the person's real e-mail and every space with its roles. */
export const ME = (roles: Role[] = ['admin']) => ({
  user_id: '0192a000-0000-7000-8000-0000000000u1',
  tenant_id: TENANT,
  email: 'ana.costa@empresa.com',
  name: null,
  spaces: PROJECTS.flatMap((p) => p.spaces.map(({ cell_id: _cell, ...s }) => ({ ...s, roles }))),
})

const ALL_FEATURES = ['turns', 'state', 'agent_state', 'signals', 'claims', 'coordination', 'measurement', 'notifications', 'legal_holds']

/** A space that turned no agent feature on: its `features` document is the default one. */
export const NO_FEATURES: Record<string, Handler> = {
  [`GET ${CONTROL}/v1/config/features`]: (r: Req) => ({ space_id: r.url.searchParams.get('space_id'), type: 'features', version: 0, document: { enabled: [] } }),
}

/**
 * The routes every signed-in screen needs. The login token is for the
 * production space; `POST /v1/auth/token` answers for whatever space is asked.
 */
export function baseRoutes(extra: Record<string, Handler> = {}, roles: Role[] = ['admin']): Record<string, Handler> {
  return {
    'GET /config.json': CONFIG,
    [`POST ${CONTROL}/v1/auth/login`]: tokenOut({ roles }),
    [`POST ${CONTROL}/v1/auth/token`]: (r: Req) => tokenOut({ roles, space: r.body.space_id, env: r.body.space_id === SP_SBX ? 'sandbox' : 'production' }),
    [`GET ${CONTROL}/v1/projects`]: PROJECTS,
    [`GET ${CONTROL}/v1/users/me`]: ME(roles),
    [`GET ${CONTROL}/v1/tenants/${TENANT}`]: { tenant_id: TENANT, name: 'Empresa Exemplo', slug: 'exemplo', mfa_required: true, created_at: '2026-01-01T00:00:00Z' },
    [`GET ${CONTROL}/v1/sources`]: SOURCES,
    [`POST ${CONTROL}/v1/auth/sso/discover`]: { sso: false, protocol: null, enforced: false },
    // Every agent feature on.
    [`GET ${CONTROL}/v1/config/features`]: (r: Req) => ({ space_id: r.url.searchParams.get('space_id'), type: 'features', version: 1, document: { enabled: ALL_FEATURES } }),
    ...extra,
  }
}

/** The person of every test, as the JWT names them. */
export const USER_ID = '0192a000-0000-7000-8000-0000000000u1'

export function renderAt(path: string, { welcome = false } = {}) {
  // The welcome shows once per person; only a test about it wants it.
  if (!welcome) localStorage.setItem(`niadra.console.welcome.${USER_ID}`, 'true')
  window.history.pushState({}, '', path)
  return render(
    <BrowserRouter>
      <Routes>{consoleRoutes}</Routes>
    </BrowserRouter>,
  )
}

/**
 * Opens a deep link, signs in on the way (tokens exist only in memory) and lands on the page: the e-mail
 * first, then the password, as the sign-in asks when the domain has no SSO.
 */
export async function signInAt(path: string, options: { welcome?: boolean } = {}) {
  const user = userEvent.setup()
  const view = renderAt(path, options)
  // The default `findBy*` margin (setup.ts) already covers a slow first render under CI load.
  await user.type(await screen.findByLabelText('E-mail corporativo'), 'ana@empresa.com')
  await user.click(screen.getByRole('button', { name: 'Continuar' }))
  await user.type(await screen.findByLabelText('Senha'), 'senha-de-teste')
  await user.click(screen.getByRole('button', { name: 'Entrar' }))
  return { user, view }
}

/** Where a screen lives, for the production space. */
export const at = (rest: string) => `/console/e/${SP_PROD}/${rest}`

/** Matches a paragraph by its whole text, for lines built from several inline elements (the origin stamp). */
export const paragraph = (re: RegExp) => (_: string, el: Element | null) => el?.tagName === 'P' && re.test(el.textContent ?? '')

/**
 * Chooses an option in a dropdown (src/components/Select.tsx) the way a person does: opens the list
 * and clicks the option. `value` is the option's value, as `selectOptions` took it on a <select>.
 */
export async function choose(user: ReturnType<typeof userEvent.setup>, trigger: HTMLElement, value: string) {
  await user.click(trigger)
  const option = document.querySelector<HTMLElement>(`[role="option"][data-value="${CSS.escape(value)}"]`)
  if (!option) throw new Error(`no option with value ${value} in ${trigger.id}`)
  await user.click(option)
}
