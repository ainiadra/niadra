/**
 * The server contract, mirrored from server/src/niadra/contracts/*.py and the
 * routes in adapters/inbound/http/*.py. Enum values are ASCII English.
 * Where a route is still being written by the server team,
 * the type says so and src/console/README.md lists it under "Pending routes".
 */

type UUID = string
type ISODate = string

export type Verification = 'V0' | 'V1' | 'V2' | 'V3' | 'V4' | 'no_customer'
export type Audience = 'customer_agent' | 'internal_agent' | 'human' | 'reviewer' | 'analyst' | 'trigger_target' | 'export'
export type SubjectKind = 'person' | 'account' | 'partner'
type Environment = 'sandbox' | 'production'
export type Scope = 'track' | 'act' | 'context' | 'search' | 'identify' | 'admin' | 'analytics'
/** Control plane roles (domain/control/records.py). */
export type Role = 'admin' | 'security' | 'integration' | 'review' | 'analysis' | 'vendor'
export type EventKind = 'message' | 'system_event' | 'action'
export type HandleType =
  | 'phone_e164'
  | 'wa_id'
  | 'wa_jid'
  | 'wa_lid'
  | 'wa_bsuid'
  | 'email'
  | 'gov_id_hmac'
  | 'app_user_id'
  | 'system_id'
  | 'org_registry_hmac'
  | 'email_domain'
  | 'anon_id'
export type HandleStatus = 'active' | 'ambiguous' | 'blocked' | 'suspected_recycled'
export type AssertionMethod =
  | 'explicit_identify'
  | 'otp'
  | 'login'
  | 'system_import'
  | 'same_event'
  | 'co_occurrence'
  | 'channel_rotation'
  | 'accepted_suggestion'
  | 'external_resolver'
  | 'declared'
export type ReceiptKind = 'read' | 'history' | 'analytics' | 'admin' | 'holdout' | 'denied'
export type ReceiptSurface = 'pack' | 'live' | 'delta' | 'search' | 'timeline' | 'open' | 'object' | 'console'

export interface Page<T> {
  items: T[]
  next_cursor?: string | null
}

/** contracts/common.py Handle: personal data, only ever in request bodies. */
export interface HandleIn {
  type: HandleType
  value: string
  scope?: string | null
  subject_kind?: SubjectKind | null
}

/* ---------------- Control plane (contracts/control.py) ---------------- */

export interface LoginRequest {
  email: string
  password: string
  totp?: string
  /** In place of `totp` when the authenticator app is lost; each code works once. */
  recovery_code?: string
  space_id?: UUID
}

/** The authenticator enrollment (POST /v1/auth/second-factor): the password again, and the secret. */
export interface SecondFactorRequest {
  email: string
  password: string
}

export interface SecondFactorOut {
  secret: string
  totp_uri: string
}

export interface TokenOut {
  access_token: string
  token_type: string
  expires_in: number
  space_id: UUID
  /** Shown once: the ten recovery codes, when this login confirmed the authenticator. */
  recovery_codes?: string[] | null
  /** How many recovery codes remain, when this login used one. */
  recovery_codes_left?: number | null
}

/** GET /v1/users/me/second-factor: the authenticator in use and the recovery codes not used yet. */
export interface SecondFactorStatusOut {
  confirmed_at: ISODate | null
  recovery_codes_left: number
}

/** POST /v1/users/me/recovery-codes and /v1/users/me/second-factor/confirm. Shown once. */
export interface RecoveryCodesOut {
  recovery_codes: string[]
}

/** Claims of the person JWT (app/control.py `_token`). Read for display only; the servers verify it. */
export interface TokenClaims {
  sub: UUID
  tenant: UUID
  space: UUID
  region: string
  env: Environment
  roles: Role[]
  sources: UUID[] | null
  exp: number
}

export interface TenantOut {
  tenant_id: UUID
  name: string
  slug: string
  mfa_required: boolean
  created_at: ISODate
}

/** GET /v1/users/me: the person, and every space they can open with the roles held there. */
interface SpaceRoles {
  space_id: UUID
  project_id: UUID
  environment: Environment
  slug: string
  region: string
  roles: Role[]
}

/** GET /v1/users/me/preferences: what the Console keeps for the person across browsers, by key. */
export interface PreferencesOut {
  preferences: Record<string, unknown>
}

export interface MeOut {
  user_id: UUID
  tenant_id: UUID
  email: string
  name: string | null
  /** False for a person the tenant's SSO created: they sign in only through the identity provider. */
  password_set?: boolean
  /** The e-mail domain belongs to the tenant's SSO connection. */
  sso?: boolean
  spaces: SpaceRoles[]
}

/** GET /v1/config/types: the documents a tenant edits by diff, and who may write them besides admin. */
export interface ConfigTypeOut {
  type: string
  description: string
  roles: Role[]
}

interface SpaceOut {
  space_id: UUID
  project_id: UUID
  environment: Environment
  slug: string
  region: string
  cell_id: string
}

export interface ProjectOut {
  project_id: UUID
  name: string
  slug: string
  region: string
  spaces: SpaceOut[]
}

/** A space as the Console shows it: from /v1/users/me, with the project name and cell from /v1/projects. */
export interface Space extends SpaceOut {
  project_name: string
  roles: Role[]
}

export interface SourceOut {
  source_id: UUID
  space_id: UUID
  name: string
  audience: Audience
  vendor: string | null
  channel: string | null
  purposes: string[]
  verification_ceiling: Verification
  confirms_before_acting: boolean
  navigation_enabled: boolean
  trusted_action_ops: string[]
  critical: boolean
  revoked_at: ISODate | null
}

export interface KeyOut {
  key_id: string
  source_id: UUID
  scopes: Scope[]
  status: 'active' | 'revoked'
  created_at: ISODate
  expires_at: ISODate | null
  revoked_at: ISODate | null
}

export interface IssuedKeyOut {
  key: KeyOut
  /** The full key. Shown once; Niadra keeps only two hashes of it. */
  secret: string
}

export interface RevocationOut {
  seq: number
  space_id: UUID
  kind: string
  source_id: UUID
  key_id: string | null
  at: ISODate
}

/** POST /v1/sources (contracts/control.py SourceCreate). */
export interface SourceCreate {
  space_id: UUID
  name: string
  audience: Audience
  vendor: string | null
  channel: string | null
  purposes: string[]
  verification_ceiling: Verification
  confirms_before_acting: boolean
  navigation_enabled: boolean
  trusted_action_ops: string[]
  critical: boolean
}

/* ---------------- People, invitations and single sign-on (contracts/control.py, contracts/sso.py) ---------------- */

/** A role on the whole tenant (`space_id` null) or on one space; the vendor role names its sources. */
export interface BindingIn {
  role: Role
  space_id: UUID | null
  source_ids: UUID[]
}

export type BindingOut = BindingIn

/** Shown once: the Console turns it into `<origin>/console/convite#<token>`. */
export interface InvitationOut {
  token: string
  expires_at: ISODate
}

export interface UserOut {
  user_id: UUID
  email: string
  active: boolean
  roles: BindingOut[]
  last_login_at: ISODate | null
  totp_uri: string | null
  created_at: ISODate
  deactivated_at: ISODate | null
  /** False while an invited person has not chosen a password, and for people the SSO created. */
  password_set: boolean
  /** The e-mail domain belongs to the tenant's SSO connection. */
  sso: boolean
  last_sso_login_at: ISODate | null
  second_factor_confirmed_at: ISODate | null
  recovery_codes_left: number
  invitation_expires_at: ISODate | null
  /** Only at creation, for a person created without a password. */
  invitation: InvitationOut | null
}

export interface SecondFactorResetOut {
  reset_id: UUID
  user_id: UUID
  reset_by: UUID
  reason: string
  at: ISODate
}

export type AccessChangeKind =
  | 'invited'
  | 'invitation_accepted'
  | 'roles_changed'
  | 'deactivated'
  | 'reactivated'
  | 'second_factor_reset'
  | 'sso_person_created'
  | 'sso_roles_synced'
  | 'sso_saved'
  | 'sso_removed'

export interface AccessChangeOut {
  change_id: UUID
  kind: AccessChangeKind
  at: ISODate
  user_id: UUID | null
  /** Null when the tenant's identity provider made the change at a sign-in. */
  actor_id: UUID | null
  reason: string | null
  detail: Record<string, unknown>
}

export interface InvitationPreviewOut {
  email: string
  tenant_name: string
  expires_at: ISODate
}

export type SsoProtocol = 'oidc' | 'saml'

export interface RoleMapping {
  group: string
  role: Role
}

export interface SsoConnectionIn {
  protocol: SsoProtocol
  domains: string[]
  enabled: boolean
  enforced: boolean
  default_role: Role | null
  role_mappings: RoleMapping[]
  groups_attribute: string
  require_second_factor: boolean
  oidc: { issuer: string; client_id: string; client_secret: string | null; scopes: string[] } | null
  saml: { idp_entity_id: string; idp_sso_url: string; idp_certificate: string } | null
  reason: string
}

export interface SsoConnectionOut {
  connection_id: UUID
  protocol: SsoProtocol
  domains: string[]
  enabled: boolean
  enforced: boolean
  default_role: Role | null
  role_mappings: RoleMapping[]
  groups_attribute: string
  require_second_factor: boolean
  oidc: { issuer: string; client_id: string; scopes: string[]; client_secret_set: boolean } | null
  saml: { idp_entity_id: string; idp_sso_url: string; certificate: { subject: string; not_after: ISODate; sha256_fingerprint: string; pem?: string } } | null
  created_at: ISODate
  updated_at: ISODate
  updated_by: UUID
}

/** What the tenant pastes into its identity provider. */
export interface ServiceProviderOut {
  oidc_redirect_uri: string
  saml_entity_id: string
  saml_acs_url: string
  saml_metadata_url: string
}

export interface SsoOut {
  connection: SsoConnectionOut | null
  service_provider: ServiceProviderOut
}

export interface SsoTestOut {
  test_id: UUID
  authorize_url: string
  expires_at: ISODate
}

export interface SsoTestResultOut {
  test_id: UUID
  status: 'pending' | 'completed' | 'failed'
  created_at: ISODate
  completed_at: ISODate | null
  ok: boolean | null
  failure: string | null
  detail: string | null
  email: string | null
  groups: string[]
  roles: Role[]
  person_exists: boolean | null
}

export interface SsoDiscoverOut {
  sso: boolean
  protocol: SsoProtocol | null
  enforced: boolean
}

export interface SsoStartOut {
  authorize_url: string
  expires_at: ISODate
}

export interface SsoExchangeRequest {
  code: string
  verifier: string
  totp?: string
  recovery_code?: string
  space_id?: UUID
}

/** The password again, or, for a person the SSO created (no password), the current authenticator code. */
export type Presence = { password: string } | { totp: string }

/** Data API POST /v1/sources/{id}/revoke (contracts/edge.py): the instant cut in the cell. */
export interface SourceRevocationOut {
  source_id: UUID
  revoked_key_ids: string[]
  revoked_at: ISODate
}

/** GET /v1/sources/coverage (contracts/ingest.py): what each source sent and what arrived. */
interface CoverageDay {
  day: string
  sent: number
  received: number
  rejected: number
  unmapped: number
  last_event_at: ISODate | null
}

export interface SourceCoverage {
  source_id: UUID
  name: string | null
  channel: string | null
  sent: number
  received: number
  rejected: number
  unmapped: number
  last_event_at: ISODate | null
  days: CoverageDay[]
}

export interface CoverageOut {
  since: ISODate
  items: SourceCoverage[]
}

/** GET /v1/sources/arrived: all that any role may know of the coverage. */
export interface EventsArrived {
  arrived: boolean
  since: ISODate
}

/** Data API GET /v1/usage (contracts/metering.py). */
interface CellUsageItem {
  start: ISODate
  metric: string
  quantity: number
  cost_micros: number
  /** With group_by=source; null for the space's own work. */
  source_id: string | null
}

export interface CellUsageOut {
  since: ISODate
  until: ISODate
  granularity: 'hour' | 'day'
  source_id: string | null
  group_by: 'source' | null
  items: CellUsageItem[]
}

interface ControlUsageItem {
  space_id: UUID
  period_start: ISODate
  metric: string
  quantity: number
  cost_micros: number
}

export interface UsageOut {
  items: ControlUsageItem[]
  quotas: { tenant_id: UUID; metric: string; limit: number; period: string }[]
}

/**
 * GET /v1/usage/budget (contracts/metering.py BudgetLine): today's AI spend of one purpose against the
 * space's daily ceiling, the contract's (`llm_daily_micros:<purpose>`) or the cell's default.
 */
interface BudgetLine {
  /** `extraction` or `assistant`. */
  purpose: string
  /** The UTC day the spend counts in. */
  day: string
  /** The daily ceiling in millionths of a US dollar; null when the purpose has none. */
  cap_micros: number | null
  from_contract: boolean
  spent_micros: number
  /** Work of this purpose waits for the next UTC day. */
  reached: boolean
  resumes_at: ISODate | null
}

export interface BudgetReport {
  items: BudgetLine[]
}

/** One window of the server time of the space's context reads (`GET /v1/usage/context-latency`). */
export interface ContextLatencyWindow {
  window: '24h' | '7d'
  reads: number
  p50_ms?: number | null
  p95_ms?: number | null
  /** Share of the reads the server answered in `target_ms` or less. */
  under_target?: number | null
  within_slo?: boolean | null
  /** Share of the objective's error budget spent; above 1 the objective was missed. */
  error_budget_used?: number | null
  /** Share of the reads served degraded. */
  degraded?: number | null
}

export interface ContextLatencyReport {
  /** False when the cell's metrics could not be read; the windows are empty. */
  available: boolean
  target_ms: number
  slo: number
  windows: ContextLatencyWindow[]
  as_of: ISODate
}

export type DiffStatus = 'pending' | 'proposed' | 'applied' | 'rejected'

export interface DocumentOut {
  space_id: UUID
  type: string
  version: number
  document: unknown
}

export interface DiffOut {
  diff_id: UUID
  space_id: UUID
  type: string
  status: DiffStatus
  reason: string
  base_version: number
  author_id: UUID | null
  /** The person's e-mail, or the label of an author that is not a person (`control_plane`, `assistant@<cell>`). */
  author_label: string | null
  decided_by: UUID | null
  /** The e-mail of the person who approved or rejected. */
  decided_by_label: string | null
  decided_at: ISODate | null
  applied_version: number | null
  rollback_of: UUID | null
  created_at: ISODate
  document: unknown
}

export interface VersionOut {
  version: number
  type: string
  diff_id: UUID
  created_at: ISODate
  document: unknown
}

export interface SecretRefOut {
  space_id: UUID
  ref: string
  kind: string
  version: number
  rotated_by: UUID
  rotated_at: ISODate
}

/** A configuration document type; the list comes from GET /v1/config/types. */
export type ConfigType = string

/** config/models.py WebhookEndpoint, as it arrives inside the `webhook-endpoints` document. */
export interface WebhookEndpointDoc {
  endpoint_id: string
  url: string
  source_id: UUID
  event_types: string[]
  secret_refs: string[]
  enabled: boolean
  /** `standard`: Standard Webhooks events; `ocsf` and `hec`: the receipts stream to a SIEM. */
  format?: 'standard' | 'ocsf' | 'hec'
  authorization_ref?: string | null
}

/** config/models.py Mapping, inside the `mappings` document. */
export interface MappingDoc {
  source_id: UUID
  version: string
  auth?: unknown
  verification_challenge?: string | null
  rules: unknown[]
}

/** config/models.py ExportSchedule, inside the `export-schedules` document. */
export interface ExportScheduleDoc {
  schedule_id: string
  destination_kind: 's3' | 'gcs' | 'azure'
  bucket: string
  prefix?: string
  region: string
  tables: string[]
  interval_hours: number
  pseudonymize: boolean
  credential_ref: string
}

/* ---------------- Cell: identity (contracts/identity.py) ---------------- */

interface HandleOut {
  handle_id: UUID
  type: HandleType
  scope: string
  subject_kind: SubjectKind
  /** Masked by the caller's role. */
  value: string
  status: HandleStatus
  level: Verification
  /** How much the identifier weighs. */
  weight: 'hint' | 'declared' | 'verified' | 'authenticated' | 'system'
  first_seen: ISODate
  last_seen: ISODate
}

export interface LinkOut {
  link_id: UUID
  person_handle_id: UUID
  org_handle_id: UUID
  role: string
  can_see_contacts: boolean
  valid_from: ISODate
  valid_to: ISODate | null
}

export type ParentMethod = 'system_import' | 'declared'

/** An organization under its parent: one level, and the two stay two profiles. */
export interface ParentOut {
  parent_link_id: UUID
  /** The subsidiary's identifier. */
  org_handle_id: UUID
  parent_handle_id: UUID
  /** The subsidiary's profile now. */
  org_profile_id: UUID | null
  /** The parent's profile now. */
  parent_profile_id: UUID | null
  method: ParentMethod
  valid_from: ISODate
  valid_to: ISODate | null
}

export interface ProfileOut {
  profile_id: UUID
  kind: SubjectKind
  pseudonym: string
  membership_version: number
  privacy_version: number
  partial: boolean
  handles: HandleOut[]
  links: LinkOut[]
  /** An organization's parent, when it has one. */
  parent?: ParentOut | null
  /** The organizations this one is the parent of. */
  subsidiaries?: ParentOut[]
}

export type AssertionState = 'applied' | 'pending' | 'rejected'

export interface AssertionOut {
  assertion_id: UUID
  handle_a: UUID
  handle_b: UUID
  method: AssertionMethod
  state: AssertionState
  confidence: number
  level_at_capture: Verification
  created_at: ISODate
  origin_event_id: UUID | null
  actor: string | null
  reason: string | null
  retracted_at: ISODate | null
}

export interface OperationOut {
  op_id: UUID
  status: 'queued' | 'applied' | 'linked' | 'blocked'
  assertion_id: UUID | null
  link_id: UUID | null
}

export type LinkMethod = 'system_import' | 'co_occurrence' | 'declared' | 'login'

/** POST /v1/profiles/search (contracts/identity.py ProfileSearchIn): the query travels in the body. */
export interface ProfileSearchIn {
  query: string
  type?: HandleType
  scope?: string
  limit?: number
}

/** GET /v1/profiles: a row of the customer list. No identifier comes in full, whatever the role. */
export interface ProfileListed {
  profile_id: UUID
  pseudonym: string
  kind: SubjectKind
  /** The three with the latest activity, the latest first, each masked. */
  handles: { type: HandleType; value: string; last_seen: ISODate }[]
  handle_count: number
  last_seen: ISODate
}

export type ProfileOrder = 'last_seen_desc' | 'last_seen_asc'

export interface ProfileListQuery {
  order?: ProfileOrder
  kind?: SubjectKind
  cursor?: string
  limit?: number
}

export interface ProfileMatch {
  profile_id: UUID
  pseudonym: string
  kind: SubjectKind
  partial: boolean
  handles: HandleOut[]
  last_seen: ISODate | null
}

/** GET /v1/identity/suggestions: offline candidate pairs for a person to review. */
export interface SuggestionOut {
  suggestion_id: UUID
  handle_a: UUID
  handle_b: UUID
  /** Null when the handle no longer exists. */
  profile_a: UUID | null
  profile_b: UUID | null
  /** Masked by the caller's role. */
  identifier_a: HandleOut | null
  identifier_b: HandleOut | null
  score: number
  features: Record<string, unknown>
  status: 'open' | 'accepted' | 'rejected'
  created_at: ISODate
}

/* ---------------- Cell: memory and objects (contracts/memory.py, contracts/objects.py) ---------------- */

interface ObjectRef {
  type: string
  namespace: string
  id: string
}

/** Provenance of an item: the event, its source (agent or system), channel and time. */
export interface Origin {
  event_id?: string | null
  kind?: EventKind | null
  source_id?: string | null
  channel?: string | null
  at?: ISODate | null
  speaker?: string | null
  conversation_id?: string | null
  task_id?: string | null
}

export interface FactOut {
  id: string
  predicate: string
  /** Null when the policy masks it for this reader. */
  value: string | null
  masked: boolean
  category: string
  sensitivity: 'normal' | 'restricted' | 'sensitive'
  status: 'active' | 'expired' | 'quarantined' | 'retracted'
  confidence: number
  valid_at: ISODate
  invalid_at: ISODate | null
  last_confirmed_at: ISODate
  times_seen: number
  about_handle_id: string | null
  object: ObjectRef | null
  evidence_event_ids: string[]
  origin: Origin | null
  /** Came through an identifier a claim joined to the profile and nobody confirmed yet; only the admin and security roles see such rows. */
  unconfirmed_link?: boolean
}

export interface OpenItemOut {
  id: string
  description: string | null
  masked: boolean
  owner: 'company' | 'customer'
  promise: boolean
  status: 'open' | 'resolved' | 'overdue'
  due_at: ISODate | null
  created_at: ISODate
  object: ObjectRef | null
  expected_operation: string | null
  closed_by_action_id: string | null
  closed_at: ISODate | null
  origin: Origin | null
  /** Came through an identifier a claim joined to the profile and nobody confirmed yet; only the admin and security roles see such rows. */
  unconfirmed_link?: boolean
}

interface EpisodeOut {
  id: string
  version: number
  summary: string | null
  masked: boolean
  intent: string
  category: string
  normalized_category: string
  outcome: string
  resolution: string | null
  sentiment: number | null
  session_id: string
  started_at: ISODate
  ended_at: ISODate
  origin: Origin
  /** Came through an identifier a claim joined to the profile and nobody confirmed yet; only the admin and security roles see such rows. */
  unconfirmed_link?: boolean
}

export interface TraitOut {
  id: string
  name: string
  value: string | null
  masked: boolean
  layer: string
  rule_version: string
  first_seen: ISODate
  last_evidence_at: ISODate
  expires_at: ISODate
  confidence: number
  active: boolean
  retracted_at: ISODate | null
  evidence: { kind: string; id: string; origin?: Origin | null }[]
  /** Came through an identifier a claim joined to the profile and nobody confirmed yet; only the admin and security roles see such rows. */
  unconfirmed_link?: boolean
}

/** GET /v1/profiles/{id}/memory: read under the policy of the person's role; denied items only counted. */
/** GET /v1/profiles/{profile_id}/facts/{fact_id}/history (contracts/memory.py FactHistory). */
export interface FactHistory {
  profile_id: string
  fact_id: string
  predicate: string
  policy_version: string
  /** Every value the fact's slot held, oldest first. */
  versions: FactOut[]
  /** `from_fact_id` is the newer fact: `supersedes`, `contradicts`... */
  relations: { from_fact_id: string; to_fact_id: string; type: string }[]
  withheld: Record<string, number>
}

export interface ProfileMemory {
  profile_id: string
  policy_version: string
  audience: Audience
  facts: FactOut[]
  open_items: OpenItemOut[]
  /** Episodes, newest first. */
  timeline: EpisodeOut[]
  /** Where the timeline's next page starts (`timeline_cursor`); null when it holds no more. */
  timeline_next?: string | null
  traits: TraitOut[]
  withheld: Record<string, number>
}

export interface ActionOut {
  id: string
  operation: string
  status: 'declared' | 'confirmed' | 'divergent' | 'quarantined'
  result: string | null
  masked: boolean
  purpose: string | null
  occurred_at: ISODate
  object_id: string | null
  closes_item_id: string | null
  confirmed_by_event_id: string | null
  origin: Origin
}

/** One row of what people and agents said about an object, kept under who said it. */
export interface SaidAbout {
  kind: 'observation' | 'item' | 'instruction'
  id: string
  text: string
  said_at: ISODate
  audience: 'shared' | 'internal'
  role?: string | null
  /** The handle of who said it. */
  by?: string | null
  predicate?: string | null
  item_kind?: 'promise' | 'dispute' | 'request' | null
  due_at?: ISODate | null
  overdue?: boolean
  strength?: 'must' | 'must_not' | null
  attr?: string | null
  expires_at?: ISODate | null
  /** The field of the object's type an observation is about, shown beside the recorded value. */
  observes?: string | null
  recorded?: unknown
  diverges?: boolean
  claim_safe?: false
}

/** What the owner and participants of an object said about it, newest first. */
export interface ObjectMemoryOut {
  said: SaidAbout[]
  instructions: SaidAbout[]
  items: SaidAbout[]
}

interface ProfileObject {
  id: string
  ref: ObjectRef
  role: 'owner' | 'participant'
  state: Record<string, unknown> | null
  masked: boolean
  as_of: ISODate
  source_id: string
  record_ref: string | null
  actions: ActionOut[]
  /** Came through an identifier a claim joined to the profile and nobody confirmed yet; only the admin and security roles see such rows. */
  unconfirmed_link?: boolean
  /** What its owner and participants said about it. */
  memory?: ObjectMemoryOut | null
}

/** GET /v1/profiles/{id}/objects */
export interface ProfileObjects {
  profile_id: string
  policy_version: string
  audience: Audience
  objects: ProfileObject[]
  /** Where the objects' next page starts (`objects_cursor`); null when there are no more. */
  objects_next?: string | null
  /** Actions on no object of the list, e.g. one that closes a promise. */
  actions: ActionOut[]
  /** Where the actions' next page starts (`actions_cursor`); null when there are no more. */
  actions_next?: string | null
  withheld: Record<string, number>
}

/* ---------------- Cell: agents' working state (contracts/state.py) ---------------- */

/** One working state of a customer: never its content. */
export interface AgentStateMeta {
  agent: string
  scope_kind: 'conversation' | 'task' | 'object' | 'subject'
  source_id: string
  bytes: number
  version: number
  updated_at: string
  /** Null while the agent's type names no retention for its state: it is kept until one is named. */
  valid_until: string | null
  schema_version?: string | null
}

export interface AgentStateMetaPage {
  items: AgentStateMeta[]
}

/* ---------------- Cell: policy simulation (contracts/policy.py) ---------------- */

export interface SimulateRequest {
  profile_id: UUID
  source_id?: UUID
  audience?: Audience
  purposes?: string[]
  verification: Verification
  view: string
}

interface SimulatedItem {
  id: string
  kind: string
  category: string
  effect: 'allow' | 'mask' | 'deny'
  reason: string | null
  rule: string | null
  /** False when allowed but left out by the budget. */
  delivered: boolean
}

/** One recent turn of the customer's other conversations, as the `live` block would treat it. */
interface SimulatedLiveTurn {
  at: ISODate
  channel: string
  kind: EventKind
  speaker: string
  source_id: string
  delivered: boolean
  effect: 'allow' | 'mask' | 'deny'
  reason: string | null
  rule: string | null
  /** What the reader would get (the masked word under a `mask` rule); never the text of a withheld turn. */
  text: string | null
}

/** The `live` block of the same read, through the same policy as the pack's items. */
export interface SimulatedLive {
  turns: SimulatedLiveTurn[]
  delivered: number
  /** Withheld turns by reason. */
  withheld: Record<string, number>
  /** False when the hot layer could not be read whole; the list may be short. */
  complete: boolean
}

export interface SimulateResponse {
  policy_version: string
  verification: Verification
  audience: Audience
  items: SimulatedItem[]
  withheld: Record<string, number>
  text: string
  live: SimulatedLive
}

/* ---------------- Cell: receipts and lineage (contracts/audit.py) ---------------- */

export interface ReceiptOut {
  receipt_id: string
  kind: ReceiptKind
  surface: ReceiptSurface
  audience: Audience
  read_at: ISODate
  profile_id: string | null
  /** The profile's pseudonym, the name to show in place of `profile_id`; null when the profile is unknown. */
  pseudonym: string | null
  source_id: string | null
  person_id: string | null
  /** The e-mail of the person who acted; null for a person the space has not named yet. */
  person_label: string | null
  conversation_id: string | null
  task_id: string | null
  object_id: string | null
  about_handle_id: string | null
  purpose: string | null
  item_ids: string[]
  pack_hash: string | null
  manifest_hash: string | null
  verification: string | null
  policy_version: string | null
  experiment_group: string | null
  withheld: number
  /**
   * Why a `denied` read was refused (`reason`), or what an `admin` receipt records: the `action`
   * (app/admin_audit.py, `profiles.listed`, `review.verdicts`, `key.issued`...), its `target`
   * (`sample:<id>`, `source:<id>`), the person's `roles` or the key's `key_id`, and `actor`
   * `control_plane` for a change the control plane made with no person behind it.
   */
  detail?: Record<string, string>
  slice: number
  day: string
  seq: number
  prev_hash: string
  hash: string
}

export interface ReceiptQuery {
  profile_id?: string
  source_id?: string
  conversation_id?: string
  kind?: ReceiptKind
  since?: string
  until?: string
  cursor?: string
  limit?: number
}

interface LineageEvent {
  event_id: string
  kind: EventKind
  source_id: string
  channel: string
  occurred_at: ISODate
  conversation_id: string | null
  task_id: string | null
}

interface LineageItem {
  id: string
  kind: string | null
  evidence_event_ids: string[]
  events: LineageEvent[]
}

export interface ReceiptLineage {
  receipt: ReceiptOut
  manifest_found: boolean
  items: LineageItem[]
  excluded: Record<string, unknown>[]
}

export interface FactViewers {
  fact_id: string
  receipts: ReceiptOut[]
}

export interface ChainVerification {
  day: string
  valid: boolean
  receipts: number
  heads: { slice: number; seq: number; hash: string }[]
  breaks: { slice: number; seq: number; reason: string }[]
  anchored_root: string | null
  root_matches: boolean | null
  /** The copy of the root kept outside the database holds the recomputed root; null while not anchored or unreadable. */
  anchor_copy_matches?: boolean | null
  /** That copy is in write-once storage (Object Lock in compliance mode). */
  anchor_locked?: boolean | null
}

/* ---------------- Cell: privacy (contracts/privacy.py) ---------------- */

/** The data subject's right to correction, by a person with the privacy role. */
export interface CorrectionRequest {
  profile_id: UUID
  action: 'retract_fact' | 'correct_fact' | 'resolve_open_item'
  fact_id?: string
  open_item_id?: string
  value?: string
  reason?: string
}

/** Undoing a retraction or a correction of a fact, by the security role: the fact comes back as it was. */
export interface FactRestoreRequest {
  profile_id: UUID
  reason?: string
}

/** What the cell took of a write: accepted, already seen under the same key, or refused item by item. */
export interface BatchResult {
  accepted: number
  duplicates: number
  errors: { index: number; code: string; detail?: string | null }[]
}

export type ForgetTarget = 'profile' | 'handle' | 'conversation'

export interface ForgetRequest {
  target: ForgetTarget
  profile_id?: UUID
  handle?: HandleIn
  conversation_id?: string
  /** An objection to one purpose instead of an erasure (13, 6.6): profile or handle targets; answered final. */
  purpose?: string
}

/** config/models.py PurposeSpec, inside the `purposes` document (13, 6.6). */
interface PurposeSpecDoc {
  name: string
  legal_basis: LegalBasis
  retention_days?: number | null
  description?: string | null
}

type LegalBasis =
  | 'consent'
  | 'contract'
  | 'legal_obligation'
  | 'legitimate_interest'
  | 'vital_interest'
  | 'health_protection'
  | 'public_task'
  | 'research'
  | 'legal_claims'
  | 'credit_protection'
  | 'fraud_prevention'

/** config/models.py PurposeSettings: the `purposes` document. */
export interface PurposesDoc {
  version?: string
  purposes: PurposeSpecDoc[]
  categories?: Record<string, string[]>
  event_types?: Record<string, string[]>
  default?: string[]
}

/** contracts/insights.py, `GET /v1/insights/gaps` (13, 6.5). Counts only: never a customer, a value or a text. */
interface GapSource {
  source_id: string
  name: string
  via: 'mapping' | 'schema'
}

export interface GapReview {
  decision: 'accepted' | 'dismissed'
  decided_by: string
  decided_at: ISODate
  note: string | null
}

export interface GapOut {
  key: string
  kind: 'no_record' | 'agent_question' | 'object_unresolved'
  subject: string
  topic: string
  customers: number
  turns: number
  could_record: GapSource[]
  answered_share: number | null
  review: GapReview | null
}

export interface RecurringOut {
  key: string
  subject: string
  topic: string
  customers: number
  turns: number
  answered_share: number
  review: GapReview | null
}

export interface ProposalOut {
  key: string
  kind: 'not_recorded' | 'settled'
  subject: string
  topic: string
  text: string
  review: GapReview | null
}

export interface GapsReport {
  since: string
  until: string
  gaps: GapOut[]
  recurring: RecurringOut[]
  proposals: ProposalOut[]
  withheld_groups: number
}

export interface GapReviewIn {
  key: string
  decision: 'accepted' | 'dismissed'
  note?: string | null
}

export interface ErasureOut {
  request_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  target_kind: ForgetTarget
  target_id: string
  requested_at: ISODate
  completed_at: ISODate | null
  erased: Record<string, number>
  export_run_ids: string[]
  receipt_hash: string | null
}

export interface ExportRequest {
  profile_id?: UUID
  handle?: HandleIn
}

export interface ExportPackageOut {
  run_id: string
  /** The subject, also when it was named by a handle. */
  profile_id: string
  /** Presigned link to object storage. The Console downloads through the cell instead, which works in every deploy. */
  download_url: string
  download_expires_at: ISODate
  files: Record<string, string>
  sha256: string
}

export interface ExportRunOut {
  run_id: string
  schedule_id: string
  status: 'waiting' | 'running' | 'completed' | 'failed'
  from_seq: number
  to_seq: number
  started_at: ISODate
  finished_at: ISODate | null
  error: string | null
  manifest: Record<string, unknown> | null
}

export interface SecretWritten {
  version: number
}

/* ---------------- Cell: traits, triggers, webhooks (operate) ---------------- */

export const TRAIT_CATALOG = [
  'recurring_complaint',
  'broken_company_promise',
  'asked_to_cancel',
  'preferred_channel',
  'does_not_answer_calls',
  'escalated_to_human',
  'declining_sentiment',
  'overdue_invoices',
] as const
export type TraitCatalog = (typeof TRAIT_CATALOG)[number]

interface EvidencePointer {
  kind: string
  id: string
}

export interface TraitView {
  trait_id: string
  profile_id: string
  name: string
  layer: string
  rule_version: string
  first_seen: ISODate
  last_evidence_at: ISODate
  expires_at: ISODate
  confidence: number
  retracted_at: ISODate | null
  evidence: EvidencePointer[]
}

export interface TraitRuleDraft {
  rule_id: string
  catalog: TraitCatalog
  version?: string
  params: Record<string, unknown>
}

/** config/models.py TraitRuleConfig, inside the `trait-rules` document. */
export interface TraitRuleDoc {
  rule_id: string
  catalog: TraitCatalog
  version: string
  enabled?: boolean
  params?: Record<string, unknown>
  purposes?: string[]
}

export interface TraitDryRunResponse {
  rule_id: string
  window_days: number
  profiles_evaluated: number
  profiles_matching: number
  sample: { profile_id: string; pseudonym: string; value: string; expires_at: ISODate; evidence: EvidencePointer[] }[]
}

export interface OptOutResponse {
  profile_id: string
  opted_out_at: ISODate
}

export const TRIGGER_CONDITIONS = [
  'promise.overdue',
  'open_item.due',
  'trait.present',
  'sentiment.drop',
  'identity.ambiguous',
  'fact.contradicted_by_system_event',
  'recontact',
  'context_use.repetition_rate',
  'source.silent',
  'source.gap',
] as const
export type TriggerCondition = (typeof TRIGGER_CONDITIONS)[number]

export interface TriggerRuleDraft {
  rule_id: string
  condition: TriggerCondition
  version?: string
  params: Record<string, unknown>
  endpoint_id: string
  window_hours: number
  late_policy: 'deliver_marked' | 'suppress'
}

/** config/models.py TriggerRuleConfig, inside the `trigger-rules` document. */
export interface TriggerRuleDoc {
  rule_id: string
  condition: TriggerCondition
  version: string
  enabled?: boolean
  params?: Record<string, unknown>
  endpoint_id: string
  window_hours?: number
  late_policy?: 'deliver_marked' | 'suppress'
}

export interface TriggerDryRunResponse {
  rule_id: string
  window_days: number
  subjects_evaluated: number
  would_fire: number
  target_problem: string | null
  sample: { subject_id: string; pseudonym: string | null; evidence: EvidencePointer[] }[]
}

export interface FiringView {
  firing_id: string
  rule_id: string
  rule_version: string
  subject_id: string
  window_key: string
  fired_at: ISODate
  late: boolean
  retracted_at: ISODate | null
  evidence: EvidencePointer[]
}

export type DeliveryStatus = 'pending' | 'delivered' | 'failing' | 'dead'

export interface DeliveryView {
  delivery_id: string
  endpoint_id: string
  event_type: string
  status: DeliveryStatus
  attempts: number
  created_at: ISODate
  next_attempt_at: ISODate | null
  delivered_at: ISODate | null
  last_status_code: number | null
  last_error: string | null
}

/* ---------------- Cell: measurement, review, assistant, usage ---------------- */

export interface RateOut {
  value: number
  low: number
  high: number
  n: number
}

export interface ContextUseBucket {
  day?: string | null
  source_id?: string | null
  /** From the source configuration; "" when a source has none. */
  vendor?: string | null
  channel?: string | null
  view?: string | null
  experiment_group?: string | null
  sessions: number
  no_context: number
  deliveries: number
  not_measured: number
  late_deliveries: number
  deliveries_used: number
  questions: number
  repeated: number
  contradicted: number
  selection_misses: number
  transfers: number
  transfers_unread: number
  recontacts: number
  /** Deliveries whose pack left out what that agent never used. */
  trimmed_deliveries: number
  /** Tokens those packs left out, summed over deliveries: the compiler's estimate. */
  tokens_saved: number
  usage_rate: RateOut | null
  repetition_rate: RateOut | null
  transfer_unread_rate: RateOut | null
  recontact_rate: RateOut | null
}

/** How far sample review confirms one signal of the measurement (contracts/measure.py SignalReview). */
interface SignalReview {
  /** `used`, `repeated`, `contradicted` or `selection_miss`. */
  signal: string
  /** Reviewers' verdicts on marks of this signal. */
  judged: number
  /** Verdicts that say the mark holds. */
  confirmed: number
  /** `confirmed` over `judged`, with its interval; null without verdicts. */
  agreement: RateOut | null
  /** The lower bound of the agreement reached 0.90. */
  contract_ready: boolean
  /** The signal's count in the report's buckets. */
  measured: number
  /** `measured` corrected by the share of marks reviewers confirmed, with that share's interval. */
  calibrated?: { value: number; low: number; high: number } | null
}

/** The agents' prompt cache for one group (contracts/metering.py PromptCacheBucket). */
interface PromptCacheBucket {
  day?: string | null
  source_id?: string | null
  /** From the source configuration; "" when none. */
  vendor?: string | null
  provider?: string | null
  model?: string | null
  /** Agent turns whose model call reported its usage. */
  calls: number
  /** Every input token of those calls, cached ones included. */
  prompt_tokens: number
  /** Input tokens the provider read from its prompt cache. */
  cached_tokens: number
  cache_write_tokens: number
  /** `cached_tokens` over `prompt_tokens`; null without tokens. */
  hit_rate: number | null
  /** An estimate, in millionths of a US dollar; null when no call of the group has a price. */
  estimated_savings_micros: number | null
  /** Calls of models without a price: left out of the estimate. */
  unpriced_calls: number
}

interface ModelPriceOut {
  provider: string
  model: string
  /** USD per million input tokens. */
  input: number
  cached_input: number
  cache_write: number
  table: 'product' | 'space'
}

/** `GET /v1/usage/prompt-cache`: tokens, cache hits and the estimated saving, per group. */
export interface PromptCacheReport {
  since: string
  until: string
  group_by: string[]
  buckets: PromptCacheBucket[]
  prices: ModelPriceOut[]
  prices_as_of: string
  /** Always true: the savings are an estimate, never what a provider billed. */
  estimate: boolean
}

/** One unit an agent never used: a kind of item and its category, like `fact:contract`. */
interface TrimUnitOut {
  unit: string
  kind: string
  category: string | null
  /** Measured deliveries of the window that carried it; none used it. */
  deliveries: number
}

/** What one agent's packs of one view leave out because it never used them (contracts/measure.py). */
export interface ContextTrimOut {
  source_id: string
  view: string
  /** False when the space turned trimming off or the source is exempt. */
  active: boolean
  inactive_reason: 'disabled' | 'opt_out' | 'critical' | null
  units: TrimUnitOut[]
  derived_at: ISODate
  window_days: number
  min_deliveries: number
}

/**
 * What one retrieval channel brought to the per-turn slots of one view, and what the agents did
 * with it (contracts/measure.py SlotChannelUse). A slot two channels ranked counts for both.
 */
export interface SlotChannelUse {
  view: string
  /** `exact`, `values`, `lexical`, `temporal` or `semantic`. */
  slot_channel: string
  /** Measured reads with slots in which the channel ran. */
  turns: number
  /** Of those, reads where it missed its deadline and the slots went without it. */
  skipped: number
  slots: number
  used: number
  repeated: number
  contradicted: number
  usage_rate: RateOut | null
  /** The channel's weight in this view's fusion today. */
  weight: number
  default_weight: number
}

interface WeightRunOut {
  day: string
  computed_at: ISODate
  version: number
  outcome: 'applied' | 'unchanged' | 'no_verdicts' | 'vetoed'
  /** `no_experiment`, `too_small`, `holds`, `worse` or `narrowed`. */
  guard: string | null
}

/** The space's learned fusion weights, moved once a night by the slots' verdicts (contracts/measure.py). */
export interface RetrievalWeightsOut {
  /** Goes up each night a weight moved; 0 while the defaults hold. */
  version: number
  weights: Record<string, Record<string, number>>
  defaults: Record<string, number>
  /** The last nightly runs, newest first. */
  runs: WeightRunOut[]
}

export interface ContextUseReport {
  since: string
  until: string
  group_by: string[]
  buckets: ContextUseBucket[]
  /** The verdicts on the report's sources over its period, or its last 90 days when it is longer. */
  review?: SignalReview[]
  /** Per agent and view, what its packs leave out today for lack of use, with the evidence. */
  trims?: ContextTrimOut[]
  /** By channel: the per-turn slots of the period per view and retrieval channel. */
  slot_channels?: SlotChannelUse[]
  /** The fusion weights the slots use today. */
  retrieval_weights?: RetrievalWeightsOut | null
}

interface ContextUseEntry {
  manifest_hash: string
  source_id: string
  measure_version: string
  channel: string
  view: string
  experiment_group: string
  items: Record<string, unknown>[]
  questions: number
  repeated: number
  used: number
  contradicted: number
  late: boolean
  transfer_unread: boolean | null
  not_measured_reason: string | null
  /** Tokens this delivery's pack left out for lack of use. */
  tokens_saved?: number
  measured_at: ISODate
}

export interface ContextUseDetail {
  conversation_id: string
  entries: ContextUseEntry[]
}

interface ReviewFact {
  fact_id: string
  predicate: string
  value: string
  category: string
  evidence_event_ids: string[]
  sensitive: boolean
}

/** A pending item or a promise the sampled conversation left (contracts/review.py ReviewOpenItem). */
interface ReviewOpenItem {
  item_id: string
  description: string
  owner: 'company' | 'customer' | string
  /** An item the company owns: something it promised. */
  promise: boolean
  status: string
  due_at: ISODate | null
  evidence_event_ids: string[]
}

/** A pattern of the customer that rests on the sampled conversation (contracts/review.py ReviewTrait). */
interface ReviewTrait {
  trait_id: string
  name: string
  value: string
  first_seen: ISODate
  confidence: number
  /** The items of this conversation it rests on. */
  evidence: EvidencePointer[]
}

interface ReviewSignal {
  target_id: string
  manifest_hash: string
  item_id: string
  signal: 'used' | 'repeated' | 'contradicted' | 'selection_miss' | string
}

export interface ReviewSample {
  sample_id: string
  session_id: string
  source_id: string
  channel: string
  condition: string
  purpose: string | null
  created_at: ISODate
  facts: ReviewFact[]
  open_items: ReviewOpenItem[]
  traits: ReviewTrait[]
  signals: ReviewSignal[]
  withheld: number
}

type VerdictTarget = 'fact' | 'open_item' | 'signal' | 'trait'
export interface VerdictIn {
  target: VerdictTarget
  target_id: string
  verdict: string
}

export interface VerdictsResponse {
  sample_id: string
  recorded: number
  reviewed_at: ISODate
}

/** A verdict run as a test against today's memory and measurement (contracts/review.py ReviewCase). */
interface ReviewCase {
  sample_id: string
  target: string
  target_id: string
  verdict: string
  /** `marked`, `not_marked`, `active`, `gone`, `not_measured` or `unknown`. */
  now: string
  passes: boolean | null
}

export interface ReviewCases {
  since: ISODate
  tallies: { target: string; cases: number; passing: number; failing: number }[]
  failing: ReviewCase[]
}

export interface AgreementReport {
  since: ISODate
  targets: { target: string; judged: number; agreed: number; system_agreement: RateOut | null; reviewer_pairs: number; reviewer_agreement: RateOut | null }[]
}

export const ASSIST_TASKS = ['webhook_mapping', 'extraction_schema', 'source_policy', 'trigger_or_trait_rule'] as const
export type AssistTask = (typeof ASSIST_TASKS)[number]

/** contracts/measure.py MeasuredConversation: one measured conversation, to open without typing its id. */
export interface MeasuredConversation {
  conversation_id: string
  source_id: UUID
  channel: string
  ended_at: string
  measured_at: string
  no_context: boolean
  experiment_group: string
}

/** contracts/notify.py DeadTaskOut: work of the space that exhausted its attempts, ids only. */
export interface DeadTask {
  task_id: UUID
  name: string
  profile_id?: UUID | null
}

export interface AssistRequest {
  task: AssistTask
  source_id?: UUID
  description?: string
  /** A mapping's example event; the server masks it on arrival and keeps only the masked copy. */
  example_payload?: Record<string, unknown>
}

export interface AssistRunOut {
  run_id: UUID
  task: AssistTask
  status: 'queued' | 'proposed' | 'delivered' | 'rejected' | 'failed'
  created_at: ISODate
  config_type: string | null
  document: unknown
  diff_hash: string | null
  checks: Record<string, unknown>
  problems: string[]
  diff_id: string | null
}

/* Agent memory (contracts/agent_memory.py): the agents' own working notes, never about a customer. */
export type AgentNoteKind = 'procedure' | 'tool_note' | 'process_note' | 'pitfall'
export type AgentNoteVisibility = 'source' | 'vendor' | 'space'
type AgentNoteStatus = 'active' | 'retired'
export type AgentProposalStatus = 'drafting' | 'pending' | 'approved' | 'rejected' | 'failed'

interface AgentNoteEvidence {
  conversation_id?: string | null
  task_id?: string | null
}

export interface AgentNote {
  note_id: UUID
  source_id: UUID
  visibility: AgentNoteVisibility
  kind: AgentNoteKind
  title: string
  body: string
  tags: string[]
  evidence?: AgentNoteEvidence | null
  origin: 'agent' | 'human' | 'distilled'
  version: number
  supersedes_note_id?: UUID | null
  status: AgentNoteStatus
  valid_until?: string | null
  created_at: string
  created_by: string
}

export interface AgentNoteIn {
  kind: AgentNoteKind
  title: string
  body: string
  tags: string[]
  visibility: AgentNoteVisibility
  source_id: UUID
}

export interface AgentNotePatch {
  kind?: AgentNoteKind
  title?: string
  body?: string
  tags?: string[]
  visibility?: AgentNoteVisibility
}

export interface AgentNoteProposal {
  proposal_id: UUID
  source_id: UUID
  origin: 'distilled' | 'agent'
  kind: AgentNoteKind
  title: string
  body: string
  tags: string[]
  visibility: AgentNoteVisibility
  evidence?: AgentNoteEvidence | null
  status: AgentProposalStatus
  created_at: string
  decided_at?: string | null
  decided_by?: string | null
  note_id?: UUID | null
  problem?: string | null
}

export interface AgentMemorySettingsDoc {
  enabled: boolean
  writes: 'agent' | 'human_only'
}
