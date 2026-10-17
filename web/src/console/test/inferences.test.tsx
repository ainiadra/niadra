import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Inference, ReviewRequest } from '../api/measure'
import { MEMORY, OBJECTS, PROFILE } from './fixtures'
import { API, at, baseRoutes, mockApi, problem, signInAt, type Handler } from './harness'

const PID = PROFILE.profile_id
const INFERENCES: Inference[] = [
  {
    key: 'k-bege', kind: 'soft_constraint', attr: 'item_variant.color', value: 'bege', polarity: 'prefer', origin: 'interactions',
    evidence: { exposure: 4.5, signal: 3.2, sessions: 3 }, confidence: 0.7, used_for: ['personalization'], at: '2026-09-22T17:00:00Z',
  },
  {
    key: 'k-size', kind: 'attribute', attr: 'item_variant.size_label', value: '38', origin: 'interactions', evidence: {}, used_for: [],
    at: '2026-09-20T10:00:00Z', for: 'beneficiary:filha',
  },
  { key: 'k-bag', kind: 'interest', ref: 'item_variant:loja:102', origin: 'watch', evidence: { times: 2 }, used_for: ['personalization'], at: '2026-09-21T09:00:00Z' },
]
const OPEN: ReviewRequest = {
  request_id: 'rr1', profile_id: PID, status: 'open', created_at: '2026-09-23T09:00:00Z', reason: 'A pessoa contesta a recusa de contato.', explanation: {},
}

const routes = (inferences: Handler, extra: Record<string, Handler> = {}) =>
  baseRoutes({
    [`GET ${API}/v1/profiles/${PID}`]: PROFILE,
    [`GET ${API}/v1/profiles/${PID}/memory`]: MEMORY,
    [`GET ${API}/v1/profiles/${PID}/objects`]: OBJECTS,
    [`GET ${API}/v1/profiles/${PID}/inferences`]: inferences,
    [`GET ${API}/v1/review-requests`]: { items: [OPEN, { ...OPEN, request_id: 'rr2', profile_id: 'someone-else' }] },
    ...extra,
  })

async function openTab() {
  const signedIn = await signInAt(at(`clientes/${PID}`))
  await screen.findByText('Canal preferido')
  await signedIn.user.click(screen.getByRole('tab', { name: 'Inferências' }))
  return signedIn
}

describe('The reviewable profile', () => {
  it('lists each inference with its origin, evidence, confidence and use, and this person’s review requests', async () => {
    mockApi(routes({ items: INFERENCES }))
    await openTab()
    const list = await screen.findByRole('list', { name: 'Inferências sobre este cliente' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(within(rows[0]!).getByText('Preferência inferida')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('item_variant.color · prefere · bege')).toBeInTheDocument()
    expect(within(rows[0]!).getByText(/confiança 70,0%/)).toBeInTheDocument()
    expect(within(rows[0]!).getByText(/Evidência: exposição 4,5 · sinal 3,2 · sessões 3/)).toBeInTheDocument()
    expect(within(rows[1]!).getByText('para beneficiário filha')).toBeInTheDocument()
    expect(within(rows[2]!).queryByRole('button', { name: 'Corrigir' })).toBeNull()
    const reviews = screen.getByRole('list', { name: 'Pedidos de revisão deste cliente' })
    expect(within(reviews).getAllByRole('listitem')).toHaveLength(1)
    expect(within(reviews).getByText('Aberto')).toBeInTheDocument()
  })

  it('corrects an inference with the value the person says, and deletes one', async () => {
    const api = mockApi(
      routes(
        { items: INFERENCES },
        {
          [`POST ${API}/v1/profiles/${PID}/inferences/k-bege/correct`]: INFERENCES[0]!,
          [`DELETE ${API}/v1/profiles/${PID}/inferences/k-bag`]: new Response(null, { status: 204 }),
        },
      ),
    )
    const { user } = await openTab()
    const rows = within(await screen.findByRole('list', { name: 'Inferências sobre este cliente' })).getAllByRole('listitem')
    await user.click(within(rows[0]!).getByRole('button', { name: 'Corrigir' }))
    const dialog = await screen.findByRole('dialog', { name: 'Corrigir a inferência' })
    await user.type(within(dialog).getByLabelText('O valor correto'), 'azul')
    await user.click(within(dialog).getByRole('button', { name: 'Corrigir' }))
    await screen.findByRole('list', { name: 'Inferências sobre este cliente' })
    expect(api.find('POST', `/v1/profiles/${PID}/inferences/k-bege/correct`)[0]!.body).toEqual({ value: 'azul' })

    await user.click(within(rows[2]!).getByRole('button', { name: 'Apagar' }))
    const confirm = await screen.findByRole('dialog', { name: 'Apagar a inferência' })
    await user.click(within(confirm).getByRole('button', { name: 'Apagar' }))
    await screen.findByRole('list', { name: 'Inferências sobre este cliente' })
    expect(api.find('DELETE', `/v1/profiles/${PID}/inferences/k-bag`)).toHaveLength(1)
  })

  it('opens a review request with its reason and an idempotency key', async () => {
    const api = mockApi(routes({ items: [] }, { [`POST ${API}/v1/profiles/${PID}/review-requests`]: OPEN }))
    const { user } = await openTab()
    expect(await screen.findByText('Nada inferido sobre este cliente.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Pedir revisão de uma decisão' }))
    const dialog = await screen.findByRole('dialog', { name: 'Pedir revisão de uma decisão automatizada' })
    const send = within(dialog).getByRole('button', { name: 'Enviar o pedido' })
    expect(send).toBeDisabled()
    await user.type(within(dialog).getByLabelText('Motivo'), 'A pessoa contesta a recusa de contato.')
    await user.click(send)
    await screen.findByText('Pedir revisão de uma decisão')
    const [sent] = api.find('POST', `/v1/profiles/${PID}/review-requests`)
    expect(sent!.body).toEqual({ reason: 'A pessoa contesta a recusa de contato.' })
    expect(sent!.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('says when the signals are off', async () => {
    mockApi(routes(problem(404, 'not_found')))
    await openTab()
    expect(await screen.findByText('Os sinais estão desligados neste espaço.')).toBeInTheDocument()
  })
})
