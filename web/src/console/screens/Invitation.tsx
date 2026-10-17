import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { describeError } from '../api/describe'
import { ApiError } from '../api/http'
import { fmtDateTime } from '../lib/format'
import { Retrying, useResource } from '../lib/useResource'
import { paths } from '../paths'
import { useConsole } from '../session'
import { Centered, useTitle } from '../ui/layout'
import { ErrorState, Loading } from '../ui/States'
import { btnPrimary, danger, h1, hint, input, label, link, small } from '../ui/styles'

const MIN_PASSWORD = 12

/** The token of the link, from the fragment: it never reaches a server in a URL. Reading twice gives the same. */
const tokenFromHash = () => new URLSearchParams(window.location.hash.replace(/^#/, '')).get('t') ?? window.location.hash.replace(/^#/, '')

/**
 * An invitation: the person chooses a password with the single-use link an admin sent,
 * then signs in and sets up the authenticator app like everyone. The token stays in this screen's state.
 */
export function Invitation() {
  useTitle('Convite')
  const { control } = useConsole()
  const navigate = useNavigate()
  const [token] = useState(tokenFromHash)
  const preview = useResource(() => control.invitationPreview(token), [token, control], token.length >= 20)
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const ids = { user: useId(), password: useId(), again: useId(), err: useId() }

  useEffect(() => {
    window.history.replaceState(window.history.state, '', window.location.pathname)
  }, [])

  const tooShort = password.length < MIN_PASSWORD
  const mismatch = again !== password
  async function submit(e: FormEvent) {
    e.preventDefault()
    setTouched(true)
    if (tooShort || mismatch) return
    setBusy(true)
    setError(null)
    try {
      const { email } = await control.acceptInvitation(token, password)
      navigate(paths.login, { replace: true, state: { email, notice: 'Senha criada. Entre para configurar o aplicativo autenticador.' } })
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setError('O convite venceu ou já foi usado. Peça um link novo a quem administra o Console.')
      else {
        const d = describeError(err)
        setError(`${d.title} ${d.detail}`)
      }
      setBusy(false)
    }
  }

  if (preview.error instanceof Retrying) {
    return (
      <Centered>
        <h1 ref={heading} className={h1}>
          Abrindo o convite
        </h1>
        <div className="mt-4">
          <ErrorState error={preview.error} onRetry={preview.reload} />
        </div>
      </Centered>
    )
  }

  if (token.length < 20 || preview.status === 'error') {
    const expired = preview.error instanceof ApiError && preview.error.status === 401
    return (
      <Centered>
        <h1 ref={heading} className={h1}>
          {token.length < 20 ? 'Este link de convite está incompleto' : expired ? 'O convite venceu ou já foi usado' : 'Não foi possível abrir o convite'}
        </h1>
        <p className={`${small} mt-2`}>
          {token.length < 20 || expired
            ? 'Cada link entra uma vez, por sete dias. Peça um novo a quem administra o Console da sua empresa.'
            : `${describeError(preview.error).title} ${describeError(preview.error).detail}`}
        </p>
        <Link to={paths.login} className={`${link} mt-4 inline-flex min-h-11 items-center`}>
          Ir para o login
        </Link>
      </Centered>
    )
  }
  if (preview.status !== 'success' || !preview.data) return <Loading label="Abrindo o convite…" className="min-h-dvh justify-center" />

  const who = preview.data
  return (
    <Centered>
      <h1 ref={heading} className={h1}>
        Crie a sua senha
      </h1>
      <p className={`${small} mt-1.5`}>
        Convite de <strong className="text-ink">{who.tenant_name}</strong> para <span className="font-mono text-[0.8125rem] text-ink">{who.email}</span>, válido até {fmtDateTime(who.expires_at)}.
      </p>
      <form className="mt-6 grid gap-4" onSubmit={submit} noValidate aria-describedby={error ? ids.err : undefined}>
        <input id={ids.user} type="email" autoComplete="username" value={who.email} readOnly hidden />
        <div>
          <label htmlFor={ids.password} className={label}>
            Senha
          </label>
          <input
            id={ids.password}
            type="password"
            autoComplete="new-password"
            className={input}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={touched && tooShort}
          />
          <p className={touched && tooShort ? `mt-1.5 text-[0.8125rem] font-semibold ${danger.text}` : hint}>{MIN_PASSWORD} caracteres ou mais. Uma frase fácil de lembrar funciona bem.</p>
        </div>
        <div>
          <label htmlFor={ids.again} className={label}>
            A mesma senha de novo
          </label>
          <input
            id={ids.again}
            type="password"
            autoComplete="new-password"
            className={input}
            value={again}
            onChange={(e) => setAgain(e.target.value)}
            aria-invalid={touched && mismatch}
          />
          {touched && mismatch && <p className={`mt-1.5 text-[0.8125rem] font-semibold ${danger.text}`}>As duas senhas não são iguais.</p>}
        </div>
        {error && (
          <p id={ids.err} role="alert" className={`text-[0.875rem] font-semibold ${danger.text}`}>
            {error}
          </p>
        )}
        <button type="submit" className={`${btnPrimary} mt-1 w-full`} disabled={busy}>
          {busy ? 'Salvando…' : 'Criar a senha'}
        </button>
        <p className={hint}>Depois, você entra com o e-mail e esta senha e configura o aplicativo autenticador, que o Console pede em todo login.</p>
      </form>
    </Centered>
  )
}
