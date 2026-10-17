import { describe, expect, it } from 'vitest'
import { explainConfigProblems } from '../lib/configProblems'

// The messages of the server's app/control_config.py semantic_problems, as the control plane sends them.
describe('explainConfigProblems', () => {
  it('puts every rule problem the control plane names in words', () => {
    const detail = [
      'trait-rules p1: window_days must be between 1 and 365',
      'trait-rules p2: unknown rule parameters: min_cont',
      'trait-rules p3: rule parameter `category` names a category that is sensitive or not allowed',
      'trait-rules p4: unknown trait catalog entry',
      'trait-rules p5: window_days 180 counts system events, which stay 90 days in the space (settings.events_hot_days)',
      'trigger-rules r1: params.name names no pattern rule in use',
      'trigger-rules r2: endpoint crm cannot receive it (endpoint_not_subscribed)',
      'webhook-endpoints crm: event_types.0 is not an event type Niadra sends',
      'trait-rules: 26 rules, at most 25 per space',
      'trigger-rules: 51 rules, at most 50 per space',
      'views: 9 task views, at most 8 per space',
      "export-schedules nightly: region is not the space's region",
      'export-schedules copy: another schedule has its bucket and prefix',
    ].join('; ')
    expect(explainConfigProblems(detail)).toEqual([
      'A regra de padrão p1 tem uma janela fora do limite: de 1 a 365 dias.',
      'A regra de padrão p2 tem parâmetro que o catálogo não conhece: min_cont.',
      'A regra de padrão p3 usa uma categoria sensível ou fora da lista liberada para padrões.',
      'A regra de padrão p4 usa um padrão que não está no catálogo.',
      'A regra de padrão p5 conta eventos de sistema numa janela de 180 dias, e eles ficam 90 dias no espaço: use uma janela de até 90 dias.',
      'A regra de aviso r1 usa um padrão que não está ligado neste espaço.',
      'A regra de aviso r2 não chegaria ao endpoint crm: o endpoint não assina o evento trigger.fired.',
      'O endpoint crm assina, na posição 1 da lista, um tipo de evento que a Niadra não envia.',
      'A lista ficaria com 26 regras de padrão, e um espaço tem até 25. Desligar não libera lugar: tire uma regra do documento.',
      'A lista ficaria com 51 regras de aviso, e um espaço tem até 50. Desligar não libera lugar: tire uma regra do documento.',
      'A lista ficaria com 9 views de tarefa, e um espaço tem até 8: cada uma é um contexto que a Niadra mantém pronto para cada cliente.',
      'O agendamento nightly grava fora da região do espaço, e a exportação só sai para a região do espaço.',
      'O agendamento copy grava no mesmo bucket e prefixo de outro agendamento: cada destino recebe um agendamento só.',
    ])
  })

  it('leaves any other message to the server text', () => {
    expect(explainConfigProblems('a trigger rule names an endpoint that does not exist')).toBeNull()
    // One unknown part is enough: half a translation would hide the rest.
    expect(explainConfigProblems('trigger-rules r1: params.name names no pattern rule in use; something new')).toBeNull()
    expect(explainConfigProblems(null)).toBeNull()
  })
})
