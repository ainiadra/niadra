// The admin panel in the browser, with the API stubbed: sign-in, the session ending, both tabs and the CSV.
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AccessReport } from '../../server/access/store'
import { niceCeil } from './Access'
import type { Contact } from './api'
import { AdminApp } from './App'
import { contactsCsv, csvCell } from './csv'

const SESSION = { email: 'founder@example.com', expiresAt: new Date(Date.now() + 3_600_000).toISOString() }
const contact = (id: number, over: Partial<Contact> = {}): Contact => ({
  id, created_at: '2026-09-28T15:00:00Z', name: `Contato de teste ${id}`, email: `teste${id}@example.com`, company: `Empresa ${id}`,
  interest: 'producao', message: `Mensagem ${id}`, lang: 'pt', email_domain: 'example.com', domain_has_mx: true, source: 'direto', landing_page: '/', heard_from: null, page: '/enterprise', form: 'full', ...over,
})
const zero = { human: 0, ai: 0, search: 0, scanner: 0, bot: 0 }
const report: AccessReport = {
  range: '7d', from: '2026-09-22', to: '2026-09-28', since: '2026-09-23',
  days: ['22', '23', '24', '25', '26', '27', '28'].map((d, i) => ({ day: `2026-09-${d}`, counts: { ...zero, human: i * 10, ai: i }, visitors: i === 6 ? 7 : 0 })),
  totals: { human: 210, ai: 21, search: 4, scanner: 90, bot: 12 }, visitors: 7,
  top: { human: [{ path: '/', hits: 120 }, { path: '/blog/x', hits: 30 }], ai: [{ path: '/llms.txt', hits: 15 }], search: [], scanner: [{ path: '/.env', hits: 40 }], bot: [] },
  blog: [{ path: '/blog/x', counts: { ...zero, human: 30, ai: 3 }, total: 33 }],
  crawlers: [{ name: 'GPTBot', company: 'OpenAI', purpose: 'training', cls: 'ai', hits: 12, pages: 4, topPath: '/llms.txt' }],
  languages: { pt: { ...zero, human: 150, ai: 6 }, en: { ...zero, human: 60, ai: 0 }, other: { ...zero, ai: 15 } },
  countries: [
    { country: 'BR', counts: { ...zero, human: 140 } },
    { country: 'AE', counts: { ...zero, human: 12 } },
    { country: 'unknown', counts: { ...zero, human: 58, ai: 21 } },
  ],
  campaigns: [{ source: 'linkedin', medium: 'ads', campaign: 'uae-test', counts: { ...zero, human: 9 } }],
}

type Route = (init?: RequestInit) => { status: number; body: unknown }
function stubApi(routes: Record<string, Route>) {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${url.split('?')[0]}`
    calls.push(`${key}${url.includes('?') ? `?${url.split('?')[1]}` : ''}`)
    const r = routes[key]?.(init) ?? { status: 404, body: { error: 'not_found' } }
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } })
  }))
  return calls
}
const ok = (body: unknown): Route => () => ({ status: 200, body })

describe('admin panel', () => {
  it('signs in, shows the error for a wrong password, then the contacts', async () => {
    let signedIn = false
    stubApi({
      'GET /api/admin/session': () => (signedIn ? { status: 200, body: SESSION } : { status: 401, body: { error: 'session' } }),
      'POST /api/admin/login': (init) => {
        const { password } = JSON.parse(String(init?.body)) as { password: string }
        if (password !== 'certa') return { status: 401, body: { error: 'credentials' } }
        signedIn = true
        return { status: 200, body: SESSION }
      },
      'GET /api/admin/contacts': ok({ contacts: [contact(2), contact(1)] }),
    })
    const user = userEvent.setup()
    render(<AdminApp />)
    await user.type(await screen.findByLabelText('E-mail'), 'founder@example.com')
    await user.type(screen.getByLabelText('Senha'), 'errada')
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('E-mail ou senha incorretos.')
    expect(screen.getByLabelText('Senha')).toHaveValue('')
    await user.type(screen.getByLabelText('Senha'), 'certa')
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByRole('heading', { name: 'Contatos' })).toBeInTheDocument()
    expect(screen.getByText('2 pedidos, do mais novo para o mais antigo.')).toBeInTheDocument()
  })

  it('says how long to wait when sign-in is rate limited', async () => {
    stubApi({
      'GET /api/admin/session': () => ({ status: 401, body: { error: 'session' } }),
      'POST /api/admin/login': () => ({ status: 429, body: { error: 'locked', retryAfter: 840 } }),
    })
    const user = userEvent.setup()
    render(<AdminApp />)
    await user.type(await screen.findByLabelText('E-mail'), 'founder@example.com')
    await user.type(screen.getByLabelText('Senha'), 'x')
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A entrada está bloqueada por excesso de tentativas. Tente de novo em 14 min.')
  })

  it('goes back to the sign-in when the session ends', async () => {
    stubApi({ 'GET /api/admin/session': ok(SESSION), 'GET /api/admin/contacts': () => ({ status: 401, body: { error: 'session' } }) })
    render(<AdminApp />)
    expect(await screen.findByText('Sua sessão terminou. Entre de novo.')).toBeInTheDocument()
    expect(screen.getByLabelText('Senha')).toBeInTheDocument()
  })

  it('searches contacts without minding accents and says when nothing matches', async () => {
    stubApi({ 'GET /api/admin/session': ok(SESSION), 'GET /api/admin/contacts': ok({ contacts: [contact(1, { company: 'Açúcar Ltda' }), contact(2)] }) })
    const user = userEvent.setup()
    render(<AdminApp />)
    const search = await screen.findByLabelText('Buscar')
    await screen.findByText('2 pedidos, do mais novo para o mais antigo.')
    await user.type(search, 'acucar')
    expect(await screen.findByText('1 de 2 pedidos.')).toBeInTheDocument()
    await user.clear(search)
    await user.type(search, 'ninguém')
    expect(await screen.findByText('Nenhum contato com “ninguém”.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Exportar CSV' })).toBeDisabled()
  })

  it('shows the empty and the error states of the contacts', async () => {
    stubApi({ 'GET /api/admin/session': ok(SESSION), 'GET /api/admin/contacts': ok({ contacts: [] }) })
    render(<AdminApp />)
    expect(await screen.findByText('Nenhum pedido de contato ainda.')).toBeInTheDocument()
  })

  it('offers a retry when the contacts fail to load', async () => {
    let fail = true
    stubApi({ 'GET /api/admin/session': ok(SESSION), 'GET /api/admin/contacts': () => (fail ? { status: 500, body: { error: 'internal' } } : { status: 200, body: { contacts: [contact(1)] } }) })
    const user = userEvent.setup()
    render(<AdminApp />)
    expect(await screen.findByText('O servidor falhou ao responder.')).toBeInTheDocument()
    fail = false
    await user.click(screen.getByRole('button', { name: 'Tentar de novo' }))
    expect(await screen.findByText('1 pedido, do mais novo para o mais antigo.')).toBeInTheDocument()
  })

  it('shows the access counts, switches the class and the range', async () => {
    window.history.pushState({}, '', '/acessos')
    const calls = stubApi({ 'GET /api/admin/session': ok(SESSION), 'GET /api/admin/access': ok(report) })
    const user = userEvent.setup()
    render(<AdminApp />)
    expect(await screen.findByRole('heading', { name: 'Requisições de pessoas por dia' })).toBeInTheDocument()
    const tiles = screen.getByRole('group', { name: /Totais por tipo de visitante/ })
    expect(within(tiles).getByRole('button', { name: /Pessoas/ })).toHaveAttribute('aria-pressed', 'true')
    expect(within(tiles).getByText('7 visitantes distintos no único dia com essa contagem')).toBeInTheDocument()
    const langs = screen.getByRole('region', { name: 'Idioma pedido por pessoas' })
    expect(within(langs).getByText('Português')).toBeInTheDocument()
    expect(within(langs).getByText('150')).toBeInTheDocument()
    expect(within(langs).getByText('Inglês (/en)')).toBeInTheDocument()
    expect(within(langs).getByText('60')).toBeInTheDocument()
    await user.click(within(tiles).getByRole('button', { name: /IAs/ }))
    expect(screen.getByRole('heading', { name: 'Requisições de IAs por dia' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Páginas mais pedidas por IAs' })).toBeInTheDocument()
    expect(screen.getAllByText('/llms.txt').length).toBeGreaterThan(0)
    expect(screen.getAllByText('GPTBot').length).toBeGreaterThan(0)
    expect(screen.getAllByText('/blog/x').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: '30 dias' }))
    expect(calls).toContain('GET /api/admin/access?range=30d')
  })

  it('says when nothing has been counted yet', async () => {
    window.history.pushState({}, '', '/acessos')
    stubApi({ 'GET /api/admin/session': ok(SESSION), 'GET /api/admin/access': ok({ ...report, since: null }) })
    render(<AdminApp />)
    expect(await screen.findByText('A contagem ainda não registrou nenhum acesso.')).toBeInTheDocument()
  })
})

describe('contacts CSV', () => {
  it('quotes every cell and defuses anything that looks like a formula', () => {
    expect(csvCell('=HYPERLINK("x")')).toBe(`"'=HYPERLINK(""x"")"`)
    expect(csvCell('+55 11')).toBe(`"'+55 11"`)
    expect(csvCell('-1')).toBe(`"'-1"`)
    expect(csvCell('@SUM')).toBe(`"'@SUM"`)
    expect(csvCell('linha 1\nlinha 2')).toBe('"linha 1\nlinha 2"')
    expect(csvCell(null)).toBe('""')
  })

  it('opens in a spreadsheet with accents and one row per request', () => {
    const csv = contactsCsv([contact(1, { message: 'Olá; tudo "bem"?' })], () => '28/09/2026 12:00')
    expect(csv.startsWith('﻿"Data";"Nome";"Empresa";"E-mail"')).toBe(true)
    expect(csv).toContain('"28/09/2026 12:00";"Contato de teste 1";"Empresa 1";"teste1@example.com";"Plano Produção";"Olá; tudo ""bem""?"')
    expect(csv.trimEnd().split('\r\n')).toHaveLength(2)
  })
})

describe('chart axis', () => {
  it('rounds the top of the axis up to 1, 2 or 5 times a power of ten', () => {
    expect([1, 3, 7, 12, 50, 51, 505, 1896].map(niceCeil)).toEqual([1, 5, 10, 20, 50, 100, 1000, 2000])
  })
})
