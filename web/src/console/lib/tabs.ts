import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

/**
 * The tab of a screen, opened by `?aba=<key>` when the address asks for one: a link from another screen or a
 * tutorial step lands on the right tab, and a later change of the address (the next step of a tutorial on the
 * same screen) switches it. Clicking a tab keeps the address as it is.
 */
export function useTab<K extends string>(keys: readonly K[], fallback: K): [K, (k: K) => void] {
  const [params] = useSearchParams()
  const asked = params.get('aba')
  const valid = (k: string | null): k is K => k !== null && (keys as readonly string[]).includes(k)
  const [tab, setTab] = useState<K>(valid(asked) ? asked : fallback)
  useEffect(() => {
    if (valid(asked)) setTab(asked)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asked])
  return [tab, setTab]
}
