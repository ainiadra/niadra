import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DeliveryView, DiffOut, WebhookEndpointDoc } from '../api/types'
import { API, at, baseRoutes, choose, CONTROL, mockApi, problem, signInAt, SP_PROD, SRC_BILLING, SRC_VOICE, type Req } from './harness'

const ref = (id: string) => `spaces/${SP_PROD}/webhook/${id}`
const STANDARD: WebhookEndpointDoc = { endpoint_id: 'crm-hook', url: 'https://crm.example.com/niadra', source_id: SRC_VOICE, event_types: ['trigger.fired'], secret_refs: ['wh-1'], enabled: true }
/** A stream as config/models.py WebhookEndpoint takes it: receipts for a SIEM, in OCSF or for an HTTP Event Collector. */
const HEC: WebhookEndpointDoc = {
  endpoint_id: 'siem',
  url: 'https://siem.empresa.com.br/services/collector/event',
  source_id: SRC_BILLING,
  event_types: ['*'],
  secret_refs: [ref('siem')],
  enabled: true,
  format: 'hec',
  authorization_ref: ref('siem-token'),
}
const OCSF: WebhookEndpointDoc = { endpoint_id: 'lago-seguranca', url: 'https://lago.empresa.com.br/ocsf', source_id: SRC_VOICE, event_types: ['read', 'denied'], secret_refs: ['wh-7', 'wh-8'], enabled: false, format: 'ocsf' }
const DIFF: DiffOut = {
  diff_id: 'd31',
  space_id: SP_PROD,
  type: 'webhook-endpoints',
  status: 'pending',
  reason: 'r',
  base_version: 3,
  author_id: 'u1',
  author_label: null,
  decided_by: null,
  decided_by_label: null,
  decided_at: null,
  applied_version: null,
  rollback_of: null,
  created_at: '2026-09-24T12:00:00Z',
  document: [],
}

const routes = (document: WebhookEndpointDoc[], extra = {}, roles?: Parameters<typeof baseRoutes>[1]) =>
  baseRoutes(
    {
      [`GET ${CONTROL}/v1/config/mappings`]: { space_id: SP_PROD, type: 'mappings', version: 2, document: [] },
      [`GET ${CONTROL}/v1/config/webhook-endpoints`]: { space_id: SP_PROD, type: 'webhook-endpoints', version: 3, document },
      [`GET ${API}/v1/sources/coverage`]: { since: '2026-09-17T00:00:00Z', items: [] },
      [`GET ${CONTROL}/v1/config/types`]: [{ type: 'webhook-endpoints', description: 'Where outgoing webhooks go.', roles: ['integration'] }],
      [`PUT ${API}/v1/secrets/webhook/siem`]: { version: 1 },
      [`PUT ${API}/v1/secrets/webhook/siem-token`]: { version: 1 },
      [`POST ${CONTROL}/v1/secret-refs`]: (r: Req) => ({ space_id: SP_PROD, ref: r.body.ref, kind: 'webhook', version: r.body.version, rotated_by: 'u1', rotated_at: '2026-09-24T12:00:00Z' }),
      [`POST ${CONTROL}/v1/config/diffs`]: DIFF,
      ...extra,
    },
    roles,
  )

type User = Awaited<ReturnType<typeof signInAt>>['user']

/** Opens the SIEM stream form from Systems. */
async function openForm(user: User) {
  await user.click(await screen.findByRole('button', { name: 'Novo fluxo para o SIEM' }))
  return screen.getByRole('dialog', { name: 'Novo fluxo de comprovantes para o SIEM' })
}

describe('SIEM streams in Systems', () => {
  it('lists each stream in words, apart from the endpoints that receive warnings', async () => {
    mockApi(routes([STANDARD, HEC, OCSF]))
    await signInAt(at('sistemas'))
    const section = await screen.findByRole('region', { name: 'Comprovantes no SIEM' })
    const [hec, ocsf] = within(section).getAllByRole('listitem')
    expect(hec).toHaveTextContent('https://siem.empresa.com.br/services/collector/event')
    expect(hec).toHaveTextContent('Ligado')
    expect(hec).toHaveTextContent('HTTP Event Collector, com o evento OCSF 1.3 no envelope')
    expect(hec).toHaveTextContent('todos os tipos de comprovante · token do coletor no cabeçalho Authorization · assinatura Standard Webhooks · fonte Agente de cobrança')
    expect(within(hec!).getByRole('link', { name: 'Entregas e reenvio de siem' })).toHaveAttribute('href', `/console/e/${SP_PROD}/avisos?aba=deliveries&endpoint=siem`)
    expect(ocsf).toHaveTextContent('Desligado')
    expect(ocsf).toHaveTextContent('OCSF 1.3, um evento por linha')
    expect(ocsf).toHaveTextContent('Leitura, Leitura negada · sem token do coletor · assinatura em rotação · fonte Agente de voz')
    // A stream never receives warnings: the source that owns it lists no endpoint for them.
    const billing = screen.getByRole('link', { name: 'Agente de cobrança' }).closest('li')!
    expect(billing).toHaveTextContent('Nenhum endpoint desta fonte.')
    expect(billing).not.toHaveTextContent('siem.empresa.com.br')
  })

  it('sends a test delivery to an endpoint that is on and points to its result', async () => {
    const sent: DeliveryView = {
      delivery_id: 'dl-test',
      endpoint_id: 'siem',
      event_type: 'webhook.test',
      status: 'pending',
      attempts: 0,
      created_at: '2026-09-24T12:00:00Z',
      next_attempt_at: null,
      delivered_at: null,
      last_status_code: null,
      last_error: null,
    }
    const api = mockApi(routes([STANDARD, HEC, OCSF], { [`POST ${API}/v1/webhooks/endpoints/siem/test`]: sent }))
    const { user } = await signInAt(at('sistemas'))
    const section = await screen.findByRole('region', { name: 'Comprovantes no SIEM' })
    const [hec, ocsf] = within(section).getAllByRole('listitem')
    // A stream that is off receives nothing, not even a test.
    expect(within(ocsf!).queryByRole('button', { name: /Enviar um teste/ })).toBeNull()
    await user.click(within(hec!).getByRole('button', { name: 'Enviar um teste para siem' }))
    expect(await within(hec!).findByRole('link', { name: 'Ver o resultado nas entregas' })).toHaveAttribute(
      'href',
      `/console/e/${SP_PROD}/avisos?aba=deliveries&endpoint=siem`,
    )
    expect(api.find('POST', '/v1/webhooks/endpoints/siem/test')).toHaveLength(1)
    // The warning endpoint of a source has the same button.
    expect(screen.getByRole('button', { name: 'Enviar um teste para crm-hook' })).toBeInTheDocument()
  })

  it('says why a test did not go out', async () => {
    mockApi(
      routes([HEC], {
        [`POST ${API}/v1/webhooks/endpoints/siem/test`]: problem(409, 'conflict', 'endpoint disabled'),
      }),
    )
    const { user } = await signInAt(at('sistemas'))
    await user.click(await screen.findByRole('button', { name: 'Enviar um teste para siem' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('O teste não saiu:')
  })

  it('adds a stream: the signing secret and the collector token go to the vault, their versions to the control plane, the stream to approval', async () => {
    let reads = 0
    const api = mockApi(
      routes([STANDARD], {
        // The document is read again right before the diff: a change approved meanwhile is kept.
        [`GET ${CONTROL}/v1/config/webhook-endpoints`]: () => ({ space_id: SP_PROD, type: 'webhook-endpoints', version: 3 + reads, document: ++reads === 1 ? [STANDARD] : [STANDARD, { ...STANDARD, endpoint_id: 'erp-hook' }] }),
      }),
    )
    const { user } = await signInAt(at('sistemas'))
    const dialog = await openForm(user)
    expect(within(dialog).getByLabelText('ID do endpoint')).toHaveValue('siem')
    await user.click(within(dialog).getByRole('radio', { name: /HTTP Event Collector/ }))
    await user.type(within(dialog).getByLabelText('Endereço (HTTPS)'), 'https://siem.empresa.com.br/services/collector/event')
    expect(within(dialog).getByRole('radio', { name: /Todos os tipos de comprovante/ })).toBeChecked()
    await choose(user, within(dialog).getByLabelText('Fonte dona do endpoint'), SRC_BILLING)
    // An HTTP Event Collector reads `Authorization: Splunk <token>`.
    expect(within(dialog).getByLabelText('Como o token vai no cabeçalho')).toHaveTextContent('Splunk e o token')
    await user.type(within(dialog).getByLabelText('Token do coletor (opcional)'), 'tok-123')
    await user.type(within(dialog).getByLabelText('Motivo'), 'o SOC recebe os comprovantes em segundos')
    await user.click(within(dialog).getByRole('button', { name: 'Gerar a assinatura e enviar para aprovação' }))

    expect(await within(dialog).findByText('Fluxo siem enviado para aprovação.')).toBeInTheDocument()
    const secret = api.find('PUT', '/v1/secrets/webhook/siem')[0]!.body.value as string
    expect(secret).toMatch(/^whsec_[A-Za-z0-9+/]{43}=$/)
    expect(within(dialog).getByText(secret)).toBeInTheDocument()
    expect(api.find('PUT', '/v1/secrets/webhook/siem-token')[0]!.body).toEqual({ value: 'Splunk tok-123' })
    expect(api.find('POST', '/v1/secret-refs').map((c) => c.body)).toEqual([
      { space_id: SP_PROD, ref: ref('siem'), kind: 'webhook', version: 1 },
      // The token is not a signature: the control plane lists it apart (Configuração, Segredos).
      { space_id: SP_PROD, ref: ref('siem-token'), kind: 'collector_token', version: 1 },
    ])
    const diff = api.find('POST', '/v1/config/diffs')[0]!.body
    expect(diff).toMatchObject({ space_id: SP_PROD, type: 'webhook-endpoints', reason: 'o SOC recebe os comprovantes em segundos' })
    expect(diff.document).toEqual([
      STANDARD,
      { ...STANDARD, endpoint_id: 'erp-hook' },
      { ...HEC, url: 'https://siem.empresa.com.br/services/collector/event' },
    ])
    // The token is never shown back, and the dialog says where the first deliveries will show.
    expect(dialog).not.toHaveTextContent('tok-123')
    expect(within(dialog).getByText(/O token do coletor foi gravado no cofre da região/)).toBeInTheDocument()
    expect(within(dialog).getByRole('link', { name: 'Ver as entregas do fluxo' })).toHaveAttribute('href', `/console/e/${SP_PROD}/avisos?aba=deliveries&endpoint=siem`)
  })

  it('streams only the chosen kinds of receipt in OCSF, without a collector token', async () => {
    const api = mockApi(routes([STANDARD]))
    const { user } = await signInAt(at('sistemas'))
    const dialog = await openForm(user)
    expect(within(dialog).getByRole('radio', { name: /OCSF 1\.3/ })).toBeChecked()
    await user.type(within(dialog).getByLabelText('Endereço (HTTPS)'), 'https://lago.empresa.com.br/ocsf')
    await user.click(within(dialog).getByRole('radio', { name: /Só os tipos escolhidos/ }))
    await user.click(within(dialog).getByRole('checkbox', { name: /Ação no Console/ }))
    await user.click(within(dialog).getByRole('checkbox', { name: /Leitura negada/ }))
    await user.type(within(dialog).getByLabelText('Motivo'), 'recusas e ações no lago de segurança')
    await user.click(within(dialog).getByRole('button', { name: 'Gerar a assinatura e enviar para aprovação' }))

    expect(await within(dialog).findByText('Fluxo siem enviado para aprovação.')).toBeInTheDocument()
    expect(api.find('PUT', '/v1/secrets/webhook/siem-token')).toHaveLength(0)
    const stream = (api.find('POST', '/v1/config/diffs')[0]!.body.document as WebhookEndpointDoc[]).at(-1)
    // The kinds go in the order of domain/vocabulary.py ReceiptKind as the Console lists them, and no authorization_ref.
    expect(stream).toEqual({ endpoint_id: 'siem', url: 'https://lago.empresa.com.br/ocsf', source_id: SRC_VOICE, event_types: ['denied', 'admin'], secret_refs: [ref('siem')], enabled: true, format: 'ocsf' })
  })

  it('checks the address, the kinds, the token and the secrets of other endpoints before writing anything', async () => {
    // An endpoint whose signing secret sits where this stream's token would go: writing it would replace that secret.
    const api = mockApi(routes([STANDARD, { ...STANDARD, endpoint_id: 'coletor', secret_refs: [ref('siem-token')] }]))
    const { user } = await signInAt(at('sistemas'))
    const dialog = await openForm(user)
    const address = within(dialog).getByLabelText('Endereço (HTTPS)')
    await user.type(address, 'http://siem.empresa.com.br/hook')
    expect(address).toHaveAccessibleDescription('O endpoint precisa de HTTPS.')
    await user.click(within(dialog).getByRole('radio', { name: /Só os tipos escolhidos/ }))
    const id = within(dialog).getByLabelText('ID do endpoint')
    expect(id).not.toHaveAttribute('aria-invalid')
    await user.type(within(dialog).getByLabelText('Token do coletor (opcional)'), 'Bearer abc')
    expect(id).toHaveAccessibleDescription('Outro endpoint já usa um segredo com este nome. Escolha outro ID.')
    expect(within(dialog).getByLabelText('Token do coletor (opcional)')).toHaveAccessibleDescription('O token já começa com Bearer: tire o prefixo, ou escolha O valor inteiro.')
    await user.type(within(dialog).getByLabelText('Motivo'), 'teste de validação')
    await user.click(within(dialog).getByRole('button', { name: 'Gerar a assinatura e enviar para aprovação' }))
    expect(within(dialog).getByRole('group', { name: 'Tipos de comprovante' })).toHaveAccessibleDescription('Escolha ao menos um tipo de comprovante.')
    expect(api.find('PUT', '/v1/secrets/webhook/siem')).toHaveLength(0)
    expect(api.find('PUT', '/v1/secrets/webhook/siem-token')).toHaveLength(0)
    expect(api.find('POST', '/v1/config/diffs')).toHaveLength(0)
  })

  it('leaves new streams to people who can write the secret and propose the endpoint', async () => {
    mockApi(routes([HEC], {}, ['integration']))
    await signInAt(at('sistemas'))
    expect(await screen.findByText(/Criar um endpoint, ou um fluxo de comprovantes para o SIEM, grava a assinatura dele no cofre/)).toBeInTheDocument()
    expect(await screen.findByRole('region', { name: 'Comprovantes no SIEM' })).toHaveTextContent('siem.empresa.com.br')
    expect(screen.queryByRole('button', { name: 'Novo fluxo para o SIEM' })).not.toBeInTheDocument()
  })

  it('refuses a new warning endpoint whose id names a secret another endpoint uses', async () => {
    const api = mockApi(routes([HEC]))
    const { user } = await signInAt(at('sistemas'))
    await user.click(await screen.findByRole('button', { name: 'Novo endpoint para Agente de voz' }))
    const dialog = screen.getByRole('dialog', { name: 'Novo endpoint para Agente de voz' })
    const id = within(dialog).getByLabelText('ID do endpoint')
    await user.clear(id)
    await user.type(id, 'siem-token')
    expect(id).toHaveAccessibleDescription('Outro endpoint já usa um segredo com este nome. Escolha outro ID.')
    expect(api.find('PUT', '/v1/secrets/webhook/siem-token')).toHaveLength(0)
  })
})

describe('SIEM streams in Alerts', () => {
  const BATCH: DeliveryView = { delivery_id: 'dl9', endpoint_id: 'siem', event_type: 'receipts', status: 'dead', attempts: 30, created_at: '2026-09-23T12:00:00Z', next_attempt_at: null, delivered_at: null, last_status_code: 503, last_error: 'http_503' }
  const alerts = (extra = {}) =>
    routes([STANDARD, HEC], {
      [`GET ${API}/v1/triggers/firings`]: { items: [] },
      [`GET ${API}/v1/webhooks/deliveries`]: { items: [BATCH] },
      [`POST ${API}/v1/webhooks/deliveries/dl9/redeliver`]: { ...BATCH, status: 'pending' },
      [`GET ${CONTROL}/v1/config/trigger-rules`]: { space_id: SP_PROD, type: 'trigger-rules', version: 2, document: [] },
      ...extra,
    })

  it('opens the deliveries of one endpoint from a link, says a batch of receipts in words and resends it', async () => {
    const api = mockApi(alerts())
    const { user } = await signInAt(at('avisos?aba=deliveries&endpoint=siem'))
    expect(await screen.findByRole('tab', { name: 'Entregas de webhook', selected: true })).toBeInTheDocument()
    const table = await screen.findByRole('table', { name: 'Entregas de webhook' })
    expect(api.find('GET', '/v1/webhooks/deliveries')[0]!.url.searchParams.get('endpoint_id')).toBe('siem')
    expect(screen.getByLabelText('ID do endpoint')).toHaveValue('siem')
    expect(within(table).getByRole('row', { name: /siem/ })).toHaveTextContent('Lote de comprovantes para o SIEM')
    await user.click(within(table).getByRole('button', { name: 'Reenviar a entrega dl9' }))
    expect(await within(table).findByText('Na fila de novo')).toBeInTheDocument()
  })

  it('never offers a stream as the endpoint of a warning, and says so for a rule that pointed at one', async () => {
    const rule = { rule_id: 'promessa.vencida', condition: 'promise.overdue', version: 'v2', enabled: true, endpoint_id: 'siem', window_hours: 24, late_policy: 'deliver_marked', params: { days: 0, owner: 'company', late_after_minutes: 120 } }
    mockApi(alerts({ [`GET ${CONTROL}/v1/config/trigger-rules`]: { space_id: SP_PROD, type: 'trigger-rules', version: 2, document: [rule] } }))
    const { user } = await signInAt(at('avisos'))
    await user.click(await screen.findByRole('tab', { name: 'Criar e testar' }))
    const endpoint = await screen.findByRole('combobox', { name: 'Endpoint que recebe' })
    await user.click(endpoint)
    expect(Array.from(document.querySelectorAll('[role="option"]')).map((o) => o.getAttribute('data-value'))).toEqual(['crm-hook'])
    await user.keyboard('{Escape}')

    await user.click(screen.getByRole('tab', { name: 'Regras em uso' }))
    await user.click(await screen.findByRole('button', { name: 'Ajustar a regra promessa.vencida' }))
    const adjusted = await screen.findByRole('combobox', { name: 'Endpoint que recebe' })
    expect(adjusted).toHaveTextContent('crm-hook')
    expect(screen.getByText('A regra apontava para siem, um fluxo de comprovantes para o SIEM, que não recebe avisos. Escolha o endpoint que recebe.')).toBeInTheDocument()
  })
})
