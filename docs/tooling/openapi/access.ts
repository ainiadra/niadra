/**
 * Who can call each route, taken from the server's authentication dependencies
 * (../server/src/niadra/adapters/inbound/http: auth.py, control.py and the routes). The
 * exported contract declares no security schemes; this table is what the
 * page of each endpoint shows under "Authentication". The documentation test
 * checks that every route has an entry.
 */
/**
 * `features` names the agent features (the space's `features` document) one of which must be on for the
 * route to answer; a space with none of them on answers 404, as the server's `gated()` does.
 */
export type Access = (
  | { k: 'source'; scopes: string[] }
  | { k: 'sourceOrPerson'; scopes: string[]; roles: string[]; anyScope?: boolean }
  | { k: 'governance'; roles: string[] }
  | { k: 'aggregates'; roles: string[] }
  | { k: 'governanceOrScope'; roles: string[]; scope: string }
  | { k: 'person'; roles: string[] }
  | { k: 'webhook' }
  | { k: 'mcp' }
  | { k: 'analytics'; elevated?: boolean }
  | { k: 'public' }
  | { k: 'controlPerson' }
  | { k: 'cell' }
  | { k: 'operator' }
  | { k: 'cellOrOperator' }
  | { k: 'anyKey' }
) & { features?: string[] }

const src = (...scopes: string[]): Access => ({ k: 'source', scopes })
const gov = (...roles: string[]): Access => ({ k: 'governance', roles })
/** Governance, and an `analyst` source's key with the `analytics` scope: a report summed over the space's sources (auth.py `require_aggregates`). */
const agg = (...roles: string[]): Access => ({ k: 'aggregates', roles })
/** Governance, or a source key with one narrow scope (auth.py `governance_or_scope`). */
const govOr = (scope: string, ...roles: string[]): Access => ({ k: 'governanceOrScope', roles, scope })
const person = (...roles: string[]): Access => ({ k: 'person', roles })
const CP: Access = { k: 'controlPerson' }
const either = (scopes: string[], roles: string[], anyScope = false): Access => ({ k: 'sourceOrPerson', scopes, roles, anyScope })
/** The same access, only in a space that turned one of `features` on (server adapters/inbound/http/features.py). */
const on = (access: Access, ...features: string[]): Access => ({ ...access, features })
// The agent features (turns.py, state.py, signals.py, coordinate.py): a key needs the route's scope, a person one of its roles.
const REPLAY = either(['replay'], ['integration', 'security'])
// Agent memory (server adapters/inbound/http/agent_memory.py): a key reads with `agent_memory` or `context`,
// writes with `agent_memory:write`; people act with the integration role, and erase or approve as admins.
const AM_READ = either(['agent_memory', 'context'], ['integration'], true)
const AM_WRITE = either(['agent_memory:write'], ['integration'])
const AM_ADMIN = either(['admin'], [])

export const ACCESS: Record<string, Access> = {
  // Write
  'post /v1/batch': src(),
  'post /v1/media/uploads': src('track'),
  'post /v1/ingest/files': src('track'),
  'get /v1/ingest/files/{import_id}': src('track'),
  'post /v1/ingest/webhook/{source_id}': { k: 'webhook' },
  'get /v1/ingest/webhook/{source_id}': { k: 'webhook' },
  'post /v1/otel/v1/traces': src('track'),
  'post /v1/feedback': src('track'),
  'post /v1/feedback/batch': src('track'),
  'post /v1/ingest/status': src('track'),
  // Context and history
  'post /v1/context': src('context'),
  'post /v1/context/prefetch': src('context'),
  'get /v1/context': src('context'),
  'post /v1/subject-tokens': src('context'),
  'post /mcp': { k: 'mcp' },
  'post /v1/history/search': src('search'),
  'post /v1/history/timeline': src('search'),
  'get /v1/history/timeline': src('search'),
  'get /v1/history/items/{item_id}': src('search'),
  'post /v1/history/open': src('search'),
  'get /v1/history/tools': src('search'),
  'get /v1/objects/{object_type}/{namespace}/{external_id}': src('context'),
  // Agent memory
  'get /v1/agent-memory/block': AM_READ,
  'post /v1/agent-memory/search': AM_READ,
  'get /v1/agent-memory/tools': AM_READ,
  'post /v1/agent-memory/notes': AM_WRITE,
  'get /v1/agent-memory/notes': AM_READ,
  'get /v1/agent-memory/notes/{note_id}': AM_READ,
  'patch /v1/agent-memory/notes/{note_id}': AM_WRITE,
  'post /v1/agent-memory/notes/{note_id}/retire': AM_WRITE,
  'get /v1/agent-memory/notes/{note_id}/versions': AM_READ,
  'delete /v1/agent-memory/notes': AM_ADMIN,
  'get /v1/agent-memory/export': AM_READ,
  'post /v1/agent-memory/distill': AM_WRITE,
  'get /v1/agent-memory/proposals': AM_READ,
  'post /v1/agent-memory/proposals/{proposal_id}/approve': AM_ADMIN,
  'post /v1/agent-memory/proposals/{proposal_id}/reject': AM_ADMIN,
  'get /v1/objects/{object_type}/{namespace}/{external_id}/timeline': src('context'),
  // Identity and profiles
  'get /v1/identity/assertions': gov('security', 'integration'),
  'post /v1/identity/assertions': gov('security', 'integration'),
  'post /v1/identity/assertions/{assertion_id}/retract': gov('security', 'integration'),
  'post /v1/identity/merge': gov('security', 'integration'),
  'post /v1/identity/unmerge': gov('security', 'integration'),
  'post /v1/identity/handles/block': gov('security', 'integration'),
  'post /v1/identity/links': govOr('identity:link', 'security', 'integration'),
  'post /v1/identity/links/{link_id}/end': govOr('identity:link', 'security', 'integration'),
  'get /v1/identity/suggestions': gov('security', 'integration'),
  'post /v1/identity/suggestions/{suggestion_id}/accept': gov('security', 'integration'),
  'post /v1/identity/suggestions/{suggestion_id}/dismiss': gov('security', 'integration'),
  'get /v1/profiles': gov('security', 'integration'),
  'post /v1/profiles/search': gov('security', 'integration'),
  'get /v1/profiles/{profile_id}': gov('security', 'integration'),
  'get /v1/profiles/{profile_id}/memory': gov('security', 'review', 'analysis'),
  'get /v1/profiles/{profile_id}/facts/{fact_id}/history': gov('security', 'review', 'analysis'),
  'get /v1/profiles/{profile_id}/objects': gov('security', 'review', 'analysis', 'integration'),
  'get /v1/graph': gov('security', 'integration'),
  // Privacy and audit
  'post /v1/forget': gov('security'),
  'get /v1/forget': gov('security'),
  'get /v1/forget/{request_id}': gov('security'),
  'post /v1/corrections': gov('security'),
  'post /v1/corrections/batch': gov('security'),
  'post /v1/facts/{fact_id}/restore': gov('security'),
  'post /v1/export': gov('security'),
  'get /v1/exports/{run_id}/download': gov('security'),
  'get /v1/exports/runs': gov('security'),
  'put /v1/exports/destinations/{schedule_id}/credential': gov('security'),
  'get /v1/receipts': gov('security'),
  'post /v1/receipts/search': gov('security'),
  'get /v1/receipts/verify': gov('security'),
  'get /v1/lineage/receipt/{receipt_id}': gov('security'),
  'get /v1/lineage/fact/{fact_id}': gov('security'),
  'post /v1/policy/simulate': gov('security'),
  'put /v1/secrets/{kind}/{secret_id}': gov('security'),
  'get /v1/operations/dead-tasks': gov('security'),
  'post /v1/operations/dead-tasks/replay': gov('security'),
  // Sources
  'get /v1/sources/arrived': gov(),
  'get /v1/sources/coverage': gov('integration', 'analysis'),
  'get /v1/sources/me': { k: 'anyKey' },
  'post /v1/sources/{source_id}/revoke': gov('security'),
  // Patterns, measurement and usage
  'post /v1/traits/{trait_id}/retract': gov('security', 'integration'),
  'post /v1/traits/rules/dry-run': gov('integration', 'analysis'),
  'post /v1/profiles/{profile_id}/traits/opt-out': gov('security'),
  'get /v1/context-use': agg('analysis', 'vendor'),
  'get /v1/context-use/conversations': gov('analysis', 'vendor'),
  'get /v1/context-use/{conversation_id}': gov('analysis', 'vendor'),
  'post /v1/context-use/conversation': gov('analysis', 'vendor'),
  'get /v1/usage': gov('analysis'),
  'get /v1/usage/budget': gov('analysis'),
  'get /v1/usage/context-latency': gov('analysis'),
  'get /v1/usage/prompt-cache': gov('analysis', 'vendor'),
  // Review and assistant
  'get /v1/review/queue': gov('review'),
  'post /v1/review/{sample_id}/verdicts': person('review'),
  'get /v1/review/agreement': gov('review'),
  'get /v1/review/cases': gov('review'),
  'post /v1/assist': gov('integration'),
  'get /v1/assist': gov('integration'),
  'get /v1/assist/{run_id}': gov('integration'),
  // Triggers and webhooks
  'post /v1/triggers/dry-run': gov('integration'),
  'get /v1/triggers/firings': gov('integration', 'analysis'),
  'get /v1/webhooks/deliveries': gov('integration'),
  'post /v1/webhooks/deliveries/{delivery_id}/redeliver': gov('integration'),
  'post /v1/webhooks/endpoints/{endpoint_id}/test': gov('integration'),
  // Base-wide analysis
  'get /v1/insights/schema': { k: 'analytics' },
  'get /v1/insights/overview': { k: 'analytics' },
  'post /v1/insights/aggregate': { k: 'analytics' },
  'post /v1/insights/profiles': { k: 'analytics' },
  'post /v1/insights/search': { k: 'analytics' },
  'post /v1/insights/episodes/sample': { k: 'analytics' },
  'post /v1/insights/context': { k: 'analytics' },
  'post /v1/insights/lineage': { k: 'analytics' },
  'post /v1/insights/reveal': { k: 'analytics', elevated: true },
  'get /v1/insights/gaps': { k: 'analytics' },
  'post /v1/insights/gaps/review': { k: 'analytics' },
  'post /mcp/insights': { k: 'analytics' },
  // Turn records and replay (turns.py)
  'post /v1/turns': on(src('track'), 'turns'),
  'post /v1/turns/promote': on(src('track'), 'turns'),
  'get /v1/turns/{turn_id}': on(REPLAY, 'turns'),
  'post /v1/turns/search': on(REPLAY, 'turns'),
  'get /v1/turns/index/{day}': on(REPLAY, 'turns'),
  'post /v1/replay/cases': on(REPLAY, 'turns'),
  'post /v1/scenarios': on(REPLAY, 'turns'),
  'post /v1/scenarios/from-report': on(REPLAY, 'turns'),
  'get /v1/scenarios': on(REPLAY, 'turns'),
  'get /v1/scenarios/{scenario_id}': on(REPLAY, 'turns'),
  'patch /v1/scenarios/{scenario_id}': on(REPLAY, 'turns'),
  'post /v1/scenario-runs': on(REPLAY, 'turns'),
  'get /v1/scenario-runs/{run_id}': on(REPLAY, 'turns'),
  'get /v1/changes': on(REPLAY, 'turns'),
  'get /v1/data-issues': on(gov('integration'), 'turns', 'state'),
  'post /v1/data-issues/{issue_id}/ack': on(gov('integration'), 'turns', 'state'),
  'get /v1/intake/failed': gov('integration'),
  'post /v1/intake/failed/retry': gov('integration'),
  'get /v1/notifications': on({ k: 'anyKey' }, 'notifications'),
  // Typed state (state.py)
  'get /v1/sdk/profile': { k: 'anyKey' },
  'post /v1/objects/push': on(src('state:push'), 'state'),
  'post /v1/objects/snapshot': on(src('state:push'), 'state'),
  'get /v1/objects/coverage': on(gov('integration'), 'state'),
  'post /v1/types/fingerprint': on(src('state:push'), 'state'),
  'post /v1/state/view': on(src('context'), 'state'),
  'post /v1/state/read': on(src('context'), 'state'),
  'post /v1/state/verify': on(src('context'), 'state'),
  'get /v1/state/refresh-requests': on(src('state:push'), 'state'),
  'post /v1/state/refresh-requests/{request_id}/release': on(src('state:push'), 'state'),
  'post /v1/content/{ref}/release': on(gov('security'), 'state'),
  // Working memory (state.py)
  'put /v1/agent-state': on(src('agent_state'), 'agent_state'),
  'post /v1/agent-state/read': on(src('agent_state'), 'agent_state'),
  'get /v1/profiles/{profile_id}/agent-state': on(gov('security', 'review', 'analysis', 'integration'), 'agent_state'),
  // Signals and constraints (signals.py)
  'post /v1/constraints': on(src('context'), 'signals'),
  'get /v1/profiles/{profile_id}/inferences': on(gov('security'), 'signals'),
  'post /v1/profiles/{profile_id}/inferences/{key}/correct': on(gov('security'), 'signals'),
  'delete /v1/profiles/{profile_id}/inferences/{key}': on(gov('security'), 'signals'),
  'post /v1/profiles/{profile_id}/review-requests': on(gov('security'), 'signals', 'coordination'),
  'get /v1/review-requests': on(gov('security'), 'signals', 'coordination'),
  'post /v1/review-requests/{request_id}/resolve': on(gov('security'), 'signals', 'coordination'),
  'post /v1/legal-holds': on(gov('security'), 'legal_holds'),
  'post /v1/legal-holds/{hold_id}/release': on(gov('security'), 'legal_holds'),
  // Coordination (coordinate.py)
  'post /v1/coordination/check': on(src('coordinate'), 'coordination'),
  'post /v1/coordination/check/batch': on(src('coordinate'), 'coordination'),
  'post /v1/coordination/declare': on(src('coordinate'), 'coordination'),
  'post /v1/coordination/claims': on(src('coordinate'), 'coordination'),
  'post /v1/coordination/claims/{claim_id}/release': on(src('coordinate'), 'coordination'),
  'post /v1/coordination/effects': on(src('coordinate'), 'coordination'),
  'post /v1/coordination/effects/{effect_id}/settle': on(src('coordinate'), 'coordination'),
  'post /v1/handoffs': on(src('coordinate'), 'coordination'),
  'get /v1/handoffs/{handoff_id}': on(src('coordinate'), 'coordination'),
  'post /v1/handoffs/{handoff_id}/accept': on(src('coordinate'), 'coordination'),
  'post /v1/handoffs/{handoff_id}/outcome': on(src('coordinate'), 'coordination'),
  'get /v1/suppressions': on(src('coordinate'), 'coordination'),
  'get /v1/suppressions/salt': on(src('coordinate'), 'coordination'),
  'get /.well-known/niadra-contact-keys.json': { k: 'public' },
  'get /v1/coordination/reports': on(gov('analysis', 'security'), 'coordination'),
  'get /v1/coordination/overview': on(gov('analysis', 'security'), 'coordination'),
  'post /v1/coordination/shadow': on(gov('analysis', 'security'), 'coordination'),
  // Outcomes and experiments (signals.py)
  'get /v1/measure/outcomes': on(gov('analysis'), 'measurement'),
  'get /v1/measure/attribution': on(agg('analysis'), 'measurement'),
  'post /v1/measure/reconcile/uploads': on(gov('analysis'), 'measurement'),
  'post /v1/measure/reconcile': on(gov('analysis'), 'measurement'),
  'get /v1/measure/interleaving': on(agg('analysis'), 'measurement'),
  'get /v1/measure/experiments': on(agg('analysis'), 'measurement'),
  'post /v1/measure/power': on(agg('analysis'), 'measurement'),
  'post /v1/measure/counterfactual-runs': on(either(['replay'], ['analysis']), 'measurement'),
  'get /v1/measure/counterfactual-runs': on(either(['replay'], ['analysis']), 'measurement'),
  'get /v1/measure/counterfactual-runs/{run_id}': on(either(['replay'], ['analysis']), 'measurement'),
  'get /v1/measure/unmet-demand': on(agg('analysis'), 'measurement'),
  // Control
  'post /v1/auth/login@control': { k: 'public' },
  'post /v1/auth/second-factor@control': { k: 'public' },
  'post /v1/auth/token@control': CP,
  'get /.well-known/jwks.json@control': { k: 'public' },
  'get /v1/users/me@control': CP,
  'get /v1/users/me/preferences@control': CP,
  'put /v1/users/me/preferences/{key}@control': CP,
  'get /v1/users/me/second-factor@control': CP,
  'post /v1/users/me/recovery-codes@control': CP,
  'post /v1/users/me/second-factor@control': CP,
  'post /v1/users/me/second-factor/confirm@control': CP,
  'get /v1/users@control': CP,
  'post /v1/users@control': CP,
  'put /v1/users/{user_id}/roles@control': CP,
  'post /v1/users/{user_id}/second-factor/reset@control': CP,
  'post /v1/users/{user_id}/deactivate@control': CP,
  'post /v1/users/{user_id}/reactivate@control': CP,
  'post /v1/users/{user_id}/invitation@control': CP,
  'get /v1/users/{user_id}/history@control': CP,
  'post /v1/auth/invitation/preview@control': { k: 'public' },
  'post /v1/auth/invitation@control': { k: 'public' },
  'post /v1/auth/sso/discover@control': { k: 'public' },
  'post /v1/auth/sso/start@control': { k: 'public' },
  'get /v1/auth/sso/oidc/callback@control': { k: 'public' },
  'get /v1/auth/sso/saml/{tenant_id}/metadata@control': { k: 'public' },
  'post /v1/auth/sso/saml/{tenant_id}/acs@control': { k: 'public' },
  'post /v1/auth/sso/exchange@control': { k: 'public' },
  'post /v1/auth/sso/second-factor@control': { k: 'public' },
  'get /v1/sso@control': CP,
  'put /v1/sso@control': CP,
  'delete /v1/sso@control': CP,
  'post /v1/sso/test@control': CP,
  'get /v1/sso/tests/{test_id}@control': CP,
  'get /v1/roles@control': CP,
  'post /v1/tenants@control': { k: 'operator' },
  'get /v1/tenants/{tenant_id}@control': CP,
  'get /v1/projects@control': CP,
  'post /v1/projects@control': CP,
  'get /v1/spaces/{space_id}@control': CP,
  'delete /v1/spaces/{space_id}@control': CP,
  'get /v1/spaces/{space_id}/deletion-receipt@control': CP,
  'get /v1/sources@control': CP,
  'post /v1/sources@control': CP,
  'get /v1/sources/{source_id}/keys@control': CP,
  'post /v1/sources/{source_id}/keys@control': CP,
  'post /v1/sources/{source_id}/keys/{key_id}/rotate@control': CP,
  'post /v1/sources/{source_id}/keys/{key_id}/revoke@control': CP,
  'post /v1/sources/{source_id}/revoke@control': CP,
  'get /v1/secret-refs@control': CP,
  'post /v1/secret-refs@control': CP,
  'get /v1/config/types@control': CP,
  'get /v1/config/{type}@control': CP,
  'get /v1/config/diffs@control': CP,
  'post /v1/config/diffs@control': CP,
  'post /v1/config/diffs/{diff_id}/approve@control': CP,
  'post /v1/config/diffs/{diff_id}/reject@control': CP,
  'post /v1/config/diffs/{diff_id}/rollback@control': CP,
  'get /v1/config/history@control': CP,
  'post /v1/config/proposals@control': { k: 'cell' },
  'post /v1/config/observed@control': { k: 'cell' },
  'get /v1/usage@control': CP,
  'get /v1/usage/statement@control': CP,
  'get /v1/cells@control': { k: 'cellOrOperator' },
  'post /v1/cells@control': { k: 'operator' },
  'get /v1/spaces/{space_id}/placement@control': CP,
  'post /v1/spaces/{space_id}/placement@control': { k: 'operator' },
  'post /v1/spaces/{space_id}/placement/complete@control': { k: 'operator' },
  'put /v1/quotas@control': { k: 'operator' },
  'get /v1/snapshot/{cell_id}@control': { k: 'cell' },
  'get /v1/revocations@control': { k: 'cell' },
  'get /v1/access-changes@control': { k: 'cell' },
  'post /v1/usage@control': { k: 'cell' },
  'post /v1/spaces/{space_id}/deletion-receipt@control': { k: 'cell' },
  'put /v1/spaces/{space_id}/kms-key@control': { k: 'operator' },
}

/** The table key: the two APIs have identical routes (`GET /v1/usage`), so the control ones carry the suffix. */
export const accessKey = (spec: 'cell' | 'control', method: string, path: string) => `${method} ${path}${spec === 'control' ? '@control' : ''}`
