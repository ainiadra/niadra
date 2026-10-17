import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { DiffOut, Role } from '../api/types'
import { useResource } from '../lib/useResource'
import { paths } from '../paths'
import { useSpace } from '../session'
import { Dialog } from './Dialog'
import { Field, useAction } from './forms'
import { ErrorState } from './States'
import { btnPrimary, btnSecondary, input, mono, small } from './styles'

/*
 * A rule or schedule changed from a form becomes a configuration diff with a reason, which another person
 * approves in Configuração. The control plane checks the documents again when the diff
 * arrives and when it is approved; the pieces below are what every such form shares.
 */

/**
 * Whether the person may propose a change to a configuration document: the Administration always, plus
 * the roles GET /v1/config/types lists for it. Until the list arrives, or if it fails, `fallback` decides,
 * and the server decides anyway.
 */
export function useCanPropose(type: string, fallback: Role[]): boolean {
  const { control, hasRole } = useSpace()
  const types = useResource((signal) => control.configTypes({ signal }), [control])
  const writers = types.data?.find((t) => t.type === type)?.roles ?? fallback
  return hasRole(...writers)
}

/** The reason of a diff. The person who approves reads it, and it stays in the diff with the author. */
export function DiffReason({ value, onChange, id }: { value: string; onChange: (v: string) => void; id?: string }) {
  return (
    <Field id={id} label="Motivo" hint="Quem aprova lê o motivo. Ele fica no diff, com o seu nome.">
      {(fid) => <textarea id={fid} className={`${input} h-auto min-h-20 py-2`} value={value} onChange={(e) => onChange(e.target.value)} minLength={3} maxLength={500} />}
    </Field>
  )
}

export const diffReasonOk = (reason: string) => reason.trim().length >= 3

/** What happened after the diff went: its id, where it waits, and the four-eyes rule. Takes the focus. */
export function DiffSent({ diff, title, children }: { diff: DiffOut; title: string; children?: ReactNode }) {
  const { space } = useSpace()
  const done = useRef<HTMLParagraphElement>(null)
  useEffect(() => done.current?.focus(), [])
  return (
    <div className="grid gap-2" role="status">
      <p ref={done} tabIndex={-1} className="font-semibold text-ink outline-none">
        {title}
      </p>
      <p className={small}>
        A mudança vale depois de aprovada em Configuração, na aba Diffs. Pela regra dos quatro olhos, outra pessoa aprova; numa empresa com uma só pessoa na Administração, ela mesma
        aprova. Na aprovação, o plano de controle confere a regra de novo.
      </p>
      {children}
      <p className={`${mono} break-all text-ink2`}>diff {diff.diff_id}</p>
      <div>
        <Link to={`${paths.config(space.space_id)}?aba=diffs`} className={btnSecondary}>
          Abrir os diffs em Configuração
        </Link>
      </div>
    </div>
  )
}

/**
 * The next version of a rule that changed. Firings are deduplicated by rule and version, so a changed
 * rule gets a new one: "v2" becomes "v3", "1" becomes "2", "starter" becomes "starter.2".
 */
export function nextVersion(version: string): string {
  if (/\d+$/.test(version)) return version.replace(/(\d+)$/, (d) => String(Number(d) + 1))
  return `${version || '1'}.2`
}

/** A rule id no rule of the document uses: the base, then base.2, base.3. */
export function freeId(base: string, taken: Iterable<string>): string {
  const used = new Set(taken)
  if (!used.has(base)) return base
  let n = 2
  while (used.has(`${base}.${n}`)) n += 1
  return `${base}.${n}`
}

type RuleList = { rule_id: string; enabled?: boolean }[]

/**
 * Turns a rule of a list document on or off, as a diff with a reason. The document is read again right
 * before sending, so a change approved while the dialog was open is kept.
 */
export function ToggleRule({ type, ruleId, enabled, what, onClose, onSent }: { type: 'trigger-rules' | 'trait-rules'; ruleId: string; enabled: boolean; what: string; onClose: () => void; onSent: () => void }) {
  const { control, space } = useSpace()
  const [reason, setReason] = useState('')
  const act = useAction<DiffOut>()
  const verb = enabled ? 'Desligar' : 'Ligar'
  const submit = (e: FormEvent) => {
    e.preventDefault()
    act.run(async () => {
      const now = await control.configDocument(space.space_id, type)
      if (!Array.isArray(now.document)) throw new Error(`the ${type} document is not a list`)
      const list = now.document as RuleList
      if (!list.some((r) => r.rule_id === ruleId)) throw new Error(`the rule ${ruleId} left the document`)
      const document = list.map((r) => (r.rule_id === ruleId ? { ...r, enabled: !enabled } : r))
      return control.submitDiff(space.space_id, type, document, reason.trim())
    }, onSent)
  }
  return (
    <Dialog open onClose={onClose} title={`${verb} a ${what} ${ruleId}`}>
      {act.result ? (
        <div className="grid gap-4">
          <DiffSent diff={act.result} title={`${verb} a ${what} ${ruleId}: enviado para aprovação.`} />
          <div className="flex justify-end">
            <button type="button" className={btnPrimary} onClick={onClose}>
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <form className="grid gap-4" onSubmit={submit}>
          <p className="text-ink">
            {enabled
              ? 'Desligada, a regra continua no documento e para de valer depois da aprovação. Os disparos e padrões que ela já produziu ficam como estão.'
              : 'Ligada, a regra volta a valer depois da aprovação. O plano de controle confere antes se ela ainda pode produzir efeito.'}
          </p>
          <DiffReason value={reason} onChange={setReason} />
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Cancelar
            </button>
            <button type="submit" className={btnPrimary} disabled={act.busy || !diffReasonOk(reason)}>
              {act.busy ? 'Enviando…' : 'Enviar para aprovação'}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}
