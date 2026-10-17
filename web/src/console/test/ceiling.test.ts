import { describe, expect, it } from 'vitest'
import { describeError } from '../api/describe'
import { ApiError } from '../api/http'
import { explainCeilingRefusal } from '../lib/ceiling'

// The two refusals of the server's domain/control/rules.py check_ceiling, as the control plane sends them.
describe('explainCeilingRefusal', () => {
  it('puts the ceiling an audience may declare in words', () => {
    expect(explainCeilingRefusal("a customer_agent source's ceiling is at most V3")).toBe(
      'Uma fonte com a audiência Agente de atendimento declara no máximo V3. V4, documento conferido, fica para a mesa de atendimento humano e para agentes internos. Escolha V3 ou um nível abaixo.',
    )
    expect(explainCeilingRefusal("a analyst source's ceiling is at most V3")).toMatch(/^Uma fonte com a audiência Análise declara no máximo V3\./)
    // Another top than V3 says only the top.
    expect(explainCeilingRefusal("a reviewer source's ceiling is at most V2")).toBe('Uma fonte com a audiência Revisão declara no máximo V2. Escolha V2 ou um nível abaixo.')
  })

  it('says that no customer is only for internal agents', () => {
    expect(explainCeilingRefusal('only internal agents work without the customer')).toBe(
      'Sem cliente vale só para um agente interno, que trabalha sem o cliente na conversa. Escolha um nível de verificação para esta audiência.',
    )
  })

  it('leaves any other message to describeError', () => {
    expect(explainCeilingRefusal("the level is above the source's ceiling")).toBeNull()
    expect(explainCeilingRefusal(null)).toBeNull()
  })

  it('lets every screen show the refusal in words', () => {
    const refusal = new ApiError({ kind: 'problem', status: 422, code: 'verification_not_allowed', title: 'verification not allowed', detail: "a human source's ceiling is at most V4" })
    expect(describeError(refusal)).toMatchObject({ title: 'Este nível de verificação não é permitido aqui.', detail: 'Uma fonte com a audiência Atendente humano declara no máximo V4. Escolha V4 ou um nível abaixo.' })
    // A detail this helper does not know is still said in Portuguese, never as the server wrote it.
    const other = new ApiError({ kind: 'problem', status: 422, code: 'verification_not_allowed', title: 'verification not allowed', detail: "the level is above the source's ceiling" })
    expect(describeError(other).detail).toBe('Escolha um nível até o teto da fonte: o maior que ela pode declarar.')
  })
})
