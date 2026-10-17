import { describe, expect, it } from 'vitest'
import { CONFIG_LABELS } from '../lib/labels'

/** Every document type the control plane serves today (the server's app/control_config.py DOC_TYPES). */
const DOC_TYPES = [
  'policies',
  'schemas',
  'mappings',
  'views',
  'trait-rules',
  'trigger-rules',
  'webhook-endpoints',
  'export-schedules',
  'review-sampling',
  'experiments',
  'agent-memory',
  'media',
  'extraction-guidance',
  'ingest-masking',
  'purposes',
  'settings',
  'features',
  'recording',
  'object-types',
  'tool-bindings',
  'coordination',
  'claim-contract',
  'measurement',
]

describe('document kinds', () => {
  it('has a Portuguese name for every document type the control plane serves', () => {
    const missing = DOC_TYPES.filter((t) => !CONFIG_LABELS[t] || CONFIG_LABELS[t] === t)
    expect(missing).toEqual([])
  })

  it('names the kinds the demo space showed raw', () => {
    expect(CONFIG_LABELS.measurement).toBe('Medição de desfechos')
    expect(CONFIG_LABELS['object-types']).toBe('Tipos de objeto')
    expect(CONFIG_LABELS.coordination).toBe('Coordenação dos agentes')
    expect(CONFIG_LABELS.features).toBe('Recursos ligados')
  })
})
