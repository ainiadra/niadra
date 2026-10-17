import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// jsdom has no layout, so no scrolling; the shell scrolls to the top on route changes.
window.scrollTo = () => {}

// A sign-in flows through several awaited round trips (login, projects, a space token, /me)
// before a screen's own data even starts loading. `findBy*` and `waitFor` default to 1000 ms,
// plenty on a quiet laptop but not once CI runs this file next to others: real wall-clock time
// for those steps can cross 1 s under scheduling contention, with no bug in the screen itself.
// One test file already worked around this for its own first `findByLabelText` call; this sets
// the same margin for every `findBy*` and `waitFor` in every test, so the fix does not depend on
// each call site remembering to ask for it.
configure({ asyncUtilTimeout: 5000 })

// Node 26 ships an experimental localStorage global that shadows jsdom's and is
// undefined without --localstorage-file. Put jsdom's storage back, so the test
// that proves no token is stored checks a real Storage.
const dom = (globalThis as unknown as { jsdom?: { window: Window } }).jsdom
if (dom) {
  for (const name of ['localStorage', 'sessionStorage'] as const) {
    Object.defineProperty(globalThis, name, { configurable: true, value: dom.window[name] })
  }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.history.pushState({}, '', '/')
  // What the rail and the tutorials remember per person never leaks from one test into the next.
  localStorage.clear()
})
