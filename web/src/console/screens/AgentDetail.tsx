import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { Select } from '../../components/Select'
import { useParams } from 'react-router-dom'
import { describeError } from '../api/describe'
import { newIdempotencyKey } from '../api/http'
import type { IssuedKeyOut, KeyOut, RevocationOut, Scope, SourceOut, SourceRevocationOut } from '../api/types'
import { fmtDateTime } from '../lib/format'
import { audience, channel, purposes, scope, SCOPES, verification } from '../lib/labels'
import { useResource } from '../lib/useResource'
import { paths } from '../paths'
import { useSpace } from '../session'
import { Chip, PageHeader, Props, useSources } from '../ui/bits'
import { Dialog } from '../ui/Dialog'
import { ReasonField, reasonOk, useAction } from '../ui/forms'
import { Icon } from '../ui/Icon'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, Loading } from '../ui/States'
import { btnDanger, btnDangerGhost, btnPrimary, btnQuiet, btnSecondary, card, danger, h2, hint, input, label, mono, select, small, td, th } from '../ui/styles'

export function AgentDetail() {
  const { sourceId = '' } = useParams()
  const { space } = useSpace()
  const directory = useSources()
  const s = directory.byId.get(sourceId)
  useTitle(s?.name ?? 'Fonte', space.environment === 'sandbox')
  const back = { to: paths.agents(space.space_id), label: 'Agentes e chaves' }

  if (directory.status === 'loading') return <Loading label="Carregando a fonte…" />
  if (directory.status === 'error')
    return (
      <>
        <PageHeader screen="agent" back={back} />
        <ErrorState error={directory.error} onRetry={directory.reload} />
      </>
    )
  if (!s)
    return (
      <>
        <PageHeader screen="agent" back={back} />
        <Empty title="Esta fonte não existe neste espaço." />
      </>
    )

  return (
    <>
      <PageHeader
        screen="agent"
        back={back}
        title={s.name}
        description={[s.vendor, channel(s.channel), audience(s.audience)].filter(Boolean).join(' · ')}
        actions={s.revoked_at ? <Chip tone="danger">Acesso cortado</Chip> : <Chip tone="ok">Ativa</Chip>}
      />
      <div className="grid gap-6">
        <section aria-labelledby="h-src" className={`${card} p-4 sm:p-5`}>
          <h2 id="h-src" className={`${h2} mb-4`}>
            O que esta fonte pode ler e fazer
          </h2>
          <Props
            items={[
              ['Finalidades', purposes(s.purposes)],
              ['Audiência', audience(s.audience)],
              ['Teto de verificação', <span className="font-mono">{verification(s.verification_ceiling)}</span>],
              ['Confirma antes de agir', s.confirms_before_acting ? 'Sim: pergunta de confirmação não conta como repetição' : 'Não'],
              ['Consulta ao histórico', s.navigation_enabled ? 'Ligada' : 'Desligada'],
              ['Operações confiáveis', s.trusted_action_ops.join(', ') || 'sem lista: registra qualquer operação, e a pendência espera o sistema confirmar'],
              ['Fonte crítica', s.critical ? 'Sim: fica fora de grupos de controle' : 'Não'],
              ...(s.revoked_at ? ([['Acesso cortado em', fmtDateTime(s.revoked_at)]] as [string, ReactNode][]) : []),
            ]}
          />
          <p className={`${small} mt-4 border-t border-line2 pt-3`}>
            Estas regras valem a partir da criação da fonte. Para mudar uma delas, crie uma fonte nova com as regras certas, emita a chave dela e corte esta.
          </p>
        </section>

        <Keys source={s} />
        {!s.revoked_at && <CutAccess source={s} onDone={directory.reload} />}
      </div>
    </>
  )
}

type KeyAction = { kind: 'issue' } | { kind: 'rotate'; key: KeyOut } | { kind: 'revoke'; key: KeyOut }

function Keys({ source }: { source: SourceOut }) {
  const { control, hasRole } = useSpace()
  const keys = useResource((signal) => control.keys(source.source_id, { signal }), [source.source_id, control])
  const [action, setAction] = useState<KeyAction | null>(null)
  const [secret, setSecret] = useState<{ issued: IssuedKeyOut; previous?: KeyOut; grace?: number | null } | null>(null)
  const canManage = hasRole('admin')
  const active = !source.revoked_at

  const done = (next?: { issued: IssuedKeyOut; previous?: KeyOut; grace?: number | null }) => {
    setAction(null)
    if (next) setSecret(next)
    keys.reload()
  }

  return (
    <section aria-labelledby="h-keys" data-tour="agent.keys" className={`${card} p-4 sm:p-5`}>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="h-keys" className={h2}>
            Chaves
          </h2>
          <p className={`${small} mt-0.5`}>O segredo aparece uma vez, na emissão. A Niadra guarda só dois hashes dele.</p>
        </div>
        {canManage && active && (
          <button type="button" className={btnPrimary} onClick={() => setAction({ kind: 'issue' })}>
            <Icon name="key" size={16} />
            Emitir chave
          </button>
        )}
      </div>
      {!canManage && <p className={`${small} mb-3`}>Emitir, girar e revogar chaves exige o papel Administração.</p>}

      {keys.status === 'loading' && <Loading label="Carregando as chaves…" />}
      {keys.status === 'error' && <ErrorState error={keys.error} onRetry={keys.reload} />}
      {keys.status === 'success' && keys.data && keys.data.length === 0 && <Empty title="Esta fonte ainda não tem chave.">Sem chave, o agente não consegue pedir contexto nem enviar eventos.</Empty>}
      {keys.status === 'success' && keys.data && keys.data.length > 0 && (
        <div className="-mx-4 overflow-x-auto sm:mx-0">
          <table className="w-full min-w-[46rem] border-collapse">
            <thead>
              <tr>
                <th scope="col" className={th}>Chave</th>
                <th scope="col" className={th}>Escopos</th>
                <th scope="col" className={th}>Estado</th>
                <th scope="col" className={th}>Criada</th>
                <th scope="col" className={th}>Vence</th>
                {canManage && <th scope="col" className={`${th} text-right`}>Ações</th>}
              </tr>
            </thead>
            <tbody>
              {keys.data.map((k) => (
                <tr key={k.key_id}>
                  <td className={`${td} ${mono} text-ink`}>{k.key_id}</td>
                  <td className={`${td} text-[0.8125rem]`}>{k.scopes.map(scope).join(', ')}</td>
                  <td className={td}>{k.status === 'active' ? k.expires_at ? <Chip>Em troca</Chip> : <Chip tone="ok">Ativa</Chip> : <Chip tone="danger">Revogada</Chip>}</td>
                  <td className={`${td} text-[0.8125rem]`}>{fmtDateTime(k.created_at)}</td>
                  <td className={`${td} text-[0.8125rem]`}>{k.revoked_at ? `revogada ${fmtDateTime(k.revoked_at)}` : k.expires_at ? fmtDateTime(k.expires_at) : 'sem prazo'}</td>
                  {canManage && (
                    <td className={`${td} text-right`}>
                      {k.status === 'active' && active && (
                        <div className="flex justify-end gap-1">
                          {!k.expires_at && (
                            <button type="button" className={btnQuiet} onClick={() => setAction({ kind: 'rotate', key: k })} aria-label={`Girar a chave ${k.key_id}`}>
                              Girar
                            </button>
                          )}
                          <button type="button" className={`${btnQuiet} ${danger.text}`} onClick={() => setAction({ kind: 'revoke', key: k })} aria-label={`Revogar a chave ${k.key_id}`}>
                            Revogar
                          </button>
                        </div>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {action?.kind === 'issue' && <IssueDialog source={source} onClose={() => setAction(null)} onDone={(issued) => done({ issued })} />}
      {action?.kind === 'rotate' && <RotateDialog source={source} apiKey={action.key} onClose={() => setAction(null)} onDone={(issued, grace) => done({ issued, previous: action.key, grace })} />}
      {action?.kind === 'revoke' && <RevokeKeyDialog source={source} apiKey={action.key} onClose={() => setAction(null)} onDone={() => done()} />}
      {secret && <SecretDialog {...secret} onClose={() => setSecret(null)} />}
    </section>
  )
}

function IssueDialog({ source, onClose, onDone }: { source: SourceOut; onClose: () => void; onDone: (r: IssuedKeyOut) => void }) {
  const { control } = useSpace()
  const [custom, setCustom] = useState(false)
  const [scopes, setScopes] = useState<Scope[]>([])
  const act = useAction<IssuedKeyOut>()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    act.run(() => control.issueKey(source.source_id, custom ? scopes : null), onDone)
  }
  return (
    <Dialog open onClose={onClose} title={`Emitir chave para ${source.name}`}>
      <form onSubmit={submit} className="grid gap-4">
        <p className="text-ink">Por padrão, a chave recebe os escopos que a audiência da fonte ({audience(source.audience)}) permite.</p>
        <label className="flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] text-ink">
          <input type="checkbox" className="h-5 w-5 accent-[#5e7c00]" checked={custom} onChange={(e) => setCustom(e.target.checked)} />
          Escolher os escopos
        </label>
        {custom && (
          <fieldset className="grid gap-1 sm:grid-cols-2">
            <legend className="mb-1 text-[0.8125rem] font-semibold text-ink">Escopos</legend>
            {SCOPES.map((sc) => (
              <label key={sc} className="flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] text-ink lg:min-h-8">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-[#5e7c00]"
                  checked={scopes.includes(sc)}
                  onChange={(e) => setScopes((cur) => (e.target.checked ? [...cur, sc] : cur.filter((x) => x !== sc)))}
                />
                {scope(sc)}
              </label>
            ))}
          </fieldset>
        )}
        {Boolean(act.error) && <ErrorState error={act.error} compact />}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onClose}>
            Cancelar
          </button>
          <button type="submit" className={btnPrimary} disabled={act.busy || (custom && scopes.length === 0)}>
            {act.busy ? 'Emitindo…' : 'Emitir chave'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

const GRACE: { value: string; label: string }[] = [
  { value: '3600', label: '1 hora' },
  { value: '86400', label: '24 horas' },
  { value: '604800', label: '7 dias' },
  { value: '0', label: 'Nenhum: a atual para na hora' },
]

function RotateDialog({ source, apiKey, onClose, onDone }: { source: SourceOut; apiKey: KeyOut; onClose: () => void; onDone: (r: IssuedKeyOut, grace: number) => void }) {
  const { control } = useSpace()
  const [grace, setGrace] = useState('86400')
  const act = useAction<IssuedKeyOut>()
  const gid = useId()
  return (
    <Dialog open onClose={onClose} title="Girar a chave">
      <p className="text-ink">
        Uma chave nova é emitida para <strong>{source.name}</strong>. A chave <span className={mono}>{apiKey.key_id}</span> continua valendo pelo período de troca, para o agente passar a usar a nova sem cair.
      </p>
      <div className="mt-4">
        <label htmlFor={gid} className={label}>
          Período de troca
        </label>
        <Select id={gid} triggerClassName={select} value={grace} onChange={setGrace} options={GRACE} />
      </div>
      {Boolean(act.error) && (
        <div className="mt-4">
          <ErrorState error={act.error} compact />
        </div>
      )}
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button type="button" className={btnSecondary} onClick={onClose}>
          Cancelar
        </button>
        <button type="button" className={btnPrimary} disabled={act.busy} onClick={() => act.run(() => control.rotateKey(source.source_id, apiKey.key_id, Number(grace)), (r) => onDone(r, Number(grace)))}>
          {act.busy ? 'Girando…' : 'Girar a chave'}
        </button>
      </div>
    </Dialog>
  )
}

function RevokeKeyDialog({ source, apiKey, onClose, onDone }: { source: SourceOut; apiKey: KeyOut; onClose: () => void; onDone: () => void }) {
  const { control } = useSpace()
  const [reason, setReason] = useState('')
  const act = useAction<RevocationOut>()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    act.run(() => control.revokeKey(source.source_id, apiKey.key_id, reason.trim()), onDone)
  }
  return (
    <Dialog open onClose={onClose} title="Revogar a chave">
      <form onSubmit={submit} className="grid gap-4">
        <p className="text-ink">
          A chave <span className={mono}>{apiKey.key_id}</span> de <strong>{source.name}</strong> para de funcionar. As outras chaves da fonte continuam valendo.
        </p>
        <ReasonField value={reason} onChange={setReason} />
        {Boolean(act.error) && <ErrorState error={act.error} compact />}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onClose}>
            Cancelar
          </button>
          <button type="submit" className={btnDanger} disabled={act.busy || !reasonOk(reason)}>
            {act.busy ? 'Revogando…' : 'Revogar a chave'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

/** The secret is shown once and lives only in this dialog's state; closing it drops the last copy in the browser. */
export function SecretDialog({ issued, previous, grace, onClose, children }: { issued: IssuedKeyOut; previous?: KeyOut; grace?: number | null; onClose: () => void; children?: ReactNode }) {
  const [copied, setCopied] = useState<'idle' | 'ok' | 'fail'>('idle')
  const [saved, setSaved] = useState(false)
  const checkId = useId()
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(issued.secret)
      setCopied('ok')
    } catch {
      setCopied('fail')
    }
  }
  const graceText = grace === undefined || grace === null ? null : grace === 0 ? 'parou agora' : `vale por mais ${GRACE.find((g) => g.value === String(grace))?.label ?? `${grace} s`}`
  return (
    <Dialog open onClose={onClose} title="Chave emitida" wide>
      <p className="font-semibold text-ink">Esta é a única vez que o segredo aparece.</p>
      <p className={`${small} mt-1`}>Guarde no cofre de segredos do servidor do agente. Se perder, gire a chave e use a nova.</p>
      <div className="mt-4 rounded-xl border border-line bg-bg p-3">
        <p className="text-[0.75rem] font-semibold text-ink2">Segredo da chave {issued.key.key_id}</p>
        <p className={`${mono} mt-1 select-all break-all text-[0.8125rem] text-ink`} data-testid="secret">
          {issued.secret}
        </p>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" className={btnSecondary} onClick={copy}>
          <Icon name={copied === 'ok' ? 'check' : 'copy'} size={16} />
          {copied === 'ok' ? 'Copiado' : 'Copiar'}
        </button>
        {copied === 'fail' && <span className={`text-[0.8125rem] font-semibold ${danger.text}`}>O navegador não deixou copiar. Selecione o texto e copie.</span>}
      </div>
      {previous && graceText && (
        <p className="mt-4 text-[0.875rem] text-ink">
          A chave anterior, <span className={mono}>{previous.key_id}</span>, {graceText}.
        </p>
      )}
      {children}
      <label htmlFor={checkId} className="mt-5 flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] text-ink">
        <input id={checkId} type="checkbox" className="h-5 w-5 accent-[#5e7c00]" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        Guardei o segredo em lugar seguro
      </label>
      <div className="mt-4 flex justify-end">
        <button type="button" className={btnPrimary} disabled={!saved} onClick={onClose}>
          Concluir
        </button>
      </div>
      <p className={hint}>Fechar sem guardar descarta a única cópia no navegador.</p>
    </Dialog>
  )
}

/**
 * Immediate cut: the data API stops every key of the source
 * in its cell within seconds, and the control plane records the revocation so
 * the next signed snapshot keeps it. Both calls carry the same Idempotency-Key,
 * so trying again after a partial failure is safe.
 */
function CutAccess({ source, onDone }: { source: SourceOut; onDone: () => void }) {
  const { hasRole } = useSpace()
  const [open, setOpen] = useState(false)
  const allowed = hasRole('security')
  return (
    <section aria-labelledby="h-cut" data-tour="agent.cut" className={`rounded-xl border ${danger.border} bg-surface p-4 sm:p-5`}>
      <h2 id="h-cut" className={h2}>
        Cortar o acesso agora
      </h2>
      <p className={`${small} mt-1 max-w-[70ch]`}>
        Todas as chaves de {source.name} param de funcionar em segundos, em todas as instâncias da região. O agente deixa de receber contexto e de enviar eventos. O corte vira permanente na próxima
        configuração assinada.
      </p>
      {allowed ? (
        <button type="button" className={`${btnDangerGhost} mt-4`} onClick={() => setOpen(true)}>
          <Icon name="block" size={16} />
          Cortar o acesso de {source.name}
        </button>
      ) : (
        <p className={`${small} mt-3`}>O corte exige o papel Segurança e privacidade.</p>
      )}
      {open && <CutDialog source={source} onClose={() => setOpen(false)} onDone={onDone} />}
    </section>
  )
}

type Settled = { cell?: SourceRevocationOut; cellError?: unknown; control?: RevocationOut; controlError?: unknown }

function CutDialog({ source, onClose, onDone }: { source: SourceOut; onClose: () => void; onDone: () => void }) {
  const { cell, control } = useSpace()
  const [reason, setReason] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Settled | null>(null)
  const [idem] = useState(newIdempotencyKey)
  const cid = useId()
  const matches = confirm.trim() === source.name

  async function cut(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    const [c, k] = await Promise.allSettled([cell.revokeSource(source.source_id, idem), control.revokeSource(source.source_id, reason.trim(), idem)])
    setResult({
      ...(c.status === 'fulfilled' ? { cell: c.value } : { cellError: c.reason }),
      ...(k.status === 'fulfilled' ? { control: k.value } : { controlError: k.reason }),
    })
    setBusy(false)
  }

  const finished = result?.cell && result.control
  const close = () => {
    if (result?.cell || result?.control) onDone()
    onClose()
  }

  return (
    <Dialog open onClose={close} title={`Cortar o acesso de ${source.name}`}>
      {finished ? (
        <div role="status">
          <p className="font-semibold text-ink">Acesso cortado na região às {fmtDateTime(result.cell!.revoked_at)}.</p>
          <p className={`${small} mt-1`}>
            {result.cell!.revoked_key_ids.length} {result.cell!.revoked_key_ids.length === 1 ? 'chave parou' : 'chaves pararam'} de funcionar. O corte está registrado no plano de controle (revogação{' '}
            {result.control!.seq}) e entra na próxima configuração assinada.
          </p>
          <div className="mt-5 flex justify-end">
            <button type="button" className={btnPrimary} onClick={close}>
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={cut} className="grid gap-4">
          {result && (
            <div role="alert" className="grid gap-2 text-[0.875rem]">
              {result.cell ? (
                <p className="text-ink">
                  <strong>Na região: cortado</strong> às {fmtDateTime(result.cell.revoked_at)}.
                </p>
              ) : (
                <p className={danger.text}>
                  <strong>Na região: não cortado.</strong> {describeError(result.cellError).title} {describeError(result.cellError).detail}
                </p>
              )}
              {result.control ? (
                <p className="text-ink">
                  <strong>No plano de controle: registrado.</strong>
                </p>
              ) : (
                <p className={danger.text}>
                  <strong>No plano de controle: não registrado.</strong> {describeError(result.controlError).title} Sem esse registro, o corte pode sumir quando a próxima configuração chegar. Tente de novo: a
                  mesma ação não é aplicada duas vezes.
                </p>
              )}
            </div>
          )}
          <ReasonField value={reason} onChange={setReason} />
          <div>
            <label htmlFor={cid} className={label}>
              Digite <span className="font-mono">{source.name}</span> para confirmar
            </label>
            <input id={cid} className={input} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" disabled={Boolean(result)} />
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={close}>
              {result ? 'Fechar' : 'Cancelar'}
            </button>
            <button type="submit" className={btnDanger} disabled={busy || !reasonOk(reason) || !matches}>
              {busy ? 'Cortando…' : result ? 'Tentar de novo' : 'Cortar o acesso'}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}
