/**
 * What the Console remembers about a person's tutorials: the ones done and whether the welcome was shown,
 * kept on the control plane (`/v1/users/me/preferences`) so they follow the person to another browser, with a
 * copy in localStorage that every screen reads at once and that stands alone when the control plane cannot
 * be reached. The tutorial running in this tab stays in sessionStorage: it survives a click to another screen
 * and ends with the tab. Storage may be refused or empty: every read falls back to nothing done.
 */

import { useSyncExternalStore } from 'react'

export interface Progress {
  /** Tutorial id to the day it was finished, ISO. */
  done: Record<string, string>
}

/** The part of the control client this module talks to. */
export interface PreferenceStore {
  preferences: (o?: { signal?: AbortSignal }) => Promise<{ preferences: Record<string, unknown> }>
  setPreference: (key: string, value: unknown) => Promise<unknown>
}

const TUTORIALS_KEY = 'tutorials'
const WELCOME_KEY = 'welcome_seen'
const doneKey = (user: string) => `niadra.console.tutorials.${user}`
const welcomeKey = (user: string) => `niadra.console.welcome.${user}`
const RUN_KEY = 'niadra.console.tutorial-run'

function read<T>(storage: () => Storage, key: string): T | null {
  try {
    const raw = storage().getItem(key)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

function write(storage: () => Storage, key: string, value: unknown): void {
  try {
    if (value === null) storage().removeItem(key)
    else storage().setItem(key, JSON.stringify(value))
  } catch {
    // The screen still works; it only forgets.
  }
}

export function readProgress(user: string): Progress {
  return read<Progress>(() => localStorage, doneKey(user)) ?? { done: {} }
}

/** Screens that show the progress read it through this, so they follow a sync or a tutorial just finished. */
export function useProgress(user: string): Progress {
  useSyncExternalStore(subscribe, () => version)
  return readProgress(user)
}

let version = 0
const listeners = new Set<() => void>()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function changed(): void {
  version += 1
  for (const listener of listeners) listener()
}

/** The tutorials a stored value names, whatever was written there: each id with the day it was done. */
function doneIn(value: unknown): Record<string, string> {
  const done = (value as Progress | null)?.done
  if (!done || typeof done !== 'object') return {}
  return Object.fromEntries(Object.entries(done).filter((entry): entry is [string, string] => typeof entry[1] === 'string'))
}

/**
 * Brings the two copies together, once per session: what either side has done is done, with the earlier day.
 * What only this browser knew goes up, so progress made before the control plane kept it is not lost. A
 * control plane that does not answer leaves the browser's copy as it is; the next session tries again.
 */
export async function syncPreferences(user: string, store: PreferenceStore, signal?: AbortSignal): Promise<void> {
  try {
    const { preferences } = await store.preferences({ signal })
    const remote = doneIn(preferences[TUTORIALS_KEY])
    const local = readProgress(user).done
    const done = { ...remote }
    for (const [id, at] of Object.entries(local)) if (!done[id] || at < done[id]) done[id] = at
    write(() => localStorage, doneKey(user), { done })
    const seenThere = preferences[WELCOME_KEY] === true
    if (seenThere) write(() => localStorage, welcomeKey(user), true)
    if (Object.keys(done).some((id) => done[id] !== remote[id])) await store.setPreference(TUTORIALS_KEY, { done })
    if (!seenThere && welcomeSeen(user)) await store.setPreference(WELCOME_KEY, true)
  } catch {
    // The browser's copy stands.
  }
  changed()
}

export function markDone(user: string, tutorialId: string, store: PreferenceStore, when = new Date()): Progress {
  const next: Progress = { done: { ...readProgress(user).done, [tutorialId]: when.toISOString() } }
  write(() => localStorage, doneKey(user), next)
  changed()
  // Kept in this browser if the write fails; the next sync sends it.
  store.setPreference(TUTORIALS_KEY, next).catch(() => undefined)
  return next
}

export function welcomeSeen(user: string): boolean {
  return read<boolean>(() => localStorage, welcomeKey(user)) === true
}

export function setWelcomeSeen(user: string, store: PreferenceStore): void {
  write(() => localStorage, welcomeKey(user), true)
  store.setPreference(WELCOME_KEY, true).catch(() => undefined)
}

export interface TutorialRun {
  id: string
  /** Zero-based. */
  step: number
}

export function readRun(): TutorialRun | null {
  const run = read<TutorialRun>(() => sessionStorage, RUN_KEY)
  return run && typeof run.id === 'string' && typeof run.step === 'number' ? run : null
}

export function writeRun(run: TutorialRun | null): void {
  write(() => sessionStorage, RUN_KEY, run)
}
