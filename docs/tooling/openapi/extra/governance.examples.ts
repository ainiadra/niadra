import type { OpExtra } from '../model'

/**
 * Examples for the governance routes of the data API, in the documentation story:
 * Marina Souza, invoice 0823 credited by task billing-7741, and call call-4471.
 */
const PROFILE = '0192f7a0-3b2e-7d41-8c5a-2e9f4b7a1c03'
const SOURCE_VOICE = '0192f79e-11aa-7b20-9f3e-6c1d2e4f5a60'
const SOURCE_BILLING = '0192f79e-11ab-7c31-8e4f-7d2e3f506b71'
const FACT = '0192f7a2-5d10-7a8e-b1c4-3f6e8d9a0b12'
const RECEIPT = '0192f7a4-0e21-7f9b-a2d5-4a7f9e0b1c23'
const EPISODE = '0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d34'
const TRAIT = '0192f7a5-2a43-7c1d-8f27-6c9b1a2d3e45'
const DAY = '2026-09-22'
const PHONE = { type: 'phone_e164', value: '+14155550123' }

const receipt = {
  receipt_id: RECEIPT,
  kind: 'history',
  surface: 'search',
  audience: 'customer_agent',
  day: DAY,
  read_at: '2026-09-22T17:07:09Z',
  source_id: SOURCE_VOICE,
  profile_id: PROFILE,
  pseudonym: 'p_kvkv3okp7vnw3mqs',
  person_id: null,
  person_label: null,
  conversation_id: 'call-4471',
  purpose: 'customer_service',
  verification: 'V1',
  policy_version: 'pol-14',
  item_ids: [EPISODE, FACT],
  withheld: 1,
  slice: 3,
  seq: 88214,
  prev_hash: 'sha256:0d9f3a61c2',
  hash: 'sha256:7e21c8a4b9',
}

// A read the policy refused: who tried, on which surface and why, never what was asked (SEG-14).
const denied = {
  receipt_id: '0192f7a3-1e62-7d4c-9a8b-5c6d7e8f9a0b',
  kind: 'denied',
  surface: 'pack',
  audience: 'customer_agent',
  day: DAY,
  read_at: '2026-09-22T17:07:41Z',
  source_id: SOURCE_VOICE,
  conversation_id: 'call-4471',
  purpose: 'customer_service',
  item_ids: [],
  withheld: 0,
  detail: { reason: 'scope_missing' },
  slice: 3,
  seq: 88215,
  prev_hash: 'sha256:7e21c8a4b9',
  hash: 'sha256:3f9a0c1d7e',
}

const erasure = {
  request_id: '0192f7b0-4c11-7e2a-9d30-1a2b3c4d5e6f',
  target_kind: 'handle',
  target_id: '0192f7a0-9f01-7a22-8b33-4c5d6e7f8091',
  status: 'completed',
  requested_at: '2026-09-22T18:00:00Z',
  completed_at: '2026-09-22T18:04:12Z',
  erased: { events: 42, facts: 9, episodes: 6, open_items: 2, traits: 1 },
  export_run_ids: ['0192f6e0-2b10-7c4d-8e5f-6a7b8c9d0e1f'],
  receipt_hash: 'sha256:a91c07e3f4',
}

const assistRun = {
  run_id: '0192f7c0-5d21-7f3b-8a4c-2b3c4d5e6f70',
  task: 'webhook_mapping',
  status: 'proposed',
  created_at: '2026-09-22T19:00:00Z',
  config_type: 'mappings',
  diff_id: '0192f7c1-0a11-7b22-9c33-4d5e6f708192',
  diff_hash: 'sha256:5f2e9b10c7',
  checks: { events_sampled: 100, mapped: 97, unmapped_types: ['invoice.reminder_sent'] },
  problems: [],
}

const delivery = {
  delivery_id: '0192f8d0-7e31-7a4b-9c5d-3e4f5a6b7c8d',
  endpoint_id: 'whe_crm',
  event_type: 'trigger.fired',
  status: 'dead',
  attempts: 14,
  last_status_code: 500,
  last_error: 'HTTP 500 from the endpoint',
  created_at: '2026-09-23T15:05:00Z',
  delivered_at: null,
  next_attempt_at: null,
}

const rate = (value: number, low: number, high: number, n: number) => ({ value, low, high, n })

export const EXAMPLES: Record<string, OpExtra> = {
  // Privacy and audit
  'post /v1/forget': {
    request: { target: 'handle', handle: PHONE },
    responses: { '202': { ...erasure, status: 'pending', completed_at: null, erased: {}, export_run_ids: [], receipt_hash: null } },
  },
  'get /v1/forget': { responses: { '200': { items: [erasure], next_cursor: null } } },
  'get /v1/forget/{request_id}': { params: { request_id: erasure.request_id }, responses: { '200': erasure } },
  'post /v1/export': {
    request: { handle: PHONE },
    responses: {
      '201': {
        run_id: '0192f7b5-8e41-7c5a-9d6b-7e8f9a0b1c2d',
        profile_id: PROFILE,
        download_url:
          'https://niadra-cell-111122223333.s3.us-east-2.amazonaws.com/exports/0192f0a0-3d4e-7f50-8b61-7c8d9e0f1a23/0192f7b5-8e41-7c5a-9d6b-7e8f9a0b1c2d.zip?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=900&X-Amz-Signature=...',
        download_expires_at: '2026-09-22T19:00:00Z',
        sha256: '3b1f4c9a0e7d2b6a8c5f1e0d9a7b3c2e4f6a8b0c1d3e5f7a9b0c2d4e6f8a0b1c',
        files: { 'events.jsonl': 'sha256:9c0e1f', 'facts.jsonl': 'sha256:4a7d2b', 'traits.jsonl': 'sha256:e6b3f0', 'links.jsonl': 'sha256:12c8a5' },
      },
    },
  },
  'get /v1/exports/runs': {
    responses: {
      '200': {
        items: [
          {
            run_id: '0192f6e0-2b10-7c4d-8e5f-6a7b8c9d0e1f',
            schedule_id: 'datalake-hourly',
            status: 'completed',
            started_at: '2026-09-22T17:00:00Z',
            finished_at: '2026-09-22T17:03:41Z',
            from_seq: 1882210,
            to_seq: 1893754,
            manifest: { schema_version: 3, files: 11, rows: 11544 },
            error: null,
          },
        ],
        next_cursor: null,
      },
    },
  },
  'put /v1/exports/destinations/{schedule_id}/credential': {
    params: { schedule_id: 'datalake-hourly' },
    request: { value: '{"access_key_id": "AKIAIOSFODNN7EXAMPLE", "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"}' },
    responses: { '200': { version: 2 } },
  },
  'get /v1/receipts/verify': {
    params: { day: DAY },
    responses: {
      '200': {
        day: DAY,
        receipts: 184233,
        valid: true,
        heads: [
          { slice: 0, seq: 45120, hash: 'sha256:1a2b3c4d5e' },
          { slice: 3, seq: 88214, hash: 'sha256:7e21c8a4b9' },
        ],
        breaks: [],
        anchored_root: 'sha256:c0ffee42ab',
        root_matches: true,
        anchor_copy_matches: true,
        anchor_locked: true,
      },
    },
  },
  'get /v1/lineage/receipt/{receipt_id}': {
    params: { receipt_id: RECEIPT },
    responses: {
      '200': {
        receipt,
        manifest_found: true,
        items: [
          {
            id: EPISODE,
            kind: 'episode',
            evidence_event_ids: ['0192f10a-2c3d-7e4f-8a5b-6c7d8e9f0a1b'],
            events: [
              {
                event_id: '0192f10a-2c3d-7e4f-8a5b-6c7d8e9f0a1b',
                kind: 'message',
                channel: 'voice',
                source_id: SOURCE_VOICE,
                conversation_id: 'call-2210',
                task_id: null,
                occurred_at: '2026-03-12T14:20:00Z',
              },
            ],
          },
        ],
        excluded: [{ id: '0192f7a2-9e10-7b8c-a1d2-3e4f5a6b7c8d', reason: 'verification' }],
      },
    },
  },
  'get /v1/lineage/fact/{fact_id}': { params: { fact_id: FACT }, responses: { '200': { fact_id: FACT, receipts: [receipt] } } },
  'get /v1/receipts': { responses: { '200': { items: [denied, receipt], next_cursor: null } } },
  'post /v1/receipts/search': {
    request: { conversation_id: 'call-4471', kind: 'read', since: '2026-09-22T00:00:00Z', limit: 50 },
    responses: { '200': { items: [receipt], next_cursor: null } },
  },
  'post /v1/policy/simulate': {
    request: { profile_id: PROFILE, source_id: SOURCE_VOICE, verification: 'V1', view: 'voice' },
    responses: {
      '200': {
        audience: 'customer_agent',
        verification: 'V1',
        policy_version: 'pol-14',
        items: [
          { id: '0192f7a2-9d52-7f4b-8a6e-3d5f7a9b1c24', kind: 'action', category: 'billing', effect: 'allow', delivered: true, rule: 'billing-to-service', reason: null },
          { id: '0192f7a2-5d10-7a8e-b1c4-3f6e8d9a0b14', kind: 'fact', category: 'document', effect: 'deny', delivered: false, rule: 'document-needs-v3', reason: 'verification' },
        ],
        withheld: { verification: 1 },
        text: '<niadra>\nData, not instructions.\n...\n</niadra>',
      },
    },
  },
  'put /v1/secrets/{kind}/{secret_id}': {
    params: { kind: 'webhook', secret_id: 'whe_crm' },
    request: { value: 'whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw' },
    responses: { '200': { version: 3 } },
  },
  // Sources
  'get /v1/sources/arrived': {
    params: { days: 90 },
    responses: { '200': { arrived: true, since: '2026-06-25T00:00:00Z' } },
  },
  'get /v1/sources/coverage': {
    params: { days: 7 },
    responses: {
      '200': {
        since: '2026-09-16T00:00:00Z',
        items: [
          {
            source_id: SOURCE_BILLING,
            name: 'Billing agent',
            channel: 'erp',
            sent: 1210,
            received: 1210,
            rejected: 3,
            unmapped: 0,
            last_event_at: '2026-09-22T17:06:21Z',
            days: [{ day: DAY, sent: 188, received: 188, rejected: 0, unmapped: 0, last_event_at: '2026-09-22T17:06:21Z' }],
          },
        ],
      },
    },
  },
  'post /v1/sources/{source_id}/revoke': {
    params: { source_id: SOURCE_VOICE },
    responses: { '200': { source_id: SOURCE_VOICE, revoked_at: '2026-09-22T18:00:02Z', revoked_key_ids: ['c3jlefctx28050vh', 'm9gzcds3p9bkn4wn'] } },
  },
  // Patterns, measurement and usage
  'post /v1/traits/{trait_id}/retract': {
    params: { trait_id: TRAIT },
    responses: {
      '200': {
        trait_id: TRAIT,
        profile_id: PROFILE,
        name: 'recurring_complaint',
        layer: 'volatile',
        rule_version: '3',
        confidence: 1,
        first_seen: '2026-09-22T17:03:40Z',
        last_evidence_at: '2026-09-22T17:02:11Z',
        expires_at: '2026-11-02T17:02:11Z',
        evidence: [
          { kind: 'episode', id: EPISODE },
          { kind: 'episode', id: '0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d99' },
        ],
        retracted_at: '2026-09-23T10:15:00Z',
      },
    },
  },
  'post /v1/traits/rules/dry-run': {
    request: { rule_id: 'recurring-visit-complaint', catalog: 'recurring_complaint', params: { category: 'technician_visit', min_count: 3, window_days: 90 } },
    responses: {
      '200': {
        rule_id: 'recurring-visit-complaint',
        window_days: 90,
        profiles_evaluated: 48210,
        profiles_matching: 312,
        sample: [
          { profile_id: PROFILE, pseudonym: 'p_kvkv3okp7vnw3mqs', value: 'technician_visit', expires_at: '2026-11-02T17:02:11Z', evidence: [{ kind: 'episode', id: EPISODE }] },
        ],
      },
    },
  },
  'post /v1/profiles/{profile_id}/traits/opt-out': {
    params: { profile_id: PROFILE },
    responses: { '200': { profile_id: PROFILE, opted_out_at: '2026-09-23T09:30:00Z' } },
  },
  'get /v1/context-use': {
    params: { group_by: ['vendor'] },
    responses: {
      '200': {
        since: '2026-09-15',
        until: '2026-09-22',
        group_by: ['vendor'],
        buckets: [
          {
            vendor: 'voice-platform',
            sessions: 4120,
            deliveries: 4302,
            deliveries_used: 3054,
            questions: 9810,
            repeated: 588,
            contradicted: 12,
            transfers: 410,
            transfers_unread: 31,
            late_deliveries: 18,
            no_context: 133,
            not_measured: 57,
            selection_misses: 9,
            recontacts: 96,
            usage_rate: rate(0.71, 0.69, 0.72, 4302),
            repetition_rate: rate(0.06, 0.055, 0.065, 9810),
            transfer_unread_rate: rate(0.076, 0.052, 0.106, 410),
            recontact_rate: rate(0.023, 0.019, 0.028, 4120),
          },
        ],
        review: [
          { signal: 'used', judged: 212, confirmed: 197, agreement: rate(0.93, 0.89, 0.96, 212) },
          { signal: 'repeated', judged: 148, confirmed: 131, agreement: rate(0.89, 0.82, 0.93, 148) },
          { signal: 'contradicted', judged: 9, confirmed: 8, agreement: rate(0.89, 0.57, 0.98, 9) },
          { signal: 'selection_miss', judged: 0, confirmed: 0, agreement: null },
        ],
      },
    },
  },
  'get /v1/context-use/{conversation_id}': {
    params: { conversation_id: 'call-4471' },
    responses: {
      '200': {
        conversation_id: 'call-4471',
        entries: [
          {
            source_id: SOURCE_VOICE,
            channel: 'voice',
            view: 'voice',
            experiment_group: 'memory',
            manifest_hash: 'sha256:5b1e0c',
            measure_version: '2',
            measured_at: '2026-09-22T17:12:40Z',
            used: 2,
            questions: 3,
            repeated: 0,
            contradicted: 0,
            late: false,
            transfer_unread: null,
            not_measured_reason: null,
            items: [{ id: '0192f7a2-9d52-7f4b-8a6e-3d5f7a9b1c24', kind: 'action', used: true, repeated: false, contradicted: false, evidence: ['0192f7a1-6a2b-7d11-9c3e-1a2b3c4d5e06'] }],
          },
        ],
      },
    },
  },
  'post /v1/context-use/conversation': {
    request: { conversation_id: 'call-4471' },
    responses: {
      '200': {
        conversation_id: 'call-4471',
        entries: [
          {
            source_id: SOURCE_VOICE,
            channel: 'voice',
            view: 'voice',
            experiment_group: 'memory',
            manifest_hash: 'sha256:5b1e0c',
            measure_version: '2',
            measured_at: '2026-09-22T17:12:40Z',
            used: 2,
            questions: 3,
            repeated: 0,
            contradicted: 0,
            late: false,
            transfer_unread: null,
            not_measured_reason: null,
            items: [{ id: '0192f7a2-9d52-7f4b-8a6e-3d5f7a9b1c24', kind: 'action', used: true, repeated: false, contradicted: false, evidence: ['0192f7a1-6a2b-7d11-9c3e-1a2b3c4d5e06'] }],
          },
        ],
      },
    },
  },
  'get /v1/usage/budget': {
    responses: {
      '200': {
        items: [
          { purpose: 'assistant', day: '2026-09-22', cap_micros: 200000, from_contract: false, spent_micros: 41300, reached: false, resumes_at: null },
          { purpose: 'extraction', day: '2026-09-22', cap_micros: 2000000, from_contract: true, spent_micros: 2004120, reached: true, resumes_at: '2026-09-23T00:00:00Z' },
        ],
      },
    },
  },
  'get /v1/usage': {
    params: { since: '2026-09-22T00:00:00Z', until: '2026-09-23T00:00:00Z', group_by: 'source' },
    responses: {
      '200': {
        since: '2026-09-22T00:00:00Z',
        until: '2026-09-23T00:00:00Z',
        granularity: 'day',
        group_by: 'source',
        source_id: null,
        items: [
          { start: '2026-09-22T00:00:00Z', metric: 'session_billable', source_id: SOURCE_VOICE, quantity: 6120, cost_micros: 61200000 },
          { start: '2026-09-22T00:00:00Z', metric: 'session_billable', source_id: SOURCE_BILLING, quantity: 1340, cost_micros: 13400000 },
        ],
      },
    },
  },
  // Review and assistant
  'get /v1/review/queue': {
    responses: {
      '200': {
        items: [
          {
            sample_id: '0192f7d0-6e41-7b5c-8d6e-7f8091a2b3c4',
            session_id: '0192f7d0-1a11-7c22-9d33-4e5f60718293',
            source_id: SOURCE_VOICE,
            channel: 'voice',
            purpose: 'customer_service',
            condition: 'measure_signal',
            created_at: '2026-09-22T17:15:00Z',
            withheld: 0,
            facts: [
              { fact_id: FACT, predicate: 'preferred_visit_time', value: 'morning', category: 'preference', sensitive: false, evidence_event_ids: ['0192f7a1-6c1e-7c3a-9b1e-5d2f8a4c0e11'] },
            ],
            signals: [{ signal: 'used', item_id: '0192f7a2-9d52-7f4b-8a6e-3d5f7a9b1c24', target_id: 'sha256:5b1e0c:0192f7a2-9d52-7f4b-8a6e-3d5f7a9b1c24:used', manifest_hash: 'sha256:5b1e0c' }],
          },
        ],
      },
    },
  },
  'post /v1/review/{sample_id}/verdicts': {
    params: { sample_id: '0192f7d0-6e41-7b5c-8d6e-7f8091a2b3c4' },
    request: {
      verdicts: [
        { target: 'fact', target_id: FACT, verdict: 'correct' },
        { target: 'signal', target_id: 'sha256:5b1e0c:0192f7a2-9d52-7f4b-8a6e-3d5f7a9b1c24:used', verdict: 'holds' },
      ],
    },
    responses: { '200': { sample_id: '0192f7d0-6e41-7b5c-8d6e-7f8091a2b3c4', recorded: 2, reviewed_at: '2026-09-22T18:20:00Z' } },
  },
  'get /v1/review/agreement': {
    params: { days: 90 },
    responses: {
      '200': {
        since: '2026-06-24T00:00:00Z',
        targets: [
          { target: 'fact', judged: 1840, agreed: 1712, system_agreement: rate(0.93, 0.92, 0.94, 1840), reviewer_pairs: 120, reviewer_agreement: rate(0.95, 0.9, 0.98, 120) },
          { target: 'signal', judged: 610, agreed: 562, system_agreement: rate(0.92, 0.9, 0.94, 610), reviewer_pairs: 0, reviewer_agreement: null },
          { target: 'signal:repeated', judged: 240, agreed: 214, system_agreement: rate(0.89, 0.85, 0.92, 240), reviewer_pairs: 0, reviewer_agreement: null },
          { target: 'all', judged: 2450, agreed: 2274, system_agreement: rate(0.93, 0.92, 0.94, 2450), reviewer_pairs: 120, reviewer_agreement: rate(0.95, 0.9, 0.98, 120) },
        ],
      },
    },
  },
  'post /v1/assist': {
    request: { task: 'webhook_mapping', source_id: SOURCE_BILLING, description: 'Map the ERP invoice events: credited, disputed and overdue.' },
    responses: { '202': { ...assistRun, status: 'queued', config_type: null, diff_id: null, diff_hash: null, checks: {} } },
  },
  'get /v1/assist': { responses: { '200': { items: [assistRun], next_cursor: null } } },
  'get /v1/assist/{run_id}': { params: { run_id: assistRun.run_id }, responses: { '200': assistRun } },
  // Triggers and webhooks
  'post /v1/triggers/dry-run': {
    request: { rule_id: 'overdue-visit', condition: 'promise.overdue', params: { days: 1 }, endpoint_id: 'whe_crm', window_hours: 24, late_policy: 'deliver_marked' },
    responses: {
      '200': {
        rule_id: 'overdue-visit',
        window_days: 30,
        subjects_evaluated: 48210,
        would_fire: 214,
        target_problem: null,
        sample: [{ subject_id: PROFILE, pseudonym: 'p_kvkv3okp7vnw3mqs', evidence: [{ kind: 'open_item', id: '0192f7a2-8c41-7e3a-9b5d-2c4e6f8a0b13' }] }],
      },
    },
  },
  'get /v1/triggers/firings': {
    responses: {
      '200': {
        items: [
          {
            firing_id: '0192f7e0-3a41-7b52-8c63-4d5e6f708192',
            rule_id: 'overdue-visit',
            rule_version: '4',
            subject_id: PROFILE,
            window_key: '24h:20719',
            fired_at: '2026-09-23T15:05:00Z',
            late: false,
            retracted_at: null,
            evidence: [{ kind: 'open_item', id: '0192f7a2-8c41-7e3a-9b5d-2c4e6f8a0b13' }],
          },
        ],
        next_cursor: null,
      },
    },
  },
  'get /v1/webhooks/deliveries': { params: { status: 'dead' }, responses: { '200': { items: [delivery], next_cursor: null } } },
  'post /v1/webhooks/deliveries/{delivery_id}/redeliver': {
    params: { delivery_id: delivery.delivery_id },
    responses: { '202': { ...delivery, status: 'pending', next_attempt_at: '2026-09-24T09:00:05Z' } },
  },
  'post /v1/webhooks/endpoints/{endpoint_id}/test': {
    params: { endpoint_id: 'whe_crm' },
    responses: {
      '202': {
        ...delivery,
        delivery_id: '0192f8d4-1a2b-7c3d-8e4f-5a6b7c8d9e0f',
        event_type: 'webhook.test',
        status: 'pending',
        attempts: 0,
        last_status_code: null,
        last_error: null,
        created_at: '2026-09-24T12:00:00Z',
      },
    },
  },
  // Base-wide analysis
  'get /v1/insights/schema': {
    responses: {
      '200': {
        entities: {
          episodes: { dimensions: ['intent', 'category', 'normalized_category', 'outcome', 'channel', 'source_id', 'initiated_by', 'sentiment_band'], metrics: ['count', 'profiles', 'avg_sentiment'] },
          open_items: { dimensions: ['owner', 'status', 'object_type', 'expected_operation', 'age_band'], metrics: ['count', 'profiles'] },
        },
        min_group_size: 10,
      },
    },
  },
  'get /v1/insights/overview': {
    responses: {
      '200': {
        day: '2026-09-22',
        dimensions: { 'episodes.normalized_category': { technician_visit: 1840, invoice: 1312, cancellation: 204 }, 'open_items.status': { open: 912, overdue: 214 } },
        suppressed_buckets: 6,
      },
    },
  },
  'post /v1/insights/aggregate': {
    request: {
      entity: 'episodes',
      since: '2026-06-24T00:00:00Z',
      until: '2026-09-22T23:59:59Z',
      filters: { intent: ['complaint'] },
      group_by: ['normalized_category', 'channel'],
      metrics: ['count', 'profiles'],
    },
    responses: {
      '200': {
        min_group_size: 10,
        suppressed_groups: 3,
        rows: [
          { group: { normalized_category: 'technician_visit', channel: 'voice' }, count: 1204, profiles: 1030, avg_sentiment: null },
          { group: { normalized_category: 'technician_visit', channel: 'whatsapp' }, count: 636, profiles: 588, avg_sentiment: null },
        ],
      },
    },
  },
  'post /v1/insights/profiles': {
    request: { entity: 'episodes', since: '2026-06-24T00:00:00Z', until: '2026-09-22T23:59:59Z', filters: { intent: ['complaint'], normalized_category: ['technician_visit'] }, min_matches: 3, limit: 50 },
    responses: { '200': { items: [{ pseudonym: 'p_kvkv3okp7vnw3mqs', matches: 3, reason: '3 episodes with intent complaint and category technician_visit' }], next_cursor: null } },
  },
  'post /v1/insights/search': {
    request: { query: 'technician did not show up and the customer asked for a credit', since: '2026-06-24T00:00:00Z', until: '2026-09-22T23:59:59Z', limit: 10 },
    responses: {
      '200': {
        hits: [
          {
            pseudonym: 'p_kvkv3okp7vnw3mqs',
            episode_id: EPISODE,
            category: 'technician_visit',
            outcome: 'resolved',
            ended_at: '2026-09-22T17:03:00Z',
            score: 0.87,
            snippet: 'Technician did not show up for the visit promised for the morning; the customer asked for [redacted].',
          },
        ],
        scanned: 18420,
        truncated: false,
        notice: 'Pseudonymous results. Reveal a handle only with a reason.',
      },
    },
  },
  'post /v1/insights/episodes/sample': {
    request: { since: '2026-09-01T00:00:00Z', until: '2026-09-22T23:59:59Z', filters: { normalized_category: ['invoice'], outcome: ['resolved'] }, limit: 5 },
    responses: {
      '200': {
        items: [
          {
            pseudonym: 'p_kvkv3okp7vnw3mqs',
            episode_id: '0192f7a3-7c32-7b0c-9e16-5b8a0f1c2e10',
            channel: 'app',
            intent: 'dispute',
            category: 'invoice',
            outcome: 'resolved',
            sentiment: -0.4,
            ended_at: '2026-09-22T17:35:00Z',
            summary: 'Disputed the August bill after a missed visit; a credit was applied by the billing agent.',
          },
        ],
        notice: 'Redacted sample. Handles and free text with personal data are removed.',
      },
    },
  },
  'post /v1/insights/context': {
    request: { pseudonym: 'p_kvkv3okp7vnw3mqs' },
    responses: {
      '200': {
        pseudonym: 'p_kvkv3okp7vnw3mqs',
        kind: 'person',
        facts: [{ predicate: 'plan', value: 'family', since: '2021-04-10' }],
        episodes: [{ category: 'technician_visit', outcome: 'open', ended_at: '2026-09-22T17:03:00Z' }],
        open_items: [{ kind: 'promise', what: 'Technician visit rescheduled', status: 'open' }],
        traits: [{ name: 'recurring_complaint', expires_at: '2026-11-02T17:02:11Z' }],
        notice: 'Analyst view. Sensitive categories are left out unless your policy opens them.',
      },
    },
  },
  'post /v1/insights/lineage': {
    request: { pseudonym: 'p_kvkv3okp7vnw3mqs', item_id: FACT },
    responses: {
      '200': {
        item: { id: FACT, kind: 'fact', predicate: 'preferred_visit_time' },
        events: [{ event_id: '0192f7a1-6c1e-7c3a-9b1e-5d2f8a4c0e11', channel: 'whatsapp', kind: 'message', occurred_at: '2026-09-22T17:02:11Z' }],
      },
    },
  },
  'post /v1/insights/reveal': {
    request: { pseudonym: 'p_kvkv3okp7vnw3mqs', reason: 'Customer escalated to the ombudsman; case OMB-2026-311' },
    responses: { '200': { pseudonym: 'p_kvkv3okp7vnw3mqs', kind: 'person', handles: [{ type: 'phone_e164', value: '+14155550123', scope: null }] } },
  },
}
