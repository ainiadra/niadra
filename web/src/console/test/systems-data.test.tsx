import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DataIssue, FailedIntake } from '../api/objectTypes'
import { API, at, baseRoutes, CONTROL, mockApi, signInAt, SP_PROD, SRC_BILLING } from './harness'

const KEPT: FailedIntake = {
  intake_id: '0192a000-0000-7000-8000-0000000000f1',
  kind: 'events',
  source_id: SRC_BILLING,
  received_at: '2026-10-08T09:00:00Z',
  failed_at: '2026-10-08T09:05:00Z',
  attempts: 5,
}
const issue = (kind: DataIssue['kind'], n: number): DataIssue => ({
  issue_id: `0192a000-0000-7000-8000-00000000000${n}`,
  kind,
  object_type: kind === 'drift' || kind === 'inference_limit' ? 'pedido' : null,
  field: kind === 'drift' ? 'cupom' : kind === 'inference_limit' ? 'fields_or_states' : 'acknowledged_batch',
  source: 'erp',
  refs: [],
  occurrences: 2,
  opened_at: '2026-10-08T09:05:00Z',
  last_seen_at: '2026-10-08T09:30:00Z',
})

const routes = (extra = {}) =>
  baseRoutes({
    [`GET ${CONTROL}/v1/config/mappings`]: { space_id: SP_PROD, type: 'mappings', version: 2, document: [] },
    [`GET ${CONTROL}/v1/config/webhook-endpoints`]: { space_id: SP_PROD, type: 'webhook-endpoints', version: 3, document: [] },
    [`GET ${API}/v1/sources/coverage`]: { since: '2026-10-01T00:00:00Z', items: [] },
    [`GET ${API}/v1/intake/failed`]: { items: [KEPT], kept: 1 },
    [`GET ${API}/v1/data-issues`]: { items: [issue('row_failed', 1), issue('budget_reached', 2), issue('drift', 3)], next_cursor: null },
    [`POST ${API}/v1/intake/failed/retry`]: { retried: 1 },
    ...extra,
  })

describe('Systems: what the memory kept aside and noticed', () => {
  it('lists a batch kept aside with its source and puts it back in the queue', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('sistemas'))
    const kept = await screen.findByRole('table', { name: 'Lotes guardados para nova tentativa' })
    const row = within(kept).getAllByRole('row')[1]!
    expect(row).toHaveTextContent('Eventos')
    expect(row).toHaveTextContent('5')
    expect(screen.queryByText(/Mostrando/)).not.toBeInTheDocument()
    await user.click(within(row).getByRole('button', { name: 'Tentar de novo' }))
    expect((await screen.findByRole('status')).textContent).toBe('1 lote voltou à fila.')
    expect(api.find('POST', '/v1/intake/failed/retry')[0]!.body).toEqual({ intake_ids: [KEPT.intake_id] })

    await user.click(screen.getByRole('button', { name: 'Tentar todos de novo' }))
    expect(api.find('POST', '/v1/intake/failed/retry')[1]!.body).toEqual({})
  })

  it('counts every batch kept when the list shows only some', async () => {
    mockApi(routes({ [`GET ${API}/v1/intake/failed`]: { items: [KEPT], kept: 742 } }))
    await signInAt(at('sistemas'))
    expect(await screen.findByText(/Mostrando 1 de 742 lotes guardados/)).toBeInTheDocument()
  })

  it('says nothing was lost when no batch is kept', async () => {
    mockApi(routes({ [`GET ${API}/v1/intake/failed`]: { items: [], kept: 0 } }))
    await signInAt(at('sistemas'))
    expect(await screen.findByText('Nenhum lote guardado: tudo o que chegou foi guardado na memória.')).toBeInTheDocument()
  })

  it('says an inference limit in words: what was reached, and that nothing past it was lost', async () => {
    mockApi(routes({ [`GET ${API}/v1/data-issues`]: { items: [issue('inference_limit', 4)], next_cursor: null } }))
    await signInAt(at('sistemas'))
    const notes = await screen.findByRole('list', { name: 'Avisos dos dados' })
    const note = within(notes).getByRole('listitem')
    expect(within(note).getByText('Limite da inferência')).toBeInTheDocument()
    expect(within(note).getByText(/Nada se perde/)).toBeInTheDocument()
    expect(within(note).getByText(/Campos ou estados demais neste tipo/)).toBeInTheDocument()
    expect(within(note).getByText('pedido')).toBeInTheDocument()
    expect(note).not.toHaveTextContent('fields_or_states')
  })

  it('has words for every limit the inference names', async () => {
    const { INFERENCE_LIMITS, inferenceLimit } = await import('../lib/labels')
    for (const what of ['types', 'fields_or_states', 'operations', 'operations_not_offered', 'unsafe_types', 'unsafe_names', 'unreadable_values', 'left_out'])
      expect(INFERENCE_LIMITS[what], what).toBeTruthy()
    expect(inferenceLimit('something_new')).toContain('something_new')
  })

  it('shows each data note in words, and leaves the notes about a type to Tipos', async () => {
    mockApi(routes())
    await signInAt(at('sistemas'))
    const notes = await screen.findByRole('list', { name: 'Avisos dos dados' })
    expect(within(notes).getByText('Lote guardado para nova tentativa')).toBeInTheDocument()
    expect(within(notes).getByText('Teto de IA do dia atingido')).toBeInTheDocument()
    expect(within(notes).queryByText('Campo novo no tráfego')).not.toBeInTheDocument()
    expect(within(notes).getAllByRole('listitem')).toHaveLength(2)
  })
})
