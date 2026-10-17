import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, type NavigateFunction } from 'react-router-dom'
import { SCREENS, screenOf, type ScreenId } from '../lib/screens'
import { markDone, readRun, writeRun, type TutorialRun } from '../lib/tutorialProgress'
import { tutorial, type TutorialStep } from '../lib/tutorials'
import { useSpace } from '../session'
import { Icon } from './Icon'
import { btnPrimary, btnQuiet, btnSecondary, small } from './styles'

/** The player and the hub share the run through sessionStorage and this event. */
const RUN_EVENT = 'niadra:tutorial'

/** The screens that show one item: a tutorial step there waits for the person to open an item from the list. */
const DETAIL: Partial<Record<ScreenId, ScreenId>> = { profile: 'customers', receipt: 'receipts', turn: 'turns', agent: 'agents' }

/** Where a step happens, with the tab it asks for. */
function stepPath(step: TutorialStep, space: string): string {
  const to = SCREENS[DETAIL[step.screen] ?? step.screen].to(space)
  return step.tab ? `${to}?aba=${encodeURIComponent(step.tab)}` : to
}

/** Starts a tutorial at a step and opens its screen; the player picks it up from the run. */
export function startTutorial(navigate: NavigateFunction, space: string, id: string, step = 0): void {
  const t = tutorial(id)
  if (!t) return
  writeRun({ id, step })
  window.dispatchEvent(new Event(RUN_EVENT))
  navigate(stepPath(t.steps[step]!, space))
}

/** The highlight of the part of the screen a step points at: Tailwind classes, added and removed by hand. */
const RING = ['outline', 'outline-[3px]', 'outline-brand', 'outline-offset-4', 'rounded-lg']

/**
 * The guide that follows the person from screen to screen during a tutorial: the step, where to click, the
 * part of the screen highlighted, and Anterior, Próximo and Concluir. It lives in sessionStorage, so a click
 * to another screen does not end it, and a step on a detail screen waits for the person to open an item.
 */
export function TutorialPlayer() {
  const { space, auth, control } = useSpace()
  const loc = useLocation()
  const navigate = useNavigate()
  const [run, setRun] = useState<TutorialRun | null>(readRun)
  const stepText = useRef<HTMLParagraphElement>(null)

  useEffect(() => {
    const sync = () => setRun(readRun())
    window.addEventListener(RUN_EVENT, sync)
    return () => window.removeEventListener(RUN_EVENT, sync)
  }, [])

  const t = run ? tutorial(run.id) : null
  const index = run && t ? Math.min(run.step, t.steps.length - 1) : 0
  const step = t?.steps[index] ?? null
  const here = screenOf(loc.pathname)

  const go = useCallback(
    (next: number) => {
      if (!t) return
      const target = t.steps[next]!
      writeRun({ id: t.id, step: next })
      setRun({ id: t.id, step: next })
      const elsewhere = screenOf(loc.pathname) !== target.screen && !(DETAIL[target.screen] && screenOf(loc.pathname) === DETAIL[target.screen])
      // A step on another tab of the same screen: the address asks for the tab, and the screen follows it.
      const otherTab = Boolean(target.tab) && new URLSearchParams(loc.search).get('aba') !== target.tab
      if (elsewhere || otherTab) navigate(stepPath(target, space.space_id))
    },
    [t, loc.pathname, loc.search, navigate, space],
  )

  // Opening the item a detail step waits for moves the guide to that step by itself.
  useEffect(() => {
    if (!t || !run) return
    const next = t.steps[index + 1]
    if (next && DETAIL[next.screen] && here === next.screen && here !== t.steps[index]!.screen) {
      writeRun({ id: t.id, step: index + 1 })
      setRun({ id: t.id, step: index + 1 })
    }
  }, [here, t, run, index])

  // The highlight: the anchor may arrive after the data, so look for it for a few seconds.
  useEffect(() => {
    if (!step?.anchor || here !== step.screen) return
    let el: HTMLElement | null = null
    let tries = 0
    const find = () => {
      el = document.querySelector<HTMLElement>(`[data-tour="${step.anchor}"]`)
      tries += 1
      if (el) {
        clearInterval(timer)
        el.classList.add(...RING)
        el.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
      } else if (tries > 25) clearInterval(timer)
    }
    const timer = window.setInterval(find, 200)
    find()
    return () => {
      window.clearInterval(timer)
      el?.classList.remove(...RING)
    }
  }, [step, here])

  useEffect(() => {
    stepText.current?.focus()
  }, [index, run?.id])

  if (!run || !t || !step) return null

  const close = () => {
    writeRun(null)
    setRun(null)
  }
  const finish = () => {
    markDone(auth.claims.sub, t.id, control)
    close()
  }
  const last = index === t.steps.length - 1
  const onScreen = here === step.screen
  const waitsForItem = !onScreen && DETAIL[step.screen] !== undefined
  const listOf = DETAIL[step.screen]

  return (
    <aside
      role="region"
      aria-label={`Tutorial: ${t.title}`}
      className="fixed inset-x-0 bottom-0 z-[70] border-t border-line bg-surface px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-4 shadow-[0_-8px_32px_-20px_rgba(24,28,18,0.35)] lg:inset-x-auto lg:bottom-6 lg:right-6 lg:w-[23rem] lg:rounded-xl lg:border lg:pb-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[0.75rem] font-semibold text-ink2">
            Passo {index + 1} de {t.steps.length}
          </p>
          <p className="text-[0.9rem] font-semibold text-ink">{t.title}</p>
        </div>
        <button type="button" onClick={close} className="-m-2 inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-ink2 hover:bg-bg hover:text-ink lg:h-9 lg:w-9" aria-label="Sair do tutorial">
          <Icon name="close" />
        </button>
      </div>
      <p ref={stepText} tabIndex={-1} className="mt-2 text-[0.875rem] leading-5 text-ink outline-none">
        {step.text}
      </p>
      {!onScreen && (
        <p className={`${small} mt-2`}>
          {waitsForItem ? (
            <>
              Este passo é na tela de um item: abra um item da lista em {SCREENS[listOf!].label} para seguir.{' '}
              {here !== listOf && (
                <Link to={stepPath(step, space.space_id)} className="font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4">
                  Ir para {SCREENS[listOf!].label}
                </Link>
              )}
            </>
          ) : (
            <>
              Este passo é em {SCREENS[step.screen].label}.{' '}
              <Link to={stepPath(step, space.space_id)} className="font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4">
                Me leve lá
              </Link>
            </>
          )}
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <button type="button" className={btnQuiet} onClick={() => go(index - 1)} disabled={index === 0}>
          Anterior
        </button>
        {last ? (
          <button type="button" className={btnPrimary} onClick={finish}>
            <Icon name="check" size={16} />
            Concluir
          </button>
        ) : (
          <button type="button" className={btnSecondary} onClick={() => go(index + 1)}>
            Próximo
            <Icon name="chevron" size={16} />
          </button>
        )}
      </div>
    </aside>
  )
}
