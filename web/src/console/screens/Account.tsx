import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode, type RefObject } from 'react'
import { useLocation } from 'react-router-dom'
import { ApiError } from '../api/http'
import type { Presence, RecoveryCodesOut, SecondFactorOut, SecondFactorStatusOut } from '../api/types'
import { fmtDateTime } from '../lib/format'
import { useResource } from '../lib/useResource'
import { paths } from '../paths'
import { useConsole } from '../session'
import { PageHeader, Props } from '../ui/bits'
import { Dialog } from '../ui/Dialog'
import { Field, useAction } from '../ui/forms'
import { Icon } from '../ui/Icon'
import { Centered, useTitle } from '../ui/layout'
import { QrCode } from '../ui/QrCode'
import { codesLeft, ManualKey, RecoveryCodes, SavedCodes, Steps } from '../ui/SecondFactor'
import { ErrorState, Loading } from '../ui/States'
import { btnPrimary, btnPrimaryWrap, btnQuiet, btnSecondary, btnSecondaryWrap, danger, h2, hint, input, small } from '../ui/styles'

type Opened = 'codes' | 'change'

// Few enough that losing the phone twice more leaves the person locked out.
const FEW_CODES = 3

/**
 * The signed-in person's account: the second factor in use, ten new recovery
 * codes, and a change of authenticator app. Both writes ask for the password again; the control plane
 * answers a wrong one with 403, so a typo keeps the session, which a 401 would end.
 */
export function Account() {
  useTitle('Sua conta')
  const { control, auth, me } = useConsole()
  const loc = useLocation()
  const from = (loc.state as { from?: string } | null)?.from
  const back = from?.startsWith('/console/e/') ? { to: from, label: 'Voltar ao espaço' } : { to: paths.spaces, label: 'Escolher o espaço' }
  const status = useResource((signal) => control.secondFactorStatus({ signal }), [control])
  const [opened, setOpened] = useState<Opened | null>(null)
  const email = me?.email ?? auth?.email ?? ''
  const titleId = useId()
  // A person the tenant's SSO created has no password here: the current authenticator code stands in for it.
  const byCode = me?.password_set === false

  const close = () => {
    setOpened(null)
    status.reload()
  }

  return (
    <Centered wide>
      <PageHeader title="Sua conta" back={back} description={<span className="font-mono text-[0.8125rem] text-ink">{email}</span>} />
      <section aria-labelledby={titleId} className="border-t border-line pt-5">
        <h2 id={titleId} className={h2}>
          Segundo fator
        </h2>
        <p className={`${small} mt-1 max-w-[60ch]`}>
          Depois da senha, o Console pede o código de seis dígitos do aplicativo autenticador. Se o celular se perder, um código de recuperação entra no lugar do aplicativo, uma vez cada.
        </p>
        <div className="mt-4">
          {status.status === 'error' ? (
            <ErrorState error={status.error} onRetry={status.reload} />
          ) : status.status === 'success' && status.data ? (
            byCode && !status.data.confirmed_at ? (
              <p className="rounded-lg border border-line bg-bg px-3 py-2.5 text-[0.875rem] leading-6 text-ink">
                Você entra pelo SSO da sua empresa, e o segundo fator é o do provedor de identidade. Se a empresa pedir também o código da Niadra, o aplicativo autenticador é configurado no
                próximo login.
              </p>
            ) : (
              <SecondFactor status={status.data} onOpen={setOpened} />
            )
          ) : (
            <Loading label="Carregando o segundo fator…" />
          )}
        </div>
        <p className={`${small} mt-5 border-t border-line2 pt-3`}>
          Perdeu o aplicativo e os códigos? Quem administra o Console da sua empresa redefine o seu segundo fator, e você configura um aplicativo novo no próximo login.
        </p>
      </section>
      {opened === 'codes' && <NewCodesDialog email={email} byCode={byCode} onClose={close} />}
      {opened === 'change' && <ChangeAppDialog email={email} byCode={byCode} first={!status.data?.confirmed_at} onClose={close} />}
    </Centered>
  )
}

function SecondFactor({ status, onOpen }: { status: SecondFactorStatusOut; onOpen: (o: Opened) => void }) {
  const on = status.confirmed_at !== null
  const n = status.recovery_codes_left
  const few = on && n <= FEW_CODES
  return (
    <>
      <Props
        items={[
          ['Aplicativo autenticador', on ? `Ativo desde ${fmtDateTime(status.confirmed_at)}` : 'Nenhum configurado'],
          ['Códigos de recuperação', n === 0 ? 'Nenhum disponível' : n === 1 ? '1 disponível' : `${n} disponíveis`],
        ]}
      />
      {few && (
        <p className="mt-4 flex items-start gap-2 rounded-lg border border-line bg-bg px-3 py-2.5 text-[0.875rem] text-ink">
          <Icon name="alert" className="mt-0.5" />
          {n === 0 ? 'Você está sem códigos de recuperação. Gere dez agora, antes de precisar deles.' : `${codesLeft(n)} Gere dez novos antes que acabem.`}
        </p>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        {on ? (
          <>
            <button type="button" className={few ? btnPrimaryWrap : btnSecondaryWrap} onClick={() => onOpen('codes')}>
              Gerar novos códigos de recuperação
            </button>
            <button type="button" className={btnSecondaryWrap} onClick={() => onOpen('change')}>
              Trocar de aplicativo autenticador
            </button>
          </>
        ) : (
          <button type="button" className={btnPrimaryWrap} onClick={() => onOpen('change')}>
            Configurar o aplicativo autenticador
          </button>
        )}
      </div>
      {!on && <p className={hint}>Os códigos de recuperação nascem junto com o aplicativo autenticador.</p>}
    </>
  )
}

const stepTitle = 'text-[1rem] font-semibold leading-snug text-ink outline-none'

/**
 * The password once more, before anything changes; for a person the tenant's SSO created, who has no password
 * here, the current code of the authenticator app. A wrong answer is said here and the session stays.
 */
function PasswordForm({
  intro,
  action,
  busyLabel,
  busy,
  error,
  field,
  byCode,
  onSubmit,
  onCancel,
}: {
  intro: ReactNode
  action: string
  busyLabel: string
  busy: boolean
  error: unknown
  field: RefObject<HTMLInputElement | null>
  byCode: boolean
  onSubmit: (proof: Presence) => void
  onCancel: () => void
}) {
  const [value, setValue] = useState('')
  const errId = useId()
  const wrong = error instanceof ApiError && error.status === 403
  useEffect(() => {
    if (wrong) field.current?.select()
  }, [error, wrong, field])
  const submit = (e: FormEvent) => {
    e.preventDefault()
    onSubmit(byCode ? { totp: value } : { password: value })
  }
  return (
    <form className="grid gap-4" onSubmit={submit}>
      <p className="text-ink">{intro}</p>
      {byCode ? (
        <Field label="Código do autenticador" hint="Você entra pelo SSO e não tem senha no Console: confirme com os seis dígitos que o aplicativo mostra agora.">
          {(id) => (
            <input
              id={id}
              ref={field}
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              className={`${input} font-mono tracking-[0.3em]`}
              value={value}
              onChange={(e) => setValue(e.target.value.replace(/\D/g, ''))}
              aria-invalid={wrong || undefined}
              aria-describedby={wrong ? errId : undefined}
            />
          )}
        </Field>
      ) : (
        <Field label="Senha" hint="A senha do login, mais uma vez: a sessão aberta sozinha não muda o segundo fator.">
          {(id) => (
            <input
              id={id}
              ref={field}
              type="password"
              autoComplete="current-password"
              required
              className={input}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              aria-invalid={wrong || undefined}
              aria-describedby={wrong ? errId : undefined}
            />
          )}
        </Field>
      )}
      {wrong ? (
        <p id={errId} role="alert" className={`text-[0.875rem] font-semibold ${danger.text}`}>
          {byCode ? 'O código não confere ou já venceu.' : 'A senha não confere.'}
        </p>
      ) : (
        Boolean(error) && <ErrorState error={error} compact />
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className={btnSecondary} onClick={onCancel}>
          Cancelar
        </button>
        <button type="submit" className={btnPrimary} disabled={busy || (byCode ? value.length !== 6 : !value)}>
          {busy ? busyLabel : action}
        </button>
      </div>
    </form>
  )
}

/** The new codes, once: the same grid, download and copy as the first sign-in, then "Concluir". */
function CodesStep({ steps, title, intro, codes, email, onDone }: { steps?: ReactNode; title: string; intro: string; codes: string[]; email: string; onDone: () => void }) {
  const [saved, setSaved] = useState(false)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    heading.current?.focus()
  }, [])
  return (
    <div>
      {steps && <div className="mb-5">{steps}</div>}
      <h3 ref={heading} tabIndex={-1} className={stepTitle}>
        {title}
      </h3>
      <p className={`${small} mt-1`}>{intro}</p>
      <div className="mt-4">
        <RecoveryCodes codes={codes} email={email} />
      </div>
      <SavedCodes checked={saved} onChange={setSaved} />
      <div className="mt-2 flex justify-end">
        <button type="button" className={btnPrimary} disabled={!saved} onClick={onDone}>
          Concluir
        </button>
      </div>
      <p className={hint}>Fechar sem guardar descarta a única cópia no navegador. Você gera outros quando quiser.</p>
    </div>
  )
}

function NewCodesDialog({ email, byCode, onClose }: { email: string; byCode: boolean; onClose: () => void }) {
  const { control } = useConsole()
  const act = useAction<RecoveryCodesOut>()
  const field = useRef<HTMLInputElement>(null)
  return (
    <Dialog open onClose={onClose} title="Gerar novos códigos de recuperação" initialFocus={field}>
      {act.result ? (
        <CodesStep title="Seus dez códigos novos" intro="Os códigos anteriores deixaram de valer. Estes aparecem só agora." codes={act.result.recovery_codes} email={email} onDone={onClose} />
      ) : (
        <PasswordForm
          intro="Os códigos atuais deixam de valer assim que os novos forem gerados."
          action="Gerar os códigos"
          busyLabel="Gerando…"
          busy={act.busy}
          error={act.error}
          field={field}
          byCode={byCode}
          onSubmit={(proof) => act.run(() => control.newRecoveryCodes(proof))}
          onCancel={onClose}
        />
      )}
    </Dialog>
  )
}

type ChangeStep = 'password' | 'connect' | 'verify' | 'save'

/**
 * A change of authenticator app in the three steps of the first sign-in. Until the first code of the
 * new app arrives, the current one keeps working, so closing halfway changes nothing.
 */
function ChangeAppDialog({ email, byCode, first, onClose }: { email: string; byCode: boolean; first: boolean; onClose: () => void }) {
  const { control } = useConsole()
  const [step, setStep] = useState<ChangeStep>('password')
  const [otp, setOtp] = useState('')
  const start = useAction<SecondFactorOut>()
  const confirm = useAction<RecoveryCodesOut>()
  const field = useRef<HTMLInputElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const otpRef = useRef<HTMLInputElement>(null)
  const errId = useId()
  const wrongCode = confirm.error instanceof ApiError && confirm.error.status === 403

  useEffect(() => {
    if (step === 'verify') otpRef.current?.focus()
    else if (step === 'connect') heading.current?.focus()
  }, [step])

  useEffect(() => {
    if (!wrongCode) return
    setOtp('')
    otpRef.current?.focus()
  }, [confirm.error, wrongCode])

  const verify = (e: FormEvent) => {
    e.preventDefault()
    confirm.run(
      () => control.confirmSecondFactorChange(otp),
      () => setStep('save'),
    )
  }

  return (
    <Dialog open onClose={onClose} title={first ? 'Configurar o aplicativo autenticador' : 'Trocar de aplicativo autenticador'} initialFocus={field}>
      {step === 'password' && (
        <PasswordForm
          intro={first ? 'O aplicativo autenticador passa a ser pedido em todo login, depois da senha.' : 'O aplicativo atual continua valendo até o novo ser confirmado.'}
          action="Continuar"
          busyLabel="Conferindo…"
          busy={start.busy}
          error={start.error}
          field={field}
          byCode={byCode}
          onSubmit={(proof) => start.run(() => control.startSecondFactorChange(proof), () => setStep('connect'))}
          onCancel={onClose}
        />
      )}

      {step === 'connect' && start.result && (
        <div>
          <Steps current={1} />
          <h3 ref={heading} tabIndex={-1} className={`${stepTitle} mt-5`}>
            Conecte o aplicativo {first ? 'autenticador' : 'novo'}
          </h3>
          <ol className="mt-3 grid list-decimal gap-2 pl-5 text-[0.875rem] leading-6 text-ink">
            <li>Abra um aplicativo autenticador no celular: Google Authenticator, Microsoft Authenticator, 1Password ou Authy.</li>
            <li>Toque em adicionar conta e leia o QR code.</li>
          </ol>
          <div className="mt-4 flex justify-center">
            <QrCode value={start.result.totp_uri} label={first ? 'QR code para o aplicativo autenticador' : 'QR code para o novo aplicativo autenticador'} size={196} />
          </div>
          <ManualKey secret={start.result.secret} />
          <button type="button" className={`${btnPrimary} mt-5 w-full`} onClick={() => setStep('verify')}>
            Já adicionei, continuar
          </button>
        </div>
      )}

      {step === 'verify' && (
        <form className="grid gap-4" onSubmit={verify} aria-describedby={confirm.error ? errId : undefined}>
          <Steps current={2} />
          <h3 className={`${stepTitle} mt-1`}>Digite o código do aplicativo {first ? 'autenticador' : 'novo'}</h3>
          <Field
            label="Código do autenticador"
            hint={first ? 'Os seis dígitos que o aplicativo mostra agora. O primeiro código certo ativa o segundo fator.' : 'Os seis dígitos que o aplicativo novo mostra agora. Com o código certo, o aplicativo anterior para de valer.'}
          >
            {(id) => (
              <input
                id={id}
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
            )}
          </Field>
          {wrongCode ? (
            <p id={errId} role="alert" className={`text-[0.875rem] font-semibold ${danger.text}`}>
              O código não confere ou já venceu. Use o código que o aplicativo {first ? '' : 'novo '}mostra agora.
            </p>
          ) : (
            Boolean(confirm.error) && (
              <div id={errId}>
                <ErrorState error={confirm.error} compact />
              </div>
            )
          )}
          <div className="flex flex-wrap justify-between gap-2">
            <button type="button" className={btnQuiet} onClick={() => setStep('connect')}>
              Voltar ao QR code
            </button>
            <button type="submit" className={btnPrimary} disabled={confirm.busy || otp.length !== 6}>
              {confirm.busy ? 'Conferindo…' : first ? 'Confirmar e ativar' : 'Confirmar e trocar'}
            </button>
          </div>
        </form>
      )}

      {step === 'save' && confirm.result && (
        <CodesStep
          steps={<Steps current={3} />}
          title="Guarde os códigos de recuperação"
          intro={first ? 'O aplicativo autenticador já vale. Os códigos aparecem só agora.' : 'O aplicativo novo já vale, e o anterior parou. Os códigos anteriores também deixaram de valer.'}
          codes={confirm.result.recovery_codes}
          email={email}
          onDone={onClose}
        />
      )}
    </Dialog>
  )
}
