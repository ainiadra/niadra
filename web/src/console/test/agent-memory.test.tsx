import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { AgentNote, AgentNoteProposal } from '../api/types'
import { API, at, baseRoutes, CONTROL, mockApi, problem, signInAt, SP_PROD, SRC_BILLING, SRC_VOICE } from './harness'

const NOTE: AgentNote = {
  note_id: '0192a000-0000-7000-8000-0000000000n1',
  source_id: SRC_BILLING,
  visibility: 'source',
  kind: 'procedure',
  title: 'Crédito só aparece depois do refresh',
  body: 'No ERP, chamar post_credit e depois refresh_invoice, nessa ordem.',
  tags: ['invoice', 'credit'],
  evidence: { conversation_id: 'conv-77' },
  origin: 'agent',
  version: 2,
  supersedes_note_id: '0192a000-0000-7000-8000-0000000000n0',
  status: 'active',
  created_at: '2026-09-24T12:00:00Z',
  created_by: `source:${SRC_BILLING}`,
}
const PROPOSAL: AgentNoteProposal = {
  proposal_id: '0192a000-0000-7000-8000-0000000000q1',
  source_id: SRC_VOICE,
  origin: 'distilled',
  kind: 'pitfall',
  title: 'Agenda recusa data sem fuso',
  body: 'Mandar a data com o deslocamento de fuso.',
  tags: ['visit'],
  visibility: 'source',
  evidence: { conversation_id: 'conv-9' },
  status: 'pending',
  created_at: '2026-09-24T13:00:00Z',
}

describe('Agent memory', () => {
  const routes = (extra = {}) =>
    baseRoutes({
      [`GET ${API}/v1/agent-memory/notes`]: { items: [NOTE], next_cursor: null },
      [`GET ${API}/v1/agent-memory/proposals`]: { items: [PROPOSAL], next_cursor: null },
      [`GET ${CONTROL}/v1/config/agent-memory`]: { space_id: SP_PROD, type: 'agent-memory', version: 1, document: { enabled: false, writes: 'agent' } },
      [`GET ${CONTROL}/v1/config/types`]: [{ type: 'agent-memory', description: '', roles: ['integration'] }],
      ...extra,
    })

  it('lists the notes with their agent, kind, version and origin, and never a customer', async () => {
    mockApi(routes())
    await signInAt(at('memoria-dos-agentes'))
    const list = await screen.findByRole('list', { name: 'Notas dos agentes' })
    expect(within(list).getByText('Crédito só aparece depois do refresh')).toBeInTheDocument()
    expect(within(list).getByText('Procedimento')).toBeInTheDocument()
    expect(within(list).getByText('Versão 2')).toBeInTheDocument()
    expect(within(list).getByText(/Escrita pelo agente/)).toBeInTheDocument()
    expect(within(list).getByText('origem: conversa conv-77')).toBeInTheDocument()
  })

  it('says in words that a note with personal data was refused', async () => {
    const api = mockApi(routes({ [`POST ${API}/v1/agent-memory/notes`]: problem(422, 'personal_data_in_agent_memory', 'agent notes never hold personal data (found: phone)') }))
    const { user } = await signInAt(at('memoria-dos-agentes'))
    await user.click(await screen.findByRole('button', { name: 'Nova nota' }))
    const dialog = await screen.findByRole('dialog', { name: 'Nova nota' })
    await user.type(within(dialog).getByLabelText('Título'), 'Ligar de volta')
    await user.type(within(dialog).getByLabelText('Texto'), 'Ligar para o cliente no fim do dia')
    await user.type(within(dialog).getByLabelText('Tags'), 'callback')
    await user.click(within(dialog).getByRole('button', { name: 'Gravar nota' }))
    expect(await within(dialog).findByText('A nota tem dado pessoal e não foi gravada.')).toBeInTheDocument()
    const [sent] = api.find('POST', '/v1/agent-memory/notes')
    expect(sent!.body).toMatchObject({ kind: 'procedure', visibility: 'source', tags: ['callback'], source_id: SRC_VOICE })
  })

  it('edits a note into a new version and retires one', async () => {
    const api = mockApi(
      routes({
        [`PATCH ${API}/v1/agent-memory/notes/${NOTE.note_id}`]: { ...NOTE, version: 3 },
        [`POST ${API}/v1/agent-memory/notes/${NOTE.note_id}/retire`]: { ...NOTE, status: 'retired' },
      }),
    )
    const { user } = await signInAt(at('memoria-dos-agentes'))
    await user.click(await screen.findByRole('button', { name: `Editar ${NOTE.title}` }))
    const dialog = await screen.findByRole('dialog', { name: 'Editar nota (gera versão nova)' })
    await user.clear(within(dialog).getByLabelText('Título'))
    await user.type(within(dialog).getByLabelText('Título'), 'Crédito aparece após refresh')
    await user.click(within(dialog).getByRole('button', { name: 'Gravar versão nova' }))
    expect(api.find('PATCH', `/v1/agent-memory/notes/${NOTE.note_id}`)[0]!.body).toMatchObject({ title: 'Crédito aparece após refresh' })
    await user.click(screen.getByRole('button', { name: `Aposentar ${NOTE.title}` }))
    expect(api.find('POST', `/v1/agent-memory/notes/${NOTE.note_id}/retire`)).toHaveLength(1)
  })

  it('approves a distilled proposal and asks for a new one with the conversation id in the body', async () => {
    const api = mockApi(
      routes({
        [`POST ${API}/v1/agent-memory/proposals/${PROPOSAL.proposal_id}/approve`]: { ...NOTE, origin: 'distilled' },
        [`POST ${API}/v1/agent-memory/distill`]: { ...PROPOSAL, status: 'drafting', title: '', body: '' },
      }),
    )
    const { user } = await signInAt(at('memoria-dos-agentes'))
    await user.click(await screen.findByRole('tab', { name: 'Propostas' }))
    await user.click(await screen.findByRole('button', { name: `Aprovar ${PROPOSAL.title}` }))
    expect(api.find('POST', `/v1/agent-memory/proposals/${PROPOSAL.proposal_id}/approve`)).toHaveLength(1)
    await user.type(screen.getByLabelText('ID da conversa'), '+5511999990001')
    await user.click(screen.getByRole('button', { name: 'Destilar' }))
    const [asked] = api.find('POST', '/v1/agent-memory/distill')
    expect(asked!.body).toEqual({ source_id: SRC_VOICE, conversation_id: '+5511999990001' })
    expect(asked!.url.href).not.toContain('5511999990001')
  })

  it('turns agent memory on by a diff with a reason', async () => {
    const api = mockApi(routes({ [`POST ${CONTROL}/v1/config/diffs`]: { diff_id: 'df1', space_id: SP_PROD, type: 'agent-memory', status: 'pending', reason: 'ligar', created_at: '2026-09-24T14:00:00Z', created_by: 'u1' } }))
    const { user } = await signInAt(at('memoria-dos-agentes'))
    await user.click(await screen.findByRole('tab', { name: 'Ligar e limites' }))
    expect(await screen.findByText(/Desligada neste espaço/)).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'Memória dos agentes' }))
    await user.click(await screen.findByRole('option', { name: 'Ligada' }))
    await user.type(screen.getByLabelText('Motivo'), 'Piloto com o agente de cobrança')
    await user.click(screen.getByRole('button', { name: 'Propor a mudança' }))
    const [diff] = api.find('POST', '/v1/config/diffs')
    // A document saved with the old notes cap is proposed without it: every note is kept now.
    expect(diff!.body).toMatchObject({ type: 'agent-memory' })
    expect(diff!.body.document).toEqual({ enabled: true, writes: 'agent' })
    expect(await screen.findByText('Mudança enviada para aprovação.')).toBeInTheDocument()
  })
})
