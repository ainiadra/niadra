import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { DiffOut, Role } from '../api/types'
import { API, at, baseRoutes, choose, CONTROL, mockApi, problem, signInAt, SP_PROD, SRC_BILLING, SRC_VOICE, USER_ID } from './harness'
import { PID } from './fixtures'

const ERASURE = { request_id: 'er1', status: 'pending', target_kind: 'profile', target_id: PID, requested_at: '2026-09-23T10:00:00Z', completed_at: null, erased: {}, export_run_ids: [], receipt_hash: null }
const SCHEDULES = {
  space_id: SP_PROD,
  type: 'export-schedules',
  version: 1,
  document: [{ schedule_id: 'lake', destination_kind: 's3', bucket: 'empresa-lake', prefix: 'niadra/', region: 'sa-east-1', tables: ['facts', 'episodes'], interval_hours: 24, pseudonymize: true, credential_ref: 'lake-cred' }],
}

/** Every table of domain/privacy/export.py EXPORT_TABLES, in the server's order. */
const ALL_TABLES = ['events', 'conversations', 'episodes', 'facts', 'fact_mentions', 'open_items', 'business_objects', 'object_events', 'agent_actions', 'traits', 'trait_evidence', 'receipts', 'context_use', 'handles', 'profile_membership', 'profiles', 'profile_links', 'org_parents', 'assertions', 'merge_log']
const EXPORT_TYPE = (roles: Role[]) => [{ type: 'export-schedules', description: 'Continuous export to the customer bucket.', roles }]

describe('Data', () => {
  const routes = (extra = {}, roles?: Role[]) =>
    baseRoutes(
      {
      [`POST ${API}/v1/forget`]: ERASURE,
      [`GET ${API}/v1/forget`]: (r: { url: URL }) =>
        r.url.searchParams.get('status') === 'completed'
          ? { items: [{ ...ERASURE, request_id: 'er0', status: 'completed', completed_at: '2026-09-20T10:05:00Z', erased: { events: 42, facts: 7 }, receipt_hash: 'abcdef0123456789abcdef0123456789' }] }
          : { items: [ERASURE] },
      [`POST ${API}/v1/export`]: { run_id: 'run9', profile_id: PID, download_url: 'https://storage.internal/run9.zip?sig=x', download_expires_at: '2026-09-23T11:00:00Z', files: { 'facts.jsonl': 'aa11', 'episodes.jsonl': 'bb22' }, sha256: 'cc33' },
      [`GET ${API}/v1/exports/run9/download`]: new Response('zip', { status: 200, headers: { 'Content-Type': 'application/zip' } }),
      [`GET ${CONTROL}/v1/config/export-schedules`]: SCHEDULES,
      [`GET ${API}/v1/exports/runs`]: { items: [{ run_id: 'r1', schedule_id: 'lake', status: 'failed', from_seq: 100, to_seq: 250, started_at: '2026-09-22T03:00:00Z', finished_at: '2026-09-22T03:01:00Z', error: 'AccessDenied', manifest: null }] },
      [`PUT ${API}/v1/exports/destinations/lake/credential`]: { version: 3 },
      [`GET ${API}/v1/operations/dead-tasks`]: { items: [] },
      [`POST ${CONTROL}/v1/secret-refs`]: { space_id: SP_PROD, ref: 'lake-cred', kind: 'export', version: 3, rotated_by: 'u1', rotated_at: '2026-09-23T10:00:00Z' },
      ...extra,
      },
      roles,
    )

  it('sends a failed erasure back with fresh attempts, for security only', async () => {
    let dead = [{ task_id: 't1', name: 'erase' }, { task_id: 't2', name: 'erase' }, { task_id: 't3', name: 'deliver_webhook' }]
    const api = mockApi(
      routes({
        [`GET ${API}/v1/operations/dead-tasks`]: () => ({ items: dead }),
        [`POST ${API}/v1/operations/dead-tasks/replay`]: () => {
          dead = dead.filter((t) => t.name !== 'erase')
          return { replayed: 2 }
        },
      }),
    )
    const { user } = await signInAt(at('dados'))
    const stuck = await screen.findByRole('list', { name: 'Tarefas paradas' })
    const erase = within(stuck).getByText('Apagamento de um titular').closest('li')!
    await user.click(within(erase).getByRole('button', { name: 'Mandar de novo' }))
    expect(await screen.findByText('2 tarefas voltaram para a fila.')).toBeInTheDocument()
    expect(api.find('POST', '/v1/operations/dead-tasks/replay')[0]!.body).toEqual({ task: 'erase' })
    expect(within(await screen.findByRole('list', { name: 'Tarefas paradas' })).queryByText('Apagamento de um titular')).not.toBeInTheDocument()
  })

  /** Opens the continuous export tab and the new schedule form. */
  const openScheduleForm = async (user: Awaited<ReturnType<typeof signInAt>>['user']) => {
    await user.click(await screen.findByRole('tab', { name: 'Exportação contínua' }))
    await user.click(await screen.findByRole('button', { name: 'Novo agendamento' }))
    return screen.getByRole('dialog', { name: 'Novo agendamento de exportação' })
  }

  it('asks for erasure with confirmation and an idempotency key, and lists requests by status', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('dados'))
    expect(await screen.findByText(PID, { selector: 'span' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('ID do perfil'), PID)
    const submit = screen.getByRole('button', { name: 'Pedir o apagamento' })
    expect(submit).toBeDisabled()
    await user.click(screen.getByLabelText('Entendo que o apagamento não se desfaz'))
    await user.click(submit)
    expect(await screen.findByText('Pedido registrado.')).toBeInTheDocument()
    const call = api.find('POST', '/v1/forget')[0]!
    expect(call.body).toEqual({ target: 'profile', profile_id: PID })
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
    expect(api.find('GET', '/v1/forget').length).toBeGreaterThanOrEqual(2)
    await choose(user, screen.getByLabelText('Estado'), 'completed')
    expect(await screen.findByText('events: 42 · facts: 7')).toBeInTheDocument()
    expect(api.find('GET', '/v1/forget').at(-1)!.url.searchParams.get('status')).toBe('completed')
  })

  it('builds the portable package and downloads it through the session', async () => {
    const created = vi.fn(() => 'blob:console/1')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() }))
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const api = mockApi(routes())
    const { user } = await signInAt(at('dados'))
    await user.click(await screen.findByRole('tab', { name: 'Arquivo do titular' }))
    // Where the package is asked for, the person reads how long it lives (app/privacy.py PACKAGE_TTL).
    expect(
      screen.getByText(/O arquivo vale por 24 horas: depois disso, o download para de funcionar e o arquivo sai do armazenamento\. Um apagamento que toca o titular apaga na hora os arquivos dele\./),
    ).toBeInTheDocument()
    await user.type(screen.getByLabelText('ID do perfil'), PID)
    await user.click(screen.getByRole('button', { name: 'Montar o arquivo' }))
    expect(await screen.findByText('Arquivo pronto.')).toBeInTheDocument()
    expect(screen.getByText('facts.jsonl · aa11')).toBeInTheDocument()
    expect(screen.getByText(/vale por 24 horas a partir de agora/)).toBeInTheDocument()
    // `download_expires_at` is the presigned link's 15 minutes, which the Console never uses: it is not the package's life.
    expect(screen.queryByText(/23\/09\/2026 08:00/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Baixar o arquivo' }))
    await vi.waitFor(() => expect(click).toHaveBeenCalled())
    expect(api.find('GET', '/v1/exports/run9/download')[0]!.headers.get('Authorization')).toMatch(/^Bearer /)
    expect(api.find('POST', '/v1/export')[0]!.body).toEqual({ profile_id: PID })
    // The presigned storage link is never fetched: the file comes through the cell.
    expect(api.calls.some((c) => c.url.hostname === 'storage.internal')).toBe(false)
  })

  it('says a package past its day, or erased with its subject, cannot be downloaded any more', async () => {
    mockApi(routes({ [`GET ${API}/v1/exports/run9/download`]: problem(404, 'not_found', 'the export package expired; ask for a new one') }))
    const { user } = await signInAt(at('dados'))
    await user.click(await screen.findByRole('tab', { name: 'Arquivo do titular' }))
    await user.type(screen.getByLabelText('ID do perfil'), PID)
    await user.click(screen.getByRole('button', { name: 'Montar o arquivo' }))
    await user.click(await screen.findByRole('button', { name: 'Baixar o arquivo' }))
    expect(await screen.findByText('O arquivo não existe mais: passou das 24 horas ou saiu com um apagamento do titular. Monte um novo.')).toBeInTheDocument()
  })

  it('shows each package asked for among the runs, with how long it lives or when it left', async () => {
    const pkg = (run_id: string, manifest: Record<string, unknown>) => ({ run_id, schedule_id: 'on_demand', status: 'completed', from_seq: 0, to_seq: 0, started_at: '2026-01-01T15:00:00Z', finished_at: '2026-01-01T15:00:00Z', error: null, manifest: { profile_id: PID, sha256: 'cc33', files: {}, ...manifest } })
    mockApi(
      routes({
        [`GET ${API}/v1/exports/runs`]: {
          items: [
            pkg('p1', { expires_at: '2099-06-01T15:00:00Z' }),
            pkg('p2', { expires_at: '2026-01-02T15:00:00Z' }),
            pkg('p3', { expires_at: '2026-01-02T15:00:00Z', deleted_at: '2026-01-01T18:30:00Z' }),
          ],
        },
      }),
    )
    const { user } = await signInAt(at('dados'))
    await user.click(await screen.findByRole('tab', { name: 'Exportação contínua' }))
    const table = await screen.findByRole('table', { name: 'Execuções da exportação contínua' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => within(r).getAllByRole('cell')[1]!.textContent)).toEqual(['Arquivo do titular', 'Arquivo do titular', 'Arquivo do titular'])
    expect(within(rows[0]!).getAllByRole('cell')[2]).toHaveTextContent('disponível até 01/06/2099 12:00')
    expect(within(rows[1]!).getAllByRole('cell')[2]).toHaveTextContent('expirou em 02/01/2026 12:00')
    expect(within(rows[2]!).getAllByRole('cell')[2]).toHaveTextContent('apagado em 01/01/2026 15:30')
    expect(table).not.toHaveTextContent('on_demand')
  })

  it('lists schedules and runs, and writes a destination credential without showing it back', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('dados'))
    await user.click(await screen.findByRole('tab', { name: 'Exportação contínua' }))
    expect(await screen.findByText('s3://empresa-lake/niadra/')).toBeInTheDocument()
    expect(await screen.findByText('AccessDenied')).toBeInTheDocument()
    // A pseudonymized export drops each handle's value and keeps its keyed hash (domain/privacy/export.py).
    expect(screen.getByText('Identificadores sem o valor, só com o hash com chave.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Gravar a credencial de lake' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('ID da chave de acesso'), 'AKIAEXEMPLO')
    await user.type(within(dialog).getByLabelText('Chave secreta de acesso'), 'segredo-de-teste')
    await user.click(within(dialog).getByRole('button', { name: 'Gravar credencial' }))
    expect(await within(dialog).findByText('Credencial gravada no cofre da região, versão 3.')).toBeInTheDocument()
    expect(dialog).not.toHaveTextContent('segredo-de-teste')
    // The S3 destination reads the credential as JSON with the two halves of the key (destinations.py).
    expect(api.find('PUT', '/v1/exports/destinations/lake/credential')[0]!.body).toEqual({ value: JSON.stringify({ access_key_id: 'AKIAEXEMPLO', secret_access_key: 'segredo-de-teste' }) })
    expect(api.find('POST', '/v1/secret-refs')[0]!.body).toEqual({ space_id: SP_PROD, ref: 'lake-cred', kind: 'export', version: 3 })
  })

  it('writes an Azure destination as a container SAS, never as a key', async () => {
    const azure = { ...SCHEDULES, document: [{ ...SCHEDULES.document[0], destination_kind: 'azure', bucket: 'lake' }] }
    const api = mockApi(routes({ [`GET ${CONTROL}/v1/config/export-schedules`]: azure }))
    const { user } = await signInAt(at('dados'))
    await user.click(await screen.findByRole('tab', { name: 'Exportação contínua' }))
    await user.click(await screen.findByRole('button', { name: 'Gravar a credencial de lake' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('só crie e grave (permissões c e w)')
    expect(within(dialog).queryByLabelText('ID da chave de acesso')).not.toBeInTheDocument()
    const sas = 'https://conta.blob.core.windows.net/lake?sp=cw&sig=teste'
    await user.type(within(dialog).getByLabelText('URL da SAS do contêiner'), sas)
    await user.click(within(dialog).getByRole('button', { name: 'Gravar credencial' }))
    expect(await within(dialog).findByText('Credencial gravada no cofre da região, versão 3.')).toBeInTheDocument()
    expect(api.find('PUT', '/v1/exports/destinations/lake/credential')[0]!.body).toEqual({ value: JSON.stringify({ sas_url: sas }) })
  })

  it('adds a schedule from a form: the entry goes after the current document as a diff with a reason, which waits for approval', async () => {
    const api = mockApi(
      routes({
        [`GET ${CONTROL}/v1/config/types`]: EXPORT_TYPE(['security']),
        [`POST ${CONTROL}/v1/config/diffs`]: { ...DIFF, diff_id: 'd9', type: 'export-schedules', reason: 'cópia de hora em hora para o lago' },
      }),
    )
    const { user } = await signInAt(at('dados'))
    const dialog = await openScheduleForm(user)
    // The region is the space's: the control plane refuses a destination elsewhere.
    expect(within(dialog).getByText('sa-east-1')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Nome do agendamento')).toHaveFocus()
    await user.type(within(dialog).getByLabelText('Nome do agendamento'), 'lake-hora')
    await user.clear(within(dialog).getByLabelText('A cada quantas horas'))
    await user.type(within(dialog).getByLabelText('A cada quantas horas'), '1')
    await user.type(within(dialog).getByLabelText('Bucket'), 'empresa-lake')
    await user.type(within(dialog).getByLabelText('Prefixo (opcional)'), 'niadra/hora/')
    expect(within(dialog).getAllByRole('checkbox', { checked: true })).toHaveLength(ALL_TABLES.length)
    await user.click(within(dialog).getByRole('checkbox', { name: /^Comprovantes/ }))
    await user.click(within(dialog).getByRole('checkbox', { name: /^Aproveitamento do contexto/ }))
    const pseudonymize = within(dialog).getByRole('checkbox', { name: /^Tirar o valor dos identificadores/ })
    expect(pseudonymize).not.toBeChecked()
    expect(pseudonymize).toHaveAccessibleDescription(/só com o hash com chave, e as linhas continuam se ligando entre arquivos e execuções\. As outras tabelas saem como estão\.$/)
    await user.click(pseudonymize)
    await user.type(within(dialog).getByLabelText('Motivo'), 'cópia de hora em hora para o lago')
    await user.click(within(dialog).getByRole('button', { name: 'Enviar para aprovação' }))

    expect(await within(dialog).findByText('Agendamento lake-hora enviado para aprovação.')).toHaveFocus()
    expect(within(dialog).getByText(/aprovada em Configuração, na aba Diffs. Pela regra dos quatro olhos, outra pessoa aprova/)).toBeInTheDocument()
    expect(within(dialog).getByRole('link', { name: 'Abrir os diffs em Configuração' })).toHaveAttribute('href', `/console/e/${SP_PROD}/configuracao?aba=diffs`)
    // The document is read again right before the diff, which carries it whole plus the new entry.
    expect(api.find('GET', '/v1/config/export-schedules').length).toBeGreaterThanOrEqual(2)
    expect(api.find('POST', '/v1/config/diffs')).toHaveLength(1)
    expect(api.find('POST', '/v1/config/diffs')[0]!.body).toEqual({
      space_id: SP_PROD,
      type: 'export-schedules',
      document: [
        SCHEDULES.document[0],
        {
          schedule_id: 'lake-hora',
          destination_kind: 's3',
          bucket: 'empresa-lake',
          prefix: 'niadra/hora/',
          region: 'sa-east-1',
          endpoint_url: null,
          tables: ALL_TABLES.filter((t) => t !== 'receipts' && t !== 'context_use'),
          interval_hours: 1,
          pseudonymize: true,
          credential_ref: `spaces/${SP_PROD}/export/lake-hora`,
        },
      ],
      reason: 'cópia de hora em hora para o lago',
    })
  })

  it('checks the schedule fields before sending and moves the focus to the first one to fix', async () => {
    const api = mockApi(routes({ [`GET ${CONTROL}/v1/config/types`]: EXPORT_TYPE(['security']) }))
    const { user } = await signInAt(at('dados'))
    const dialog = await openScheduleForm(user)
    await user.type(within(dialog).getByLabelText('Nome do agendamento'), 'Lake Novo')
    await user.type(within(dialog).getByLabelText('Bucket'), 'empresa-lake')
    await user.type(within(dialog).getByLabelText('Prefixo (opcional)'), 'niadra/')
    await user.click(within(dialog).getByRole('button', { name: 'Desmarcar todas' }))
    await user.click(within(dialog).getByRole('button', { name: 'Enviar para aprovação' }))

    const name = within(dialog).getByLabelText('Nome do agendamento')
    expect(name).toHaveFocus()
    expect(name).toHaveAttribute('aria-invalid', 'true')
    expect(name).toHaveAccessibleDescription(/^Use só letras minúsculas/)
    // One schedule per destination: the same bucket and prefix as `lake` is refused.
    expect(within(dialog).getByLabelText('Prefixo (opcional)')).toHaveAccessibleDescription('O agendamento lake já grava neste bucket e prefixo. Escolha outro prefixo.')
    expect(within(dialog).getByRole('group', { name: 'Tabelas' })).toHaveAccessibleDescription('Escolha pelo menos uma tabela.')
    expect(within(dialog).getByLabelText('Motivo')).toHaveAccessibleDescription('Escreva o motivo, com pelo menos 3 caracteres.')
    // After the first try, a field says what is wrong as the person types.
    await user.type(within(dialog).getByLabelText('Endpoint (opcional)'), 'http://armazenamento.interno')
    expect(within(dialog).getByLabelText('Endpoint (opcional)')).toHaveAccessibleDescription('Use um endereço https completo, como https://s3.exemplo.com.')
    expect(api.find('POST', '/v1/config/diffs')).toHaveLength(0)
  })

  it('reads the document again before sending, and stops on a name another person took meanwhile', async () => {
    let reads = 0
    const api = mockApi(
      routes({
        [`GET ${CONTROL}/v1/config/types`]: EXPORT_TYPE(['security']),
        [`GET ${CONTROL}/v1/config/export-schedules`]: () =>
          ++reads === 1 ? SCHEDULES : { ...SCHEDULES, version: 2, document: [...SCHEDULES.document, { ...SCHEDULES.document[0], schedule_id: 'lake-hora', prefix: 'hora/' }] },
      }),
    )
    const { user } = await signInAt(at('dados'))
    const dialog = await openScheduleForm(user)
    await user.type(within(dialog).getByLabelText('Nome do agendamento'), 'lake-hora')
    await user.type(within(dialog).getByLabelText('Bucket'), 'outro-lake')
    await user.type(within(dialog).getByLabelText('Motivo'), 'cópia de hora em hora')
    await user.click(within(dialog).getByRole('button', { name: 'Enviar para aprovação' }))
    expect(await within(dialog).findByText('Já existe um agendamento com esse nome.')).toBeInTheDocument()
    await vi.waitFor(() => expect(within(dialog).getByLabelText('Nome do agendamento')).toHaveFocus())
    expect(reads).toBeGreaterThanOrEqual(2)
    expect(api.find('POST', '/v1/config/diffs')).toHaveLength(0)
  })

  it('offers the schedule form only to the roles the control plane lists for the document', async () => {
    mockApi(routes({ [`GET ${CONTROL}/v1/config/types`]: EXPORT_TYPE(['security']), [`GET ${CONTROL}/v1/config/export-schedules`]: { ...SCHEDULES, document: [] } }, ['integration']))
    const { user } = await signInAt(at('dados'))
    await user.click(await screen.findByRole('tab', { name: 'Exportação contínua' }))
    expect(await screen.findByText(/Criar um agendamento exige o papel Segurança e privacidade\./)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Novo agendamento' })).not.toBeInTheDocument()
    // What the export needs, and an example marked as such.
    expect(screen.getByText(/precisa de um bucket que já exista, de uma credencial com permissão de escrita nele/)).toBeInTheDocument()
    expect(screen.getByText('exemplo')).toBeInTheDocument()
    expect(screen.getByText(/a cada 24 horas · eventos, conversas, fatos e desfechos, em Parquet/)).toBeInTheDocument()
  })

  it('offers the schedule form to a role the control plane adds to the document', async () => {
    mockApi(routes({ [`GET ${CONTROL}/v1/config/types`]: EXPORT_TYPE(['security', 'integration']) }, ['integration']))
    const { user } = await signInAt(at('dados'))
    await user.click(await screen.findByRole('tab', { name: 'Exportação contínua' }))
    expect(await screen.findByRole('button', { name: 'Novo agendamento' })).toBeInTheDocument()
  })
})

const DIFF: DiffOut = {
  diff_id: 'd1',
  space_id: SP_PROD,
  type: 'policies',
  status: 'pending',
  reason: 'restringir saúde para cobrança',
  base_version: 4,
  author_id: 'u2',
  author_label: 'bruno@empresa.com',
  decided_by: null,
  decided_by_label: null,
  decided_at: null,
  applied_version: null,
  rollback_of: null,
  created_at: '2026-09-22T12:00:00Z',
  document: { rules: [] },
}

describe('Configuration', () => {
  const routes = (extra = {}) =>
    baseRoutes({
      [`GET ${CONTROL}/v1/config/types`]: [
        { type: 'policies', description: 'Policy: what each audience may read', roles: ['security'] },
        { type: 'mappings', description: 'Webhook and batch mappings', roles: ['integration'] },
      ],
      [`GET ${API}/v1/assist`]: { items: [{ run_id: 'ar0', task: 'source_policy', status: 'failed', created_at: '2026-09-20T12:00:00Z', config_type: null, document: null, diff_hash: null, checks: {}, problems: ['amostra pequena'], diff_id: null }] },
      [`GET ${CONTROL}/v1/config/policies`]: { space_id: SP_PROD, type: 'policies', version: 4, document: { rules: [{ category: 'health', deny: ['billing'] }] } },
      [`POST ${CONTROL}/v1/config/diffs`]: DIFF,
      [`GET ${CONTROL}/v1/config/diffs`]: [DIFF, { ...DIFF, diff_id: 'd0', status: 'applied', applied_version: 4, reason: 'política inicial' }],
      [`POST ${CONTROL}/v1/config/diffs/d1/approve`]: { ...DIFF, status: 'applied', applied_version: 5 },
      [`POST ${CONTROL}/v1/config/diffs/d0/rollback`]: { ...DIFF, diff_id: 'd2', rollback_of: 'd0' },
      [`GET ${CONTROL}/v1/config/history`]: [{ version: 4, type: 'policies', diff_id: 'd0', created_at: '2026-09-20T12:00:00Z', document: {} }],
      [`GET ${CONTROL}/v1/secret-refs`]: [
        { space_id: SP_PROD, ref: 'wh-1', kind: 'webhook', version: 2, rotated_by: 'u1', rotated_at: '2026-09-01T12:00:00Z' },
        { space_id: SP_PROD, ref: `spaces/${SP_PROD}/webhook/siem-token`, kind: 'collector_token', version: 1, rotated_by: 'u1', rotated_at: '2026-09-02T12:00:00Z' },
      ],
      [`PUT ${API}/v1/secrets/webhook/wh-1`]: { version: 3 },
      [`POST ${CONTROL}/v1/secret-refs`]: { space_id: SP_PROD, ref: 'wh-1', kind: 'webhook', version: 3, rotated_by: 'u1', rotated_at: '2026-09-23T12:00:00Z' },
      [`POST ${API}/v1/assist`]: { run_id: 'ar1', task: 'webhook_mapping', status: 'queued', created_at: '2026-09-23T12:00:00Z', config_type: null, document: null, diff_hash: null, checks: {}, problems: [], diff_id: null },
      [`GET ${API}/v1/assist/ar1`]: { run_id: 'ar1', task: 'webhook_mapping', status: 'delivered', created_at: '2026-09-23T12:00:00Z', config_type: 'mappings', document: [{ source_id: SRC_VOICE }], diff_hash: 'h', checks: { matched: 20 }, problems: [], diff_id: 'd7' },
      ...extra,
    })

  it('shows a document and proposes a diff with a reason', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('configuracao'))
    expect(await screen.findByText(/"category": "health"/)).toBeInTheDocument()
    expect(await screen.findByText(/além da Administração: Segurança e privacidade/)).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'Documento' }))
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Política', 'Mapeamentos de sistemas'])
    await user.keyboard('{Escape}')
    expect(await screen.findByText('Declarado')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Ajustar (opcional)' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('Motivo'), 'restringir saúde para cobrança')
    await user.click(within(dialog).getByRole('button', { name: 'Enviar para aprovação' }))
    expect(await screen.findByRole('tab', { name: 'Diffs', selected: true })).toBeInTheDocument()
    expect(api.find('POST', '/v1/config/diffs')[0]!.body).toEqual({
      space_id: SP_PROD,
      type: 'policies',
      document: { rules: [{ category: 'health', deny: ['billing'] }] },
      reason: 'restringir saúde para cobrança',
    })
  })

  it('says where a document in force came from, and never asks for a change', async () => {
    mockApi(
      routes({
        [`GET ${CONTROL}/v1/config/diffs`]: [
          {
            ...DIFF,
            diff_id: 'd8',
            type: 'policies',
            author_id: null,
            author_label: 'niadra:observed',
            status: 'applied',
            applied_version: null,
            document: { rules: [{ category: 'inferida_do_trafego', deny: [] }] },
          },
        ],
      }),
    )
    await signInAt(at('configuracao'))
    expect(await screen.findByText('Inferido pela Niadra')).toBeInTheDocument()
    // The document shown is what Niadra inferred, not the declared default.
    expect(screen.getByLabelText(/O que a Niadra inferiu/)).toHaveTextContent('inferida_do_trafego')
    expect(screen.getByText(/Um ajuste seu vale por cima do que foi inferido/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ajustar (opcional)' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Propor mudança' })).not.toBeInTheDocument()
  })

  it('reads a document no one changed as the Niadra default', async () => {
    mockApi(routes({ [`GET ${CONTROL}/v1/config/diffs`]: [] }))
    await signInAt(at('configuracao'))
    expect(await screen.findByText('Padrão da Niadra')).toBeInTheDocument()
    expect(screen.getByText('Funciona como está. Ajustar é opcional.')).toBeInTheDocument()
  })

  it('names the author and the decider of a diff by e-mail, and every document kind in Portuguese', async () => {
    mockApi(
      routes({
        [`GET ${CONTROL}/v1/config/diffs`]: [
          { ...DIFF, diff_id: 'd3', type: 'measurement', author_id: USER_ID, author_label: 'ana.costa@empresa.com', status: 'applied', applied_version: 13, decided_by: 'u2', decided_by_label: 'carlos.lima@empresa.com', decided_at: '2026-09-30T23:50:00Z' },
          { ...DIFF, diff_id: 'd4', type: 'object-types', author_id: 'u2', author_label: 'carlos.lima@empresa.com', status: 'rejected', decided_by: USER_ID, decided_by_label: 'ana.costa@empresa.com', decided_at: '2026-09-30T23:52:00Z' },
          { ...DIFF, diff_id: 'd5', type: 'features', author_id: null, author_label: 'assistant@cell-sa-1' },
          { ...DIFF, diff_id: 'd6', type: 'coordination', author_id: null, author_label: 'control_plane' },
        ],
      }),
    )
    await signInAt(at('configuracao?aba=diffs'))
    await screen.findByText('Medição de desfechos')
    const diffs = within(screen.getByRole('main')).getByRole('list')
    const items = within(diffs).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('Medição de desfechos')
    expect(items[0]).toHaveTextContent('ana.costa@empresa.com (você) · 22/09/2026 09:00 · sobre a versão 4 · virou a versão 13 · aprovado por carlos.lima@empresa.com em 30/09/2026 20:50')
    expect(items[1]).toHaveTextContent('Tipos de objeto')
    expect(items[1]).toHaveTextContent('carlos.lima@empresa.com · ')
    expect(items[1]).toHaveTextContent('recusado por ana.costa@empresa.com (você) em')
    expect(items[2]).toHaveTextContent('Recursos ligados')
    expect(items[2]).toHaveTextContent('Assistente de configuração')
    expect(items[3]).toHaveTextContent('Coordenação dos agentes')
    expect(items[3]).toHaveTextContent('Plano de controle')
    expect(diffs).not.toHaveTextContent('u2')
  })

  it('opens the documents on the one the address names, as the screens that need a document do', async () => {
    const api = mockApi(routes({ [`GET ${CONTROL}/v1/config/purposes`]: { space_id: SP_PROD, type: 'purposes', version: 1, document: { purposes: [] } } }))
    await signInAt(at('configuracao?documento=purposes'))
    expect(await screen.findByRole('heading', { level: 2, name: 'Finalidades e bases legais' })).toBeInTheDocument()
    expect(api.find('GET', '/v1/config/purposes')).toHaveLength(1)
    expect(api.find('GET', '/v1/config/policies')).toHaveLength(0)
  })

  it('opens on the diffs when a link asks for them, as the new schedule form does', async () => {
    mockApi(routes())
    await signInAt(at('configuracao?aba=diffs'))
    expect(await screen.findByRole('tab', { name: 'Diffs', selected: true })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Aprovar o diff d1' })).toBeInTheDocument()
  })

  it('approves a pending diff and rolls back an applied one with a reason', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('configuracao'))
    await user.click(await screen.findByRole('tab', { name: 'Diffs' }))
    await user.click(await screen.findByRole('button', { name: 'Aprovar o diff d1' }))
    expect(api.find('POST', '/v1/config/diffs/d1/approve')).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Desfazer o diff d0' }))
    const dialog = screen.getByRole('dialog', { name: 'Desfazer a mudança' })
    // Undoing is a change too, approved by a second person when there is one.
    expect(dialog).toHaveTextContent('passa pela mesma aprovação')
    await user.type(within(dialog).getByLabelText('Motivo'), 'regra bloqueou o agente de voz')
    await user.click(within(dialog).getByRole('button', { name: 'Desfazer a mudança' }))
    expect(api.find('POST', '/v1/config/diffs/d0/rollback')[0]!.body).toEqual({ reason: 'regra bloqueou o agente de voz' })
  })

  it('shows history, and writes a secret to the vault before registering its version', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('configuracao'))
    await user.click(await screen.findByRole('tab', { name: 'Histórico' }))
    expect(await screen.findByText('Política, versão 4')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Segredos' }))
    expect(await screen.findByText('wh-1')).toBeInTheDocument()
    // A SIEM stream's collector token is listed as what it is, not as a signature.
    expect(screen.getByRole('row', { name: /siem-token/ })).toHaveTextContent('Token do coletor do SIEM')
    await user.type(screen.getByLabelText('Referência'), 'wh-1')
    await user.type(screen.getByLabelText('Valor'), 'whsec_novo')
    await user.click(screen.getByRole('button', { name: 'Gravar o segredo' }))
    expect(await screen.findByText('Gravado no cofre, versão 3.')).toBeInTheDocument()
    expect(api.find('PUT', '/v1/secrets/webhook/wh-1')[0]!.body).toEqual({ value: 'whsec_novo' })
    expect(api.find('POST', '/v1/secret-refs')[0]!.body).toEqual({ space_id: SP_PROD, ref: 'wh-1', kind: 'webhook', version: 3 })
    expect(screen.queryByText('whsec_novo')).not.toBeInTheDocument()
  })

  it('lists the assistant runs and asks for a new proposal', async () => {
    let n = 0
    const api = mockApi(
      routes({
        [`GET ${API}/v1/assist`]: () =>
          ++n === 1
            ? { items: [{ run_id: 'ar0', task: 'source_policy', status: 'failed', created_at: '2026-09-20T12:00:00Z', config_type: null, document: null, diff_hash: null, checks: {}, problems: ['amostra pequena'], diff_id: null }] }
            : { items: [{ run_id: 'ar1', task: 'webhook_mapping', status: 'delivered', created_at: '2026-09-23T12:00:00Z', config_type: 'mappings', document: [{}], diff_hash: 'h', checks: {}, problems: [], diff_id: 'd7' }] },
      }),
    )
    const { user } = await signInAt(at('configuracao'))
    await user.click(await screen.findByRole('tab', { name: 'Assistente' }))
    expect(await screen.findByText('amostra pequena')).toBeInTheDocument()
    // The masked sample goes to the AI provider of the contract; the screen no longer says it stays in the region.
    expect(screen.getByText(/a amostra vai mascarada, sem retenção, ao provedor de IA listado no contrato\./)).toBeInTheDocument()
    expect(screen.queryByText(/nunca sai da região/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Pedir a proposta' }))
    expect(await screen.findByText(/diff d7/)).toBeInTheDocument()
    expect(api.find('POST', '/v1/assist')[0]!.body).toEqual({ task: 'webhook_mapping', source_id: SRC_VOICE })
  })

  it('sends an example event with a mapping request, and refuses one that is not a JSON object', async () => {
    const api = mockApi(routes({ [`GET ${API}/v1/assist`]: { items: [] } }))
    const { user } = await signInAt(at('configuracao'))
    await user.click(await screen.findByRole('tab', { name: 'Assistente' }))
    const field = await screen.findByLabelText('Evento de exemplo (opcional)')
    await user.click(field)
    await user.paste('[1, 2]')
    expect(await screen.findByText('O evento de exemplo precisa ser um objeto JSON, entre chaves.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pedir a proposta' })).toBeDisabled()
    await user.clear(field)
    await user.click(field)
    await user.paste('{"type": "invoice.paid", "customer": {"email": "ana@x.com"}}')
    await user.click(screen.getByRole('button', { name: 'Pedir a proposta' }))
    expect(api.find('POST', '/v1/assist')[0]!.body).toEqual({
      task: 'webhook_mapping',
      source_id: SRC_VOICE,
      example_payload: { type: 'invoice.paid', customer: { email: 'ana@x.com' } },
    })
  })
})

describe('Systems', () => {
  const ENDPOINT_ROUTES = (extra = {}, roles?: Parameters<typeof baseRoutes>[1]) =>
    baseRoutes(
      {
        [`GET ${CONTROL}/v1/config/mappings`]: { space_id: SP_PROD, type: 'mappings', version: 2, document: [] },
        [`GET ${CONTROL}/v1/config/webhook-endpoints`]: { space_id: SP_PROD, type: 'webhook-endpoints', version: 3, document: [{ endpoint_id: 'crm-hook', url: 'https://crm.example.com/niadra', source_id: SRC_VOICE, event_types: ['trigger.fired'], secret_refs: ['wh-1'], enabled: true }] },
        [`GET ${API}/v1/sources/coverage`]: { since: '2026-09-16T00:00:00Z', items: [] },
        [`GET ${CONTROL}/v1/config/types`]: [{ type: 'webhook-endpoints', description: 'Where outgoing webhooks go.', roles: ['integration'] }],
        [`PUT ${API}/v1/secrets/webhook/agente-de-cobranca`]: { version: 1 },
        [`POST ${CONTROL}/v1/secret-refs`]: (r: { body: { ref: string; version: number } }) => ({ space_id: SP_PROD, ref: r.body.ref, kind: 'webhook', version: r.body.version, rotated_by: 'u1', rotated_at: '2026-09-24T12:00:00Z' }),
        [`POST ${CONTROL}/v1/config/diffs`]: { ...DIFF, diff_id: 'd12', type: 'webhook-endpoints' },
        ...extra,
      },
      roles,
    )

  it('adds an endpoint: the secret goes to the vault, its version to the control plane, the endpoint to approval', async () => {
    const api = mockApi(ENDPOINT_ROUTES())
    const { user } = await signInAt(at('sistemas'))
    await user.click(await screen.findByRole('button', { name: 'Novo endpoint para Agente de cobrança' }))
    const dialog = screen.getByRole('dialog', { name: 'Novo endpoint para Agente de cobrança' })
    // The id comes from the source name, free among the endpoints.
    expect(within(dialog).getByLabelText('ID do endpoint')).toHaveValue('agente-de-cobranca')
    await user.type(within(dialog).getByLabelText('Endereço (HTTPS)'), 'https://erp.empresa.com.br/webhooks/niadra')
    await user.click(within(dialog).getByRole('checkbox', { name: /Um agente registrou o que fez num sistema/ }))
    await user.type(within(dialog).getByLabelText('Motivo'), 'o ERP recebe os avisos da cobrança')
    await user.click(within(dialog).getByRole('button', { name: 'Gerar a assinatura e enviar para aprovação' }))

    expect(await within(dialog).findByText('Endpoint agente-de-cobranca enviado para aprovação.')).toBeInTheDocument()
    const written = api.find('PUT', '/v1/secrets/webhook/agente-de-cobranca')[0]!.body.value as string
    expect(written).toMatch(/^whsec_[A-Za-z0-9+/]{43}=$/)
    // Shown once, exactly as it went to the vault.
    expect(within(dialog).getByText(written)).toBeInTheDocument()
    const ref = `spaces/${SP_PROD}/webhook/agente-de-cobranca`
    expect(api.find('POST', '/v1/secret-refs')[0]!.body).toEqual({ space_id: SP_PROD, ref, kind: 'webhook', version: 1 })
    const diff = api.find('POST', '/v1/config/diffs')[0]!.body
    expect(diff.type).toBe('webhook-endpoints')
    expect(diff.reason).toBe('o ERP recebe os avisos da cobrança')
    expect(diff.document).toEqual([
      { endpoint_id: 'crm-hook', url: 'https://crm.example.com/niadra', source_id: SRC_VOICE, event_types: ['trigger.fired'], secret_refs: ['wh-1'], enabled: true },
      { endpoint_id: 'agente-de-cobranca', url: 'https://erp.empresa.com.br/webhooks/niadra', source_id: SRC_BILLING, event_types: ['trigger.fired', 'action.recorded'], secret_refs: [ref], enabled: true, format: 'standard' },
    ])
  })

  it('checks the id, the HTTPS address and the event types before writing anything', async () => {
    const api = mockApi(ENDPOINT_ROUTES())
    const { user } = await signInAt(at('sistemas'))
    await user.click(await screen.findByRole('button', { name: 'Novo endpoint para Agente de cobrança' }))
    const dialog = screen.getByRole('dialog')
    const id = within(dialog).getByLabelText('ID do endpoint')
    await user.clear(id)
    await user.type(id, 'crm-hook')
    expect(id).toHaveAccessibleDescription('Outro endpoint já usa este ID.')
    await user.clear(id)
    await user.type(id, 'erp')
    const address = within(dialog).getByLabelText('Endereço (HTTPS)')
    await user.type(address, 'http://erp.empresa.com.br/hook')
    expect(address).toHaveAccessibleDescription('O endpoint precisa de HTTPS.')
    await user.click(within(dialog).getByRole('checkbox', { name: /Uma regra de aviso disparou/ }))
    await user.type(within(dialog).getByLabelText('Motivo'), 'teste de validação')
    await user.click(within(dialog).getByRole('button', { name: 'Gerar a assinatura e enviar para aprovação' }))
    expect(within(dialog).getByText('Escolha ao menos um tipo de evento.')).toBeInTheDocument()
    expect(api.find('PUT', '/v1/secrets/webhook/erp')).toHaveLength(0)
    expect(api.find('POST', '/v1/config/diffs')).toHaveLength(0)
  })

  it('leaves new endpoints to people who can write the secret and propose the endpoint', async () => {
    mockApi(ENDPOINT_ROUTES({}, ['integration']))
    await signInAt(at('sistemas'))
    expect(await screen.findByText(/Criar um endpoint, ou um fluxo de comprovantes para o SIEM, grava a assinatura dele no cofre, o que pede também o papel Segurança e privacidade\./)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Novo endpoint/ })).not.toBeInTheDocument()
  })

  it('shows the inbound webhook from apiURL, the mapping, the endpoints and the coverage', async () => {
    const api = mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/config/mappings`]: { space_id: SP_PROD, type: 'mappings', version: 2, document: [{ source_id: SRC_BILLING, version: 'erp-v4', auth: { kind: 'hmac' }, rules: [{}, {}, {}] }] },
        [`GET ${CONTROL}/v1/config/webhook-endpoints`]: { space_id: SP_PROD, type: 'webhook-endpoints', version: 3, document: [{ endpoint_id: 'crm-hook', url: 'https://crm.example.com/niadra', source_id: SRC_VOICE, event_types: ['trigger.fired'], secret_refs: ['wh-1', 'wh-2'], enabled: true }] },
        [`GET ${API}/v1/sources/coverage`]: {
          since: '2026-09-16T00:00:00Z',
          items: [
            { source_id: SRC_VOICE, name: 'Agente de voz', channel: 'voice', sent: 1000, received: 990, rejected: 4, unmapped: 0, last_event_at: '2026-09-22T17:07:00Z', days: [] },
            { source_id: SRC_BILLING, name: 'Agente de cobrança', channel: 'erp', sent: 0, received: 0, rejected: 0, unmapped: 0, last_event_at: null, days: [] },
          ],
        },
      }),
    )
    await signInAt(at('sistemas'))
    expect(await screen.findByText(`${API}/v1/ingest/webhook/${SRC_VOICE}`)).toBeInTheDocument()
    expect(screen.getByText(/Mapeamento versão/)).toHaveTextContent('Mapeamento versão erp-v4 · 3 regras · autenticação configurada')
    expect(screen.getByText('https://crm.example.com/niadra')).toBeInTheDocument()
    expect(screen.getByText(/assinatura em rotação/)).toBeInTheDocument()
    expect(await screen.findByText(/eventos recebidos/)).toHaveTextContent('990 eventos recebidos de 1.000 enviados')
    expect(screen.getByText('10 não chegaram · 4 recusados')).toBeInTheDocument()
    expect(screen.getByText('Silenciosa')).toBeInTheDocument()
    expect(screen.getAllByText(/Sem mapeamento/).length).toBe(1)
    expect(api.find('GET', '/v1/sources/coverage')[0]!.url.searchParams.get('days')).toBe('7')
  })
})
