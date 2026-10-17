import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { CoordinationOverview } from '../api/coordination'
import { API, at, baseRoutes, json, mockApi, problem, signInAt } from './harness'

const OVERVIEW: CoordinationOverview = {
  since: '2026-09-22T12:00:00Z',
  ownership: [
    { kind: 'owner', level: 'human_active', via: 'mapping', holder_kind: 'human', claims: 3, holders: 1 },
    { kind: 'task_lock', level: 'agent_active', via: 'declaration', holder_kind: 'agent', claims: 2, holders: 2 },
  ],
  effects: [
    { kind: 'delivery', state: 'done', effects: 40 },
    { kind: 'delivery', state: 'unknown_outcome', effects: 1 },
  ],
  awaiting_decision: 1,
  conflicts: { ownership_overlaps: 4, commitments_superseded: 1, handoffs_expired: 0, tokens_reused: 0 },
  budgets: [{ purpose: 'marketing', limit: 2, per_hours: 168, subjects: 120, at_limit: 30, spent: 150 }],
  contacts: [{ purpose: 'marketing', direction: 'outbound', state: 'made', contacts: 150 }],
}
const REPORTS = {
  items: [{ report_id: 'r1', status: 'done', days: 30, requested_at: '2026-09-20T10:00:00Z', finished_at: '2026-09-20T10:04:00Z', summary: { conflicts: 12 } }],
}

describe('Coordination', () => {
  it('shows ownership, effects, conflicts, budgets and shadow runs, in counts', async () => {
    const api = mockApi(
      baseRoutes({
        [`GET ${API}/v1/coordination/overview`]: OVERVIEW,
        [`GET ${API}/v1/coordination/reports`]: REPORTS,
        [`POST ${API}/v1/coordination/shadow`]: json(202, { report_id: 'r2', status: 'pending' }),
      }),
    )
    const { user } = await signInAt(at('coordenacao'))
    expect(await screen.findByRole('heading', { name: 'Coordenação' })).toBeInTheDocument()
    const ownership = await screen.findByRole('region', { name: 'Posse agora' })
    expect(within(ownership).getByText('Pessoa atendendo')).toBeInTheDocument()
    expect(within(ownership).getByText('Evento de sistema mapeado')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Efeitos' })).getByText('Resultado desconhecido')).toBeInTheDocument()
    expect(screen.getByText('Efeito esperando decisão')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Orçamentos de contato' })).getByText('2 a cada semana')).toBeInTheDocument()
    expect(api.find('GET', '/v1/coordination/overview')[0]!.url.searchParams.get('days')).toBe('7')

    await user.click(screen.getByRole('button', { name: '30 dias' }))
    expect(await screen.findByRole('button', { name: '30 dias', pressed: true })).toBeInTheDocument()
    expect(api.find('GET', '/v1/coordination/overview').at(-1)!.url.searchParams.get('days')).toBe('30')

    const runs = await screen.findByRole('list', { name: 'Execuções em sombra' })
    await user.click(within(runs).getByRole('button', { name: 'Ver o relatório' }))
    expect(within(runs).getByLabelText('Relatório da execução em sombra')).toHaveTextContent('"conflicts": 12')
    await user.click(screen.getByRole('button', { name: 'Rodar em sombra' }))
    expect(api.find('POST', '/v1/coordination/shadow')[0]!.body).toEqual({ days: 30 })
  })

  it('says when the space has coordination off', async () => {
    mockApi(baseRoutes({ [`GET ${API}/v1/coordination/overview`]: problem(404, 'not_found', 'not found') }))
    await signInAt(at('coordenacao'))
    expect(await screen.findByText('A coordenação não está ligada neste espaço.')).toBeInTheDocument()
  })
})
