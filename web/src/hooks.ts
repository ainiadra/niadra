import { useEffect, useRef, useState, type RefObject } from 'react'

/**
 * "Has this sentinel gone past the top?" via IntersectionObserver, not via
 * scroll events: the observer does not fire on every frame and also responds to
 * layout changes and anchor jumps.
 */
export function usePassed<T extends HTMLElement>(): [RefObject<T | null>, boolean] {
  const ref = useRef<T>(null)
  const [passed, setPassed] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(
      ([e]) => setPassed(!e.isIntersecting && e.boundingClientRect.top < 0),
      { threshold: 0 },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [])

  return [ref, passed]
}

/** "Is this element on screen?" Used to hide the action bar near the closing. */
export function useInView<T extends HTMLElement>(): [RefObject<T | null>, boolean] {
  const ref = useRef<T>(null)
  const [inView, setInView] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(([e]) => setInView(e.isIntersecting), { threshold: 0 })
    io.observe(el)
    return () => io.disconnect()
  }, [])

  return [ref, inView]
}
