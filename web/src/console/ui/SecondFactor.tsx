import { useId, useState } from 'react'
import { fmtDateTime } from '../lib/format'
import { Icon } from './Icon'
import { btnQuiet, btnSecondary, danger, small } from './styles'

/** Setting up an authenticator takes the same three steps at the first sign-in and in a change of app. */
const AUTHENTICATOR_STEPS = ['Conectar o aplicativo', 'Confirmar o código', 'Guardar os códigos de recuperação']

/** Where the person is in the setup. On a phone only the current step keeps its name on screen. */
export function Steps({ current, steps = AUTHENTICATOR_STEPS }: { current: number; steps?: string[] }) {
  return (
    <ol className="flex items-start" aria-label={`Passo ${current} de ${steps.length}`}>
      {steps.map((name, i) => {
        const n = i + 1
        const done = n < current
        const here = n === current
        return (
          <li key={name} className="relative flex min-w-0 flex-1 flex-col items-center gap-1.5 px-1 text-center" aria-current={here ? 'step' : undefined}>
            {n < steps.length && <span aria-hidden="true" className="absolute left-[calc(50%+1.25rem)] right-[calc(-50%+1.25rem)] top-3.5 h-px bg-line" />}
            <span
              className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-[0.8125rem] font-semibold ${
                here ? 'border-accent bg-brand text-on-brand' : done ? 'border-accent bg-surface text-accent' : 'border-edge bg-surface text-ink2'
              }`}
            >
              {done ? <Icon name="check" /> : n}
            </span>
            <span className={`text-[0.75rem] leading-4 ${here ? 'font-semibold text-ink' : 'sr-only text-ink2 sm:not-sr-only'}`}>{name}</span>
          </li>
        )
      })}
    </ol>
  )
}

/** The secret as text, for a phone without a camera or an authenticator on the computer. */
export function ManualKey({ secret }: { secret: string }) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const grouped = secret.match(/.{1,4}/g)?.join(' ') ?? secret
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }
  if (!open)
    return (
      <p className="mt-4 text-center">
        <button type="button" onClick={() => setOpen(true)} className={`${btnQuiet} text-[0.8125rem]`}>
          Não consegue ler o QR code? Digite a chave
        </button>
      </p>
    )
  return (
    <div className="mt-4 rounded-lg border border-line bg-bg p-3">
      <p className={small}>No aplicativo, escolha digitar a chave, use o nome Niadra e o tipo baseado em tempo.</p>
      <div className="mt-2 flex items-center justify-between gap-3">
        <span translate="no" className="select-all break-all font-mono text-[0.875rem] tracking-[0.08em] text-ink">
          {grouped}
        </span>
        <button type="button" onClick={copy} className={btnSecondary}>
          <Icon name={copied ? 'check' : 'copy'} />
          <span aria-live="polite">{copied ? 'Chave copiada' : 'Copiar chave'}</span>
        </button>
      </div>
    </div>
  )
}

const RECOVERY_FILE = 'niadra-codigos-de-recuperacao.txt'

/** The downloaded file: what it is, whose it is, when, and the numbered codes. */
function recoveryCodesText(codes: string[], email: string, at: Date = new Date()): string {
  return [
    'Niadra: códigos de recuperação do segundo fator',
    `Conta: ${email}`,
    `Data: ${fmtDateTime(at.toISOString())}`,
    '',
    'Cada código entra no Console uma vez só, no lugar do código do aplicativo autenticador.',
    '',
    ...codes.map((c, i) => `${String(i + 1).padStart(2, ' ')}. ${c}`),
    '',
  ].join('\n')
}

/**
 * The recovery codes, shown once: a numbered grid in mono, a .txt download and a copy. They live only
 * in the state of the screen that received them; nothing here keeps them in the browser.
 */
export function RecoveryCodes({ codes, email }: { codes: string[]; email: string }) {
  const [copied, setCopied] = useState<'idle' | 'ok' | 'fail'>('idle')
  const download = () => {
    const url = URL.createObjectURL(new Blob([recoveryCodesText(codes, email)], { type: 'text/plain;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = RECOVERY_FILE
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(codes.join('\n'))
      setCopied('ok')
    } catch {
      setCopied('fail')
    }
  }
  return (
    <div>
      {/* role="list" keeps the semantics Safari drops with the markers. Two columns fit from 360px up because,
          on a phone, the codes lose the extra tracking; they never break at the dash. */}
      <ol role="list" aria-label="Códigos de recuperação" translate="no" className="grid grid-cols-2 gap-x-3 gap-y-2.5 rounded-xl border border-line bg-bg px-3 py-3.5 sm:gap-x-4 sm:px-4">
        {codes.map((code, i) => (
          <li key={code} className="flex min-w-0 items-baseline gap-1.5">
            <span aria-hidden="true" className="w-4 shrink-0 text-right font-mono text-[0.75rem] text-ink2 sm:w-5">
              {i + 1}.
            </span>
            <span className="select-all whitespace-nowrap font-mono text-[0.875rem] text-ink sm:text-[0.9375rem] sm:tracking-[0.06em]">{code}</span>
          </li>
        ))}
      </ol>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" className={btnSecondary} onClick={download}>
          <Icon name="download" size={16} />
          Baixar os códigos (.txt)
        </button>
        <button type="button" className={btnSecondary} onClick={copy}>
          <Icon name={copied === 'ok' ? 'check' : 'copy'} size={16} />
          <span aria-live="polite">{copied === 'ok' ? 'Copiado' : 'Copiar'}</span>
        </button>
      </div>
      {copied === 'fail' && (
        <p role="alert" className={`mt-2 text-[0.8125rem] font-semibold ${danger.text}`}>
          O navegador não deixou copiar. Selecione os códigos e copie.
        </p>
      )}
    </div>
  )
}

/** Required before leaving the codes: they do not come back. */
export function SavedCodes({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  const id = useId()
  return (
    <label htmlFor={id} className="mt-4 flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] text-ink">
      <input id={id} type="checkbox" required className="h-5 w-5 shrink-0 accent-[#5e7c00]" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      Guardei os códigos num lugar seguro
    </label>
  )
}

/** "Restam 7 códigos de recuperação." with the singular and the last one said in words. */
export function codesLeft(n: number): string {
  if (n === 0) return 'Não resta nenhum código de recuperação.'
  if (n === 1) return 'Resta 1 código de recuperação.'
  return `Restam ${n} códigos de recuperação.`
}
