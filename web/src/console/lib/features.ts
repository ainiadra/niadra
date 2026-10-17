import { createContext, useContext } from 'react'
import type { ControlClient } from '../api/clients'

/**
 * The agent features a space turned on (the `features` document on the control plane). A screen of a feature that
 * is off stays out of the rail and the tabs.
 */
export type Feature = 'turns' | 'state' | 'agent_state' | 'signals' | 'claims' | 'coordination' | 'measurement'

export interface Features {
  status: 'loading' | 'ready'
  on: ReadonlySet<string>
}

const NO_FEATURES: Features = { status: 'loading', on: new Set() }

export const FeaturesCtx = createContext<Features>(NO_FEATURES)
export const useFeatures = () => useContext(FeaturesCtx)

/** The features the space turned on. */
export async function loadFeatures(control: ControlClient, spaceId: string, signal: AbortSignal): Promise<Set<string>> {
  const doc = await control.configDocument(spaceId, 'features', { signal })
  const enabled = (doc.document as { enabled?: unknown } | null)?.enabled
  return new Set(Array.isArray(enabled) ? enabled.map(String) : [])
}
