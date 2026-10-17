import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { AgentStateMetaPage } from '../api/types'
import { fmtSize } from '../screens/profile/AgentStateTab'
import { MEMORY, OBJECTS, PROFILE } from './fixtures'
import { API, at, baseRoutes, NO_FEATURES, mockApi, problem, signInAt, SRC_VOICE, type Handler } from './harness'

const PID = PROFILE.profile_id
const STATES: AgentStateMetaPage = {
  items: [
    { agent: 'closing', scope_kind: 'conversation', source_id: SRC_VOICE, bytes: 2150, version: 7, updated_at: '2026-09-22T16:58:00Z', valid_until: '2026-10-22T16:58:00Z', schema_version: '1' },
    { agent: 'stylist', scope_kind: 'subject', source_id: SRC_VOICE, bytes: 412, version: 1, updated_at: '2026-09-21T09:00:00Z', valid_until: '2026-10-21T09:00:00Z' },
    { agent: 'billing', scope_kind: 'task', source_id: SRC_VOICE, bytes: 70_000, version: 3, updated_at: '2026-09-20T09:00:00Z', valid_until: null },
  ],
}

const routes = (states: Handler) =>
  baseRoutes({
    [`GET ${API}/v1/profiles/${PID}`]: PROFILE,
    [`GET ${API}/v1/profiles/${PID}/memory`]: MEMORY,
    [`GET ${API}/v1/profiles/${PID}/objects`]: OBJECTS,
    [`GET ${API}/v1/profiles/${PID}/agent-state`]: states,
  })

describe('Working memory of the agents', () => {
  it('loads only when its tab opens, and shows size, version and dates, never the content', async () => {
    const api = mockApi(routes(STATES))
    const { user } = await signInAt(at(`clientes/${PID}`))
    await screen.findByText('Canal preferido')
    expect(api.find('GET', `/v1/profiles/${PID}/agent-state`)).toHaveLength(0)
    await user.click(screen.getByRole('tab', { name: 'Memória de trabalho' }))
    const list = await screen.findByRole('list', { name: /Memória de trabalho dos agentes/ })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(within(rows[0]!).getByText('closing')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('Conversa')).toBeInTheDocument()
    expect(within(rows[0]!).getByText(/2,1 KB/)).toBeInTheDocument()
    expect(within(rows[0]!).getByText(/versão 7/)).toBeInTheDocument()
    expect(within(rows[1]!).getByText('Cliente')).toBeInTheDocument()
    expect(within(rows[1]!).getByText(/412 B/)).toBeInTheDocument()
    expect(within(rows[1]!).getByText(/vale até/)).toBeInTheDocument()
    // A state is kept whole, and kept until the agent's type names a retention: never "valid until no date".
    expect(within(rows[2]!).getByText(/68,4 KB/)).toBeInTheDocument()
    expect(within(rows[2]!).getByText(/guardada até o tipo do agente definir uma retenção/)).toBeInTheDocument()
    expect(within(rows[2]!).queryByText(/vale até/)).not.toBeInTheDocument()
    expect(screen.getByText(/O conteúdo é do agente e não aparece aqui/)).toBeInTheDocument()
  })

  it('says when no agent kept a state, and when the space has the feature off', async () => {
    mockApi(routes({ items: [] }))
    const { user } = await signInAt(at(`clientes/${PID}`))
    await screen.findByText('Canal preferido')
    await user.click(screen.getByRole('tab', { name: 'Memória de trabalho' }))
    expect(await screen.findByText('Nenhum agente guardou memória de trabalho sobre este cliente.')).toBeInTheDocument()
  })

  it('tells a feature turned off from an error', async () => {
    mockApi(routes(problem(404, 'not_found')))
    const { user } = await signInAt(at(`clientes/${PID}`))
    await screen.findByText('Canal preferido')
    await user.click(screen.getByRole('tab', { name: 'Memória de trabalho' }))
    expect(await screen.findByText('A memória de trabalho dos agentes está desligada neste espaço.')).toBeInTheDocument()
  })

  it('writes sizes in bytes below a kilobyte and in KB above', () => {
    expect(fmtSize(0)).toBe('0 B')
    expect(fmtSize(1023)).toBe('1.023 B')
    expect(fmtSize(16384)).toBe('16 KB')
  })

  it('leaves the working memory and inferences tabs out of a space that turned them off', async () => {
    const api = mockApi({ ...routes(STATES), ...NO_FEATURES })
    await signInAt(at(`clientes/${PID}`))
    await screen.findByText('Canal preferido')
    await waitFor(() => expect(api.find('GET', '/v1/config/features')).toHaveLength(1))
    await new Promise((resolve) => setTimeout(resolve, 50))
    const tabs = screen.getByRole('tablist', { name: 'Memória do cliente' })
    expect(within(tabs).getByRole('tab', { name: 'Fatos' })).toBeInTheDocument()
    expect(within(tabs).queryByRole('tab', { name: 'Memória de trabalho' })).not.toBeInTheDocument()
    expect(within(tabs).queryByRole('tab', { name: 'Inferências' })).not.toBeInTheDocument()
  })
})
