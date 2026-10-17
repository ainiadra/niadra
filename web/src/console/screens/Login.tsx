import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { describeError } from '../api/describe'
import { ApiError } from '../api/http'
import type { SecondFactorOut, SsoDiscoverOut, TokenOut } from '../api/types'
import { ssoFailure } from '../lib/labels'
import { bindingOf, browser, dropTab, keepForTab, newVerifier } from '../lib/ssoTab'
import { paths } from '../paths'
import { useConsole } from '../session'
import { Centered, useTitle } from '../ui/layout'
import { QrCode } from '../ui/QrCode'
import { codesLeft, ManualKey, RecoveryCodes, SavedCodes, Steps } from '../ui/SecondFactor'
import { Loading } from '../ui/States'
import { btnPrimary, btnQuiet, btnSecondary, danger, h1, hint, input, label, small } from '../ui/styles'

type Stage = 'email' | 'choice' | 'password' | 'exchange' | 'code' | 'connect' | 'verify' | 'save' | 'recovered'

/** The first factor behind the second-factor steps: the password, or the tenant's SSO. */
type Proof = { kind: 'password' } | { kind: 'sso'; code: string; verifier: string }

/** An SSO sign-in that came back from the identity provider (screens/SsoReturn.tsx). */
export interface SsoLanding {
  code: string
  verifier: string
  email: string
  from: string | null
}

/**
 * Sign-in in steps, one thing per screen:
 *  - email: the corporate e-mail first. The control plane says, by domain only, whether it signs in through
 *    the tenant's SSO;
 *  - choice: for an SSO domain, "Entrar com SSO", with the password left for admins when SSO is enforced;
 *  - password: for everyone else;
 *  - exchange: back from the identity provider, the one-time sign-in code becomes a session;
 *  - code, connect, verify and save: the Niadra second factor, the same after a password or after SSO when
 *    the tenant asks for it (a code, or a recovery code; or the first setup of the authenticator, whose
 *    recovery codes are shown once);
 *  - recovered: how many recovery codes are left after one was used.
 */
export function Login({ sso }: { sso?: SsoLanding }) {
  useTitle('Entrar')
  const { control, signIn, notice } = useConsole()
  const navigate = useNavigate()
  const loc = useLocation()
  const state = loc.state as { from?: string; email?: string; notice?: string } | null
  const wanted = sso?.from ?? state?.from
  const dest = wanted && (wanted.startsWith('/console/e/') || wanted === paths.account) ? wanted : paths.spaces
  const [stage, setStage] = useState<Stage>(sso ? 'exchange' : 'email')
  const [proof, setProof] = useState<Proof>(sso ? { kind: 'sso', code: sso.code, verifier: sso.verifier } : { kind: 'password' })
  const [email, setEmail] = useState(sso?.email ?? state?.email ?? '')
  const [discovery, setDiscovery] = useState<SsoDiscoverOut | null>(null)
  const [checkNote, setCheckNote] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const [otp, setOtp] = useState('')
  const [recovery, setRecovery] = useState(false)
  const [recoveryCode, setRecoveryCode] = useState('')
  const [enroll, setEnroll] = useState<{ secret: string; uri: string } | null>(null)
  const [codes, setCodes] = useState<string[] | null>(null)
  const [saved, setSaved] = useState(false)
  const [left, setLeft] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const emailRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)
  const otpRef = useRef<HTMLInputElement>(null)
  const recoveryRef = useRef<HTMLInputElement>(null)
  const ids = { email: useId(), password: useId(), otp: useId(), recovery: useId(), err: useId(), user: useId() }

  // Each step moves the focus: to the field the step asks for, to the title otherwise.
  const first = useRef(true)
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    if (stage === 'code' && recovery) recoveryRef.current?.focus()
    else if (stage === 'code' || stage === 'verify') otpRef.current?.focus()
    else if (stage === 'password') passwordRef.current?.focus()
    else if (stage === 'email') emailRef.current?.focus()
    else heading.current?.focus()
  }, [stage, recovery])

  const restart = () => {
    if (proof.kind === 'sso') {
      // The sign-in code belongs to one person; another account starts over at the e-mail.
      navigate(paths.login, { replace: true })
      setProof({ kind: 'password' })
    }
    setStage('email')
    setPassword('')
    setOtp('')
    setRecovery(false)
    setRecoveryCode('')
    setEnroll(null)
    setDiscovery(null)
    setCheckNote(null)
    setError(null)
  }

  const switchToRecovery = (on: boolean) => {
    setRecovery(on)
    setError(null)
  }

  // Signed in: the recovery codes of a confirmation are shown before anything else, and a spent
  // recovery code says how many remain; otherwise straight to the Console.
  function signedIn(res: TokenOut, mail: string) {
    signIn(res, mail)
    if (res.recovery_codes?.length) {
      setCodes(res.recovery_codes)
      setStage('save')
    } else if (typeof res.recovery_codes_left === 'number') {
      setLeft(res.recovery_codes_left)
      setStage('recovered')
    } else navigate(dest, { replace: true })
  }

  const attempt = (second: { totp?: string; recovery_code?: string }, p: Proof = proof): Promise<TokenOut> =>
    p.kind === 'sso' ? control.ssoExchange({ code: p.code, verifier: p.verifier, ...second }) : control.login({ email: email.trim(), password, ...second })

  const enrollment = (p: Proof = proof): Promise<SecondFactorOut> =>
    p.kind === 'sso' ? control.ssoSecondFactor(p.code, p.verifier) : control.secondFactor({ email: email.trim(), password })

  async function signInWith(second: { totp?: string; recovery_code?: string } = {}, p: Proof = proof) {
    const mail = email.trim()
    try {
      signedIn(await attempt(second, p), mail)
    } catch (err) {
      // The control plane checks the first factor, then the second (app/control.py issue_session).
      const detail = err instanceof ApiError ? (err.detail ?? '') : ''
      const unauth = err instanceof ApiError && err.status === 401
      if (unauth && detail.includes('enrollment required')) {
        try {
          const setup = await enrollment(p)
          setEnroll({ secret: setup.secret, uri: setup.totp_uri })
          setStage('connect')
        } catch (setupErr) {
          const d = describeError(setupErr)
          setError(`${d.title} ${d.detail}`)
        }
      } else if (unauth && detail.includes('second factor')) {
        if (stage === 'password' || stage === 'exchange') setStage('code')
        else if (second.recovery_code !== undefined) {
          setError('O código de recuperação não confere ou já foi usado. Cada código entra uma vez só.')
          recoveryRef.current?.focus()
        } else {
          setError('O código não confere ou já venceu. Use o código que o aplicativo mostra agora.')
          setOtp('')
          otpRef.current?.focus()
        }
      } else if (unauth && detail.includes('single sign-on required')) {
        setError('Este e-mail entra pelo SSO da empresa.')
        setDiscovery((d) => d ?? { sso: true, protocol: null, enforced: true })
        setStage('choice')
      } else if (unauth && p.kind === 'sso') {
        setError(detail.includes('no longer active') ? ssoFailure('deactivated') : 'O login pelo SSO expirou ou já foi usado. Entre de novo.')
        setProof({ kind: 'password' })
        setStage('email')
      } else if (unauth) {
        setError('E-mail ou senha não conferem.')
        if (stage !== 'password') setStage('password')
      } else {
        const d = describeError(err)
        setError(`${d.title} ${d.detail}`)
      }
    }
  }

  // Back from the identity provider: the exchange runs once, even when a strict render runs effects twice.
  const exchanged = useRef(false)
  useEffect(() => {
    if (!sso || exchanged.current) return
    exchanged.current = true
    void signInWith({}, { kind: 'sso', code: sso.code, verifier: sso.verifier })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sso])

  async function continueWithEmail(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setCheckNote(null)
    setBusy(true)
    try {
      const found = await control.ssoDiscover(email.trim())
      setDiscovery(found)
      setStage(found.sso ? 'choice' : 'password')
    } catch {
      // Without the answer, the password still works; SSO can be tried again from the start.
      setDiscovery(null)
      setCheckNote('Não foi possível conferir agora se o seu domínio entra pelo SSO. Entre com a senha ou tente de novo em instantes.')
      setStage('password')
    } finally {
      setBusy(false)
    }
  }

  async function startSso() {
    setError(null)
    setBusy(true)
    const mail = email.trim()
    try {
      const verifier = newVerifier()
      const started = await control.ssoStart(mail, await bindingOf(verifier))
      keepForTab({ verifier, email: mail, from: wanted ?? null })
      browser.go(started.authorize_url)
    } catch (err) {
      dropTab()
      const d = describeError(err)
      setError(`${d.title} ${d.detail}`)
      setBusy(false)
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (stage === 'code' && recovery) await signInWith({ recovery_code: recoveryCode.trim() })
      else if (stage === 'code' || stage === 'verify') await signInWith({ totp: otp.trim() })
      else await signInWith()
    } finally {
      setBusy(false)
    }
  }

  const alert = error && (
    <p id={ids.err} role="alert" className={`text-[0.875rem] font-semibold ${danger.text}`}>
      {error}
    </p>
  )

  if (stage === 'exchange') {
    return (
      <Centered>
        <h1 ref={heading} tabIndex={-1} className={`${h1} outline-none`}>
          Entrando pelo SSO
        </h1>
        <AccountLine email={email} onSwitch={restart} />
        {error ? <div className="mt-5">{alert}</div> : <Loading label="Conferindo a resposta do provedor de identidade…" />}
      </Centered>
    )
  }

  if (stage === 'connect' && enroll) {
    return (
      <Centered>
        <Steps current={1} />
        <h1 ref={heading} tabIndex={-1} className={`${h1} mt-5 outline-none`}>
          Conecte o aplicativo autenticador
        </h1>
        <AccountLine email={email} onSwitch={restart} />
        <ol className="mt-5 grid list-decimal gap-2 pl-5 text-[0.875rem] leading-6 text-ink">
          <li>Abra um aplicativo autenticador no celular: Google Authenticator, Microsoft Authenticator, 1Password ou Authy.</li>
          <li>Toque em adicionar conta e leia o QR code.</li>
        </ol>
        <div className="mt-5 flex justify-center">
          <QrCode value={enroll.uri} label="QR code para o aplicativo autenticador" size={196} />
        </div>
        <ManualKey secret={enroll.secret} />
        <button type="button" className={`${btnPrimary} mt-6 w-full`} onClick={() => setStage('verify')}>
          Já adicionei, continuar
        </button>
      </Centered>
    )
  }

  if (stage === 'save' && codes) {
    return (
      <Centered>
        <Steps current={3} />
        <h1 ref={heading} tabIndex={-1} className={`${h1} mt-5 outline-none`}>
          Guarde os códigos de recuperação
        </h1>
        <p className="mt-1.5 font-mono text-[0.8125rem] text-ink">{email.trim()}</p>
        <p className="mt-4 text-[0.875rem] leading-6 text-ink">
          Se você perder o celular, cada código entra no Console uma vez, no lugar do código do aplicativo. Eles aparecem só agora.
        </p>
        <div className="mt-4">
          <RecoveryCodes codes={codes} email={email.trim()} />
        </div>
        <SavedCodes checked={saved} onChange={setSaved} />
        <button type="button" className={`${btnPrimary} mt-2 w-full`} disabled={!saved} onClick={() => navigate(dest, { replace: true })}>
          Entrar no Console
        </button>
        <p className={hint}>Em Sua conta, você gera dez códigos novos quando quiser, e estes deixam de valer.</p>
      </Centered>
    )
  }

  if (stage === 'recovered' && left !== null) {
    return (
      <Centered>
        <h1 ref={heading} tabIndex={-1} className={`${h1} outline-none`}>
          Você entrou com um código de recuperação
        </h1>
        <p role="status" className="mt-4 rounded-lg border border-line bg-bg px-3 py-2.5 text-[0.875rem] font-semibold text-ink">
          {codesLeft(left)}
        </p>
        <p className="mt-3 text-[0.875rem] leading-6 text-ink">
          O código usado não entra de novo. Se o celular não voltar, troque de aplicativo autenticador em Sua conta, onde você também gera códigos novos.
        </p>
        <div className="mt-6 grid gap-2">
          <button type="button" className={`${btnPrimary} w-full`} onClick={() => navigate(paths.account, { replace: true, state: { from: dest } })}>
            Abrir Sua conta
          </button>
          <button type="button" className={`${btnSecondary} w-full`} onClick={() => navigate(dest, { replace: true })}>
            Continuar para o Console
          </button>
        </div>
      </Centered>
    )
  }

  if (stage === 'code' || stage === 'verify') {
    const enrolling = stage === 'verify'
    const byRecovery = stage === 'code' && recovery
    return (
      <Centered>
        {enrolling && <Steps current={2} />}
        <h1 ref={heading} tabIndex={-1} className={`${h1} ${enrolling ? 'mt-5' : ''} outline-none`}>
          {enrolling ? 'Digite o código do aplicativo' : byRecovery ? 'Use um código de recuperação' : 'Digite o código de acesso'}
        </h1>
        <AccountLine email={email} onSwitch={restart} />
        <form className="mt-6 grid gap-4" onSubmit={submit} aria-describedby={error ? ids.err : undefined}>
          {byRecovery ? (
            <div>
              <label htmlFor={ids.recovery} className={label}>
                Código de recuperação
              </label>
              <input
                id={ids.recovery}
                ref={recoveryRef}
                autoComplete="off"
                autoCapitalize="characters"
                spellCheck={false}
                maxLength={24}
                required
                translate="no"
                className={`${input} h-12 text-center font-mono text-[1.125rem] uppercase tracking-[0.12em] lg:h-12`}
                value={recoveryCode}
                onChange={(e) => setRecoveryCode(e.target.value)}
              />
              <p className={hint}>Um dos dez códigos que você guardou ao ativar o segundo fator, no formato XXXXX-XXXXX. Cada código entra uma vez só.</p>
            </div>
          ) : (
            <div>
              <label htmlFor={ids.otp} className={label}>
                Código do autenticador
              </label>
              <input
                id={ids.otp}
                ref={otpRef}
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                maxLength={6}
                required
                className={`${input} h-12 text-center font-mono text-[1.25rem] tracking-[0.4em] lg:h-12`}
                value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))}
              />
              <p className={hint}>
                {enrolling
                  ? 'Os seis dígitos que o aplicativo mostra agora para a conta Niadra. O primeiro código certo ativa o segundo fator.'
                  : 'Os seis dígitos que o aplicativo autenticador mostra agora para a conta Niadra.'}
              </p>
            </div>
          )}
          {alert}
          <button type="submit" className={`${btnPrimary} w-full`} disabled={busy || (byRecovery ? recoveryCode.trim().length < 10 : otp.length !== 6)}>
            {busy ? 'Conferindo…' : enrolling ? 'Ativar e entrar' : byRecovery ? 'Entrar com o código' : 'Confirmar código'}
          </button>
          {enrolling && (
            <button type="button" className={`${btnQuiet} w-full`} onClick={() => setStage('connect')}>
              Voltar ao QR code
            </button>
          )}
          {stage === 'code' && (
            <p className="text-center">
              <button type="button" onClick={() => switchToRecovery(!recovery)} className={linkButton}>
                {recovery ? 'Usar o código do aplicativo' : 'Perdeu o acesso ao aplicativo? Use um código de recuperação'}
              </button>
            </p>
          )}
        </form>
      </Centered>
    )
  }

  if (stage === 'choice') {
    const enforced = Boolean(discovery?.enforced)
    return (
      <Centered>
        <h1 ref={heading} tabIndex={-1} className={`${h1} outline-none`}>
          Entrar no Console
        </h1>
        <AccountLine email={email} onSwitch={restart} />
        <p className="mt-5 text-[0.875rem] leading-6 text-ink">O seu domínio entra pelo provedor de identidade da empresa, com o login e o MFA que você já usa.</p>
        <div className="mt-5 grid gap-3">
          {alert}
          <button type="button" className={`${btnPrimary} w-full`} onClick={startSso} disabled={busy}>
            {busy ? 'Abrindo o provedor…' : 'Entrar com SSO'}
          </button>
          <button
            type="button"
            className={`${btnSecondary} w-full`}
            onClick={() => {
              setError(null)
              setStage('password')
            }}
            disabled={busy}
          >
            {enforced ? 'Entrar com senha (administração)' : 'Entrar com senha'}
          </button>
          {enforced && <p className={small}>Neste domínio, o SSO é obrigatório. A senha fica para quem administra o Console, se o provedor falhar.</p>}
        </div>
      </Centered>
    )
  }

  if (stage === 'password') {
    return (
      <Centered>
        <h1 ref={heading} tabIndex={-1} className={`${h1} outline-none`}>
          Entrar no Console
        </h1>
        <AccountLine email={email} onSwitch={restart} />
        {checkNote && (
          <p role="status" className="mt-5 rounded-lg border border-line bg-bg px-3 py-2.5 text-[0.875rem] text-ink">
            {checkNote}
          </p>
        )}
        <form className="mt-6 grid gap-4" onSubmit={submit} aria-describedby={error ? ids.err : undefined}>
          {/* The e-mail again, hidden, so password managers pair the password with the right account. */}
          <input id={ids.user} type="email" autoComplete="username" value={email} readOnly hidden />
          <div>
            <label htmlFor={ids.password} className={label}>
              Senha
            </label>
            <input
              id={ids.password}
              ref={passwordRef}
              type="password"
              autoComplete="current-password"
              required
              className={input}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {alert}
          <button type="submit" className={`${btnPrimary} mt-1 w-full`} disabled={busy || !password}>
            {busy ? 'Entrando…' : 'Entrar'}
          </button>
          {discovery?.sso && (
            <button type="button" className={`${btnQuiet} w-full`} onClick={() => setStage('choice')}>
              Voltar ao login pelo SSO
            </button>
          )}
        </form>
      </Centered>
    )
  }

  return (
    <Centered>
      <h1 ref={heading} tabIndex={-1} className={`${h1} outline-none`}>
        Entrar no Console
      </h1>

      {(state?.notice ?? notice) && (
        <p role="status" className="mt-5 rounded-lg border border-line bg-bg px-3 py-2.5 text-[0.875rem] text-ink">
          {state?.notice ?? notice}
        </p>
      )}

      <form className="mt-6 grid gap-4" onSubmit={continueWithEmail} aria-describedby={error ? ids.err : undefined}>
        <div>
          <label htmlFor={ids.email} className={label}>
            E-mail corporativo
          </label>
          <input
            id={ids.email}
            ref={emailRef}
            type="email"
            autoComplete="username"
            required
            className={input}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        {alert}
        <button type="submit" className={`${btnPrimary} mt-1 w-full`} disabled={busy || !email.includes('@')}>
          {busy ? 'Conferindo…' : 'Continuar'}
        </button>
      </form>
    </Centered>
  )
}

/** An action inside the step that reads as a link: it swaps what the step asks for. */
const linkButton =
  'inline-flex min-h-11 items-center text-[0.8125rem] font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4 hover:decoration-ink lg:min-h-0'

/** The account the steps are for, with the way back to another one. */
function AccountLine({ email, onSwitch }: { email: string; onSwitch: () => void }): ReactNode {
  return (
    <p className={`${small} mt-1.5 flex flex-wrap items-center gap-x-2`}>
      <span className="font-mono text-[0.8125rem] text-ink">{email.trim()}</span>
      <button type="button" onClick={onSwitch} className={linkButton}>
        Usar outra conta
      </button>
    </p>
  )
}
