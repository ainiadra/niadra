import type { DiffOut } from '../api/types'

/**
 * Where a configuration document in force came from: the Niadra default, what Niadra
 * inferred from the traffic (diffs authored `niadra:observed`), or a declaration a person approved. Zero touch
 * means the first two are complete answers: nothing here is ever a task, and a declaration is an optional
 * adjustment that wins over what was inferred.
 */
export type Provenance =
  | { kind: 'default' }
  | { kind: 'inferred'; at: string }
  | { kind: 'declared'; diff: DiffOut }

/** The author the control plane records on the diffs the observed layer writes. */
const OBSERVED_AUTHOR = 'niadra:observed'

export const isObserved = (d: Pick<DiffOut, 'author_id' | 'author_label'>) => !d.author_id && d.author_label === OBSERVED_AUTHOR

/** The latest applied diff of the document decides; with none, the document is the default. */
export function provenanceOf(type: string, diffs: readonly DiffOut[]): Provenance {
  const applied = diffs
    .filter((d) => d.type === type && d.status === 'applied')
    .sort((a, b) => (b.applied_version ?? 0) - (a.applied_version ?? 0) || b.created_at.localeCompare(a.created_at))
  const last = applied[0]
  if (!last) return { kind: 'default' }
  if (isObserved(last)) return { kind: 'inferred', at: last.created_at }
  return { kind: 'declared', diff: last }
}

/**
 * The latest document the observed layer recorded for `type`: the whole of what Niadra inferred of it from the
 * traffic, which the cell serves under any declaration. Null when Niadra inferred nothing of it.
 */
export function observedDocument(type: string, diffs: readonly DiffOut[]): { at: string; document: unknown } | null {
  const last = diffs
    .filter((d) => d.type === type && d.status === 'applied' && isObserved(d))
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
  return last ? { at: last.created_at, document: last.document } : null
}
