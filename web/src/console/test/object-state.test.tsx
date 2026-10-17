import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { fmtDateLike, fmtMoney } from '../lib/format'
import { category, eventType, intent, objectType, operation, outcome, predicate, purpose, purposes, stateValue } from '../lib/labels'
import { ObjectState } from '../screens/profile/ObjectState'

/** The state of an order as the demo ERP sends it (01/10): lines with an exposure token, money as strings, a calendar day. */
const ORDER = {
  last_event_type: 'order.delivered',
  lines: [
    { amount: '289.90', discount: '28.99', exposure_token: 'nx1.synthetic-line.2.test', line_id: '1', quantity: 1, ref: 'item_variant:loja:vestido-midi-linho-azul' },
    { amount: '219.90', line_id: '2', quantity: 2, ref: 'item_variant:loja:calca-alfaiataria' },
  ],
  state: 'delivered',
  total: '309.80',
  delivery_estimate: '2026-09-27',
  tracking_code: 'BR03904471SP',
}

describe('the state of a business object, readable', () => {
  it('shows the fields with labels, money in reais, the day in the local format and the event in words', () => {
    render(<ObjectState state={ORDER} name="Pedido PED-90039" />)
    expect(screen.getByText('Último evento')).toHaveAttribute('title', 'last_event_type')
    expect(screen.getByText('Pedido entregue')).toHaveAttribute('title', 'order.delivered')
    expect(screen.getByText('Estado').closest('dt')!.nextElementSibling).toHaveTextContent('Entregue')
    expect(screen.getByText('Total').closest('dt')!.nextElementSibling).toHaveTextContent('R$ 309,80')
    expect(screen.getByText('Previsão de entrega').closest('dt')!.nextElementSibling).toHaveTextContent('27/09/2026')
    expect(screen.getByText('Código de rastreio').closest('dt')!.nextElementSibling).toHaveTextContent('BR03904471SP')
  })

  it('renders the lines as a small table, each item by its catalog reference, and never the token', () => {
    render(<ObjectState state={ORDER} name="Pedido PED-90039" />)
    const table = screen.getByRole('table', { name: 'Itens de Pedido PED-90039' })
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Valor', 'Desconto', 'Linha', 'Quantidade', 'Item'])
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('R$ 289,90')
    expect(rows[0]).toHaveTextContent('R$ 28,99')
    expect(within(rows[0]!).getByText('Item do catálogo vestido-midi-linho-azul')).toHaveAttribute('title', 'item_variant:loja:vestido-midi-linho-azul')
    expect(table).not.toHaveTextContent('nx1.')
    expect(screen.getByText('Itens')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('exposure_token')
    expect(document.body.textContent).not.toContain('[{')
  })

  it('keeps the raw JSON, tokens included, behind the technical details', async () => {
    const user = userEvent.setup()
    render(<ObjectState state={ORDER} name="Pedido PED-90039" />)
    const toggle = screen.getByRole('button', { name: 'Detalhes técnicos (1 campo interno)' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)
    expect(screen.getByRole('button', { name: 'Esconder os detalhes técnicos' })).toHaveAttribute('aria-expanded', 'true')
    expect(document.body.textContent).toContain('"exposure_token": "nx1.synthetic-line.2.test"')
  })

  it('formats an invoice in the currency the object names, and nested objects as pairs', () => {
    render(<ObjectState state={{ amount: 462.9, currency: 'USD', due_date: '2026-10-04', status: 'open', customer: { name: 'Mirante', segment: 'retail' }, tags: ['vip', 'b2b'] }} name="Fatura FAT-88390" />)
    expect(screen.getByText('Valor').closest('dt')!.nextElementSibling).toHaveTextContent('US$ 462,90')
    expect(screen.getByText('Vencimento').closest('dt')!.nextElementSibling).toHaveTextContent('04/10/2026')
    expect(screen.getByText('Situação').closest('dt')!.nextElementSibling).toHaveTextContent('Em aberto')
    expect(screen.getByText('Nome').closest('dt')!.nextElementSibling).toHaveTextContent('Mirante')
    expect(screen.getByText('tags').closest('dt')!.nextElementSibling).toHaveTextContent('vip, b2b')
  })

  it('says when the system sent nothing', () => {
    render(<ObjectState state={{}} name="Chamado 1" />)
    expect(screen.getByText('O sistema não mandou nenhum campo.')).toBeInTheDocument()
  })
})

describe('values of the API in words', () => {
  it('formats money and dates the way the person reads them', () => {
    expect(fmtMoney('289.90')).toBe('R$\u00a0289,90')
    expect(fmtMoney(40)).toBe('R$\u00a040,00')
    expect(fmtMoney('1234.5', 'USD')).toBe('US$\u00a01.234,50')
    expect(fmtMoney('abc')).toBe('abc')
    expect(fmtMoney(null)).toBe('sem valor')
    expect(fmtDateLike('2026-09-27')).toBe('27/09/2026')
    expect(fmtDateLike('BR03904471SP')).toBe('BR03904471SP')
  })

  it('translates the values the demo systems send, and keeps a value it does not know as it came', () => {
    expect(objectType('order')).toBe('Pedido')
    expect(objectType('invoice')).toBe('Fatura')
    expect(objectType('warehouse_slot')).toBe('warehouse_slot')
    expect(stateValue('returned')).toEqual({ label: 'Devolvido', raw: 'returned' })
    expect(stateValue('zzz')).toEqual({ label: 'zzz', raw: null })
    expect(eventType('invoice.issued').label).toBe('Fatura emitida')
    expect(eventType('shipment.returned').label).toBe('Entrega devolvida')
    expect(eventType('loan.approved')).toEqual({ label: 'Empréstimo: aprovado', raw: 'loan.approved' })
    expect(eventType('foo.bar')).toEqual({ label: 'foo.bar', raw: null })
    expect(operation('invoice.credit').label).toBe('Crédito na fatura')
    expect(predicate('preferred_channel').label).toBe('Canal preferido')
    expect(predicate('e2e_marker')).toEqual({ label: 'e2e_marker', raw: null })
    expect(intent('request').label).toBe('Pedido')
    expect(outcome('unknown').label).toBe('Sem desfecho registrado')
    expect(category('uncategorized').label).toBe('Sem categoria')
    expect(category('preferência')).toEqual({ label: 'preferência', raw: null })
    expect(purpose('support')).toBe('Atendimento')
    expect(purpose('billing')).toBe('Cobrança')
    expect(purposes(['sales', 'support'])).toBe('Vendas, Atendimento')
    expect(purposes([])).toBe('nenhuma')
  })
})
