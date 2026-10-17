import { useState } from 'react'
import { ApiError } from '../../api/http'
import type { Inference, InferenceKind, ReviewRequest } from '../../api/measure'
import { fmtDateTime, fmtPct } from '../../lib/format'
import { usePaged } from '../../lib/useResource'
import { useSpace } from '../../session'
import { Chip } from '../../ui/bits'
import { Dialog } from '../../ui/Dialog'
import { ReasonField, reasonOk, TextInput, useAction } from '../../ui/forms'
import { Empty, ErrorState, LoadMore, Loading } from '../../ui/States'
import { btnDangerGhost, btnPrimary, btnSecondary, card, mono, small } from '../../ui/styles'

/**
 * What the signals infer about this person, each with its origin, evidence, confidence, use and date: the
 * reviewable profile of LGPD art. 20. A correction becomes what the person said; a deletion leaves a tombstone,
 * so the same evidence never infers it again; and a request to review an automated decision goes to the
 * controller's data protection officer. What the person said is not an inference and is not listed here.
 */

const KIND: Record<InferenceKind, string> = {
  affinity: 'Afinidade',
  soft_constraint: 'Preferência inferida',
  attribute: 'Atributo inferido',
  interest: 'Interesse',
}
const ORIGIN: Record<string, string> = {
  interactions: 'do que a pessoa viu e engajou',
  implicit_negative: 'mostrado muitas vezes e nunca escolhido',
  engaged: 'a pessoa engajou com o objeto',
  feedback: 'a pessoa deu um retorno sobre o objeto',
  watch: 'a pessoa pediu para acompanhar o objeto',
}
const FOR = (f: string | undefined) => (!f || f === 'self' ? null : f === 'gift' ? 'presente' : `para ${f.replace('beneficiary:', 'beneficiário ')}`)
const REVIEW_OUTCOME: Record<NonNullable<ReviewRequest['outcome']>, string> = { upheld: 'Mantida', reversed: 'Revertida', corrected: 'Corrigida' }

function what(i: Inference): string {
  if (i.kind === 'interest') return i.ref ?? 'objeto'
  const value = i.value === undefined || i.value === null ? '' : String(i.value)
  const polarity = i.polarity === 'avoid' ? 'evita' : i.polarity === 'prefer' ? 'prefere' : ''
  return [i.attr, polarity, value].filter(Boolean).join(' · ')
}

function evidence(e: Record<string, unknown>): string {
  const names: Record<string, string> = { exposure: 'exposição', signal: 'sinal', events: 'eventos', sessions: 'sessões', lift: 'força', times: 'vezes' }
  return Object.entries(e)
    .map(([k, v]) => `${names[k] ?? k} ${typeof v === 'number' ? new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 }).format(v) : String(v)}`)
    .join(' · ')
}

export function InferencesTab({ profileId }: { profileId: string }) {
  const { cell } = useSpace()
  const m = cell.measure
  const list = usePaged((cursor, signal) => m.inferences(profileId, cursor, { signal }), [profileId, m])
  const reviews = usePaged((cursor, signal) => m.reviewRequests(cursor, { signal }), [m])
  const [correcting, setCorrecting] = useState<Inference | null>(null)
  const [deleting, setDeleting] = useState<Inference | null>(null)
  const [reviewing, setReviewing] = useState(false)

  if (list.status === 'loading' || list.status === 'idle') return <Loading label="Carregando as inferências…" />
  if (list.status === 'error') {
    if (list.error instanceof ApiError && list.error.status === 404)
      return <Empty title="Os sinais estão desligados neste espaço.">Sem sinais, nada é inferido sobre a pessoa.</Empty>
    return <ErrorState error={list.error} onRetry={list.reload} />
  }
  const mine = reviews.items.filter((r) => r.profile_id === profileId)

  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className={`${small} max-w-[64ch]`}>
          Cada inferência mostra de onde veio, a evidência, a confiança e para que é usada. Corrigir guarda o que a pessoa diz no lugar; apagar impede que a mesma evidência
          volte a inferir o mesmo.
        </p>
        <button type="button" className={btnSecondary} onClick={() => setReviewing(true)}>
          Pedir revisão de uma decisão
        </button>
      </div>

      {list.items.length === 0 ? (
        <Empty title="Nada inferido sobre este cliente." />
      ) : (
        <ul className={`${card} divide-y divide-line2`} aria-label="Inferências sobre este cliente">
          {list.items.map((i) => (
            <li key={i.key} className="px-4 py-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Chip>{KIND[i.kind]}</Chip>
                    {FOR(i.for) && <Chip>{FOR(i.for)}</Chip>}
                  </div>
                  <p className={`${mono} mt-1 break-all text-ink`}>{what(i)}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {i.kind !== 'interest' && (
                    <button type="button" className={btnSecondary} onClick={() => setCorrecting(i)}>
                      Corrigir
                    </button>
                  )}
                  <button type="button" className={btnDangerGhost} onClick={() => setDeleting(i)}>
                    Apagar
                  </button>
                </div>
              </div>
              <p className={`${small} mt-1`}>
                Origem: {ORIGIN[i.origin] ?? i.origin}
                {i.confidence != null && ` · confiança ${fmtPct(i.confidence)}`} · última evidência em {fmtDateTime(i.at)}
              </p>
              {Object.keys(i.evidence).length > 0 && <p className={`${small} mt-0.5`}>Evidência: {evidence(i.evidence)}</p>}
              <p className={`${small} mt-0.5`}>Usada para: {i.used_for.length ? i.used_for.join(', ') : 'nenhuma finalidade hoje'}</p>
            </li>
          ))}
        </ul>
      )}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} error={list.moreError} onClick={list.loadMore} />

      <section aria-labelledby="h-reviews">
        <h3 id="h-reviews" className="mb-2 text-[0.875rem] font-semibold text-ink">
          Pedidos de revisão deste cliente
        </h3>
        {reviews.status === 'loading' || reviews.status === 'idle' ? (
          <Loading label="Carregando os pedidos de revisão…" />
        ) : reviews.status === 'error' ? (
          reviews.error instanceof ApiError && reviews.error.status === 404 ? (
            <p className={small}>Os pedidos de revisão precisam dos sinais e da coordenação ligados.</p>
          ) : (
            <ErrorState error={reviews.error} onRetry={reviews.reload} compact />
          )
        ) : mine.length === 0 ? (
          <p className={small}>Nenhum pedido de revisão aberto por este cliente.</p>
        ) : (
          <ul className={`${card} divide-y divide-line2`} aria-label="Pedidos de revisão deste cliente">
            {mine.map((r) => (
              <li key={r.request_id} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-1.5">
                  {r.status === 'open' ? <Chip tone="danger">Aberto</Chip> : <Chip tone="ok">{r.outcome ? REVIEW_OUTCOME[r.outcome] : 'Resolvido'}</Chip>}
                  <span className={small}>aberto em {fmtDateTime(r.created_at)}</span>
                </div>
                {r.reason && <p className="mt-1 text-ink">{r.reason}</p>}
                {r.resolution && <p className={`${small} mt-0.5`}>Resolução: {r.resolution}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>

      {correcting && <CorrectDialog profileId={profileId} inference={correcting} onClose={() => setCorrecting(null)} onDone={list.reload} />}
      {deleting && <DeleteDialog profileId={profileId} inference={deleting} onClose={() => setDeleting(null)} onDone={list.reload} />}
      {reviewing && <ReviewDialog profileId={profileId} onClose={() => setReviewing(false)} onDone={reviews.reload} />}
    </div>
  )
}

function CorrectDialog({ profileId, inference, onClose, onDone }: { profileId: string; inference: Inference; onClose: () => void; onDone: () => void }) {
  const { cell } = useSpace()
  const [value, setValue] = useState('')
  const [reason, setReason] = useState('')
  const action = useAction<Inference>()
  const numeric = typeof inference.value === 'number'
  const submit = () =>
    action.run(
      () => cell.measure.correctInference(profileId, inference.key, { value: numeric ? Number(value) : value.trim(), ...(reason.trim() ? { reason: reason.trim() } : {}) }),
      () => {
        onDone()
        onClose()
      },
    )
  const ok = value.trim().length > 0 && (!numeric || Number.isFinite(Number(value)))
  return (
    <Dialog open onClose={onClose} title="Corrigir a inferência">
      <p className={small}>
        {what(inference)}. O valor que a pessoa diz passa a valer como declarado, e a inferência sai.
      </p>
      <div className="mt-4 grid gap-4">
        <TextInput label="O valor correto" value={value} onChange={setValue} inputMode={numeric ? 'decimal' : undefined} maxLength={256} />
        <ReasonField value={reason} onChange={setReason} required={false} />
        {Boolean(action.error) && <ErrorState error={action.error} compact />}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onClose}>
            Cancelar
          </button>
          <button type="button" className={btnPrimary} disabled={!ok || action.busy} onClick={submit}>
            {action.busy ? 'Corrigindo…' : 'Corrigir'}
          </button>
        </div>
      </div>
    </Dialog>
  )
}

function DeleteDialog({ profileId, inference, onClose, onDone }: { profileId: string; inference: Inference; onClose: () => void; onDone: () => void }) {
  const { cell } = useSpace()
  const action = useAction<void>()
  const submit = () =>
    action.run(
      () => cell.measure.deleteInference(profileId, inference.key),
      () => {
        onDone()
        onClose()
      },
    )
  return (
    <Dialog open onClose={onClose} title="Apagar a inferência">
      <p className="text-ink">{what(inference)}</p>
      <p className={`${small} mt-2`}>A inferência sai de toda leitura, e a mesma evidência não volta a inferi-la. A ação fica no registro com o seu nome.</p>
      {Boolean(action.error) && (
        <div className="mt-3">
          <ErrorState error={action.error} compact />
        </div>
      )}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <button type="button" className={btnSecondary} onClick={onClose}>
          Cancelar
        </button>
        <button type="button" className={btnDangerGhost} disabled={action.busy} onClick={submit}>
          {action.busy ? 'Apagando…' : 'Apagar'}
        </button>
      </div>
    </Dialog>
  )
}

function ReviewDialog({ profileId, onClose, onDone }: { profileId: string; onClose: () => void; onDone: () => void }) {
  const { cell } = useSpace()
  const [reason, setReason] = useState('')
  const [receipt, setReceipt] = useState('')
  const action = useAction<ReviewRequest>()
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
  const receiptOk = receipt.trim() === '' || uuid.test(receipt.trim())
  const submit = () =>
    action.run(
      () => cell.measure.reviewRequest(profileId, { reason: reason.trim(), ...(receipt.trim() ? { decision_receipt_id: receipt.trim() } : {}) }),
      () => {
        onDone()
        onClose()
      },
    )
  return (
    <Dialog open onClose={onClose} title="Pedir revisão de uma decisão automatizada">
      <p className={small}>
        O pedido vai ao encarregado de dados da empresa, com a explicação montada dos comprovantes: regras, versões e insumos, nunca o conteúdo. Quem revisa é a empresa.
      </p>
      <div className="mt-4 grid gap-4">
        <ReasonField value={reason} onChange={setReason} />
        <TextInput
          label="Comprovante da decisão (opcional)"
          hint="Sem comprovante, o pedido cobre as decisões recentes sobre a pessoa."
          value={receipt}
          onChange={setReceipt}
          mono
          aria-invalid={receiptOk ? undefined : true}
        />
        {!receiptOk && <p className={`${small} font-semibold`}>O comprovante é um identificador como os da tela de Comprovantes.</p>}
        {Boolean(action.error) && <ErrorState error={action.error} compact />}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onClose}>
            Cancelar
          </button>
          <button type="button" className={btnPrimary} disabled={!reasonOk(reason) || !receiptOk || action.busy} onClick={submit}>
            {action.busy ? 'Enviando…' : 'Enviar o pedido'}
          </button>
        </div>
      </div>
    </Dialog>
  )
}
