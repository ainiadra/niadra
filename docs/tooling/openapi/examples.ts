import { readFileSync } from 'node:fs'
import type { OpExtra } from './model'

/** The voice agent's pack of the guide pages, compiled by the server for a synthetic customer (tooling/pack). */
const PACK = JSON.parse(readFileSync(new URL('../pack/examples.json', import.meta.url), 'utf8')).packs.en.voice as {
  text: string
  withheld: number
  as_of: string
  etag: string
  manifest_hash: string
  variables: Record<string, string>
}

/**
 * Request and response examples written for the documentation, keyed by "method path".
 * Each one is checked against the contract schema in the documentation test.
 */
export const EXAMPLES = {
  "post /v1/batch": {
    "request": {
      "items": [
        {
          "type": "event",
          "kind": "message",
          "idempotency_key": "wamid.HBgLMTQxNTU1NTAxMjMVAgASGBQzQUQ",
          "channel": "whatsapp",
          "conversation_id": "wa-8812",
          "handles": [
            {
              "type": "phone_e164",
              "value": "+14155550123"
            }
          ],
          "speaker": {
            "role": "customer"
          },
          "direction": "inbound",
          "content": {
            "type": "text",
            "text": "The technician never showed up. I am calling you."
          },
          "occurred_at": "2026-09-22T17:02:11Z"
        },
        {
          "type": "event",
          "kind": "action",
          "idempotency_key": "0192f7a1-6c1e-7c3a-9b1e-5d2f8a4c0e11",
          "channel": "erp",
          "task_id": "billing-7741",
          "handles": [
            {
              "type": "system_id",
              "value": "48213",
              "scope": "crm"
            }
          ],
          "object_refs": [
            {
              "type": "invoice",
              "namespace": "erp",
              "id": "0823"
            }
          ],
          "speaker": {
            "role": "ai_agent",
            "id": "billing-agent"
          },
          "action": {
            "operation": "credit",
            "result": "$40 credit on the August bill",
            "closes": {
              "object": {
                "type": "invoice",
                "namespace": "erp",
                "id": "0823"
              },
              "operation": "dispute"
            }
          },
          "occurred_at": "2026-09-22T17:06:21Z"
        }
      ]
    },
    "responses": {
      "207": {
        "accepted": 2,
        "duplicates": 0,
        "errors": []
      }
    }
  },
  "post /v1/media/uploads": {
    "request": {
      "content_type": "audio/ogg",
      "size_bytes": 48213,
      "sha256": "a3f1c2d4e5b6978812ab34cd56ef7890a1b2c3d4e5f60718293a4b5c6d7e8f90"
    },
    "responses": {
      "201": {
        "media_ref": "med_0192f7b2a4c87e319d5b6f0e1c2a3b4d",
        "upload_url": "https://niadra-cell-111122223333.s3.us-east-2.amazonaws.com/media/0192f0a0-3d4e-7f50-8b61-7c8d9e0f1a23/_/0192f7b2-a4c8-7e31-9d5b-6f0e1c2a3b4d?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=900&X-Amz-Signature=...",
        "expires_at": "2026-09-22T17:17:03Z"
      }
    }
  },
  "post /v1/feedback": {
    "request": {
      "idempotency_key": "fb-2026-09-22-0001",
      "subject": {
        "type": "phone_e164",
        "value": "+14155550123"
      },
      "action": "resolve_open_item",
      "open_item_id": "0192f7a2-8c41-7e3a-9b5d-2c4e6f8a0b13",
      "reason": "Visit rescheduled and confirmed by the customer"
    }
  },
  "post /v1/context": {
    "request": {
      "subject": {
        "type": "phone_e164",
        "value": "+14155550123"
      },
      "view": "voice",
      "verification": "V1",
      "conversation_id": "call-4471",
      "target": {
        "provider": "openai",
        "model": "gpt-realtime"
      }
    },
    "responses": {
      "200": {
        "not_modified": false,
        "text": PACK.text,
        "variables": PACK.variables,
        "version": "g42",
        "etag": PACK.etag,
        "manifest_hash": PACK.manifest_hash,
        "as_of": PACK.as_of,
        "lag_seconds": 60.8,
        "coverage": [
          {
            "source_id": "0192f79e-11ac-7d42-9f50-8e3f40617c82",
            "status": "ok",
            "last_event_at": "2026-09-22T17:02:11Z"
          }
        ],
        "verification": {
          "requested": "V1",
          "effective": "V1",
          "reason": null
        },
        "withheld": PACK.withheld,
        "live": [
          {
            "at": "2026-09-22T17:06:30Z",
            "channel": "whatsapp",
            "kind": "message",
            "speaker": "customer",
            "text": "I am calling you now.",
            "source_id": "0192f79e-11ac-7d42-9f50-8e3f40617c82"
          }
        ],
        "live_complete": true,
        "delta": null,
        "cache": null,
        "timing": {
          "resolve": 3.1,
          "policy": 1.2,
          "pack": 6.4
        },
        "path": "t0",
        "degraded": false
      }
    }
  },
  "post /v1/subject-tokens": {
    "request": {
      "subject": {
        "type": "phone_e164",
        "value": "+14155550123"
      },
      "verification": "V1",
      "conversation_id": "call-4471"
    },
    "responses": {
      "201": {
        "token": "nst1.eyJleHAiOjE3NTg1NjIxMjN9.Q2sX...",
        "expires_at": "2026-09-22T17:22:03Z"
      }
    }
  },
  "post /v1/history/search": {
    "request": {
      "subject": {
        "type": "phone_e164",
        "value": "+14155550123"
      },
      "query": "credit for missed technician visit",
      "max_tokens": 300,
      "verification": "V1",
      "conversation_id": "call-4471"
    },
    "responses": {
      "200": {
        "items": [
          {
            "id": "0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d34",
            "kind": "episode",
            "text": "Technician did not show up · $40 credit on the April bill · settled",
            "at": "2026-03-12T14:20:00Z",
            "channel": "voice",
            "source_id": "0192f79e-11aa-7b20-9f3e-6c1d2e4f5a60",
            "outcome": "resolved"
          },
          {
            "id": "0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d35",
            "kind": "episode",
            "text": "Customer confirmed the credit and the new visit",
            "at": "2026-03-14T12:02:00Z",
            "channel": "whatsapp",
            "source_id": "0192f79e-11ac-7d42-9f50-8e3f40617c82",
            "outcome": "resolved"
          }
        ],
        "recurrence": {
          "category": "technician_visit",
          "occurrences": 1,
          "window_days": 90,
          "last_at": "2026-09-22T17:02:00Z",
          "last_outcome": "unresolved",
          "last_resolution": null
        },
        "withheld": 0,
        "as_of": "2026-09-22T17:07:02Z",
        "tokens_used": 61,
        "degraded": null
      }
    }
  },
  "post /v1/history/timeline": {
    "request": {
      "subject": {
        "type": "phone_e164",
        "value": "+14155550123"
      },
      "filters": {
        "since": "2026-01-01T00:00:00Z"
      },
      "limit": 20,
      "verification": "V1"
    },
    "responses": {
      "200": {
        "items": [
          {
            "id": "0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d35",
            "kind": "episode",
            "text": "Customer confirmed the credit and the new visit",
            "at": "2026-03-14T12:02:00Z",
            "channel": "whatsapp",
            "source_id": "0192f79e-11ac-7d42-9f50-8e3f40617c82",
            "outcome": "resolved"
          },
          {
            "id": "0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d34",
            "kind": "episode",
            "text": "Technician did not show up · $40 credit on the April bill · settled",
            "at": "2026-03-12T14:20:00Z",
            "channel": "voice",
            "source_id": "0192f79e-11aa-7b20-9f3e-6c1d2e4f5a60",
            "outcome": "resolved"
          }
        ],
        "next_cursor": "c_01J8ZQ",
        "withheld": 1,
        "as_of": "2026-09-22T17:07:02Z"
      }
    }
  },
  "get /v1/history/items/{item_id}": {
    "params": {
      "item_id": "0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d34"
    },
    "responses": {
      "200": {
        "id": "0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d34",
        "kind": "episode",
        "summary": "Technician did not show up for the scheduled visit.",
        "requested": "A new visit and compensation",
        "promises": [
          {
            "by": "company",
            "what": "New visit on Mar 13, morning",
            "due_at": "2026-03-13T15:00:00Z",
            "status": "kept"
          }
        ],
        "outcome": "resolved",
        "resolution": "$40 credit on the April bill",
        "derived": [],
        "timeline": [],
        "as_of": "2026-09-22T17:07:02Z"
      }
    }
  },
  "post /v1/history/open": {
    "request": {
      "item_id": "0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d34",
      "verification": "V1",
      "conversation_id": "call-4471"
    },
    "responses": {
      "200": {
        "id": "0192f7a3-7c32-7b0c-9e16-5b8a0f1c2d34",
        "kind": "episode",
        "summary": "Technician did not show up for the scheduled visit.",
        "requested": "A new visit and compensation",
        "promises": [
          {
            "by": "company",
            "what": "New visit on Mar 13, morning",
            "due_at": "2026-03-13T15:00:00Z",
            "status": "kept"
          }
        ],
        "outcome": "resolved",
        "resolution": "$40 credit on the April bill",
        "derived": [],
        "timeline": [],
        "as_of": "2026-09-22T17:07:02Z"
      }
    }
  },
  "get /v1/history/tools": {
    "responses": {
      "200": {
        "tools": [
          {
            "type": "function",
            "function": {
              "name": "search_customer_history",
              "description": "Search everything that happened with this customer. Check \"From the history\" in the context first.",
              "parameters": {
                "type": "object",
                "properties": {
                  "query": {
                    "type": "string"
                  }
                },
                "required": [
                  "query"
                ]
              }
            }
          }
        ]
      }
    }
  },
  "get /v1/objects/{object_type}/{namespace}/{external_id}": {
    "responses": {
      "200": {
        "ref": {
          "type": "invoice",
          "namespace": "erp",
          "id": "0823"
        },
        "as_of": "2026-09-22T17:06:21Z",
        "fields": {
          "status": {
            "v": "credited",
            "logic": "yes",
            "status": "fresh",
            "claim_safe": false,
            "observed_at": "2026-09-22T17:06:21Z",
            "src": "erp"
          },
          "amount_due": {
            "v": 49.9,
            "logic": "yes",
            "status": "fresh",
            "claim_safe": false,
            "observed_at": "2026-09-22T17:06:21Z",
            "src": "erp"
          }
        }
      }
    },
    "params": {
      "object_type": "invoice",
      "namespace": "erp",
      "external_id": "0823"
    }
  },
  "get /v1/objects/{object_type}/{namespace}/{external_id}/timeline": {
    "params": {
      "object_type": "invoice",
      "namespace": "erp",
      "external_id": "0823"
    }
  },
  "post /v1/identity/assertions/{assertion_id}/retract": {
    "request": {
      "reason": "Shared store tablet linked two customers"
    }
  },
  "post /v1/identity/links": {
    "request": {
      "person": {
        "type": "phone_e164",
        "value": "+14155550123"
      },
      "organization": {
        "type": "system_id",
        "value": "ACC-2201",
        "scope": "crm",
        "subject_kind": "account"
      },
      "role": "buyer",
      "method": "system_import"
    }
  },
  "control:get /v1/sources": {
    "responses": {
      "200": {
        "items": [
          {
            "id": "0192f0a0-5f60-7172-8d83-9e0f1a2b3c45",
            "name": "Billing agent",
            "vendor": "in-house",
            "channel": "erp",
            "purpose": "billing",
            "audience": "internal_agent",
            "verification_ceiling": "no_customer",
            "confirms_before_acting": false,
            "navigation": true,
            "environment": "production",
            "created_at": "2026-08-01T12:00:00Z"
          }
        ],
        "next_cursor": null
      }
    }
  }
} as Record<string, OpExtra>
