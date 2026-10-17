import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SCREENS, type ScreenId } from '../lib/screens'
import { readProgress } from '../lib/tutorialProgress'
import { TUTORIALS, tutorialsFor } from '../lib/tutorials'
import { RECEIPT } from './fixtures'
import { API, at, baseRoutes, CONTROL, mockApi, signInAt, USER_ID, type Handler, type Req } from './harness'

const routes = (extra: Record<string, Handler> = {}) =>
  baseRoutes({
    [`POST ${API}/v1/receipts/search`]: { items: [RECEIPT], next_cursor: null },
    [`GET ${API}/v1/profiles`]: { items: [], next_cursor: null },
    [`GET ${API}/v1/sources/arrived`]: { arrived: false, since: '2026-07-01T00:00:00Z' },
    [`GET ${API}/v1/sources/0192a000-0000-7000-8000-00000000b001/keys`]: [],
    [`GET ${API}/v1/sources/0192a000-0000-7000-8000-00000000b002/keys`]: [],
    ...extra,
  })

/** The control plane's store of a person's Console preferences, as the two routes read and write it. */
function preferences(kept: Record<string, unknown> = {}) {
  return {
    kept,
    routes: {
      [`GET ${CONTROL}/v1/users/me/preferences`]: () => ({ preferences: kept }),
      [`PUT ${CONTROL}/v1/users/me/preferences/tutorials`]: (r: Req) => ({ preferences: Object.assign(kept, { tutorials: r.body.value }) }),
      [`PUT ${CONTROL}/v1/users/me/preferences/welcome_seen`]: (r: Req) => ({ preferences: Object.assign(kept, { welcome_seen: r.body.value }) }),
    },
  }
}

describe('tutorials', () => {
  it('has a walkthrough for every screen of the registry, each step on a screen with its anchor in the code', () => {
    const ids = TUTORIALS.map((t) => t.id)
    expect(ids).toEqual([
      'tour',
      'customer-memory',
      'customer-relations',
      'relation-map',
      'identity',
      'patterns',
      'agent-memory',
      'why-answer',
      'turns',
      'coordination',
      'usage',
      'sales',
      'review',
      'gaps',
      'connect',
      'keys',
      'systems',
      'alerts',
      'types',
      'approve',
      'config',
      'erasure',
      'data-export',
      'who-read',
      'people',
      'sso',
    ])
    expect(new Set(ids).size).toBe(ids.length)
    // Every anchor a step names is in the source of a screen: data-tour="x", tour="x", or a per-tab anchor `x.${tab}`.
    const root = join(__dirname, '..')
    const sources = readdirSync(join(root, 'screens'), { recursive: true })
      .map(String)
      .filter((f) => f.endsWith('.tsx'))
      .map((f) => readFileSync(join(root, 'screens', f), 'utf8'))
      .join('\n')
    const anchored = (anchor: string) => {
      if (sources.includes(`data-tour="${anchor}"`) || sources.includes(`tour="${anchor}"`) || sources.includes(`'${anchor}'`)) return true
      const dot = anchor.indexOf('.')
      return dot > 0 && sources.includes(`data-tour={\`${anchor.slice(0, dot)}.\${tab}\`}`)
    }
    for (const t of TUTORIALS) {
      expect(t.steps.length, t.id).toBeGreaterThanOrEqual(4)
      expect(t.minutes, t.id).toBeGreaterThan(0)
      for (const s of t.steps) {
        expect(SCREENS[s.screen], `${t.id}: ${s.screen}`).toBeDefined()
        expect(s.anchor, `${t.id}: a step without an anchor`).toBeTruthy()
        expect(anchored(s.anchor!), `${t.id}: anchor ${s.anchor} is in no screen`).toBe(true)
        expect(s.text).not.toMatch(/[\u2013\u2014]/)
      }
    }
    // Every screen a person opens from the rail has a tutorial passing through it; the hub itself needs none.
    const uncovered = (Object.keys(SCREENS) as ScreenId[]).filter((id) => id !== 'tutorials' && tutorialsFor(id).length === 0)
    expect(uncovered).toEqual([])
    expect(tutorialsFor('receipts').map((t) => t.id)).toContain('why-answer')
  })

  it('lists the walkthroughs with the progress, and shows the steps of one', async () => {
    mockApi(routes())
    const { user } = await signInAt(at('tutoriais'))
    expect(await screen.findByRole('heading', { level: 1, name: 'Tutoriais' })).toBeInTheDocument()
    expect(screen.getByText('0 de 26 tutoriais concluídos')).toBeInTheDocument()
    // The hub groups the walkthroughs by the group of the rail their screen is in.
    expect(screen.getAllByRole('list', { name: /^Tutoriais: / }).map((l) => l.getAttribute('aria-label'))).toEqual([
      'Tutoriais: Para começar',
      'Tutoriais: Clientes e memória',
      'Tutoriais: O que os agentes fizeram',
      'Tutoriais: Privacidade e direitos',
      'Tutoriais: Conexões',
      'Tutoriais: Avançado',
      'Tutoriais: Administração',
    ])
    const list = screen.getByRole('list', { name: 'Tutoriais: Clientes e memória' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(5)
    await user.click(within(list).getAllByRole('button', { name: 'Ver os passos' })[0]!)
    const step = screen.getByText(/Clique em Abrir memória numa linha/)
    expect(step).toHaveTextContent(/^2\. Em Clientes: Clique em Abrir memória/)
  })

  it('takes the person to the first step, follows them from screen to screen, and records the walkthrough as done', async () => {
    mockApi(routes())
    const { user } = await signInAt(at('tutoriais'))
    const list = await screen.findByRole('list', { name: 'Tutoriais: Clientes e memória' })
    const card = within(list).getAllByRole('listitem')[0]!
    expect(card).toHaveTextContent('Ver o que um agente sabe de um cliente')
    await user.click(within(card).getByRole('button', { name: 'Começar' }))
    // The first step is on Clientes: the guide opens there, with the part of the screen it points at highlighted.
    expect(await screen.findByRole('heading', { level: 1, name: 'Clientes' })).toBeInTheDocument()
    const guide = screen.getByRole('region', { name: 'Tutorial: Ver o que um agente sabe de um cliente' })
    expect(guide).toHaveTextContent('Passo 1 de 5')
    await waitFor(() => expect(document.querySelector('[data-tour="customers.list"]')).toHaveClass('outline-brand'))
    await user.click(within(guide).getByRole('button', { name: 'Próximo' }))
    expect(guide).toHaveTextContent('Passo 2 de 5')
    await user.click(within(guide).getByRole('button', { name: 'Próximo' }))
    // The third step is on the memory of a customer: the guide waits for the person to open one.
    expect(guide).toHaveTextContent('Passo 3 de 5')
    expect(guide).toHaveTextContent('abra um item da lista em Clientes para seguir')
    expect(within(guide).getByRole('button', { name: 'Anterior' })).toBeEnabled()
    await user.click(within(guide).getByRole('button', { name: 'Próximo' }))
    await user.click(within(guide).getByRole('button', { name: 'Próximo' }))
    expect(guide).toHaveTextContent('Passo 5 de 5')
    await user.click(within(guide).getByRole('button', { name: 'Concluir' }))
    expect(screen.queryByRole('region', { name: /^Tutorial:/ })).not.toBeInTheDocument()
    expect(readProgress(USER_ID).done['customer-memory']).toBeTruthy()
    expect(sessionStorage.getItem('niadra.console.tutorial-run')).toBeNull()
  })

  it('lets the person leave the guide, and the help of a screen reopens the walkthroughs that pass through it', async () => {
    mockApi(routes())
    const { user } = await signInAt(at('recibos'))
    await screen.findByRole('heading', { level: 1, name: 'Comprovantes' })
    await user.click(screen.getAllByRole('button', { name: 'Ajuda desta tela' })[0]!)
    const dialog = screen.getByRole('dialog', { name: 'Ajuda: Comprovantes' })
    expect(dialog).toHaveTextContent(SCREENS.receipts.what)
    expect(within(dialog).getByText('Tutoriais que passam por aqui')).toBeInTheDocument()
    const row = within(dialog).getByText('Entender por que o agente respondeu aquilo').closest('li')!
    await user.click(within(row).getByRole('button', { name: 'Me leve lá' }))
    const guide = await screen.findByRole('region', { name: 'Tutorial: Entender por que o agente respondeu aquilo' })
    expect(guide).toHaveTextContent('Passo 1 de 5')
    await user.click(within(guide).getByRole('button', { name: 'Sair do tutorial' }))
    expect(screen.queryByRole('region', { name: /^Tutorial:/ })).not.toBeInTheDocument()
    expect(readProgress(USER_ID).done['why-answer']).toBeUndefined()
  })

  it('switches the tab of the same screen when the next step is on another tab, and highlights that tab', async () => {
    mockApi(
      routes({
        [`GET ${API}/v1/identity/assertions`]: { items: [], next_cursor: null },
        [`GET ${API}/v1/identity/suggestions`]: { items: [], next_cursor: null },
      }),
    )
    const { user } = await signInAt(at('tutoriais'))
    const list = await screen.findByRole('list', { name: 'Tutoriais: Clientes e memória' })
    const card = within(list).getAllByRole('listitem')[3]!
    expect(card).toHaveTextContent('Conferir que é o mesmo cliente em todos os canais')
    await user.click(within(card).getByRole('button', { name: 'Começar' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Identidade' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Asserções' })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(document.querySelector('[data-tour="identity.assertions"]')).toHaveClass('outline-brand'))
    const guide = screen.getByRole('region', { name: 'Tutorial: Conferir que é o mesmo cliente em todos os canais' })
    await user.click(within(guide).getByRole('button', { name: 'Próximo' }))
    expect(guide).toHaveTextContent('Passo 2 de 5')
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Sugestões' })).toHaveAttribute('aria-selected', 'true'))
    await waitFor(() => expect(document.querySelector('[data-tour="identity.suggestions"]')).toHaveClass('outline-brand'))
    expect(window.location.search).toBe('?aba=suggestions')
  })

  it('welcomes the person once, offering the tour, and never again', async () => {
    mockApi(routes())
    const { user } = await signInAt(at('clientes'), { welcome: true })
    const welcome = await screen.findByRole('dialog', { name: 'Bem-vindo ao Console da Niadra' })
    expect(welcome).toHaveTextContent('Não é uma tela de atendimento')
    await user.click(within(welcome).getByRole('button', { name: 'Fazer o tour de 5 minutos' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Início' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Tutorial: Conheça o Console em 5 minutos' })).toHaveTextContent('Passo 1 de 8')
    expect(localStorage.getItem(`niadra.console.welcome.${USER_ID}`)).toBe('true')
    // Leaving and coming back to a screen shows no welcome again.
    await user.click(within(screen.getByRole('navigation', { name: 'Seções do Console' })).getByRole('link', { name: 'Clientes' }))
    await screen.findByRole('heading', { level: 1, name: 'Clientes' })
    expect(screen.queryByRole('dialog', { name: 'Bem-vindo ao Console da Niadra' })).not.toBeInTheDocument()
  })

  it('keeps the progress on the control plane, so it follows the person to another browser', async () => {
    const store = preferences()
    const api = mockApi(routes(store.routes))
    const { user } = await signInAt(at('tutoriais'))
    const list = await screen.findByRole('list', { name: 'Tutoriais: Clientes e memória' })
    await user.click(within(within(list).getAllByRole('listitem')[0]!).getByRole('button', { name: 'Começar' }))
    const guide = await screen.findByRole('region', { name: 'Tutorial: Ver o que um agente sabe de um cliente' })
    for (let step = 1; step < 5; step++) await user.click(within(guide).getByRole('button', { name: 'Próximo' }))
    await user.click(within(guide).getByRole('button', { name: 'Concluir' }))
    await waitFor(() => expect(api.find('PUT', '/v1/users/me/preferences/tutorials')).toHaveLength(1))
    expect(Object.keys((store.kept.tutorials as { done: object }).done)).toEqual(['customer-memory'])
  })

  it('shows in a new browser what the person did in another, and no welcome again', async () => {
    const store = preferences({ tutorials: { done: { tour: '2026-09-20T10:00:00.000Z', erasure: '2026-09-21T10:00:00.000Z' } }, welcome_seen: true })
    const api = mockApi(routes(store.routes))
    await signInAt(at('tutoriais'), { welcome: true })
    expect(await screen.findByText('2 de 26 tutoriais concluídos')).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Bem-vindo ao Console da Niadra' })).not.toBeInTheDocument()
    expect(readProgress(USER_ID).done.tour).toBe('2026-09-20T10:00:00.000Z')
    // Nothing this browser knew was missing there: nothing is written back.
    expect(api.calls.filter((c) => c.method === 'PUT')).toHaveLength(0)
  })

  it('sends up what only this browser knew, and keeps working from its own copy when the control plane fails', async () => {
    localStorage.setItem(`niadra.console.tutorials.${USER_ID}`, JSON.stringify({ done: { tour: '2026-09-18T10:00:00.000Z' } }))
    const store = preferences({ tutorials: { done: { tour: '2026-09-19T10:00:00.000Z', approve: '2026-09-22T10:00:00.000Z' } } })
    const api = mockApi(routes(store.routes))
    const { view } = await signInAt(at('tutoriais'))
    expect(await screen.findByText('2 de 26 tutoriais concluídos')).toBeInTheDocument()
    // The earlier day of a tutorial done on both sides stands, and the welcome seen here goes up.
    await waitFor(() => expect(store.kept.welcome_seen).toBe(true))
    expect(store.kept.tutorials).toEqual({ done: { tour: '2026-09-18T10:00:00.000Z', approve: '2026-09-22T10:00:00.000Z' } })
    expect(api.find('PUT', '/v1/users/me/preferences/tutorials')).toHaveLength(1)
    view.unmount()

    mockApi(routes({ [`GET ${CONTROL}/v1/users/me/preferences`]: new Response('', { status: 500 }) }))
    await signInAt(at('tutoriais'))
    expect(await screen.findByText('2 de 26 tutoriais concluídos')).toBeInTheDocument()
  })
})
