import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fmtDate } from '../lib/format'
import { feature, role } from '../lib/labels'
import { NAV_GROUPS, SCREENS, type ScreenId } from '../lib/screens'
import { useProgress } from '../lib/tutorialProgress'
import { TUTORIALS, type Tutorial } from '../lib/tutorials'
import { useFeatures } from '../lib/features'
import { useSpace } from '../session'
import { Chip, PageHeader } from '../ui/bits'
import { Icon } from '../ui/Icon'
import { useTitle } from '../ui/layout'
import { btnPrimary, btnQuiet, btnSecondary, card, h2, small } from '../ui/styles'
import { startTutorial } from '../ui/TutorialPlayer'

/** The hub of the guided walkthroughs: one card per job, what it teaches, and how many the person has done. */
export function Tutorials() {
  const { space, auth } = useSpace()
  useTitle('Tutoriais', space.environment === 'sandbox')
  const progress = useProgress(auth.claims.sub)
  const done = TUTORIALS.filter((t) => progress.done[t.id]).length
  return (
    <>
      <PageHeader screen="tutorials" />
      <Progress done={done} total={TUTORIALS.length} />
      {groups().map((g) => (
        <section key={g.id} aria-labelledby={`tut-${g.id}`} className="mt-8">
          <h2 id={`tut-${g.id}`} className={h2}>
            {g.label}
          </h2>
          <p className={`${small} mt-0.5`}>{g.short}</p>
          <ul className="mt-3 grid gap-4 lg:grid-cols-2" aria-label={`Tutoriais: ${g.label}`}>
            {g.tutorials.map((t) => (
              <TutorialCard key={t.id} t={t} doneAt={progress.done[t.id] ?? null} />
            ))}
          </ul>
        </section>
      ))}
    </>
  )
}

/** The screen a tutorial is about: where most of its steps happen, the first in a tie (a detail screen counts as its list). */
const LIST_OF: Partial<Record<ScreenId, ScreenId>> = { profile: 'customers', receipt: 'receipts', turn: 'turns', agent: 'agents' }
const screenOfTutorial = (t: Tutorial): ScreenId => {
  if (t.screen) return t.screen
  const counts = new Map<ScreenId, number>()
  for (const s of t.steps) {
    const id = LIST_OF[s.screen] ?? s.screen
    counts.set(id, (counts.get(id) ?? 0) + 1)
  }
  let best: ScreenId = LIST_OF[t.steps[0]!.screen] ?? t.steps[0]!.screen
  for (const [id, n] of counts) if (n > (counts.get(best) ?? 0)) best = id
  return best
}

/** The tutorials in the order of the rail, under the group of the screen each one starts on. */
function groups(): { id: string; label: string; short: string; tutorials: Tutorial[] }[] {
  return NAV_GROUPS.map((g) => ({
    id: g.id,
    label: g.id === 'start' ? 'Para começar' : g.label,
    short: g.id === 'start' ? 'O tour pelas telas principais' : g.short,
    tutorials: TUTORIALS.filter((t) => g.screens.includes(screenOfTutorial(t))),
  })).filter((g) => g.tutorials.length > 0)
}

/** How many tutorials the person finished, as a bar and in words. */
export function Progress({ done, total }: { done: number; total: number }) {
  return (
    <div className={`${card} px-4 py-3 sm:px-5`} role="group" aria-label="Seu progresso nos tutoriais">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-[0.875rem] font-semibold text-ink">
          {done === total ? 'Você fez todos os tutoriais.' : `${done} de ${total} tutoriais concluídos`}
        </p>
        <p className={small}>O progresso fica neste navegador, para você.</p>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-bg" aria-hidden="true">
        <div className="h-full rounded-full bg-brand transition-[width]" style={{ width: `${total ? Math.round((done / total) * 100) : 0}%` }} />
      </div>
    </div>
  )
}

function TutorialCard({ t, doneAt }: { t: Tutorial; doneAt: string | null }) {
  const { space, hasRole } = useSpace()
  const features = useFeatures()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const needsRole = t.roles && !hasRole(...t.roles)
  const featureOff = t.feature && features.status === 'ready' && !features.on.has(t.feature)
  return (
    <li className={`${card} flex flex-col p-4 sm:p-5`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h2 className={h2}>{t.title}</h2>
        {doneAt ? <Chip tone="ok">Concluído em {fmtDate(doneAt)}</Chip> : <Chip>Não feito</Chip>}
      </div>
      <p className={`${small} mt-1.5 text-ink`}>{t.outcome}</p>
      <p className={`${small} mt-1`}>
        {t.minutes} min · {t.steps.length} passos
        {needsRole && ` · pede o papel ${t.roles!.map(role).join(' ou ')}`}
        {featureOff && ` · pede o recurso ${feature(t.feature!)} ligado neste espaço`}
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" className={btnPrimary} onClick={() => startTutorial(navigate, space.space_id, t.id)}>
          <Icon name="book" size={16} />
          {doneAt ? 'Fazer de novo' : 'Começar'}
        </button>
        <button type="button" className={btnQuiet} aria-expanded={open} aria-controls={`steps-${t.id}`} onClick={() => setOpen((v) => !v)}>
          {open ? 'Esconder os passos' : 'Ver os passos'}
        </button>
      </div>
      {open && (
        <ol id={`steps-${t.id}`} className="mt-4 grid gap-3 border-t border-line2 pt-4">
          {t.steps.map((step, i) => (
            <li key={i} className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0 text-[0.875rem] leading-5 text-ink">
                <span className="font-semibold">{i + 1}. </span>
                <span className="text-ink2">Em {SCREENS[step.screen].label}: </span>
                {step.text}
              </div>
              <button type="button" className={`${btnSecondary} shrink-0`} onClick={() => startTutorial(navigate, space.space_id, t.id, i)} aria-label={`Me leve ao passo ${i + 1} de ${t.title}`}>
                Me leve lá
              </button>
            </li>
          ))}
        </ol>
      )}
    </li>
  )
}
