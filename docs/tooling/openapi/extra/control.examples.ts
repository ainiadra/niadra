import type { OpExtra } from '../model'

/**
 * Examples of the control API, keyed `control:<method> <path>`. Tenant Acme, project
 * Support, spaces acme-prod and acme-sandbox in us-east-2, Ana (admin) and Rui (security).
 */
const TENANT = '0192f0a0-1b2c-7d3e-8f40-5a6b7c8d9e01'
const PROJECT = '0192f0a0-2c3d-7e4f-9a50-6b7c8d9e0f12'
const PROD = '0192f0a0-3d4e-7f50-8b61-7c8d9e0f1a23'
const SANDBOX = '0192f0a0-3d4e-7f50-8b61-7c8d9e0f1a24'
const ANA = '0192f0a0-4e5f-7061-9c72-8d9e0f1a2b34'
const RUI = '0192f0a0-4e5f-7061-9c72-8d9e0f1a2b35'
const BILLING = '0192f0a0-5f60-7172-8d83-9e0f1a2b3c45'
const CRM = '0192f0a0-5f60-7172-8d83-9e0f1a2b3c46'
const DIFF = '0192f7b2-6071-7283-9e94-0f1a2b3c4d56'
const DIFF2 = '0192f7b2-6071-7283-9e94-0f1a2b3c4d57'
const MOVE = '0192f7c3-7182-7394-8fa5-1a2b3c4d5e67'
const RESET = '0192f7d4-8293-74a5-9fb6-2b3c4d5e6f78'
// The sandbox space deleted: the receipt as the cell drafted it, and the SHA-256 of its canonical JSON.
const DELETED_AT = '2026-09-24T15:02:00Z'
const RECEIPT_DOC = {
  space_id: SANDBOX,
  cell_id: 'cell-1',
  db_name: 's_0192f0a03d4e7f508b617c8d9e0f1a24',
  dropped_at: '2026-09-24T15:02:07+00:00',
  dek_versions_destroyed: [1],
  objects_deleted: 184,
  backups_deleted: 7,
  secrets_deleted: 2,
  instance_snapshot_expiry: '2026-10-29T15:02:07+00:00',
}
const RECEIPT_HASH = '444231a958691293872e18e8de0fad4d0e2de2fec1c66cad1b2e56503f64db37'
const deletionReceipt = {
  ...RECEIPT_DOC,
  dropped_at: '2026-09-24T15:02:07Z',
  instance_snapshot_expiry: '2026-10-29T15:02:07Z',
  receipt_hash: RECEIPT_HASH,
  received_at: '2026-09-24T15:02:09Z',
}
const RECOVERY_CODES = ['7KQ2M-XA9BD', 'H3VNP-8R2TW', 'C4ZE6-JM7QK', 'P9WXT-2BD5N', 'R8HCV-6KMZ3', 'T2NQA-WV47H', 'Y6DJ9-QX3PC', 'B5MRK-HT8WZ', 'E7GXN-4PQ2V', 'W3ZKC-9NBHD']
const NEW_SECRET = 'KRSXG5CTMVRXEZLUKRSXG5CTMVRXEZLU'
const NINA = '0192f0a0-4e5f-7061-9c72-8d9e0f1a2b36'
const CONNECTION = '0192f8e5-93a4-75b6-8fc7-3c4d5e6f7a89'
const TEST = '0192f8e5-93a4-75b6-8fc7-3c4d5e6f7a90'
const CHANGE = '0192f8e5-93a4-75b6-8fc7-3c4d5e6f7a91'
const INVITATION_TOKEN = 'Q2hvb3NlIGEgcGFzc3dvcmQgb25jZS4gTm90IGEgc2Vzc2lvbi4'
const CONTROL = 'https://control.api.niadra.com'
const SERVICE_PROVIDER = {
  oidc_redirect_uri: `${CONTROL}/v1/auth/sso/oidc/callback`,
  saml_entity_id: `urn:niadra:sso:${TENANT}`,
  saml_acs_url: `${CONTROL}/v1/auth/sso/saml/${TENANT}/acs`,
  saml_metadata_url: `${CONTROL}/v1/auth/sso/saml/${TENANT}/metadata`,
}
const OIDC_CONNECTION = {
  connection_id: CONNECTION,
  protocol: 'oidc',
  domains: ['acme.example'],
  enabled: true,
  enforced: false,
  default_role: null,
  role_mappings: [
    { group: 'niadra-admins', role: 'admin' },
    { group: 'cx-quality', role: 'review' },
  ],
  groups_attribute: 'groups',
  require_second_factor: false,
  oidc: { issuer: 'https://login.acme.example', client_id: 'niadra-console', scopes: ['openid', 'email', 'profile', 'groups'], client_secret_set: true },
  saml: null,
  created_at: '2026-09-24T10:00:00Z',
  updated_at: '2026-09-24T10:00:00Z',
  updated_by: ANA,
}
const person = (over: Record<string, unknown>) => ({
  active: true,
  last_login_at: null,
  totp_uri: null,
  created_at: '2026-09-01T12:00:00Z',
  deactivated_at: null,
  password_set: true,
  sso: false,
  last_sso_login_at: null,
  second_factor_confirmed_at: null,
  recovery_codes_left: 0,
  invitation_expires_at: null,
  invitation: null,
  ...over,
})
const TOKEN = 'eyJhbGciOiJFZERTQSIsImtpZCI6ImN0bC0yMDI2LTA5In0.eyJzdWIiOiIwMTkyZjBhMC00ZTVmIiwiZXhwIjoxNzU4NTYyMTIzfQ.Q2sX...'
const KEY_SECRET = 'nia_sk_live_us-east-2_acme-prod_tt7vkavsfpfm6ov2_k2a3NlsIBfPrzbrdHAcP9e1nwGV4mvNghJEwcDf6'

const prodSpace = { space_id: PROD, project_id: PROJECT, environment: 'production', slug: 'acme-prod', region: 'us-east-2', cell_id: 'cell-1' }
const sandboxSpace = { space_id: SANDBOX, project_id: PROJECT, environment: 'sandbox', slug: 'acme-sandbox', region: 'us-east-2', cell_id: 'cell-1' }
const project = { project_id: PROJECT, name: 'Support', slug: 'support', region: 'us-east-2', spaces: [prodSpace, sandboxSpace] }
const tenant = { tenant_id: TENANT, name: 'Acme', slug: 'acme', mfa_required: true, created_at: '2026-08-01T12:00:00Z' }
const billingSource = {
  source_id: BILLING,
  space_id: PROD,
  name: 'Billing agent',
  audience: 'internal_agent',
  vendor: 'in-house',
  channel: 'erp',
  purposes: ['billing'],
  verification_ceiling: 'no_customer',
  confirms_before_acting: false,
  navigation_enabled: true,
  trusted_action_ops: ['credit'],
  critical: false,
  revoked_at: null,
}
const key = (id: string, created: string) => ({
  key_id: id,
  source_id: BILLING,
  scopes: ['context', 'search', 'track', 'act'],
  status: 'active',
  created_at: created,
  expires_at: null,
  revoked_at: null,
})
const webhookDoc = [
  {
    endpoint_id: 'whe_crm',
    url: 'https://crm.acme.example/hooks/niadra',
    source_id: CRM,
    event_types: ['trigger.fired', 'action.recorded'],
    secret_refs: [`spaces/${PROD}/webhook/whsec-crm`],
    enabled: true,
  },
]
const diff = (over: Record<string, unknown>) => ({
  diff_id: DIFF,
  space_id: PROD,
  type: 'webhook-endpoints',
  document: webhookDoc,
  reason: 'Send trigger firings and agent actions to the CRM',
  status: 'pending',
  base_version: 7,
  applied_version: null,
  author_id: ANA,
  author_label: 'ana@acme.example',
  created_at: '2026-09-22T18:10:00Z',
  decided_at: null,
  decided_by: null,
  decided_by_label: null,
  rollback_of: null,
  ...over,
})
const reviewDoc = { base_rate: 0.01, boosted_rate: 0.1, boost_on: ['measure_signal', 'sensitive'], daily_cap: 500 }
const cell = (id: string, capacity: number) => ({
  cell_id: id,
  region: 'us-east-2',
  // A reserved example domain: a cell address is operator data, never under the space addresses.
  address: `https://${id}.niadra.example`,
  capacity,
  status: 'active',
  code_version: '2026.09.3',
  schema_revision: '0042',
})
const move = (over: Record<string, unknown>) => ({
  move_id: MOVE,
  from_cell: 'cell-1',
  to_cell: 'cell-2',
  requested_at: '2026-09-23T02:00:00Z',
  status: 'copying',
  completed_at: null,
  ...over,
})
const usageItems = [
  { space_id: PROD, period_start: '2026-09-01T00:00:00Z', metric: 'session_billable', quantity: 182340, cost_micros: 0 },
  { space_id: PROD, period_start: '2026-09-01T00:00:00Z', metric: 'event_ingested', quantity: 4120775, cost_micros: 0 },
]

export const EXAMPLES: Record<string, OpExtra> = {
  'control:post /v1/auth/login': {
    request: { email: 'ana@acme.example', password: 'correct horse battery staple', totp: '492817', space_id: PROD },
    responses: { '200': { access_token: TOKEN, token_type: 'Bearer', expires_in: 900, space_id: PROD, recovery_codes: null, recovery_codes_left: null } },
  },
  'control:post /v1/auth/second-factor': {
    request: { email: 'ana@acme.example', password: 'correct horse battery staple' },
    responses: {
      '200': {
        secret: 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP',
        totp_uri: 'otpauth://totp/Niadra:ana@acme.example?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Niadra&algorithm=SHA1&digits=6&period=30',
      },
    },
  },
  'control:post /v1/auth/token': {
    request: { space_id: SANDBOX },
    responses: { '200': { access_token: TOKEN, token_type: 'Bearer', expires_in: 900, space_id: SANDBOX } },
  },
  'control:get /.well-known/jwks.json': {
    responses: { '200': { keys: [{ kty: 'OKP', crv: 'Ed25519', kid: 'ctl-2026-09', use: 'sig', alg: 'EdDSA', x: '11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo' }] } },
  },
  'control:get /v1/users/me': {
    responses: {
      '200': {
        user_id: ANA,
        tenant_id: TENANT,
        email: 'ana@acme.example',
        name: null,
        password_set: true,
        sso: true,
        spaces: [
          { space_id: PROD, project_id: PROJECT, environment: 'production', slug: 'acme-prod', region: 'us-east-2', roles: ['admin'] },
          { space_id: SANDBOX, project_id: PROJECT, environment: 'sandbox', slug: 'acme-sandbox', region: 'us-east-2', roles: ['admin'] },
        ],
      },
    },
  },
  'control:get /v1/users/me/preferences': {
    responses: { '200': { preferences: { tutorials: { done: { tour: '2026-09-22T11:20:00.000Z' } }, welcome_seen: true } } },
  },
  'control:put /v1/users/me/preferences/{key}': {
    params: { key: 'welcome_seen' },
    request: { value: true },
    responses: { '200': { preferences: { tutorials: { done: { tour: '2026-09-22T11:20:00.000Z' } }, welcome_seen: true } } },
  },
  'control:get /v1/users/me/second-factor': {
    responses: { '200': { confirmed_at: '2026-09-22T11:02:00Z', recovery_codes_left: 9 } },
  },
  'control:post /v1/users/me/recovery-codes': {
    request: { password: 'correct horse battery staple' },
    responses: { '200': { recovery_codes: RECOVERY_CODES } },
  },
  'control:post /v1/users/me/second-factor': {
    request: { password: 'correct horse battery staple' },
    responses: {
      '200': {
        secret: NEW_SECRET,
        totp_uri: `otpauth://totp/Niadra:ana@acme.example?secret=${NEW_SECRET}&issuer=Niadra&algorithm=SHA1&digits=6&period=30`,
      },
    },
  },
  'control:post /v1/users/me/second-factor/confirm': {
    request: { totp: '731594' },
    responses: { '200': { recovery_codes: RECOVERY_CODES } },
  },
  'control:post /v1/users/{user_id}/second-factor/reset': {
    params: { user_id: RUI },
    request: { reason: 'Lost the phone and the recovery codes; identity checked on a video call' },
    responses: {
      '200': {
        reset_id: RESET,
        user_id: RUI,
        reset_by: ANA,
        reason: 'Lost the phone and the recovery codes; identity checked on a video call',
        at: '2026-09-23T14:20:00Z',
      },
    },
  },
  'control:get /v1/users': {
    responses: {
      '200': [
        person({
          user_id: ANA,
          email: 'ana@acme.example',
          last_login_at: '2026-09-22T11:02:00Z',
          roles: [{ role: 'admin', space_id: null, source_ids: [] }],
          second_factor_confirmed_at: '2026-09-02T09:15:00Z',
          recovery_codes_left: 9,
        }),
        person({
          user_id: NINA,
          email: 'nina@acme.example',
          password_set: false,
          roles: [{ role: 'review', space_id: PROD, source_ids: [] }],
          invitation_expires_at: '2026-10-01T12:00:00Z',
        }),
      ],
    },
  },
  'control:post /v1/users': {
    request: { email: 'nina@acme.example', roles: [{ role: 'review', space_id: PROD }], with_totp: false },
    responses: {
      '201': person({
        user_id: NINA,
        email: 'nina@acme.example',
        created_at: '2026-09-24T12:00:00Z',
        password_set: false,
        roles: [{ role: 'review', space_id: PROD, source_ids: [] }],
        invitation_expires_at: '2026-10-01T12:00:00Z',
        invitation: { token: INVITATION_TOKEN, expires_at: '2026-10-01T12:00:00Z' },
      }),
    },
  },
  'control:put /v1/users/{user_id}/roles': {
    params: { user_id: RUI },
    request: { roles: [{ role: 'security' }, { role: 'review', space_id: PROD }], reason: 'Rui now also reviews samples in production' },
    responses: {
      '200': [
        { role: 'security', space_id: null, source_ids: [] },
        { role: 'review', space_id: PROD, source_ids: [] },
      ],
    },
  },
  'control:post /v1/users/{user_id}/deactivate': {
    params: { user_id: RUI },
    request: { reason: 'Left the company on 2026-09-24' },
    responses: {
      '200': person({
        user_id: RUI,
        email: 'rui@acme.example',
        active: false,
        deactivated_at: '2026-09-24T18:00:00Z',
        roles: [{ role: 'security', space_id: null, source_ids: [] }],
        second_factor_confirmed_at: '2026-09-03T10:00:00Z',
        recovery_codes_left: 10,
      }),
    },
  },
  'control:post /v1/users/{user_id}/reactivate': {
    params: { user_id: RUI },
    request: { reason: 'Back from leave' },
    responses: {
      '200': person({
        user_id: RUI,
        email: 'rui@acme.example',
        roles: [{ role: 'security', space_id: null, source_ids: [] }],
        second_factor_confirmed_at: '2026-09-03T10:00:00Z',
        recovery_codes_left: 10,
      }),
    },
  },
  'control:post /v1/users/{user_id}/invitation': {
    params: { user_id: NINA },
    responses: { '201': { token: INVITATION_TOKEN, expires_at: '2026-10-01T12:00:00Z' } },
  },
  'control:get /v1/users/{user_id}/history': {
    params: { user_id: RUI },
    responses: {
      '200': [
        { change_id: CHANGE, kind: 'deactivated', at: '2026-09-24T18:00:00Z', user_id: RUI, actor_id: ANA, reason: 'Left the company on 2026-09-24', detail: {} },
        {
          change_id: DIFF,
          kind: 'roles_changed',
          at: '2026-09-10T14:00:00Z',
          user_id: RUI,
          actor_id: ANA,
          reason: 'Rui now also reviews samples in production',
          detail: {
            before: [{ role: 'security', space_id: null, source_ids: [] }],
            after: [
              { role: 'security', space_id: null, source_ids: [] },
              { role: 'review', space_id: PROD, source_ids: [] },
            ],
          },
        },
      ],
    },
  },
  'control:post /v1/auth/invitation/preview': {
    request: { token: INVITATION_TOKEN },
    responses: { '200': { email: 'nina@acme.example', tenant_name: 'Acme', expires_at: '2026-10-01T12:00:00Z' } },
  },
  'control:post /v1/auth/invitation': {
    request: { token: INVITATION_TOKEN, password: 'a long passphrase I can remember' },
    responses: { '200': { email: 'nina@acme.example' } },
  },
  'control:post /v1/auth/sso/discover': {
    request: { email: 'nina@acme.example' },
    responses: { '200': { sso: true, protocol: 'oidc', enforced: false } },
  },
  'control:post /v1/auth/sso/start': {
    request: { email: 'nina@acme.example', binding: 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM' },
    responses: {
      '200': {
        authorize_url:
          'https://login.acme.example/authorize?response_type=code&client_id=niadra-console&redirect_uri=https%3A%2F%2Fcontrol.api.niadra.com%2Fv1%2Fauth%2Fsso%2Foidc%2Fcallback&scope=openid+email+profile+groups&state=Zk9x...&nonce=Pq2w...&code_challenge=K8Hm...&code_challenge_method=S256&login_hint=nina%40acme.example',
        expires_at: '2026-09-24T12:10:00Z',
      },
    },
  },
  'control:post /v1/auth/sso/exchange': {
    request: { code: 'bXktb25lLXRpbWUtc2lnbi1pbi1jb2Rl', verifier: 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk' },
    responses: { '200': { access_token: TOKEN, token_type: 'Bearer', expires_in: 900, space_id: SANDBOX, recovery_codes: null, recovery_codes_left: null } },
  },
  'control:post /v1/auth/sso/second-factor': {
    request: { code: 'bXktb25lLXRpbWUtc2lnbi1pbi1jb2Rl', verifier: 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk' },
    responses: {
      '200': {
        secret: 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP',
        totp_uri: 'otpauth://totp/Niadra:nina@acme.example?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Niadra&algorithm=SHA1&digits=6&period=30',
      },
    },
  },
  'control:get /v1/sso': {
    responses: { '200': { connection: OIDC_CONNECTION, service_provider: SERVICE_PROVIDER } },
  },
  'control:put /v1/sso': {
    request: {
      protocol: 'oidc',
      domains: ['acme.example'],
      enabled: true,
      enforced: false,
      default_role: null,
      role_mappings: [
        { group: 'niadra-admins', role: 'admin' },
        { group: 'cx-quality', role: 'review' },
      ],
      groups_attribute: 'groups',
      require_second_factor: false,
      oidc: { issuer: 'https://login.acme.example', client_id: 'niadra-console', client_secret: 'the-client-secret-from-the-provider', scopes: ['groups'] },
      reason: 'The Console signs in with the company login',
    },
    responses: { '200': { connection: OIDC_CONNECTION, service_provider: SERVICE_PROVIDER } },
  },
  'control:delete /v1/sso': {
    request: { reason: 'Moving to another identity provider' },
  },
  'control:post /v1/sso/test': {
    responses: {
      '201': {
        test_id: TEST,
        authorize_url: 'https://login.acme.example/authorize?response_type=code&client_id=niadra-console&state=...&code_challenge_method=S256',
        expires_at: '2026-09-24T12:10:00Z',
      },
    },
  },
  'control:get /v1/sso/tests/{test_id}': {
    params: { test_id: TEST },
    responses: {
      '200': {
        test_id: TEST,
        status: 'completed',
        created_at: '2026-09-24T12:00:00Z',
        completed_at: '2026-09-24T12:01:10Z',
        ok: true,
        failure: null,
        detail: null,
        email: 'nina@acme.example',
        groups: ['cx-quality', 'all-staff'],
        roles: ['review'],
        person_exists: false,
      },
    },
  },
  'control:get /v1/roles': {
    responses: {
      '200': [
        { role: 'admin', description: 'Sources, keys, people, roles and SSO' },
        { role: 'security', description: 'Reads and receipts, policy and simulation, access cuts, erasure, export' },
        { role: 'integration', description: 'System mappings, task views, pattern and trigger rules, webhooks, configuration assistant' },
        { role: 'review', description: "The sampled review queue, masked by each session's purpose" },
        { role: 'analysis', description: 'Measurement, patterns and usage, aggregated or by pseudonym' },
        { role: 'vendor', description: "Only the vendor's own sources and their measurement" },
      ],
    },
  },
  'control:post /v1/tenants': {
    request: { name: 'Acme', slug: 'acme', mfa_required: true },
    responses: { '201': tenant },
  },
  'control:get /v1/tenants/{tenant_id}': { params: { tenant_id: TENANT }, responses: { '200': tenant } },
  'control:get /v1/projects': { responses: { '200': [project] } },
  'control:post /v1/projects': {
    request: { name: 'Support', slug: 'support', region: 'us-east-2' },
    responses: { '201': project },
  },
  'control:get /v1/spaces/{space_id}': { params: { space_id: PROD }, responses: { '200': prodSpace } },
  'control:delete /v1/spaces/{space_id}': {
    params: { space_id: SANDBOX },
    responses: { '202': { space_id: SANDBOX, deleted_at: DELETED_AT, status: 'deleting', receipt: null } },
  },
  'control:get /v1/spaces/{space_id}/deletion-receipt': {
    params: { space_id: SANDBOX },
    responses: { '200': { space_id: SANDBOX, deleted_at: DELETED_AT, status: 'deleted', receipt: deletionReceipt } },
  },
  'control:get /v1/sources': { params: { space_id: PROD }, responses: { '200': [billingSource] } },
  'control:post /v1/sources': {
    request: {
      space_id: PROD,
      name: 'Billing agent',
      audience: 'internal_agent',
      vendor: 'in-house',
      channel: 'erp',
      purposes: ['billing'],
      verification_ceiling: 'no_customer',
      trusted_action_ops: ['credit'],
      navigation_enabled: true,
      confirms_before_acting: false,
      critical: false,
    },
    responses: { '201': billingSource },
  },
  'control:get /v1/sources/{source_id}/keys': {
    params: { source_id: BILLING },
    responses: { '200': [key('tt7vkavsfpfm6ov2', '2026-09-22T12:00:00Z')] },
  },
  'control:post /v1/sources/{source_id}/keys': {
    params: { source_id: BILLING },
    request: { scopes: ['context', 'search', 'track', 'act'] },
    responses: { '201': { key: key('tt7vkavsfpfm6ov2', '2026-09-22T12:00:00Z'), secret: KEY_SECRET } },
  },
  'control:post /v1/sources/{source_id}/keys/{key_id}/rotate': {
    params: { source_id: BILLING, key_id: 'tt7vkavsfpfm6ov2' },
    request: { grace_seconds: 3600 },
    responses: {
      '201': { key: key('p2gxftvcparqg7de', '2026-09-24T09:00:00Z'), secret: 'nia_sk_live_us-east-2_acme-prod_p2gxftvcparqg7de_2DVxTG1odxZF3n4HrdC0cnDzMzIMDxZcHN7RBhum' },
    },
  },
  'control:post /v1/sources/{source_id}/keys/{key_id}/revoke': {
    params: { source_id: BILLING, key_id: 'tt7vkavsfpfm6ov2' },
    request: { reason: 'Key exposed in a vendor log' },
    responses: { '200': { kind: 'key', seq: 4812, space_id: PROD, source_id: BILLING, key_id: 'tt7vkavsfpfm6ov2', at: '2026-09-22T18:00:00Z' } },
  },
  'control:post /v1/sources/{source_id}/revoke': {
    params: { source_id: CRM },
    request: { reason: 'Vendor contract ended' },
    responses: { '200': { kind: 'source', seq: 4813, space_id: PROD, source_id: CRM, key_id: null, at: '2026-09-30T23:59:00Z' } },
  },
  'control:get /v1/secret-refs': {
    params: { space_id: PROD },
    responses: {
      '200': [{ space_id: PROD, ref: `spaces/${PROD}/webhook/whsec-crm`, kind: 'webhook', version: 2, rotated_by: RUI, rotated_at: '2026-09-20T15:30:00Z' }],
    },
  },
  'control:post /v1/secret-refs': {
    request: { space_id: PROD, ref: `spaces/${PROD}/webhook/whsec-crm`, kind: 'webhook', version: 2 },
    responses: {
      '201': { space_id: PROD, ref: `spaces/${PROD}/webhook/whsec-crm`, kind: 'webhook', version: 2, rotated_by: RUI, rotated_at: '2026-09-20T15:30:00Z' },
    },
  },
  'control:get /v1/config/types': {
    responses: {
      '200': [
        { type: 'policies', description: 'Who may read what: categories, rules, competitors.', roles: ['security'] },
        { type: 'trigger-rules', description: 'Conditions that notify an endpoint.', roles: ['integration'] },
        { type: 'review-sampling', description: 'How many sessions go to sampled review.', roles: ['review', 'security'] },
      ],
    },
  },
  'control:get /v1/config/{type}': {
    params: { type: 'review-sampling', space_id: PROD },
    responses: { '200': { space_id: PROD, type: 'review-sampling', version: 7, document: reviewDoc } },
  },
  'control:get /v1/config/diffs': {
    params: { space_id: PROD, status: 'pending' },
    responses: { '200': [diff({})] },
  },
  'control:post /v1/config/diffs': {
    request: { space_id: PROD, type: 'webhook-endpoints', document: webhookDoc, reason: 'Send trigger firings and agent actions to the CRM' },
    responses: { '201': diff({}) },
  },
  'control:post /v1/config/diffs/{diff_id}/approve': {
    params: { diff_id: DIFF },
    responses: { '200': diff({ status: 'applied', applied_version: 8, decided_by: RUI, decided_by_label: 'rui@acme.example', decided_at: '2026-09-22T18:25:00Z' }) },
  },
  'control:post /v1/config/diffs/{diff_id}/reject': {
    params: { diff_id: DIFF },
    responses: { '200': diff({ status: 'rejected', decided_by: RUI, decided_by_label: 'rui@acme.example', decided_at: '2026-09-22T18:25:00Z' }) },
  },
  'control:post /v1/config/diffs/{diff_id}/rollback': {
    params: { diff_id: DIFF },
    request: { reason: 'The CRM endpoint answered 500 to every delivery' },
    responses: {
      '200': diff({
        diff_id: DIFF2,
        document: [],
        reason: 'The CRM endpoint answered 500 to every delivery',
        status: 'applied',
        base_version: 8,
        applied_version: 9,
        created_at: '2026-09-22T19:00:00Z',
        decided_by: ANA,
        decided_by_label: 'ana@acme.example',
        decided_at: '2026-09-22T19:00:00Z',
        rollback_of: DIFF,
      }),
    },
  },
  'control:get /v1/config/history': {
    params: { space_id: PROD, type: 'webhook-endpoints', limit: 50 },
    responses: { '200': [{ version: 8, type: 'webhook-endpoints', diff_id: DIFF, created_at: '2026-09-22T18:25:00Z', document: webhookDoc }] },
  },
  'control:post /v1/config/proposals': {
    request: {
      cell_id: 'cell-1',
      space_id: PROD,
      type: 'trigger-rules',
      document: [
        { rule_id: 'overdue_visit', condition: 'promise.overdue', version: '1', enabled: true, params: { days: 1 }, endpoint_id: 'whe_crm', window_hours: 24, late_policy: 'deliver_marked' },
      ],
      reason: 'Notify the CRM one day after a promised visit is missed',
    },
    responses: {
      '201': diff({
        type: 'trigger-rules',
        document: [
          { rule_id: 'overdue_visit', condition: 'promise.overdue', version: '1', enabled: true, params: { days: 1 }, endpoint_id: 'whe_crm', window_hours: 24, late_policy: 'deliver_marked' },
        ],
        reason: 'Notify the CRM one day after a promised visit is missed',
        status: 'proposed',
        author_id: null,
        author_label: 'assistant@cell-1',
      }),
    },
  },
  'control:get /v1/usage': {
    params: { since: '2026-09-01T00:00:00Z', until: '2026-10-01T00:00:00Z' },
    responses: { '200': { items: usageItems, quotas: [{ tenant_id: TENANT, metric: 'session_billable', limit: 250000, period: 'month' }] } },
  },
  'control:get /v1/cells': { responses: { '200': [cell('cell-1', 40), cell('cell-2', 40)] } },
  'control:post /v1/cells': {
    request: { cell_id: 'cell-2', region: 'us-east-2', address: 'https://cell-2.niadra.example', capacity: 40 },
    responses: { '201': { ...cell('cell-2', 40), code_version: null, schema_revision: null } },
  },
  'control:get /v1/spaces/{space_id}/placement': {
    params: { space_id: PROD },
    responses: { '200': { space_id: PROD, cell_id: 'cell-1', move: move({}) } },
  },
  'control:post /v1/spaces/{space_id}/placement': {
    params: { space_id: PROD },
    request: { cell_id: 'cell-2', reason: 'Rebalance before the October peak' },
    responses: { '202': move({}) },
  },
  'control:post /v1/spaces/{space_id}/placement/complete': {
    params: { space_id: PROD },
    responses: { '200': move({ status: 'completed', completed_at: '2026-09-23T04:10:00Z' }) },
  },
  'control:put /v1/quotas': {
    request: { tenant_id: TENANT, metric: 'llm_daily_micros:extraction', limit: 2000000, period: 'day' },
    responses: { '200': { tenant_id: TENANT, metric: 'llm_daily_micros:extraction', limit: 2000000, period: 'day' } },
  },
  'control:get /v1/snapshot/{cell_id}': {
    params: { cell_id: 'cell-1', after: 41, wait: 25 },
    responses: {
      '200': {
        manifest: { cell_id: 'cell-1', version: 42, documents: { [PROD]: 'sha256:9f2c4e...' } },
        signature: 'MEUCIQ...',
        kid: 'ctl-2026-09',
        documents: { [PROD]: { space_id: PROD, region: 'us-east-2', environment: 'production' } },
        keys: { [PROD]: [{ key_id: 'tt7vkavsfpfm6ov2', source_id: BILLING, scopes: ['context', 'search', 'track', 'act'] }] },
        keys_signature: 'MEQCIF...',
      },
    },
  },
  'control:get /v1/revocations': {
    params: { cell_id: 'cell-1', after: 4811 },
    responses: {
      '200': {
        body: { cell_id: 'cell-1', seq: 4813, keys: [{ space_id: PROD, key_id: 'tt7vkavsfpfm6ov2' }], sources: [{ space_id: PROD, source_id: CRM }] },
        kid: 'ctl-2026-09',
        signature: 'x9Yt2Q...',
      },
    },
  },
  'control:post /v1/usage': {
    request: { cell_id: 'cell-1', items: usageItems },
    responses: { '202': { accepted: 2 } },
  },
  'control:post /v1/spaces/{space_id}/deletion-receipt': {
    params: { space_id: SANDBOX },
    request: { cell_id: 'cell-1', receipt: RECEIPT_DOC, receipt_hash: RECEIPT_HASH },
    responses: { '200': deletionReceipt },
  },
}
