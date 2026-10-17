/** Every Console address. Only opaque ids go in a path, never a handle or free text. */
const e = encodeURIComponent
const ROOT = '/console'
const sp = (space: string, rest: string) => `${ROOT}/e/${e(space)}/${rest}`
export const paths = {
  login: `${ROOT}/entrar`,
  spaces: `${ROOT}/espacos`,
  account: `${ROOT}/conta`,
  /** Where the tenant's identity provider sends the browser back, with a one-time code in the fragment. */
  ssoReturn: `${ROOT}/sso`,
  /** An invitation to choose a password; the token travels in the fragment, never to a server. */
  invitation: `${ROOT}/convite`,
  /** The first screen of a space: the getting-started list and the jobs. */
  home: (space: string) => sp(space, 'inicio'),
  tutorials: (space: string) => sp(space, 'tutoriais'),
  customers: (space: string) => sp(space, 'clientes'),
  profile: (space: string, profile: string) => sp(space, `clientes/${e(profile)}`),
  /** The map of relationships of the whole space; a customer's own graph is a tab of the profile. */
  relations: (space: string) => sp(space, 'relacoes'),
  identity: (space: string) => sp(space, 'identidade'),
  patterns: (space: string) => sp(space, 'padroes'),
  agentMemory: (space: string) => sp(space, 'memoria-dos-agentes'),
  receipts: (space: string) => sp(space, 'recibos'),
  receipt: (space: string, receipt: string) => sp(space, `recibos/${e(receipt)}`),
  /** Turn ids are system ids; a conversation id never goes in an address. */
  turns: (space: string) => sp(space, 'turnos'),
  turn: (space: string, turn: string) => sp(space, `turnos/${e(turn)}`),
  coordination: (space: string) => sp(space, 'coordenacao'),
  usage: (space: string) => sp(space, 'uso'),
  measure: (space: string) => sp(space, 'medicao'),
  objectTypes: (space: string) => sp(space, 'tipos'),
  review: (space: string) => sp(space, 'revisao'),
  gaps: (space: string) => sp(space, 'lacunas'),
  data: (space: string) => sp(space, 'dados'),
  agents: (space: string) => sp(space, 'agentes'),
  agent: (space: string, source: string) => sp(space, `agentes/${e(source)}`),
  systems: (space: string) => sp(space, 'sistemas'),
  alerts: (space: string) => sp(space, 'avisos'),
  config: (space: string) => sp(space, 'configuracao'),
  people: (space: string) => sp(space, 'pessoas'),
  sso: (space: string) => sp(space, 'sso'),
}
