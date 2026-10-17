import { coordinationClient } from './coordination'
import { graphClient } from './graph'
import { call, callBlob, newIdempotencyKey, type Transport } from './http'
import { measureClient } from './measure'
import { objectTypesClient } from './objectTypes'
import { turnsClient } from './turns'
import type {
  AgentStateMetaPage,
  AccessChangeOut,
  BindingIn,
  BindingOut,
  InvitationOut,
  InvitationPreviewOut,
  Presence,
  SecondFactorResetOut,
  SourceCreate,
  SsoConnectionIn,
  SsoDiscoverOut,
  SsoExchangeRequest,
  SsoOut,
  SsoStartOut,
  SsoTestOut,
  SsoTestResultOut,
  UserOut,
  AgreementReport,
  ReviewCases,
  BudgetReport,
  ContextLatencyReport,
  CellUsageOut,
  ConfigTypeOut,
  CoverageOut,
  EventsArrived,
  MeOut,
  PreferencesOut,
  RecoveryCodesOut,
  SecondFactorOut,
  SecondFactorRequest,
  SecondFactorStatusOut,
  SourceRevocationOut,
  AssertionOut,
  AssertionState,
  AssistRequest,
  AssistRunOut,
  ChainVerification,
  ConfigType,
  ContextUseDetail,
  ContextUseReport,
  PromptCacheReport,
  DeliveryStatus,
  DeliveryView,
  DiffOut,
  DiffStatus,
  DocumentOut,
  ErasureOut,
  GapReview,
  GapReviewIn,
  GapsReport,
  ExportPackageOut,
  ExportRequest,
  ExportRunOut,
  FactHistory,
  FactRestoreRequest,
  FactViewers,
  FiringView,
  ForgetRequest,
  CorrectionRequest,
  BatchResult,
  HandleIn,
  IssuedKeyOut,
  KeyOut,
  LinkMethod,
  LinkOut,
  LoginRequest,
  OperationOut,
  OptOutResponse,
  Page,
  ParentMethod,
  ParentOut,
  ProfileListed,
  ProfileListQuery,
  ProfileMatch,
  ProfileSearchIn,
  SuggestionOut,
  ProfileMemory,
  ProfileObjects,
  ProfileOut,
  ProjectOut,
  ReceiptLineage,
  ReceiptOut,
  ReceiptQuery,
  ReviewSample,
  RevocationOut,
  Scope,
  SecretRefOut,
  SecretWritten,
  SimulateRequest,
  SimulateResponse,
  SourceOut,
  SubjectKind,
  TenantOut,
  TokenOut,
  TraitDryRunResponse,
  TraitRuleDraft,
  TraitView,
  TriggerDryRunResponse,
  TriggerRuleDraft,
  UsageOut,
  VerdictIn,
  VerdictsResponse,
  VersionOut,
  AssertionMethod,
  AgentNote,
  AgentNoteIn,
  MeasuredConversation,
  DeadTask,
  AgentNotePatch,
  AgentNoteProposal,
  AgentProposalStatus,
} from './types'

const enc = encodeURIComponent
type Opt = { signal?: AbortSignal }

/** Control plane (adapters/inbound/http/control.py): Console people, spaces, sources, keys, configuration. */
export function controlClient(t: Transport) {
  return {
    login: (body: LoginRequest) => call<TokenOut>(t, 'POST', '/v1/auth/login', { body }),
    /** Only for a person whose login answered "second factor enrollment required". */
    secondFactor: (body: SecondFactorRequest) => call<SecondFactorOut>(t, 'POST', '/v1/auth/second-factor', { body }),
    /** Renewal and space switch: roles are read again, so a removed role disappears. */
    token: (spaceId: string) => call<TokenOut>(t, 'POST', '/v1/auth/token', { body: { space_id: spaceId } }),
    tenant: (id: string, o: Opt = {}) => call<TenantOut>(t, 'GET', `/v1/tenants/${enc(id)}`, o),
    me: (o: Opt = {}) => call<MeOut>(t, 'GET', '/v1/users/me', o),
    /** The person's own Console state (tutorials done, welcome seen), kept across browsers. */
    preferences: (o: Opt = {}) => call<PreferencesOut>(t, 'GET', '/v1/users/me/preferences', o),
    /** Writes one key whole and answers every key; at most 16 keys of 4096 bytes of JSON each. */
    setPreference: (key: string, value: unknown) => call<PreferencesOut>(t, 'PUT', `/v1/users/me/preferences/${enc(key)}`, { body: { value } }),
    secondFactorStatus: (o: Opt = {}) => call<SecondFactorStatusOut>(t, 'GET', '/v1/users/me/second-factor', o),
    /** Ten new recovery codes; the earlier ones stop working. A wrong password (or code) is 403, which keeps the session. */
    newRecoveryCodes: (proof: Presence) => call<RecoveryCodesOut>(t, 'POST', '/v1/users/me/recovery-codes', { body: proof }),
    /** A new authenticator secret, pending: the current app keeps working until the new one is confirmed. */
    startSecondFactorChange: (proof: Presence) => call<SecondFactorOut>(t, 'POST', '/v1/users/me/second-factor', { body: proof }),
    confirmSecondFactorChange: (totp: string) => call<RecoveryCodesOut>(t, 'POST', '/v1/users/me/second-factor/confirm', { body: { totp } }),
    configTypes: (o: Opt = {}) => call<ConfigTypeOut[]>(t, 'GET', '/v1/config/types', o),
    projects: (o: Opt = {}) => call<ProjectOut[]>(t, 'GET', '/v1/projects', o),

    sources: (spaceId: string, o: Opt = {}) => call<SourceOut[]>(t, 'GET', '/v1/sources', { ...o, query: { space_id: spaceId } }),
    createSource: (body: SourceCreate) => call<SourceOut>(t, 'POST', '/v1/sources', { body, idempotencyKey: newIdempotencyKey() }),
    keys: (sourceId: string, o: Opt = {}) => call<KeyOut[]>(t, 'GET', `/v1/sources/${enc(sourceId)}/keys`, o),
    issueKey: (sourceId: string, scopes: Scope[] | null) =>
      call<IssuedKeyOut>(t, 'POST', `/v1/sources/${enc(sourceId)}/keys`, { body: { scopes }, idempotencyKey: newIdempotencyKey() }),
    rotateKey: (sourceId: string, keyId: string, graceSeconds: number | null) =>
      call<IssuedKeyOut>(t, 'POST', `/v1/sources/${enc(sourceId)}/keys/${enc(keyId)}/rotate`, {
        body: { grace_seconds: graceSeconds },
        idempotencyKey: newIdempotencyKey(),
      }),
    revokeKey: (sourceId: string, keyId: string, reason: string) =>
      call<RevocationOut>(t, 'POST', `/v1/sources/${enc(sourceId)}/keys/${enc(keyId)}/revoke`, { body: { reason }, idempotencyKey: newIdempotencyKey() }),
    /** Makes the cut permanent: the next signed snapshot and the revocation feed carry it. */
    revokeSource: (sourceId: string, reason: string, idempotencyKey: string) =>
      call<RevocationOut>(t, 'POST', `/v1/sources/${enc(sourceId)}/revoke`, { body: { reason }, idempotencyKey }),

    usage: (since: string, until: string, o: Opt = {}) => call<UsageOut>(t, 'GET', '/v1/usage', { ...o, query: { since, until } }),
    /** One UTC month per space of the tenant, the file invoices are made from. */
    usageStatementCsv: (month: string) => callBlob(t, `/v1/usage/statement?month=${enc(month)}&format=csv`),

    /* People of the tenant (admin) */
    users: (o: Opt = {}) => call<UserOut[]>(t, 'GET', '/v1/users', o),
    /** Without a password: the answer carries the invitation, shown once; the person sets up the authenticator at the first sign-in. */
    createUser: (email: string, roles: BindingIn[]) => call<UserOut>(t, 'POST', '/v1/users', { body: { email, roles, with_totp: false } }),
    setRoles: (userId: string, roles: BindingIn[], reason: string) =>
      call<BindingOut[]>(t, 'PUT', `/v1/users/${enc(userId)}/roles`, { body: { roles, reason: reason.trim() || null } }),
    deactivateUser: (userId: string, reason: string) => call<UserOut>(t, 'POST', `/v1/users/${enc(userId)}/deactivate`, { body: { reason } }),
    reactivateUser: (userId: string, reason: string) => call<UserOut>(t, 'POST', `/v1/users/${enc(userId)}/reactivate`, { body: { reason } }),
    newInvitation: (userId: string) => call<InvitationOut>(t, 'POST', `/v1/users/${enc(userId)}/invitation`, { body: {} }),
    resetSecondFactor: (userId: string, reason: string) =>
      call<SecondFactorResetOut>(t, 'POST', `/v1/users/${enc(userId)}/second-factor/reset`, { body: { reason } }),
    userHistory: (userId: string, o: Opt = {}) => call<AccessChangeOut[]>(t, 'GET', `/v1/users/${enc(userId)}/history`, { ...o, query: { limit: 50 } }),

    /* Invitations (public: the token is the credential, and it travels only in bodies) */
    invitationPreview: (token: string) => call<InvitationPreviewOut>(t, 'POST', '/v1/auth/invitation/preview', { body: { token } }),
    acceptInvitation: (token: string, password: string) => call<{ email: string }>(t, 'POST', '/v1/auth/invitation', { body: { token, password } }),

    /* Single sign-on: the tenant's connection (admin) */
    sso: (o: Opt = {}) => call<SsoOut>(t, 'GET', '/v1/sso', o),
    saveSso: (body: SsoConnectionIn) => call<SsoOut>(t, 'PUT', '/v1/sso', { body }),
    removeSso: (reason: string) => call<void>(t, 'DELETE', '/v1/sso', { body: { reason } }),
    startSsoTest: () => call<SsoTestOut>(t, 'POST', '/v1/sso/test', { body: {} }),
    ssoTest: (testId: string, o: Opt = {}) => call<SsoTestResultOut>(t, 'GET', `/v1/sso/tests/${enc(testId)}`, o),

    /* Single sign-on: the sign-in (public) */
    ssoDiscover: (email: string) => call<SsoDiscoverOut>(t, 'POST', '/v1/auth/sso/discover', { body: { email } }),
    ssoStart: (email: string, binding: string) => call<SsoStartOut>(t, 'POST', '/v1/auth/sso/start', { body: { email, binding } }),
    ssoExchange: (body: SsoExchangeRequest) => call<TokenOut>(t, 'POST', '/v1/auth/sso/exchange', { body }),
    ssoSecondFactor: (code: string, verifier: string) => call<SecondFactorOut>(t, 'POST', '/v1/auth/sso/second-factor', { body: { code, verifier } }),

    configDocument: (spaceId: string, type: ConfigType, o: Opt = {}) =>
      call<DocumentOut>(t, 'GET', `/v1/config/${enc(type)}`, { ...o, query: { space_id: spaceId } }),
    diffs: (spaceId: string, status: DiffStatus | null, o: Opt = {}) =>
      call<DiffOut[]>(t, 'GET', '/v1/config/diffs', { ...o, query: { space_id: spaceId, status } }),
    submitDiff: (spaceId: string, type: ConfigType, document: unknown, reason: string) =>
      call<DiffOut>(t, 'POST', '/v1/config/diffs', { body: { space_id: spaceId, type, document, reason } }),
    approveDiff: (diffId: string) => call<DiffOut>(t, 'POST', `/v1/config/diffs/${enc(diffId)}/approve`, { body: {} }),
    rejectDiff: (diffId: string) => call<DiffOut>(t, 'POST', `/v1/config/diffs/${enc(diffId)}/reject`, { body: {} }),
    rollbackDiff: (diffId: string, reason: string) => call<DiffOut>(t, 'POST', `/v1/config/diffs/${enc(diffId)}/rollback`, { body: { reason } }),
    history: (spaceId: string, type: ConfigType | null, o: Opt = {}) =>
      call<VersionOut[]>(t, 'GET', '/v1/config/history', { ...o, query: { space_id: spaceId, type, limit: 50 } }),
    secretRefs: (spaceId: string, o: Opt = {}) => call<SecretRefOut[]>(t, 'GET', '/v1/secret-refs', { ...o, query: { space_id: spaceId } }),
    putSecretRef: (spaceId: string, ref: string, kind: string, version: number) =>
      call<SecretRefOut>(t, 'POST', '/v1/secret-refs', { body: { space_id: spaceId, ref, kind, version } }),
  }
}

/** Data API of the space's cell (niadra-admin): customer data, receipts, privacy, rules in execution. */
export function cellClient(t: Transport) {
  const idem = () => newIdempotencyKey()
  return {
    /* Customers and identity */
    /** The customers of the space by latest activity, with masked identifiers; one admin receipt per page. */
    listProfiles: (q: ProfileListQuery, o: Opt = {}) => call<Page<ProfileListed>>(t, 'GET', '/v1/profiles', { ...o, query: { ...q } }),
    /** The query (pseudonym or identifier) goes in the body, never in a URL. */
    searchProfiles: (input: ProfileSearchIn, o: Opt = {}) =>
      call<{ items: ProfileMatch[] }>(t, 'POST', '/v1/profiles/search', { ...o, body: { limit: 20, ...input } }),
    profile: (id: string, o: Opt = {}) => call<ProfileOut>(t, 'GET', `/v1/profiles/${enc(id)}`, o),
    /** The timeline is paged: `timeline_cursor` is the `timeline_next` of the page before. */
    memory: (id: string, o: Opt = {}, q: { timeline_cursor?: string } = {}) =>
      call<ProfileMemory>(t, 'GET', `/v1/profiles/${enc(id)}/memory`, { ...o, query: { ...q } }),
    /** Objects and actions are paged apart, each by the `*_next` of the page before. */
    objects: (id: string, o: Opt = {}, q: { objects_cursor?: string; actions_cursor?: string } = {}) =>
      call<ProfileObjects>(t, 'GET', `/v1/profiles/${enc(id)}/objects`, { ...o, query: { ...q } }),
    agentStates: (id: string, o: Opt = {}) =>
      call<AgentStateMetaPage>(t, 'GET', `/v1/profiles/${enc(id)}/agent-state`, o),
    simulate: (body: SimulateRequest, o: Opt = {}) => call<SimulateResponse>(t, 'POST', '/v1/policy/simulate', { ...o, body }),

    assertions: (q: { profile_id?: string; state?: AssertionState; cursor?: string | null }, o: Opt = {}) =>
      call<Page<AssertionOut>>(t, 'GET', '/v1/identity/assertions', { ...o, query: { ...q, limit: 50 } }),
    createAssertion: (body: { handles: [HandleIn, HandleIn]; method: AssertionMethod; subject_kind: SubjectKind; reason?: string }) =>
      call<OperationOut>(t, 'POST', '/v1/identity/assertions', { body, idempotencyKey: idem() }),
    retractAssertion: (id: string, reason: string) =>
      call<OperationOut>(t, 'POST', `/v1/identity/assertions/${enc(id)}/retract`, { body: { reason }, idempotencyKey: idem() }),
    merge: (profileIds: [string, string], force: boolean, reason: string) =>
      call<OperationOut>(t, 'POST', '/v1/identity/merge', { body: { profile_ids: profileIds, force, reason: reason || null }, idempotencyKey: idem() }),
    unmerge: (profileId: string, handleIds: string[], reason: string) =>
      call<OperationOut>(t, 'POST', '/v1/identity/unmerge', {
        body: { profile_id: profileId, handle_ids: handleIds, reason: reason || null },
        idempotencyKey: idem(),
      }),
    /** Retracts the assertions that joined the profile and lets the graph decide where it splits. */
    unmergeAssertions: (profileId: string, assertionIds: string[], reason: string) =>
      call<OperationOut>(t, 'POST', '/v1/identity/unmerge', {
        body: { profile_id: profileId, assertion_ids: assertionIds, reason: reason || null },
        idempotencyKey: idem(),
      }),
    blockHandle: (handle: HandleIn, reason: string) =>
      call<OperationOut>(t, 'POST', '/v1/identity/handles/block', { body: { handle, reason: reason || null }, idempotencyKey: idem() }),
    createLink: (body: { person: HandleIn; organization: HandleIn; role: string; can_see_contacts: boolean; method: LinkMethod; valid_from?: string; valid_to?: string }) =>
      call<LinkOut>(t, 'POST', '/v1/identity/links', { body, idempotencyKey: idem() }),
    suggestions: (cursor: string | null, o: Opt = {}) =>
      call<Page<SuggestionOut>>(t, 'GET', '/v1/identity/suggestions', { ...o, query: { cursor, limit: 50 } }),
    acceptSuggestion: (id: string, reason: string) =>
      call<OperationOut>(t, 'POST', `/v1/identity/suggestions/${enc(id)}/accept`, { body: { reason: reason || null }, idempotencyKey: idem() }),
    dismissSuggestion: (id: string) =>
      call<SuggestionOut>(t, 'POST', `/v1/identity/suggestions/${enc(id)}/dismiss`, { body: {}, idempotencyKey: idem() }),
    endLink: (linkId: string, validTo: string | null) =>
      call<LinkOut>(t, 'POST', `/v1/identity/links/${enc(linkId)}/end`, { body: { valid_to: validTo }, idempotencyKey: idem() }),
    setParent: (body: { organization: HandleIn; parent: HandleIn; method: ParentMethod; valid_from?: string; valid_to?: string }) =>
      call<ParentOut>(t, 'POST', '/v1/identity/parents', { body, idempotencyKey: idem() }),
    endParent: (parentLinkId: string, validTo: string | null) =>
      call<ParentOut>(t, 'POST', `/v1/identity/parents/${enc(parentLinkId)}/end`, { body: { valid_to: validTo }, idempotencyKey: idem() }),

    /* Receipts and lineage */
    /** The receipt filters go in the body: a conversation id may be a phone number or an e-mail, and never travels in a URL. */
    searchReceipts: (q: ReceiptQuery, o: Opt = {}) => call<Page<ReceiptOut>>(t, 'POST', '/v1/receipts/search', { ...o, body: { ...q } }),
    verifyChain: (day: string, o: Opt = {}) => call<ChainVerification>(t, 'GET', '/v1/receipts/verify', { ...o, query: { day } }),
    lineage: (receiptId: string, o: Opt = {}) => call<ReceiptLineage>(t, 'GET', `/v1/lineage/receipt/${enc(receiptId)}`, o),
    factViewers: (factId: string, o: Opt = {}) => call<FactViewers>(t, 'GET', `/v1/lineage/fact/${enc(factId)}`, o),
    factHistory: (profileId: string, factId: string, o: Opt = {}) =>
      call<FactHistory>(t, 'GET', `/v1/profiles/${enc(profileId)}/facts/${enc(factId)}/history`, o),

    /* Privacy and exports */
    forget: (body: ForgetRequest) => call<ErasureOut>(t, 'POST', '/v1/forget', { body, idempotencyKey: idem() }),
    correct: (body: CorrectionRequest) => call<BatchResult>(t, 'POST', '/v1/corrections', { body, idempotencyKey: idem() }),
    restoreFact: (factId: string, body: FactRestoreRequest) =>
      call<BatchResult>(t, 'POST', `/v1/facts/${enc(factId)}/restore`, { body, idempotencyKey: idem() }),
    erasures: (q: { status?: ErasureOut['status']; cursor?: string | null }, o: Opt = {}) =>
      call<Page<ErasureOut>>(t, 'GET', '/v1/forget', { ...o, query: { ...q, limit: 50 } }),
    exportPackage: (body: ExportRequest) => call<ExportPackageOut>(t, 'POST', '/v1/export', { body }),
    downloadExport: (runId: string) => callBlob(t, `/v1/exports/${enc(runId)}/download`),
    exportRuns: (q: { schedule_id?: string; cursor?: string | null }, o: Opt = {}) =>
      call<Page<ExportRunOut>>(t, 'GET', '/v1/exports/runs', { ...o, query: { ...q, limit: 50 } }),
    putDestinationCredential: (scheduleId: string, value: string) =>
      call<SecretWritten>(t, 'PUT', `/v1/exports/destinations/${enc(scheduleId)}/credential`, { body: { value } }),
    putSecret: (kind: 'webhook' | 'model', secretId: string, value: string) =>
      call<SecretWritten>(t, 'PUT', `/v1/secrets/${enc(kind)}/${enc(secretId)}`, { body: { value } }),

    /* Patterns, triggers, webhooks */
    retractTrait: (traitId: string) => call<TraitView>(t, 'POST', `/v1/traits/${enc(traitId)}/retract`, { body: {} }),
    dryRunTraitRule: (body: TraitRuleDraft) => call<TraitDryRunResponse>(t, 'POST', '/v1/traits/rules/dry-run', { body }),
    optOutOfTraits: (profileId: string) => call<OptOutResponse>(t, 'POST', `/v1/profiles/${enc(profileId)}/traits/opt-out`, { body: {} }),
    dryRunTrigger: (body: TriggerRuleDraft) => call<TriggerDryRunResponse>(t, 'POST', '/v1/triggers/dry-run', { body }),
    firings: (q: { rule_id?: string; subject_id?: string; cursor?: string | null }, o: Opt = {}) =>
      call<Page<FiringView>>(t, 'GET', '/v1/triggers/firings', { ...o, query: { ...q, limit: 50 } }),
    deliveries: (q: { status?: DeliveryStatus; endpoint_id?: string; cursor?: string | null }, o: Opt = {}) =>
      call<Page<DeliveryView>>(t, 'GET', '/v1/webhooks/deliveries', { ...o, query: { ...q, limit: 50 } }),
    redeliver: (deliveryId: string) => call<DeliveryView>(t, 'POST', `/v1/webhooks/deliveries/${enc(deliveryId)}/redeliver`, { body: {} }),
    /** One signed delivery marked `webhook.test`, tried once, to an endpoint or a SIEM stream; it shows up in the deliveries. */
    testEndpoint: (endpointId: string) => call<DeliveryView>(t, 'POST', `/v1/webhooks/endpoints/${enc(endpointId)}/test`, { body: {} }),

    /* Sources in the cell: the instant cut and what each one sent */
    /** Every key of the source stops in this cell within seconds; send with the same key as the control plane's cut. */
    revokeSource: (sourceId: string, idempotencyKey: string) =>
      call<SourceRevocationOut>(t, 'POST', `/v1/sources/${enc(sourceId)}/revoke`, { body: {}, idempotencyKey }),
    /** Whether any event arrived in the window: readable by every role, unlike the coverage. */
    eventsArrived: (days: number, o: Opt = {}) => call<EventsArrived>(t, 'GET', '/v1/sources/arrived', { ...o, query: { days } }),
    coverage: (days: number, o: Opt = {}) => call<CoverageOut>(t, 'GET', '/v1/sources/coverage', { ...o, query: { days } }),
    /** Usage per agent (group_by=source), daily. */
    usageBySource: (since: string, until: string, o: Opt = {}) =>
      call<CellUsageOut>(t, 'GET', '/v1/usage', { ...o, query: { since, until, granularity: 'day', group_by: 'source' } }),
    /** Today's AI spend per purpose against the space's daily ceiling, and whether work waits for the next UTC day. */
    budget: (o: Opt = {}) => call<BudgetReport>(t, 'GET', '/v1/usage/budget', o),
    /** The server time of the space's context reads over 24 hours and 7 days, refreshed at most once a minute. */
    contextLatency: (o: Opt = {}) => call<ContextLatencyReport>(t, 'GET', '/v1/usage/context-latency', o),
    /** The agents' prompt cache per agent or vendor, with the estimated saving (dates, YYYY-MM-DD). */
    promptCache: (q: { since: string; until: string; group_by: string[] }, o: Opt = {}) =>
      call<PromptCacheReport>(t, 'GET', '/v1/usage/prompt-cache', { ...o, query: { since: q.since, until: q.until, group_by: q.group_by } }),

    /* Measurement, review, assistant */
    contextUse: (q: { since: string; until: string; group_by: string[] }, o: Opt = {}) =>
      call<ContextUseReport>(t, 'GET', '/v1/context-use', { ...o, query: { since: q.since, until: q.until, group_by: q.group_by } }),
    /** One conversation's measurement, with the id in the body, never in the URL. */
    contextUseDetail: (conversationId: string, o: Opt = {}) =>
      call<ContextUseDetail>(t, 'POST', '/v1/context-use/conversation', { ...o, body: { conversation_id: conversationId } }),
    /** The measured conversations of a period and an agent; the ids come back in the body. */
    measuredConversations: (q: { source_id: string | null; since: string; until: string; cursor: string | null }, o: Opt = {}) =>
      call<Page<MeasuredConversation>>(t, 'GET', '/v1/context-use/conversations', { ...o, query: { ...q, limit: 20 } }),
    deadTasks: (o: Opt = {}) => call<{ items: DeadTask[] }>(t, 'GET', '/v1/operations/dead-tasks', o),
    /** Sends buried tasks back with fresh attempts; a failed erasure resumes where it stopped. */
    replayDeadTasks: (task: string | null) =>
      call<{ replayed: number }>(t, 'POST', '/v1/operations/dead-tasks/replay', { body: task ? { task } : {} }),
    reviewQueue: (o: Opt = {}) => call<{ items: ReviewSample[] }>(t, 'GET', '/v1/review/queue', { ...o, query: { limit: 10 } }),
    verdicts: (sampleId: string, verdicts: VerdictIn[]) =>
      call<VerdictsResponse>(t, 'POST', `/v1/review/${enc(sampleId)}/verdicts`, { body: { verdicts } }),
    agreement: (days: number, o: Opt = {}) => call<AgreementReport>(t, 'GET', '/v1/review/agreement', { ...o, query: { days } }),
    reviewCases: (days: number, o: Opt = {}) => call<ReviewCases>(t, 'GET', '/v1/review/cases', { ...o, query: { days } }),
    /** What customers ask that memory cannot answer, and what it answers every time (13, 6.5); counts only. */
    gaps: (days: number, o: Opt = {}) => call<GapsReport>(t, 'GET', '/v1/insights/gaps', { ...o, query: { days } }),
    reviewGap: (body: GapReviewIn) => call<GapReview>(t, 'POST', '/v1/insights/gaps/review', { body }),
    assist: (body: AssistRequest) => call<AssistRunOut>(t, 'POST', '/v1/assist', { body }),
    assistRuns: (cursor: string | null, o: Opt = {}) => call<Page<AssistRunOut>>(t, 'GET', '/v1/assist', { ...o, query: { cursor, limit: 50 } }),

    /* Agent memory: the agents' own notes (contracts/agent_memory.py) */
    agentNotes: (q: { source_id?: string | null; status?: 'active' | 'retired' | null; cursor?: string | null }, o: Opt = {}) =>
      call<Page<AgentNote>>(t, 'GET', '/v1/agent-memory/notes', { ...o, query: { ...q, status: q.status ?? 'active', limit: 50 } }),
    createAgentNote: (body: AgentNoteIn) => call<{ note?: AgentNote; proposal_id?: string }>(t, 'POST', '/v1/agent-memory/notes', { body }),
    reviseAgentNote: (noteId: string, body: AgentNotePatch) => call<AgentNote>(t, 'PATCH', `/v1/agent-memory/notes/${enc(noteId)}`, { body }),
    retireAgentNote: (noteId: string) => call<AgentNote>(t, 'POST', `/v1/agent-memory/notes/${enc(noteId)}/retire`, { body: {} }),
    agentNoteVersions: (noteId: string, o: Opt = {}) => call<AgentNote[]>(t, 'GET', `/v1/agent-memory/notes/${enc(noteId)}/versions`, o),
    eraseAgentNotes: (sourceId: string) =>
      call<{ source_id: string; deleted: number }>(t, 'DELETE', '/v1/agent-memory/notes', { query: { source_id: sourceId } }),
    agentProposals: (q: { status?: AgentProposalStatus | null }, o: Opt = {}) =>
      call<Page<AgentNoteProposal>>(t, 'GET', '/v1/agent-memory/proposals', { ...o, query: { status: q.status ?? null, limit: 50 } }),
    /** The conversation or task id goes in the body: a channel's id may be a phone number. */
    distill: (body: { source_id: string; conversation_id?: string; task_id?: string }) =>
      call<AgentNoteProposal>(t, 'POST', '/v1/agent-memory/distill', { body }),
    approveProposal: (proposalId: string, body: AgentNotePatch = {}) =>
      call<AgentNote>(t, 'POST', `/v1/agent-memory/proposals/${enc(proposalId)}/approve`, { body }),
    rejectProposal: (proposalId: string) => call<AgentNoteProposal>(t, 'POST', `/v1/agent-memory/proposals/${enc(proposalId)}/reject`, { body: {} }),

    /* Turn records (api/turns.ts) */
    turns: turnsClient(t),
    /* Measurement and the reviewable profile (api/measure.ts) */
    measure: measureClient(t),
    /* The type registry's coverage and drift (api/objectTypes.ts) */
    objectTypes: objectTypesClient(t),
    /* Coordination: ownership, effects, conflicts, budgets, shadow runs (api/coordination.ts) */
    coordination: coordinationClient(t),
    /* The relationship graph around a customer, and the overview of the space (api/graph.ts) */
    graph: graphClient(t),
  }
}

export type ControlClient = ReturnType<typeof controlClient>
export type CellClient = ReturnType<typeof cellClient>
