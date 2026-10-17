import { describe, expect, it } from 'vitest'
import { describeError, SEARCH_REFUSAL } from '../api/describe'
import { ApiError } from '../api/http'
import { recognize } from '../lib/identifiers'

const problem = (code: string, detail: string | null, status = 422) => new ApiError({ kind: 'problem', status, code, title: code, detail, requestId: 'req_x' })

/** No English word of the server's detail may show: the person reads Portuguese and a next step. */
const english = /\b(the|neither|query|cursor|reached|already|decided|entity|belong)\b/

describe('describeError', () => {
  it('says the identifier search refusal in words, with what the search takes instead', () => {
    const e = describeError(problem('invalid_input', 'the query is neither a pseudonym nor a recognizable identifier; give its type'))
    expect(e.title).toBe('A busca precisa de um dado exato.')
    expect(e.detail).toBe(SEARCH_REFUSAL)
    expect(e.detail).toMatch(/telefone com o código do país/)
    expect(e.detail).toMatch(/lista de clientes/)
    expect(e.detail).not.toMatch(english)
    expect(e.requestId).toBe('req_x')
  })

  it('never shows the raw detail of an unknown problem, and keeps the request id for support', () => {
    for (const [code, status] of [
      ['invalid_input', 422],
      ['conflict', 409],
      ['not_found', 404],
      ['forbidden', 403],
      ['some_new_code', 400],
    ] as const) {
      const e = describeError(problem(code, 'this entity does not belong to profiles', status))
      expect(e.detail).not.toContain('entity')
      expect(e.detail).not.toMatch(english)
      expect(e.requestId).toBe('req_x')
    }
  })

  it('translates the details the Console can meet, numbers included', () => {
    expect(describeError(problem('invalid_input', 'the reason must have 3 to 1000 characters')).detail).toBe('O motivo precisa ter entre 3 e 1000 caracteres.')
    expect(describeError(problem('invalid_input', 'the cursor is not one this list returned')).detail).toMatch(/A lista mudou/)
    expect(describeError(problem('invalid_input', 'the window is limited to 90 days')).detail).toBe('A janela vai até 90 dias. Escolha um período menor.')
    expect(describeError(problem('conflict', 'the diff was already decided', 409)).detail).toMatch(/já foi aprovado ou recusado/)
    expect(describeError(problem('conflict', 'the tenant must keep at least one admin', 409)).detail).toMatch(/ao menos uma pessoa com o papel Administração/)
    expect(describeError(problem('not_found', 'the export package expired; ask for a new one', 404)).detail).toMatch(/24 horas/)
    expect(describeError(problem('rate_limited', 'daily analytics call limit reached', 429)).detail).toMatch(/limite diário de consultas de análise/)
  })

  it('keeps the next step for a status without a detail', () => {
    expect(describeError(problem('rate_limited', null, 429)).detail).toBe('Espere alguns segundos e tente de novo.')
    const limited = new ApiError({ kind: 'problem', status: 429, code: 'rate_limited', title: 'x', retryAfter: 9 })
    expect(describeError(limited).detail).toBe('Tente de novo em 9 s.')
    expect(describeError(problem('not_found', null, 404)).detail).toMatch(/fora do alcance do seu papel/)
    expect(describeError(new Error('boom')).title).toBe('Algo deu errado nesta tela.')
  })
})

describe('recognize', () => {
  it('tells the identifiers the search takes without a type, as the cell classifies them', () => {
    expect(recognize('+55 11 90000-0000')).toBe('phone_e164')
    expect(recognize('15550100123')).toBe('phone_e164')
    expect(recognize('Nome@Empresa.com')).toBe('email')
    expect(recognize('p_abcdefghijklmnop')).toBe('pseudonym')
    expect(recognize('5511900000000@s.whatsapp.net')).toBe('wa_jid')
    expect(recognize('123456789012345@lid')).toBe('wa_lid')
  })

  it('refuses a name, a word and a short number', () => {
    for (const q of ['teste', 'Marina Teixeira', '1234', 'p_7Q2M9', '', '   ']) expect(recognize(q)).toBeNull()
  })
})
