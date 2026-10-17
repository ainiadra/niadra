import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ReceiptLineage, ReceiptOut } from '../api/types'
import { API, at, baseRoutes, choose, mockApi, paragraph, signInAt, SRC_BILLING, SRC_VOICE, USER_ID } from './harness'
import { RECEIPT } from './fixtures'


const LINEAGE: ReceiptLineage = {
  receipt: RECEIPT,
  manifest_found: true,
  items: [
    { id: 'i1', kind: 'open_item', evidence_event_ids: ['e2'], events: [{ event_id: 'e2', kind: 'action', source_id: SRC_BILLING, channel: 'erp', occurred_at: '2026-09-22T17:06:00Z', conversation_id: null, task_id: 't9' }] },
    { id: 'f1', kind: 'fact', evidence_event_ids: ['e7'], events: [] },
  ],
  excluded: [{ item_id: 'f10', kind: 'fact', reason: 'policy', rule: 'saude_nunca_para_atendimento' }],
}

/** Governance actions as the cell records them (app/admin_audit.py): a person, an agent's key, the control plane. */
const action = (over: Partial<ReceiptOut>): ReceiptOut => ({
  ...RECEIPT,
  kind: 'admin',
  surface: 'console',
  audience: 'human',
  profile_id: null,
  pseudonym: null,
  source_id: null,
  person_id: USER_ID,
  conversation_id: null,
  purpose: null,
  item_ids: [],
  pack_hash: null,
  manifest_hash: null,
  verification: null,
  policy_version: null,
  withheld: 0,
  ...over,
})
const BY_ME = action({ receipt_id: 'rct_a1', read_at: '2026-10-01T13:02:22Z', detail: { roles: 'admin', action: 'profiles.listed', target: 'profiles:50' } })
const BY_CARLOS = action({ receipt_id: 'rct_a2', read_at: '2026-10-01T02:59:45Z', person_id: 'u2', person_label: 'carlos.lima@empresa.com', detail: { roles: 'review,analysis', action: 'review.verdicts', target: 'sample:smp_01' } })
const BY_KEY = action({ receipt_id: 'rct_a3', read_at: '2026-10-01T02:59:28Z', person_id: null, source_id: SRC_VOICE, audience: 'customer_agent', detail: { action: 'agent_memory.note_created', key_id: 'k5g0fkcg', target: 'agent_note:nt_01' } })
const BY_CONTROL = action({ receipt_id: 'rct_a4', read_at: '2026-09-30T23:58:00Z', person_id: null, detail: { action: 'key.issued', target: `key:key_01:source:${SRC_VOICE}`, actor: 'control_plane' } })
const ON_PROFILE = action({ receipt_id: 'rct_a5', read_at: '2026-10-01T02:59:32Z', profile_id: 'prf_1', pseudonym: 'p_7Q2M9', detail: { roles: 'admin', action: 'privacy.export', target: 'export_run:run_9' } })
const UNKNOWN = action({ receipt_id: 'rct_a6', read_at: '2026-10-01T02:00:00Z', detail: { roles: 'admin', action: 'something.new', target: 'thing:42' } })

describe('Console actions on receipts', () => {
  const routes = (roles?: Parameters<typeof baseRoutes>[1]) =>
    baseRoutes(
      {
        [`POST ${API}/v1/receipts/search`]: { items: [BY_ME, BY_CARLOS, BY_KEY, BY_CONTROL, ON_PROFILE, UNKNOWN], next_cursor: null },
        [`GET ${API}/v1/lineage/receipt/rct_a2`]: { receipt: BY_CARLOS, manifest_found: false, items: [], excluded: [] },
      },
      roles,
    )

  it('says who did what, in words: the person by e-mail, the agent by name, the control plane by itself', async () => {
    mockApi(routes())
    await signInAt(at('recibos?kind=admin'))
    const rows = within(await screen.findByRole('table')).getAllByRole('row').slice(1)
    expect(rows[0]).toHaveTextContent('ana.costa@empresa.com (você)')
    expect(rows[0]).toHaveTextContent('Listou 50 clientes')
    expect(rows[1]).toHaveTextContent('carlos.lima@empresa.com')
    expect(rows[1]).toHaveTextContent('Gravou vereditos')
    expect(rows[1]).toHaveTextContent('amostra smp_01')
    expect(rows[2]).toHaveTextContent('Agente de voz · Fornecedor A')
    expect(rows[2]).toHaveTextContent('Guardou uma nota de trabalho')
    expect(rows[3]).toHaveTextContent('Plano de controle')
    expect(rows[3]).toHaveTextContent('Emitiu uma chave de API')
    expect(rows[3]).toHaveTextContent('chave key_01 do agente Agente de voz')
    expect(rows[4]).toHaveTextContent('p_7Q2M9')
    expect(rows[4]).toHaveTextContent('Gerou o arquivo do titular')
    // A code the Console does not know yet shows as it came, never as "não informado".
    expect(rows[5]).toHaveTextContent('something.new')
    expect(screen.getByRole('table')).not.toHaveTextContent('não informado')
    expect(screen.getByRole('columnheader', { name: 'Quem' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'O que leu ou fez' })).toBeInTheDocument()
  })

  it('opens an action: the person, the roles, the target, the customer, and nothing about a delivery', async () => {
    mockApi(routes())
    await signInAt(at('recibos/rct_a2'))
    expect(await screen.findByRole('heading', { level: 1, name: /^Ação no Console de 30\/09\/2026 23:59$/ })).toBeInTheDocument()
    expect(screen.getByText('carlos.lima@empresa.com')).toBeInTheDocument()
    expect(screen.getByText('O que fez').nextElementSibling).toHaveTextContent('Gravou vereditos')
    expect(screen.getByText('O que fez').nextElementSibling).toHaveTextContent('amostra smp_01')
    expect(screen.getByText('Revisão, Análise')).toBeInTheDocument()
    expect(screen.getByText('nenhum: a ação não foi sobre uma pessoa')).toBeInTheDocument()
    expect(screen.queryByText(/não informad/)).not.toBeInTheDocument()
    expect(screen.queryByText('Nível de verificação')).not.toBeInTheDocument()
    expect(screen.queryByText('Nenhum item entregue nesta leitura.')).not.toBeInTheDocument()
    expect(screen.getByText('Dia (UTC) e posição')).toBeInTheDocument()
  })

  it('names another person by the e-mail on the receipt for a role that may not list the people, and keeps the id of a receipt older than the names', async () => {
    const api = mockApi({ ...routes(['security']), [`POST ${API}/v1/receipts/search`]: { items: [BY_ME, BY_CARLOS, { ...BY_CARLOS, receipt_id: 'rct_a7', person_label: null }] } })
    await signInAt(at('recibos?kind=admin'))
    const rows = within(await screen.findByRole('table')).getAllByRole('row').slice(1)
    expect(rows[0]).toHaveTextContent('ana.costa@empresa.com (você)')
    expect(rows[1]).toHaveTextContent('carlos.lima@empresa.com')
    expect(rows[2]).toHaveTextContent('pessoa u2')
    // The names come with the receipts: the Console no longer asks the control plane for the tenant's people.
    expect(api.find('GET', '/v1/users')).toHaveLength(0)
  })
})

describe('Receipts', () => {
  it('defaults the chain check to yesterday in UTC, the day the chain is counted in', async () => {
    // 22:30 of 30/09 in Brasília: the UTC day is already 01/10, and yesterday in UTC is 30/09, not 29/09.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-01T01:30:00Z'))
    try {
      mockApi(baseRoutes({ [`POST ${API}/v1/receipts/search`]: { items: [] } }))
      await signInAt(at('recibos'))
      expect(await screen.findByLabelText('Dia (UTC)')).toHaveValue('2026-09-30')
      expect(screen.getByText(/O dia da cadeia é contado em UTC, como o servidor grava cada comprovante: o dia de hoje termina às 21:00 no seu horário/)).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('filters by agent, kind, conversation and period with the server field names, in the body of the search', async () => {
    const api = mockApi(baseRoutes({ [`POST ${API}/v1/receipts/search`]: { items: [RECEIPT], next_cursor: null } }))
    const { user } = await signInAt(at('recibos'))
    expect(await screen.findByText('p_7Q2M9')).toBeInTheDocument()
    await choose(user, screen.getByLabelText('Agente ou fonte'), SRC_VOICE)
    await choose(user, screen.getByLabelText('Tipo'), 'read')
    await user.type(screen.getByLabelText('ID da conversa'), 'conv_881')
    await user.type(screen.getByLabelText('De'), '2026-09-01')
    await user.click(screen.getByRole('button', { name: 'Filtrar' }))
    await screen.findByRole('button', { name: 'Limpar filtros' })
    const body = api.find('POST', '/v1/receipts/search').at(-1)!.body
    expect(body).toMatchObject({ source_id: SRC_VOICE, kind: 'read', conversation_id: 'conv_881', limit: 50 })
    expect(body.since).toBe(new Date(2026, 8, 1).toISOString())
    // The shareable filters stay in the address; the conversation never does.
    expect(new URLSearchParams(window.location.search).get('kind')).toBe('read')
    expect(window.location.href).not.toContain('conv_881')
  })

  it('clears the conversation filter with the others', async () => {
    const api = mockApi(baseRoutes({ [`POST ${API}/v1/receipts/search`]: { items: [RECEIPT] } }))
    const { user } = await signInAt(at('recibos'))
    await user.type(await screen.findByLabelText('ID da conversa'), 'conv_881')
    await user.click(screen.getByRole('button', { name: 'Filtrar' }))
    await user.click(await screen.findByRole('button', { name: 'Limpar filtros' }))
    await screen.findByText('p_7Q2M9')
    expect(screen.getByLabelText('ID da conversa')).toHaveValue('')
    expect(api.find('POST', '/v1/receipts/search').at(-1)!.body.conversation_id).toBeUndefined()
  })

  it('loads the next page with the cursor', async () => {
    const api = mockApi(
      baseRoutes({
        [`POST ${API}/v1/receipts/search`]: (r) => (r.body.cursor ? { items: [{ ...RECEIPT, receipt_id: 'rct_0191', profile_id: 'prf_OUTRO', pseudonym: null }] } : { items: [RECEIPT], next_cursor: 'c2' }),
      }),
    )
    const { user } = await signInAt(at('recibos'))
    await user.click(await screen.findByRole('button', { name: 'Carregar mais' }))
    expect(await screen.findByText('prf_OUTRO')).toBeInTheDocument()
    expect(api.find('POST', '/v1/receipts/search').at(-1)!.body.cursor).toBe('c2')
  })

  it('names the customer of a receipt by pseudonym, in the list and on the receipt, and falls back to the id', async () => {
    const lineage = { receipt: RECEIPT, manifest_found: false, items: [], excluded: [] }
    mockApi(
      baseRoutes({
        [`POST ${API}/v1/receipts/search`]: { items: [RECEIPT, { ...RECEIPT, receipt_id: 'rct_0188', profile_id: 'prf_SEM_NOME', pseudonym: null }, { ...RECEIPT, receipt_id: 'rct_0187', profile_id: null, pseudonym: null }], next_cursor: null },
        [`GET ${API}/v1/lineage/receipt/${RECEIPT.receipt_id}`]: lineage,
      }),
    )
    const { user } = await signInAt(at('recibos'))
    const rows = within(await screen.findByRole('table')).getAllByRole('row').slice(1)
    expect(rows[0]).toHaveTextContent('p_7Q2M9')
    expect(rows[0]).not.toHaveTextContent('prf_1')
    expect(within(rows[0]!).getByText('p_7Q2M9')).toHaveAttribute('title', 'ID do perfil prf_1')
    expect(rows[1]).toHaveTextContent('prf_SEM_NOME')
    expect(rows[2]).toHaveTextContent('sem cliente')
    expect(screen.getByText(/O perfil aparece pelo pseudônimo, o mesmo da lista de Clientes\./)).toBeInTheDocument()
    await user.click(within(rows[0]!).getByRole('link'))
    const profile = await screen.findByRole('link', { name: 'p_7Q2M9' })
    expect(profile).toHaveAttribute('href', expect.stringContaining('/clientes/prf_1'))
  })

  it('checks the hash chain of a day and says each break in words', async () => {
    const api = mockApi(
      baseRoutes({
        [`POST ${API}/v1/receipts/search`]: { items: [] },
        [`GET ${API}/v1/receipts/verify`]: {
          day: '2026-09-22',
          valid: false,
          receipts: 120,
          heads: [{ slice: 0, seq: 120, hash: 'x' }],
          // The reasons of domain/audit/chain.py verify and app/audit.py verify_chain.
          breaks: [
            { slice: 0, seq: 77, reason: 'prev_hash_mismatch' },
            { slice: 0, seq: 78, reason: 'sequence_gap' },
            { slice: 0, seq: 90, reason: 'content_mismatch' },
            { slice: 0, seq: 120, reason: 'head_mismatch' },
            { slice: 1, seq: 5, reason: 'something_new' },
          ],
          anchored_root: 'abc',
          root_matches: false,
        },
      }),
    )
    const { user } = await signInAt(at('recibos'))
    expect(await screen.findByText('Nenhum comprovante neste espaço ainda.')).toBeInTheDocument()
    // Where the root really is: in the database and in a copy outside it; whether the copy is locked is the answer's.
    expect(screen.getByText(/a raiz do dia fica gravada no banco e numa cópia fora dele\. A conferência refaz a cadeia e compara com as duas\./)).toBeInTheDocument()
    expect(screen.queryByText(/imutável/)).not.toBeInTheDocument()
    await user.clear(screen.getByLabelText('Dia (UTC)'))
    await user.type(screen.getByLabelText('Dia (UTC)'), '2026-09-22')
    await user.click(screen.getByRole('button', { name: 'Conferir' }))
    expect(await screen.findByText('Cadeia quebrada')).toBeInTheDocument()
    const breaks = screen.getByRole('list', { name: 'Onde a cadeia quebrou' })
    expect(within(breaks).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'fatia 0, comprovante 77: o hash do anterior guardado neste comprovante não é o do comprovante anterior.',
      'fatia 0, comprovante 78: a sequência pula um número, e falta um comprovante antes deste.',
      'fatia 0, comprovante 90: o conteúdo do comprovante não confere com o hash gravado nele.',
      'fatia 0, comprovante 120: a cabeça gravada da fatia não confere com a cadeia refeita.',
      // A reason the Console does not know yet shows as the server sent it.
      'fatia 1, comprovante 5: something_new.',
    ])
    expect(api.find('GET', '/v1/receipts/verify')[0]!.url.searchParams.get('day')).toBe('2026-09-22')
  })

  it('says whether the copy of the root outside the database matches and is write-once', async () => {
    mockApi(
      baseRoutes({
        [`POST ${API}/v1/receipts/search`]: { items: [] },
        [`GET ${API}/v1/receipts/verify`]: { day: '2026-09-22', valid: true, receipts: 3, heads: [{ slice: 0, seq: 3, hash: 'x' }], breaks: [], anchored_root: 'abc', root_matches: true, anchor_copy_matches: true, anchor_locked: true },
      }),
    )
    const { user } = await signInAt(at('recibos'))
    await user.click(await screen.findByRole('button', { name: 'Conferir' }))
    expect(await screen.findByText('Cadeia íntegra')).toBeInTheDocument()
    expect(screen.getByText('A raiz confere com a ancorada (abc).')).toBeInTheDocument()
    expect(
      screen.getByText(
        'A cópia fora do banco guarda a mesma raiz. Ela foi gravada em armazenamento de gravação única (S3 com trava de objeto em modo de conformidade), que ninguém, nem a própria célula, apaga ou troca antes do fim da retenção.',
      ),
    ).toBeInTheDocument()
  })

  it('says when the copy is missing or holds another root, and when it sits without a lock', async () => {
    mockApi(
      baseRoutes({
        [`POST ${API}/v1/receipts/search`]: { items: [] },
        // Breaks are empty and the root matches: the copy alone makes the day invalid (app/audit.py verify_chain).
        [`GET ${API}/v1/receipts/verify`]: { day: '2026-09-22', valid: false, receipts: 3, heads: [{ slice: 0, seq: 3, hash: 'x' }], breaks: [], anchored_root: 'abc', root_matches: true, anchor_copy_matches: false, anchor_locked: false },
      }),
    )
    const { user } = await signInAt(at('recibos'))
    await user.click(await screen.findByRole('button', { name: 'Conferir' }))
    expect(await screen.findByText('Cadeia quebrada')).toBeInTheDocument()
    expect(screen.getByText('A cópia fora do banco falta ou guarda outra raiz. Ela foi gravada no bucket da célula, sem trava de objeto: não é gravação única.')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Onde a cadeia quebrou' })).not.toBeInTheDocument()
  })

  it('says when the copy could not be read now, and when the day is not anchored yet', async () => {
    let answer: object = { day: '2026-09-22', valid: true, receipts: 3, heads: [{ slice: 0, seq: 3, hash: 'x' }], breaks: [], anchored_root: 'abc', root_matches: true, anchor_copy_matches: null, anchor_locked: true }
    mockApi(baseRoutes({ [`POST ${API}/v1/receipts/search`]: { items: [] }, [`GET ${API}/v1/receipts/verify`]: () => answer }))
    const { user } = await signInAt(at('recibos'))
    await user.click(await screen.findByRole('button', { name: 'Conferir' }))
    expect(await screen.findByText(/^A cópia fora do banco não pôde ser lida agora e ficou sem conferir: confira de novo em alguns minutos\. Ela foi gravada em armazenamento de gravação única/)).toBeInTheDocument()

    answer = { day: '2026-09-23', valid: true, receipts: 8, heads: [{ slice: 0, seq: 8, hash: 'y' }], breaks: [], anchored_root: null, root_matches: null, anchor_copy_matches: null, anchor_locked: null }
    await user.click(screen.getByRole('button', { name: 'Conferir' }))
    expect(await screen.findByText('O dia ainda não foi ancorado: a raiz e a cópia fora do banco saem quando todos os comprovantes do dia estiverem gravados. Até lá, só a cadeia é conferida.')).toBeInTheDocument()
    expect(screen.queryByText(/A cópia fora do banco/)).not.toBeInTheDocument()
  })

  it('lists refused reads as denied, and filters by them', async () => {
    const api = mockApi(
      baseRoutes({
        [`POST ${API}/v1/receipts/search`]: { items: [{ ...RECEIPT, receipt_id: 'rct_denied', kind: 'denied', surface: 'search', source_id: SRC_BILLING, item_ids: [], withheld: 0, verification: null }] },
      }),
    )
    const { user } = await signInAt(at('recibos'))
    const row = within(await screen.findByRole('table')).getByRole('row', { name: /Leitura negada/ })
    expect(row).toHaveTextContent('Busca no histórico')
    await choose(user, screen.getByLabelText('Tipo'), 'denied')
    await user.click(screen.getByRole('button', { name: 'Filtrar' }))
    await screen.findByRole('button', { name: 'Limpar filtros' })
    expect(api.find('POST', '/v1/receipts/search').at(-1)!.body.kind).toBe('denied')
  })

  it('opens the lineage: receipt, manifest, items, origin events and what was withheld', async () => {
    mockApi(baseRoutes({ [`GET ${API}/v1/lineage/receipt/rct_0192`]: LINEAGE }))
    await signInAt(at('recibos/rct_0192'))
    expect(await screen.findByRole('heading', { level: 1, name: 'Leitura de 22/09/2026 14:07' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Linhagem' })
    expect(within(nav).getByRole('link', { name: '2 itens entregues' })).toHaveAttribute('href', '#itens')
    expect(within(nav).getByRole('link', { name: '1 evento de origem' })).toBeInTheDocument()
    expect(screen.getByText('sha256:9f2c1b7e44aa01')).toBeInTheDocument()
    expect(screen.getByText('Contexto')).toBeInTheDocument()
    expect(screen.getByText(paragraph(/^Agente de cobrança · ERP · ação · 22\/09\/2026 14:06$/))).toBeInTheDocument()
    expect(screen.getByText('1 evento(s) de origem fora do alcance do seu papel.')).toBeInTheDocument()
    expect(screen.getByText(/Regra saude_nunca_para_atendimento/)).toBeInTheDocument()
    expect(screen.getByText('22/09/2026 · fatia 0 · comprovante 42')).toBeInTheDocument()
    expect(screen.getByText('Cada comprovante guarda o hash do anterior. A raiz de cada dia, contado em UTC, fica gravada no banco e numa cópia fora dele, que a conferência da cadeia também lê.')).toBeInTheDocument()
  })

  it('says that a denied read delivered nothing and keeps who tried, never what was asked', async () => {
    const denied = { ...RECEIPT, receipt_id: 'rct_denied', kind: 'denied', surface: 'search', item_ids: [], withheld: 0, verification: null, manifest_hash: null, pack_hash: null }
    mockApi(baseRoutes({ [`GET ${API}/v1/lineage/receipt/rct_denied`]: { receipt: denied, manifest_found: false, items: [], excluded: [] } }))
    await signInAt(at('recibos/rct_denied'))
    expect(await screen.findByRole('heading', { level: 1, name: /^Leitura negada de / })).toBeInTheDocument()
    expect(screen.getByText(/A política negou esta leitura e nada foi entregue\. O comprovante guarda quem tentou, em qual superfície e quando, nunca o que foi pedido\./)).toBeInTheDocument()
    expect(screen.queryByText(/O manifesto desta leitura não foi encontrado/)).not.toBeInTheDocument()
  })
})
