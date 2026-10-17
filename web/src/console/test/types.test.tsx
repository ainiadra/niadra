import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DataIssue, ObjectCoverage, ObjectTypeRegistry } from '../api/objectTypes'
import { fmtAge } from '../screens/Types'
import { API, at, baseRoutes, CONTROL, mockApi, problem, signInAt, SP_PROD, type Handler } from './harness'

const REGISTRY: ObjectTypeRegistry = {
  types: [
    {
      type: 'contrato',
      ownership: 'subject',
      mirror_of: { system: 'postgresql', derived_by: 'introspection', fingerprint: `sha256:${'a'.repeat(64)}`, drift: 'alert' },
      fields: {
        status: { type: 'enum' },
        declaracao_saude: { type: 'text', sensitivity: 'health', pii: true, access: [{ readers: ['agent:fechamento'], effect: 'allow' }] },
      },
      states: ['rascunho', 'assinado'],
    },
    { type: 'item', ownership: 'shared', mirror_of: { system: 'catalogo' }, fields: { preco: { type: 'money' } } },
  ],
}
const COVERAGE: ObjectCoverage = {
  types: [
    { type: 'contrato', objects: 1200, fields: [{ field: 'status', coverage: 0.985, age_p50_s: 5400 }, { field: 'declaracao_saude', coverage: 0.4, age_p50_s: 172800 }] },
    { type: 'item', objects: 0, fields: [{ field: 'preco', coverage: 0, age_p50_s: null }] },
  ],
}
const DRIFT: DataIssue = {
  issue_id: '0192a000-0000-7000-8000-0000000000d1',
  kind: 'drift',
  object_type: 'contrato',
  field: 'status',
  source: null,
  refs: [],
  occurrences: 3,
  opened_at: '2026-09-28T12:00:00Z',
  last_seen_at: '2026-09-29T09:30:00Z',
}
const UNDECLARED: DataIssue = {
  issue_id: '0192a000-0000-7000-8000-0000000000d2',
  kind: 'type_undeclared',
  object_type: 'cupom',
  field: null,
  source: 'buscar_cupons',
  refs: ['cupom:loja:c-1', 'cupom:loja:c-2'],
  occurrences: 5,
  opened_at: '2026-09-29T08:00:00Z',
  last_seen_at: '2026-09-29T09:00:00Z',
}

const HELD: DataIssue = {
  issue_id: '0192a000-0000-7000-8000-0000000000d3',
  kind: 'field_held',
  object_type: 'contrato',
  field: 'cpf_titular',
  source: 'sistema_contratos',
  refs: [],
  occurrences: 2,
  opened_at: '2026-09-29T08:00:00Z',
  last_seen_at: '2026-09-29T09:10:00Z',
}
const BY_KIND: Record<string, DataIssue> = { drift: DRIFT, type_undeclared: UNDECLARED, field_held: HELD }

const OBSERVED = {
  diff_id: '0192a000-0000-7000-8000-0000000000e1',
  space_id: SP_PROD,
  type: 'object-types',
  status: 'applied',
  reason: 'types the systems sent: processo',
  base_version: 0,
  author_id: null,
  author_label: 'niadra:observed',
  decided_by: null,
  decided_by_label: null,
  decided_at: '2026-10-07T18:00:00Z',
  applied_version: null,
  rollback_of: null,
  created_at: '2026-10-07T18:00:00Z',
  document: {
    types: [
      {
        type: 'processo',
        ownership: 'subject',
        mirror_of: { system: 'sistema_processos', derived_by: 'observation' },
        fields: { cnj: { type: 'string', label: 'Número do processo' } },
        states: ['ativo', 'encerrado'],
      },
      // A type a person declared wins: the inferred copy of it is not shown twice.
      { type: 'contrato', ownership: 'subject', mirror_of: { system: 'outro', derived_by: 'observation' } },
    ],
  },
}

const routes = (over: Record<string, Handler> = {}) =>
  baseRoutes({
    [`GET ${CONTROL}/v1/config/object-types`]: { space_id: SP_PROD, type: 'object-types', version: 3, document: REGISTRY },
    [`GET ${CONTROL}/v1/config/diffs`]: [],
    [`GET ${API}/v1/objects/coverage`]: COVERAGE,
    [`GET ${API}/v1/data-issues`]: (req) => ({ items: [BY_KIND[req.url.searchParams.get('kind') ?? '']!] }),
    ...over,
  })

describe('Types', () => {
  it('shows each type with where it comes from, the coverage of its fields and the new fields as notes', async () => {
    mockApi(routes())
    await signInAt(at('tipos'))
    const list = await screen.findByRole('list', { name: 'Tipos do espaço' })
    const [contract, item] = within(list).getAllByRole('listitem')
    expect(within(contract!).getByText('Derivado do esquema')).toBeInTheDocument()
    expect(within(contract!).getByText('Campo novo')).toBeInTheDocument()
    expect(within(contract!).getByText('98,5%')).toBeInTheDocument()
    expect(within(contract!).getByText('2 h')).toBeInTheDocument()
    expect(within(contract!).getByText('Sensível')).toBeInTheDocument()
    expect(within(contract!).getByText('Acesso por agente')).toBeInTheDocument()
    expect(within(item!).getByText('Compartilhado')).toBeInTheDocument()
    expect(within(item!).getByText('Declarado')).toBeInTheDocument()
    const drift = screen.getByRole('list', { name: 'Campos novos no tráfego' })
    expect(within(drift).getByText(/pela impressão digital do catálogo/)).toBeInTheDocument()
    expect(within(drift).getByText('Visto 3 vezes')).toBeInTheDocument()
    const undeclared = screen.getByRole('table', { name: 'Tipos novos que a Niadra acompanha' })
    expect(within(undeclared).getByText('cupom')).toBeInTheDocument()
    expect(within(undeclared).getByText('buscar_cupons')).toBeInTheDocument()
    const held = screen.getByRole('table', { name: 'Campos retidos até alguém declarar' })
    expect(within(held).getByText('cpf_titular')).toBeInTheDocument()
    // Zero touch: notes, never a task to declare.
    expect(document.body).not.toHaveTextContent(/declare o tipo/i)
    expect(document.body).toHaveTextContent('Declarar é opcional')
  })

  it('shows the types Niadra inferred from the traffic in a space nobody configured, with their undeclared fields', async () => {
    mockApi(
      routes({
        [`GET ${CONTROL}/v1/config/object-types`]: { space_id: SP_PROD, type: 'object-types', version: 0, document: { types: [] } },
        [`GET ${CONTROL}/v1/config/diffs`]: [OBSERVED],
        [`GET ${API}/v1/objects/coverage`]: {
          types: [{ type: 'processo', objects: 40, fields: [{ field: 'cnj', coverage: 1, age_p50_s: 60 }, { field: 'claim_amount', coverage: 0.9, age_p50_s: 60 }, { field: 'cpf_titular', coverage: 0.5, age_p50_s: 60 }] }],
        },
        [`GET ${API}/v1/data-issues`]: (req) => ({ items: req.url.searchParams.get('kind') === 'field_held' ? [{ ...HELD, object_type: 'processo' }] : [] }),
      }),
    )
    await signInAt(at('tipos'))
    const list = await screen.findByRole('list', { name: 'Tipos do espaço' })
    const [card] = within(list).getAllByRole('listitem')
    expect(within(card!).getByText('processo')).toBeInTheDocument()
    expect(within(card!).getByText('Derivado do tráfego')).toBeInTheDocument()
    expect(within(card!).getByText('claim_amount')).toBeInTheDocument()
    expect(within(card!).getByText('servido sem declaração')).toBeInTheDocument()
    expect(within(card!).getByText('Retido até declarar')).toBeInTheDocument()
    expect(screen.queryByText('Nenhum tipo ainda.')).not.toBeInTheDocument()
  })

  it('files a new field note away through the data issue', async () => {
    const api = mockApi(routes({ [`POST ${API}/v1/data-issues/${DRIFT.issue_id}/ack`]: { ...DRIFT, acknowledged_at: '2026-09-29T10:00:00Z' } }))
    const { user } = await signInAt(at('tipos'))
    await user.click(await screen.findByRole('button', { name: 'Arquivar a nota' }))
    expect(api.find('POST', `/v1/data-issues/${DRIFT.issue_id}/ack`)).toHaveLength(1)
  })

  it('guides a space with no type and tells a feature turned off from an error', async () => {
    mockApi(
      routes({
        [`GET ${CONTROL}/v1/config/object-types`]: { space_id: SP_PROD, type: 'object-types', version: 1, document: { types: [] } },
        [`GET ${API}/v1/objects/coverage`]: problem(404, 'not_found'),
        [`GET ${API}/v1/data-issues`]: problem(404, 'not_found'),
      }),
    )
    await signInAt(at('tipos'))
    expect(await screen.findByText('Nenhum tipo ainda.')).toBeInTheDocument()
    expect((await screen.findAllByText('Os avisos de dado estão desligados neste espaço.')).length).toBeGreaterThan(0)
  })

  it('shows an error it can retry when the registry does not load', async () => {
    mockApi(routes({ [`GET ${CONTROL}/v1/config/object-types`]: problem(500, 'internal_error') }))
    await signInAt(at('tipos'))
    expect(await screen.findByRole('button', { name: /Tentar/ })).toBeInTheDocument()
  })

  it('writes an age in its unit', () => {
    expect(fmtAge(42)).toBe('42 s')
    expect(fmtAge(300)).toBe('5 min')
    expect(fmtAge(7200)).toBe('2 h')
    expect(fmtAge(259200)).toBe('3 d')
    expect(fmtAge(null)).toBe('sem dado')
  })
})
