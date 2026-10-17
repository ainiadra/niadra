import { lazy, Suspense } from 'react'
import { Route } from 'react-router-dom'

// Loaded only under /console: the marketing pages never download the Console.
const ConsoleApp = lazy(() => import('./ConsoleApp'))

function Boot() {
  return (
    <div role="status" className="flex min-h-dvh items-center justify-center bg-bg text-[0.875rem] text-ink2">
      Abrindo o Console…
    </div>
  )
}

/** Mounted by src/App.tsx next to the site, outside the marketing shell. */
export const consoleRoutes = (
  <Route
    path="/console/*"
    element={
      <Suspense fallback={<Boot />}>
        <ConsoleApp />
      </Suspense>
    }
  />
)
