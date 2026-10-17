/**
 * The "Authentication" section of each endpoint, in the page language. Who can call
 * each route is in access.ts, taken from the server's authentication dependencies.
 */
import type { Lang, SpecId } from '../types'
import { ACCESS, accessKey } from './access'

type Texts = {
  heading: string
  source: string
  sourceScopes: (s: string) => string
  sourcePerItem: string
  sourceOrPerson: (scopes: string, roles: string) => string
  sourceOrAdmin: (scopes: string) => string
  governance: (r: string) => string
  governanceAnyRole: string
  aggregates: string
  orScope: (s: string) => string
  person: (r: string) => string
  webhook: string
  mcp: string
  analytics: string
  analyticsElevated: string
  public: string
  controlPerson: string
  cell: string
  operator: string
  cellOrOperator: string
  anyKey: string
  feature: (f: string) => string
  features: (f: string) => string
}

const TEXTS: Record<Lang, Texts> = {
  pt: {
    heading: 'Autenticação',
    source: 'Chave de fonte: `Authorization: Bearer nia_sk_...`.',
    sourceScopes: (s) => `Escopo exigido: ${s}.`,
    sourcePerItem: 'Os escopos são conferidos item a item: `track` para eventos, `act` para ações, `identify` para identificação.',
    sourceOrPerson: (s, r) => `Chave de fonte com o escopo ${s}, ou token de pessoa do Console com o papel ${r} (ou admin).`,
    sourceOrAdmin: (s) => `Chave de fonte com o escopo ${s}, ou token de pessoa do Console com o papel admin.`,
    governance: (r) => `Token de pessoa do Console com o papel ${r} (ou admin), ou chave de fonte com o escopo \`admin\`.`,
    governanceAnyRole: 'Token de pessoa do Console com qualquer papel, ou chave de fonte com o escopo `admin`.',
    aggregates: 'Também a chave de uma fonte de público `analyst` com o escopo `analytics`, que lê o relatório somado de todas as fontes do espaço. Qualquer outra chave vê só a própria fonte.',
    orScope: (s) => `Também uma chave de fonte com o escopo ${s}, sem o escopo \`admin\`.`,
    person: (r) => `Só token de pessoa do Console, com o papel ${r} (ou admin). Chave de fonte não é aceita.`,
    webhook: 'Autenticação declarada no mapeamento da fonte: bearer, assinatura HMAC ou token na URL. Não usa chave de fonte.',
    mcp: 'Chave de fonte e o cabeçalho `Niadra-Subject-Token`. Cada ferramenta exige o escopo dela: `context`, `track`, `act`, `identify` ou `search`.',
    analytics: 'Chave de fonte com o escopo `analytics`, ou token de pessoa do Console com o papel analysis (ou admin). Pessoas do papel vendor não acessam.',
    analyticsElevated: 'Chave de fonte com os escopos `analytics` e `admin`, ou token de pessoa do Console com o papel security (ou admin).',
    public: 'Sem autenticação.',
    controlPerson: 'Token de pessoa emitido pelo controle (`Authorization: Bearer <JWT>`), com o papel que a operação pede.',
    cell: 'Credencial de célula (`X-Niadra-Cell-Token`). Usada pelas células da Niadra, não pelo seu código.',
    operator: 'Credencial de operador (`X-Niadra-Operator-Token`). Usada pela operação da Niadra, não pelo seu código.',
    cellOrOperator: 'Credencial de célula ou de operador. Usada pela Niadra, não pelo seu código.',
    anyKey: 'Qualquer chave de fonte, sejam quais forem os escopos: `Authorization: Bearer nia_sk_...`.',
    feature: (f) => `Só responde num espaço com a funcionalidade ${f} ligada (o documento \`features\`); num espaço sem ela, 404.`,
    features: (f) => `Só responde num espaço com uma das funcionalidades ${f} ligada (o documento \`features\`); num espaço sem nenhuma delas, 404.`,
  },
  en: {
    heading: 'Authentication',
    source: 'Source key: `Authorization: Bearer nia_sk_...`.',
    sourceScopes: (s) => `Required scope: ${s}.`,
    sourcePerItem: 'Scopes are checked item by item: `track` for events, `act` for actions, `identify` for identification.',
    sourceOrPerson: (s, r) => `Source key with the ${s} scope, or a Console person token with the ${r} role (or admin).`,
    sourceOrAdmin: (s) => `Source key with the ${s} scope, or a Console person token with the admin role.`,
    governance: (r) => `Console person token with the ${r} role (or admin), or a source key with the \`admin\` scope.`,
    governanceAnyRole: 'Console person token with any role, or a source key with the `admin` scope.',
    aggregates: 'Also the key of an `analyst` source with the `analytics` scope, which reads the report summed over every source of the space. Any other key sees its own source only.',
    orScope: (s) => `Also a source key with the ${s} scope, without the \`admin\` scope.`,
    person: (r) => `Console person token only, with the ${r} role (or admin). Source keys are not accepted.`,
    webhook: 'Authentication declared in the mapping of the source: bearer, HMAC signature or URL token. No source key.',
    mcp: 'Source key plus the `Niadra-Subject-Token` header. Each tool needs its own scope: `context`, `track`, `act`, `identify` or `search`.',
    analytics: 'Source key with the `analytics` scope, or a Console person token with the analysis role (or admin). People with the vendor role have no access.',
    analyticsElevated: 'Source key with the `analytics` and `admin` scopes, or a Console person token with the security role (or admin).',
    public: 'No authentication.',
    controlPerson: 'Person token issued by the control plane (`Authorization: Bearer <JWT>`), with the role the operation needs.',
    cell: 'Cell credential (`X-Niadra-Cell-Token`). Used by Niadra cells, not by your code.',
    operator: 'Operator credential (`X-Niadra-Operator-Token`). Used by Niadra operations, not by your code.',
    cellOrOperator: 'Cell or operator credential. Used by Niadra, not by your code.',
    anyKey: 'Any source key, whatever its scopes: `Authorization: Bearer nia_sk_...`.',
    feature: (f) => `It answers only in a space with the ${f} feature on (the \`features\` document); in a space without it, 404.`,
    features: (f) => `It answers only in a space with one of the ${f} features on (the \`features\` document); in a space with none of them, 404.`,
  },
}

const code = (xs: string[]) => xs.map((x) => `\`${x}\``).join(', ')

/** The "Authentication" lines of a route, in Markdown, with the section title. */
export function accessSection(spec: SpecId, method: string, path: string, lang: Lang): string | undefined {
  const access = ACCESS[accessKey(spec, method, path)]
  if (!access) return undefined
  const a = TEXTS[lang]
  const lines = (() => {
    switch (access.k) {
      case 'source':
        return [a.source, access.scopes.length ? a.sourceScopes(code(access.scopes)) : a.sourcePerItem]
      case 'sourceOrPerson': {
        const scopes = access.anyScope ? access.scopes.map((x) => `\`${x}\``).join(lang === 'pt' ? ' ou ' : ' or ') : code(access.scopes)
        return [access.roles.length ? a.sourceOrPerson(scopes, code(access.roles)) : a.sourceOrAdmin(scopes)]
      }
      case 'governance':
        return [access.roles.length ? a.governance(code(access.roles)) : a.governanceAnyRole]
      case 'aggregates':
        return [a.governance(code(access.roles)), a.aggregates]
      case 'governanceOrScope':
        return [a.governance(code(access.roles)), a.orScope(code([access.scope]))]
      case 'person':
        return [a.person(code(access.roles))]
      case 'analytics':
        return [access.elevated ? a.analyticsElevated : a.analytics]
      default:
        return [a[access.k]]
    }
  })()
  if (access.features?.length) {
    const named = code(access.features)
    lines.push(access.features.length === 1 ? a.feature(named) : a.features(named))
  }
  return `**${a.heading}.** ${lines.join(' ')}`
}
