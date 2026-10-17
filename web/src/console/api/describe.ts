import { explainCeilingRefusal } from '../lib/ceiling'
import { explainConfigProblems } from '../lib/configProblems'
import { Retrying } from '../lib/useResource'
import { ApiError } from './http'

/**
 * What the person reads when a call fails: what happened and what to do next, in the tenant's language.
 * The server's `detail` is written in English for developers; it never reaches the screen as it came. The
 * known ones are said in words below, and an unknown one becomes the sentence of its code, with the request
 * id kept for the Niadra team.
 */
export function describeError(failure: unknown): { title: string; detail: string; requestId: string | null } {
  // A read waiting to be tried again says what the server answered.
  const err = failure instanceof Retrying ? failure.cause : failure
  if (!(err instanceof ApiError)) {
    return { title: 'Algo deu errado nesta tela.', detail: 'Tente de novo. Se continuar, avise o time da Niadra.', requestId: null }
  }
  const rid = err.requestId
  const known = translateDetail(err.detail)
  switch (err.code) {
    case 'network':
      return { title: 'Sem conexão com a Niadra.', detail: 'Confira a sua rede e tente de novo.', requestId: rid }
    case 'timeout':
      return { title: 'A resposta demorou demais.', detail: 'Tente de novo em alguns segundos.', requestId: rid }
    case 'unauthenticated':
      return { title: 'Sua sessão terminou.', detail: 'Entre de novo para continuar.', requestId: rid }
    case 'forbidden':
      return {
        title: 'Seu papel não dá acesso a isto.',
        detail: known ?? 'Quem administra o Console da sua empresa, em Pessoas e papéis, pode dar o papel certo.',
        requestId: rid,
      }
    case 'not_found':
      return {
        title: 'Não encontramos este item.',
        detail: known ?? 'Ele pode ter sido apagado, pertencer a outro espaço ou estar fora do alcance do seu papel.',
        requestId: rid,
      }
    case 'rate_limited':
      return {
        title: 'Muitas consultas seguidas.',
        detail: known ?? (err.retryAfter ? `Tente de novo em ${err.retryAfter} s.` : 'Espere alguns segundos e tente de novo.'),
        requestId: rid,
      }
    case 'unavailable':
      return {
        title: 'A região deste espaço pediu um instante.',
        detail: `Ela está ocupada ou ainda preparando o espaço, e nenhum agente é afetado. Tente de novo ${err.retryAfter ? `em ${err.retryAfter} s` : 'em alguns segundos'}.`,
        requestId: rid,
      }
    case 'wrong_cell':
      return { title: 'Este espaço está mudando de endereço.', detail: 'Tente de novo em um minuto.', requestId: rid }
    case 'invalid_input': {
      const problems = explainConfigProblems(err.detail)
      if (problems) return { title: 'A mudança deixaria uma regra sem efeito.', detail: problems.join(' '), requestId: rid }
      if (isSearchRefusal(err.detail)) return { title: 'A busca precisa de um dado exato.', detail: SEARCH_REFUSAL, requestId: rid }
      return { title: 'Um dos campos não foi aceito.', detail: known ?? 'Revise o que você digitou e tente de novo.', requestId: rid }
    }
    case 'conflict':
      return { title: 'O estado mudou antes desta ação.', detail: known ?? 'Alguém mudou este item antes de você. Recarregue a tela e confira o estado atual.', requestId: rid }
    case 'verification_not_allowed':
      return {
        title: 'Este nível de verificação não é permitido aqui.',
        detail: explainCeilingRefusal(err.detail) ?? 'Escolha um nível até o teto da fonte: o maior que ela pode declarar.',
        requestId: rid,
      }
    case 'about_without_link':
      return { title: 'Não há vínculo entre a pessoa e a conta pedida.', detail: 'Crie o vínculo em Identidade, na aba Vínculos, e tente de novo.', requestId: rid }
    case 'personal_data_in_agent_memory':
      return {
        title: 'A nota tem dado pessoal e não foi gravada.',
        detail: 'A memória dos agentes guarda só como fazer o trabalho. Tire nomes, telefones, e-mails, documentos e números de cliente e escreva a nota de um jeito que sirva para qualquer cliente.',
        requestId: rid,
      }
    case 'lease_lost':
      return { title: 'O perfil estava sendo atualizado.', detail: 'Tente de novo em alguns segundos.', requestId: rid }
    case 'internal_error':
      return { title: 'A Niadra teve um erro interno.', detail: 'Tente de novo. Se continuar, avise o time da Niadra com o código abaixo.', requestId: rid }
    case 'parse':
      return { title: 'A resposta veio num formato inesperado.', detail: 'Avise o time da Niadra com o horário da tentativa.', requestId: rid }
    default:
      return {
        title: 'A Niadra não aceitou esta ação.',
        detail: known ?? 'Tente de novo. Se continuar, avise o time da Niadra com o código abaixo.',
        requestId: rid,
      }
  }
}

/** The search refusal of domain/identity/search.py: a value the cell cannot classify without its type. */
const isSearchRefusal = (detail: string | null) => Boolean(detail && /neither a pseudonym nor a recognizable identifier/.test(detail))

export const SEARCH_REFUSAL =
  'A Niadra encontra um cliente por um dado exato: telefone com o código do país, e-mail, identificador do WhatsApp ou o pseudônimo que aparece nos comprovantes. Nome e texto livre não encontram ninguém. Para um ID de app ou de sistema, escolha o tipo. Para abrir um cliente sem saber um dado dele, use a lista de clientes.'

/**
 * The server's English details the Console can meet, each said in words with what to do next. The match is
 * on a stable fragment, so a detail that grows a number or a name still matches.
 */
const DETAILS: [RegExp, string | ((m: RegExpMatchArray) => string)][] = [
  [/not one this list returned|unknown cursor|malformed cursor/, 'A lista mudou desde a última página. Recarregue a tela e peça as páginas de novo.'],
  [/reason must have (\d+) to (\d+) characters/, (m) => `O motivo precisa ter entre ${m[1]} e ${m[2]} caracteres.`],
  [/must have (\d+) to (\d+) characters/, (m) => `O campo precisa ter entre ${m[1]} e ${m[2]} caracteres.`],
  [/period (runs forward|must be ordered)/, 'O período precisa começar antes de terminar e ter no máximo 400 dias.'],
  [/window is limited to (\d+) days/, (m) => `A janela vai até ${m[1]} dias. Escolha um período menor.`],
  [/window_days must be between 1 and (\d+)/, (m) => `A janela vai de 1 a ${m[1]} dias.`],
  [/not a phone number/, 'O valor não é um telefone. Use o formato internacional, com o código do país (+1, +44, +55).'],
  [/invalid e-mail/, 'O e-mail não tem um formato válido.'],
  [/document has an invalid format/, 'O número de documento não tem um formato válido.'],
  [/does not look like a/, 'O valor não tem o formato do tipo escolhido. Confira o tipo e o valor.'],
  [/must be an https address/, 'O endereço precisa começar com https://.'],
  [/is too long/, 'O valor passou do tamanho máximo.'],
  [/belongs to a revoked source/, 'Um dos itens aponta para uma fonte com o acesso cortado. Escolha outra fonte.'],
  [/the file exceeds (\d+) MB/, (m) => `O arquivo passa de ${m[1]} MB.`],
  [/daily analytics call limit reached/, 'O limite diário de consultas de análise deste espaço foi atingido. Ele volta no dia seguinte, em UTC.'],
  [/daily analytics volume reached/, 'O volume diário de análise deste espaço foi atingido. Para extrair em massa, use a exportação, em Dados.'],
  [/already decided/, 'Este diff já foi aprovado ou recusado por outra pessoa. Recarregue a tela para ver o estado atual.'],
  [/keep at least one admin/, 'A empresa precisa manter ao menos uma pessoa com o papel Administração. Dê o papel a outra pessoa antes.'],
  [/erased fact is not restored|evidence of the fact was erased/, 'A evidência deste fato foi apagada a pedido do titular. Um fato apagado não volta, e nada mudou na memória.'],
  [/the proposal is \w+/, 'Esta proposta já foi decidida. Recarregue a lista.'],
  [/the handoff is \w+/, 'Este transbordo já mudou de estado. Recarregue a lista.'],
  [/membership moved while locking/, 'O perfil estava sendo atualizado. Tente de novo em alguns segundos.'],
  [/export package expired/, 'O arquivo passou das 24 horas ou saiu com um apagamento. Peça um novo.'],
  [/no measurement for this conversation/, 'Esta conversa ainda não foi medida. A medição corre depois que a conversa fecha.'],
  [/no mapping for this source/, 'Esta fonte não tem mapeamento. Ele se propõe em Configuração.'],
  [/not enabled for this space/, 'Este recurso está desligado neste espaço.'],
  [/unknown pseudonym/, 'Nenhum cliente com este pseudônimo neste espaço.'],
  [/no recorded turn of the conversation/, 'Nenhum turno desta conversa continua guardado.'],
  [/project has no live space/, 'O projeto ainda não tem espaço no ar.'],
  [/purpose is not declared/, 'A finalidade não está declarada no espaço de produção do projeto.'],
  [/the role cannot do this/, 'Esta ação pede um papel que você não tem. Quem administra o Console, em Pessoas e papéis, pode dar o papel.'],
  [/full view is for internal sources/, 'A visão completa é só para agentes internos.'],
  [/tenant has no (SAML )?single sign-on/, 'A empresa não tem login único (SSO) configurado.'],
  [/dedicated to another tenant/, 'Esta célula é de outra empresa.'],
  [/space never leaves its region/, 'Um espaço nunca sai da região dele. Escolha a região do espaço.'],
]

function translateDetail(detail: string | null): string | null {
  if (!detail) return null
  for (const [pattern, text] of DETAILS) {
    const m = detail.match(pattern)
    if (m) return typeof text === 'function' ? text(m) : text
  }
  return null
}
