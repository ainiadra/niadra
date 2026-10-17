/**
 * Examples for the write, context, history, objects, identity and profiles routes.
 * Same story as the rest of the documentation: Marina complains on WhatsApp at 14:02,
 * billing issues the credit at 14:06 and she calls at 14:07.
 */
import { EXAMPLES as BASE } from '../examples'
import type { OpExtra } from '../model'

const MARINA = { type: 'phone_e164', value: '+14155550123' }
const PROFILE = '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e01'
const PHONE_HANDLE = '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e02'
const CRM_HANDLE = '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e03'
const ACCOUNT_HANDLE = '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e05'
const OP = { op_id: '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e07', status: 'queued', assertion_id: null, link_id: null }

const handleOut = (handle_id: string, type: string, value: string, scope: string, level: string, first_seen: string) => ({
  handle_id,
  type,
  value,
  scope,
  subject_kind: 'person',
  status: 'active',
  level,
  first_seen,
  last_seen: '2026-09-22T17:07:03Z',
})
const PHONE_OUT = handleOut(PHONE_HANDLE, 'phone_e164', '+1415555****', '', 'V1', '2021-04-06T13:20:00Z')
const CRM_OUT = handleOut(CRM_HANDLE, 'system_id', '48213', 'crm', 'V4', '2021-04-06T13:21:00Z')

const IMPORT = {
  import_id: '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e0a',
  format: 'csv',
  status: 'completed',
  size_bytes: 1842201,
  sha256: '9c1e5a7b3d2f40e18a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f',
  records: 20412,
  accepted: 20377,
  duplicates: 31,
  rejected: 4,
  errors: [{ row: 1882, code: 'invalid_input', detail: 'phone_e164 is not in international format' }],
  created_at: '2026-09-22T12:00:04Z',
  finished_at: '2026-09-22T12:01:39Z',
}

const LINK = {
  link_id: '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e08',
  person_handle_id: PHONE_HANDLE,
  org_handle_id: ACCOUNT_HANDLE,
  role: 'buyer',
  can_see_contacts: false,
  valid_from: '2026-09-22T17:10:00Z',
  valid_to: null,
}

const SUGGESTION = {
  suggestion_id: '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e09',
  handle_a: PHONE_HANDLE,
  handle_b: '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e0c',
  profile_a: PROFILE,
  profile_b: '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e0d',
  identifier_a: PHONE_OUT,
  identifier_b: handleOut('0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e0c', 'email', 'm***@example.com', '', 'V0', '2026-09-20T10:15:00Z'),
  score: 0.93,
  features: { same_name: true, same_postal_code: true, co_occurrences: 2 },
  status: 'open',
  created_at: '2026-09-22T03:00:00Z',
}

const ORIGIN_WA = { event_id: '0192f7a1-6a2b-7d11-9c3e-1a2b3c4d5e01', source_id: '0192f79e-11ac-7d42-9f50-8e3f40617c82', channel: 'whatsapp', kind: 'message', speaker: 'customer', conversation_id: 'wa-8812', task_id: null, at: '2026-09-22T17:02:11Z' }
const ORIGIN_BILLING = { event_id: '0192f7a1-6a2b-7d11-9c3e-1a2b3c4d5e02', source_id: '0192f79e-11ab-7c31-8e4f-7d2e3f506b71', channel: 'erp', kind: 'action', speaker: 'ai_agent', conversation_id: null, task_id: 'billing-7741', at: '2026-09-22T17:06:21Z' }

const CREDIT_ACTION = {
  id: '0192f7a2-9d52-7f4b-8a6e-3d5f7a9b1c24',
  operation: 'credit',
  result: '$40 credit on the August bill',
  purpose: 'billing',
  status: 'confirmed',
  object_id: '0192f7a2-6b30-7d29-a4c1-2e3f4a5b6c70',
  closes_item_id: '0192f7a2-8c41-7e3a-9b5d-2c4e6f8a0b12',
  confirmed_by_event_id: '0192f7a1-6a2b-7d11-9c3e-1a2b3c4d5e03',
  occurred_at: '2026-09-22T17:06:21Z',
  origin: ORIGIN_BILLING,
}

const BATCH_OK = { accepted: 2, duplicates: 0, errors: [] }

export const EXAMPLES: Record<string, OpExtra> = {
  'post /v1/batch': {
    ...BASE['post /v1/batch'],
    responses: {
      '200': BATCH_OK,
      '207': { accepted: 1, duplicates: 0, errors: [{ index: 1, code: 'scope_missing', detail: 'the key lacks the act scope' }] },
    },
  },
  'post /v1/media/uploads': {
    request: { content_type: 'audio/ogg', size_bytes: 48213, sha256: 'a3f1c2d4e5b6978812ab34cd56ef7890a1b2c3d4e5f60718293a4b5c6d7e8f90', subject: MARINA },
    responses: {
      '201': {
        media_ref: 'med_0192f7a15b107c3a8e214d2f8a4c0e02_0192f7b2a4c87e319d5b6f0e1c2a3b4d',
        upload_url:
          'https://niadra-cell-111122223333.s3.us-east-2.amazonaws.com/media/0192f0a0-3d4e-7f50-8b61-7c8d9e0f1a23/0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e02/0192f7b2-a4c8-7e31-9d5b-6f0e1c2a3b4d?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=900&X-Amz-Signature=...',
        upload_headers: { 'Content-Type': 'audio/ogg', 'x-amz-checksum-sha256': 'o/HC1OW2l4gSqzTNVu94kKGyw9Tl9gcYKTpLXG1+j5A=' },
        expires_at: '2026-09-22T17:17:03Z',
      },
    },
  },
  'post /v1/ingest/files': { responses: { '202': { ...IMPORT, status: 'queued', records: 0, accepted: 0, duplicates: 0, rejected: 0, errors: [], finished_at: null } } },
  'get /v1/ingest/files/{import_id}': { responses: { '200': IMPORT } },
  'post /v1/ingest/webhook/{source_id}': { params: { source_id: '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e0b' }, responses: { '200': { accepted: 1, duplicates: 0, errors: [] } } },
  'get /v1/ingest/webhook/{source_id}': { params: { source_id: '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e0b' } },
  'post /v1/feedback': {
    ...BASE['post /v1/feedback'],
    request: {
      idempotency_key: 'fb-2026-09-22-0001',
      subject: MARINA,
      action: 'resolve_open_item',
      open_item_id: '0192f7a2-8c41-7e3a-9b5d-2c4e6f8a0b13',
      reason: 'Visit rescheduled and confirmed by the customer',
    },
    responses: { '200': { accepted: 1, duplicates: 0, errors: [] } },
  },
  'get /v1/context': {
    params: { profile_id: PROFILE, view: 'voice', verification: 'V1', conversation_id: 'call-4471' },
    responses: { '200': BASE['post /v1/context']?.responses?.['200'] },
  },
  'get /v1/history/timeline': {
    params: { profile_id: PROFILE, limit: 20 },
    responses: { '200': BASE['post /v1/history/timeline']?.responses?.['200'] },
  },
  'get /v1/objects/{object_type}/{namespace}/{external_id}/timeline': {
    ...BASE['get /v1/objects/{object_type}/{namespace}/{external_id}/timeline'],
    responses: {
      '200': {
        ref: { type: 'invoice', namespace: 'erp', id: '0823' },
        items: [
          { id: '0192f7a2-9d52-7f4b-8a6e-3d5f7a9b1c24', kind: 'action', text: 'credit · $40 · Billing agent · confirmed by the system', at: '2026-09-22T17:06:21Z', channel: 'erp', source_id: '0192f79e-11ab-7c31-8e4f-7d2e3f506b71', outcome: 'confirmed', confidence: null, origin_event_id: '0192f7a1-6a2b-7d11-9c3e-1a2b3c4d5e02' },
          { id: '0192f7a1-6a2b-7d11-9c3e-1a2b3c4d5e04', kind: 'system_event', text: 'invoice.disputed · August bill', at: '2026-09-22T17:05:40Z', channel: 'erp', source_id: '0192f79e-11ad-7e53-8a61-9f4051728d93', outcome: null, confidence: null, origin_event_id: '0192f7a1-6a2b-7d11-9c3e-1a2b3c4d5e04' },
        ],
        next_cursor: null,
        as_of: '2026-09-22T17:07:02Z',
      },
    },
  },
  'get /v1/identity/assertions': {
    params: { profile_id: PROFILE },
    responses: {
      '200': {
        items: [
          {
            assertion_id: '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e06',
            handle_a: PHONE_HANDLE,
            handle_b: CRM_HANDLE,
            method: 'same_event',
            confidence: 0.99,
            level_at_capture: 'V1',
            origin_event_id: '0192f7a1-6a2b-7d11-9c3e-1a2b3c4d5e05',
            state: 'applied',
            actor: null,
            reason: null,
            created_at: '2025-11-03T13:10:00Z',
            retracted_at: null,
          },
        ],
        next_cursor: null,
      },
    },
  },
  'post /v1/identity/assertions': {
    request: { handles: [MARINA, { type: 'system_id', value: '48213', scope: 'crm' }], method: 'system_import', subject_kind: 'person', reason: 'CRM contact record 48213' },
    responses: { '202': { ...OP, assertion_id: '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e06' } },
  },
  'post /v1/identity/assertions/{assertion_id}/retract': {
    request: { reason: 'Shared store tablet linked two customers' },
    responses: { '202': { ...OP, assertion_id: '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e06' } },
  },
  'post /v1/identity/merge': {
    request: { profile_ids: [PROFILE, '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e0d'], force: false, reason: 'Same person confirmed by OTP on both numbers' },
    responses: { '202': OP },
  },
  'post /v1/identity/unmerge': {
    request: { profile_id: PROFILE, handle_ids: ['0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e0c'], assertion_ids: [], reason: 'Store e-mail used by several customers' },
    responses: { '202': OP },
  },
  'post /v1/identity/handles/block': {
    request: { handle: { type: 'email', value: 'store-7@example.com' }, reason: 'Shared store inbox' },
    responses: { '202': { ...OP, status: 'blocked' } },
  },
  'post /v1/identity/links': {
    request: {
      person: MARINA,
      organization: { type: 'system_id', value: 'ACC-2201', scope: 'crm', subject_kind: 'account' },
      role: 'buyer',
      method: 'system_import',
      can_see_contacts: false,
    },
    responses: { '201': LINK },
  },
  'post /v1/identity/links/{link_id}/end': {
    request: { valid_to: '2026-10-01T00:00:00Z' },
    responses: { '200': { ...LINK, valid_to: '2026-10-01T00:00:00Z' } },
  },
  'get /v1/identity/suggestions': { responses: { '200': { items: [SUGGESTION], next_cursor: null } } },
  'post /v1/identity/suggestions/{suggestion_id}/accept': {
    request: { reason: 'Customer confirmed both contacts on the call' },
    responses: { '202': { ...OP, assertion_id: '0192f7a1-5b10-7c3a-8e21-4d2f8a4c0e0e' } },
  },
  'post /v1/identity/suggestions/{suggestion_id}/dismiss': { responses: { '200': { ...SUGGESTION, status: 'rejected' } } },
  'get /v1/profiles': {
    params: { order: 'last_seen_desc', limit: 50 },
    responses: {
      '200': {
        items: [
          {
            profile_id: PROFILE,
            pseudonym: 'p_kvkv3okp7vnw3mqs',
            kind: 'person',
            handles: [
              { type: 'phone_e164', value: '+14*****0123', last_seen: '2026-09-22T17:07:03Z' },
              { type: 'system_id', value: '48*13', last_seen: '2026-09-20T09:12:40Z' },
            ],
            handle_count: 2,
            last_seen: '2026-09-22T17:07:03Z',
          },
        ],
        next_cursor: '1790096823000000.0192f7a15b107c3a8e214d2f8a4c0e02',
      },
    },
  },
  'post /v1/profiles/search': {
    request: { query: '+14155550123', limit: 20 },
    responses: {
      '200': {
        items: [{ profile_id: PROFILE, pseudonym: 'p_kvkv3okp7vnw3mqs', kind: 'person', partial: false, handles: [PHONE_OUT, CRM_OUT], last_seen: '2026-09-22T17:07:03Z' }],
      },
    },
  },
  'get /v1/profiles/{profile_id}': {
    params: { profile_id: PROFILE },
    responses: {
      '200': {
        profile_id: PROFILE,
        pseudonym: 'p_kvkv3okp7vnw3mqs',
        kind: 'person',
        partial: false,
        handles: [PHONE_OUT, CRM_OUT],
        links: [LINK],
        membership_version: 7,
        privacy_version: 2,
      },
    },
  },
  'get /v1/profiles/{profile_id}/memory': {
    params: { profile_id: PROFILE },
    responses: {
      '200': {
        profile_id: PROFILE,
        audience: 'human',
        policy_version: 'pol-14',
        facts: [
          {
            id: '0192f7a2-5d10-7a8e-b1c4-3f6e8d9a0b10',
            predicate: 'plan',
            value: 'Family plan',
            category: 'contract',
            confidence: 0.97,
            sensitivity: 'normal',
            status: 'active',
            times_seen: 6,
            sessions_seen: 3,
            valid_at: '2021-04-06T13:20:00Z',
            last_confirmed_at: '2026-09-22T17:02:11Z',
            invalid_at: null,
            about_handle_id: null,
            object: null,
            evidence_event_ids: ['0192f7a1-6a2b-7d11-9c3e-1a2b3c4d5e01'],
            origin: ORIGIN_WA,
            masked: false,
          },
        ],
        open_items: [
          {
            id: '0192f7a2-8c41-7e3a-9b5d-2c4e6f8a0b13',
            description: 'Technician visit promised for this morning did not happen',
            owner: 'company',
            promise: true,
            status: 'overdue',
            due_at: '2026-09-22T15:00:00Z',
            expected_operation: 'reschedule',
            object: null,
            closed_at: null,
            closed_by_action_id: null,
            created_at: '2026-09-22T17:03:10Z',
            origin: ORIGIN_WA,
            masked: false,
          },
        ],
        timeline: [
          {
            id: '0192f7a3-7c32-7b0c-9e16-5b8a0f1c2e05',
            session_id: '0192f7a1-7e20-7a3b-8c4d-5e6f70819203',
            intent: 'complaint',
            category: 'technician visit',
            normalized_category: 'technician_visit',
            outcome: 'escalated',
            resolution: null,
            summary: 'Technician did not show up; customer is calling.',
            sentiment: -0.6,
            started_at: '2026-09-22T17:02:11Z',
            ended_at: '2026-09-22T17:03:00Z',
            version: 1,
            origin: ORIGIN_WA,
            masked: false,
          },
        ],
        traits: [
          {
            id: '0192f7a5-2a43-7c1d-8f27-6c9b1a2d3e46',
            name: 'recurring_complaint',
            value: 'technician_visit',
            layer: 'volatile',
            confidence: 1,
            active: true,
            rule_version: 'recurring_complaint@3',
            first_seen: '2026-09-22T17:03:30Z',
            last_evidence_at: '2026-09-22T17:03:00Z',
            expires_at: '2026-12-21T17:03:00Z',
            retracted_at: null,
            evidence: [{ id: '0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d34', kind: 'episode', origin: null }],
            masked: false,
          },
        ],
        withheld: { verification: 1 },
      },
    },
  },
  'get /v1/profiles/{profile_id}/objects': {
    params: { profile_id: PROFILE },
    responses: {
      '200': {
        profile_id: PROFILE,
        audience: 'human',
        policy_version: 'pol-14',
        objects: [
          {
            id: '0192f7a2-6b30-7d29-a4c1-2e3f4a5b6c70',
            ref: { type: 'invoice', namespace: 'erp', id: '0823' },
            role: 'owner',
            state: { status: 'credited', amount_due: 49.9, currency: 'USD' },
            as_of: '2026-09-22T17:06:24Z',
            source_id: '0192f79e-11ad-7e53-8a61-9f4051728d93',
            record_ref: 'erp://invoices/0823',
            actions: [CREDIT_ACTION],
            masked: false,
          },
        ],
        actions: [],
        withheld: {},
      },
    },
  },
}
