import { useRef, useState } from 'react'
import { useT } from '../i18n'
import { Link } from '../i18n/link'
import { EMAIL_RE, personalKind } from '../lib/email'
import { readOrigin } from '../lib/origin'
import { btnGhost, btnPrimary, h2cls, panel, textLink, wrap } from '../ui'

type Status = 'idle' | 'sending' | 'sent' | 'failed' | 'limited'
type Errors = { email?: string; what?: string }

const field = (bad: boolean) =>
  `w-full rounded-lg border bg-bg px-4 py-3 text-[1rem] text-ink placeholder:text-ink3 transition-colors focus:outline-none focus-visible:outline-2 focus-visible:outline-ink ${
    bad ? 'border-2 border-ink' : 'border-edge'
  }`
const labelCls = 'mb-1.5 block text-[0.88rem] font-medium text-ink2'

/**
 * The short early-access request, at the end of the pages a buyer or a builder reads with intent (the
 * benchmark, each integration page, the posts): a work e-mail and what the person is building, nothing
 * else. It goes to the same API and the same table as the /enterprise form (`form: 'quick'`), with the
 * page it was sent from, so the founder sees which page converts. Same rules: work e-mail only,
 * honeypot, rate limit. No count of seats and no waiting list: Niadra opens to companies by request.
 */
export function QuickAccess() {
  const { t, lang } = useT()
  const q = t.quick
  const e = t.enterprise
  const [email, setEmail] = useState('')
  const [what, setWhat] = useState('')
  const [trap, setTrap] = useState('')
  const [errors, setErrors] = useState<Errors>({})
  const [status, setStatus] = useState<Status>('idle')
  const form = useRef<HTMLFormElement>(null)

  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault()
    if (status === 'sending') return
    const next: Errors = {}
    if (!email.trim()) next.email = e.errRequired
    else if (!EMAIL_RE.test(email.trim())) next.email = e.errEmail
    else if (personalKind(email)) next.email = e.errCorporate
    if (!what.trim()) next.what = e.errRequired
    setErrors(next)
    const first = (Object.keys(next) as (keyof Errors)[])[0]
    if (first) {
      form.current?.querySelector<HTMLElement>(`[name="${first}"]`)?.focus()
      return
    }
    setStatus('sending')
    try {
      const res = await fetch('/api/contato', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          form: 'quick',
          email: email.trim(),
          message: what.trim(),
          interest: 'conversa',
          lang,
          page: window.location.pathname,
          origin: readOrigin(),
          website: trap,
        }),
      })
      if (res.ok) setStatus('sent')
      else if (res.status === 429) setStatus('limited')
      else if (res.status === 400) {
        const body = (await res.json().catch(() => ({}))) as { fields?: string[]; reasons?: { email?: string } }
        const emailMsg = { free: e.errCorporate, disposable: e.errCorporate, domain: e.errDomain }[body.reasons?.email ?? ''] ?? e.errEmail
        const bad: Errors = {}
        for (const f of body.fields ?? []) {
          if (f === 'email') bad.email = emailMsg
          if (f === 'message') bad.what = e.errRequired
        }
        setErrors(bad)
        setStatus(Object.keys(bad).length ? 'idle' : 'failed')
      } else setStatus('failed')
    } catch {
      setStatus('failed')
    }
  }

  const err = (k: keyof Errors) =>
    errors[k] ? (
      <p id={`quick-err-${k}`} className="mt-1.5 text-[0.88rem] font-medium text-ink">
        {errors[k]}
      </p>
    ) : null

  return (
    // The last section before the lime closing stays light: paper, not pale lime.
    <section className="py-12 sm:py-16 lg:py-24" aria-labelledby="quick-access-h2">
      <div className={`${wrap} grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:items-start lg:gap-14`}>
        <div>
          <h2 id="quick-access-h2" className={h2cls}>{q.h2}</h2>
          <p className="mt-4 max-w-[52ch] text-[1.02rem] leading-relaxed text-ink2 sm:text-[1.08rem]">{q.sub}</p>
          <Link to="/enterprise" className={`${textLink} mt-4`}>{q.full}</Link>
        </div>

        <div className={`${panel} p-5 sm:p-7`}>
          {status === 'sent' ? (
            <div role="status">
              <p className="flex items-center gap-2.5 text-[1.06rem] font-semibold text-ink">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand text-on-brand">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M4.5 12.5l5 5L20 7" />
                  </svg>
                </span>
                {q.sentTitle}
              </p>
              <p className="mt-2 leading-relaxed text-ink2">{q.sentBody}</p>
              <button type="button" className={`${btnGhost} mt-5`} onClick={() => { setEmail(''); setWhat(''); setStatus('idle') }}>
                {e.again}
              </button>
            </div>
          ) : (
            <form ref={form} className="relative grid gap-4" onSubmit={submit} noValidate>
              <div>
                <label className={labelCls} htmlFor="quick-email">{q.email}</label>
                <input
                  id="quick-email"
                  name="email"
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  className={field(!!errors.email)}
                  placeholder={e.emailPh}
                  value={email}
                  onChange={(ev) => { setEmail(ev.target.value); if (errors.email) setErrors((s) => ({ ...s, email: undefined })) }}
                  aria-invalid={!!errors.email}
                  aria-describedby={errors.email ? 'quick-err-email' : undefined}
                />
                {err('email')}
              </div>
              <div>
                <label className={labelCls} htmlFor="quick-what">{q.what}</label>
                <textarea
                  id="quick-what"
                  name="what"
                  rows={3}
                  className={`${field(!!errors.what)} resize-y`}
                  placeholder={q.whatPh}
                  value={what}
                  onChange={(ev) => { setWhat(ev.target.value); if (errors.what) setErrors((s) => ({ ...s, what: undefined })) }}
                  aria-invalid={!!errors.what}
                  aria-describedby={errors.what ? 'quick-err-what' : undefined}
                />
                {err('what')}
              </div>
              {/* Off screen and out of the tab order: whoever fills it in is a spam script. */}
              <div className="absolute -left-[9999px] h-px w-px overflow-hidden" aria-hidden="true">
                <label htmlFor="quick-website">Website</label>
                <input id="quick-website" name="website" tabIndex={-1} autoComplete="off" value={trap} onChange={(ev) => setTrap(ev.target.value)} />
              </div>
              <button className={`${btnPrimary} w-full disabled:cursor-wait disabled:opacity-70`} type="submit" disabled={status === 'sending'}>
                {status === 'sending' ? e.sending : q.submit}
              </button>
              {(status === 'failed' || status === 'limited') && (
                <p className="rounded-lg border border-edge bg-bg px-4 py-3 text-[0.94rem] leading-relaxed text-ink" role="alert">
                  {status === 'limited' ? e.errRate : e.errSend}
                </p>
              )}
              <p className="text-[0.86rem] leading-relaxed text-ink3">{e.hint}</p>
            </form>
          )}
        </div>
      </div>
    </section>
  )
}
