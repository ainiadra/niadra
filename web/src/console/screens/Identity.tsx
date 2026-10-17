import { useState, type FormEvent } from 'react'
import { useTab } from '../lib/tabs'
import { Link } from 'react-router-dom'
import type { AssertionMethod, AssertionOut, AssertionState, HandleIn, HandleType, LinkMethod, LinkOut, OperationOut, ParentMethod, ParentOut, ProfileOut, SubjectKind, SuggestionOut } from '../api/types'
import { dayToISO, fmtDateTime, fmtPct } from '../lib/format'
import { ASSERTION_METHODS, assertionMethod, assertionState, handleStatus, handleType, SUBJECT_KINDS, subjectKind, verification } from '../lib/labels'
import { usePaged } from '../lib/useResource'
import { paths } from '../paths'
import { useSpace } from '../session'
import { Chip, OperationNotice, PageHeader } from '../ui/bits'
import { Dialog } from '../ui/Dialog'
import { cleanHandle, emptyHandle, handleOk, HandleInput, ReasonField, reasonOk, Section, Select, TextInput, useAction } from '../ui/forms'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, LoadMore, Loading } from '../ui/States'
import { Tabs } from '../ui/Tabs'
import { btnDanger, btnPrimary, btnQuiet, btnSecondary, card, danger, mono, small, td, th } from '../ui/styles'

type TabKey = 'assertions' | 'suggestions' | 'merge' | 'block' | 'links' | 'parents'

/**
 * Identity as a first-class API: who is who, and why.
 * Every change is queued and applied by the cell; the answer carries the operation id.
 */
export function Identity() {
  const { space, hasRole } = useSpace()
  useTitle('Identidade', space.environment === 'sandbox')
  const [tab, setTab] = useTab<TabKey>(['assertions', 'suggestions', 'merge', 'block', 'links', 'parents'], 'assertions')
  const canWrite = hasRole('security', 'integration')
  return (
    <>
      <PageHeader
        screen="identity"
      />
      {!canWrite && <p className={`${small} mb-4`}>Mudar identidade exige o papel Segurança e privacidade ou Integração. Você pode consultar.</p>}
      <div data-tour="identity.tabs">
        <Tabs
          label="Identidade"
          active={tab}
          onChange={setTab}
          tabs={[
            { key: 'assertions', label: 'Asserções' },
            { key: 'suggestions', label: 'Sugestões' },
            { key: 'merge', label: 'Unir e separar' },
            { key: 'block', label: 'Bloquear identificador' },
            { key: 'links', label: 'Vínculos' },
            { key: 'parents', label: 'Matriz e subsidiárias' },
          ]}
        >
          <div data-tour={`identity.${tab}`}>
            {tab === 'assertions' && <Assertions canWrite={canWrite} />}
            {tab === 'suggestions' && <Suggestions canWrite={canWrite} />}
            {tab === 'merge' && <MergeAndUnmerge canWrite={canWrite} />}
            {tab === 'block' && <Block canWrite={canWrite} />}
            {tab === 'links' && <Links canWrite={canWrite} />}
            {tab === 'parents' && <Parents canWrite={canWrite} />}
          </div>
        </Tabs>
      </div>
    </>
  )
}

/**
 * The suggestion score as the server means it (domain/identity/suggest.py): points, the sum of the
 * weights of the signals two identifiers agree on (log2 of m/u, Fellegi-Sunter), and a pair becomes a
 * suggestion only from 10 points up. It is not a probability, so it is never shown as a percentage.
 */
const SUGGESTION_THRESHOLD = 10
const pointsFmt = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
/** The signals of suggest.py FEATURES, in words; `features.agree` lists the ones a pair agrees on. */
const AGREEMENT: Record<string, string> = {
  gmail_canonical: 'mesmo endereço Gmail, escrito com pontos ou + diferentes',
  email_local: 'mesmo nome antes do @',
  phone_subscriber: 'mesmos 8 últimos dígitos do telefone',
  declared: 'declarados juntos num identify, sem prova (método declared), por um agente ou sistema',
}
const agreedSignals = (features: Record<string, unknown>) => (Array.isArray(features.agree) ? features.agree.filter((f): f is string => typeof f === 'string') : [])

/**
 * Candidate pairs found offline, for a person to judge. Accepting turns
 * the pair into an `accepted_suggestion` assertion, queued like any other.
 */
function Suggestions({ canWrite }: { canWrite: boolean }) {
  const { cell, space } = useSpace()
  const page = usePaged((cursor, signal) => cell.suggestions(cursor, { signal }), [cell])
  const [done, setDone] = useState<Record<string, { op?: OperationOut; dismissed?: boolean; error?: unknown }>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const act = async (s: SuggestionOut, how: 'accept' | 'dismiss') => {
    setBusy(s.suggestion_id)
    try {
      if (how === 'accept') {
        const op = await cell.acceptSuggestion(s.suggestion_id, '')
        setDone((m) => ({ ...m, [s.suggestion_id]: { op } }))
      } else {
        await cell.dismissSuggestion(s.suggestion_id)
        setDone((m) => ({ ...m, [s.suggestion_id]: { dismissed: true } }))
      }
    } catch (error) {
      setDone((m) => ({ ...m, [s.suggestion_id]: { error } }))
    } finally {
      setBusy(null)
    }
  }
  const side = (h: SuggestionOut['identifier_a'], profile: string | null) => (
    <span className="block">
      {h ? (
        <>
          {handleType(h.type)} <span className={mono}>{h.value}</span>
        </>
      ) : (
        <span className="text-ink2">identificador que não existe mais</span>
      )}
      {profile && (
        <>
          {' · '}
          <Link to={paths.profile(space.space_id, profile)} className="font-mono text-[0.8125rem] text-ink underline decoration-accent decoration-2 underline-offset-4">
            {profile}
          </Link>
        </>
      )}
    </span>
  )
  return (
    <div className="grid gap-4">
      <p className={small}>
        Pares de identificadores que parecem do mesmo cliente. A pontuação soma o peso de cada sinal que os dois identificadores têm em comum, e o par só vira sugestão a partir de {SUGGESTION_THRESHOLD}{' '}
        pontos. Uma sugestão nunca une ninguém sozinha: aceitar cria uma asserção em seu nome; dispensar tira o par da lista.
      </p>
      {(page.status === 'loading' || page.status === 'idle') && <Loading label="Carregando as sugestões…" />}
      {page.status === 'error' && <ErrorState error={page.error} onRetry={page.reload} />}
      {page.status === 'success' && page.items.length === 0 && (
        <Empty
          title="Nenhuma sugestão aberta."
          example={
            <>
              Telefone <span className={mono}>•••• 4471</span> e E-mail <span className={mono}>m•••@••••.com</span> · 12 pontos · apareceram juntos em 3 eventos do agente de voz · aceitar ou dispensar
            </>
          }
        >
          Uma sugestão nasce dos eventos: quando dois identificadores aparecem juntos (o mesmo evento traz os dois, ou o canal troca um pelo outro), cada sinal soma pontos, e o par entra aqui a partir de {SUGGESTION_THRESHOLD}. Nada se une sozinho por nome parecido. Para ver sugestões, os agentes precisam mandar eventos com mais de um identificador do mesmo cliente; uma ligação que o seu time já conhece se registra à mão em Asserções.
        </Empty>
      )}
      {page.status === 'success' && page.items.length > 0 && (
        <>
          <ul className={`${card} divide-y divide-line2`}>
            {page.items.map((s) => {
              const d = done[s.suggestion_id]
              return (
                <li key={s.suggestion_id} className="grid gap-2 px-4 py-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 text-ink">
                      {side(s.identifier_a, s.profile_a)}
                      {side(s.identifier_b, s.profile_b)}
                      <p className={`${small} mt-0.5`}>
                        <span className="font-semibold text-ink">{pointsFmt.format(s.score)} pontos</span> · {fmtDateTime(s.created_at)}
                      </p>
                      {agreedSignals(s.features).length > 0 && <p className={small}>Sinais em comum: {agreedSignals(s.features).map((f) => AGREEMENT[f] ?? f).join('; ')}.</p>}
                    </div>
                    {!d?.op && !d?.dismissed && canWrite && (
                      <div className="flex gap-1">
                        <button type="button" className={btnQuiet} disabled={busy === s.suggestion_id} onClick={() => act(s, 'accept')} aria-label={`Aceitar a sugestão ${s.suggestion_id}`}>
                          Aceitar
                        </button>
                        <button type="button" className={btnQuiet} disabled={busy === s.suggestion_id} onClick={() => act(s, 'dismiss')} aria-label={`Dispensar a sugestão ${s.suggestion_id}`}>
                          Dispensar
                        </button>
                      </div>
                    )}
                    {d?.dismissed && <Chip>Dispensada</Chip>}
                  </div>
                  {d?.op && <OperationNotice op={d.op} />}
                  {Boolean(d?.error) && <ErrorState error={d!.error} compact />}
                </li>
              )
            })}
          </ul>
          <LoadMore hasMore={page.hasMore} loading={page.loadingMore} error={page.moreError} onClick={page.loadMore} />
        </>
      )}
    </div>
  )
}

const STATE_TONE: Record<AssertionState, 'ok' | 'neutral' | 'danger'> = { applied: 'ok', pending: 'neutral', rejected: 'danger' }

function Assertions({ canWrite }: { canWrite: boolean }) {
  const { cell } = useSpace()
  const [profileId, setProfileId] = useState('')
  const [state, setState] = useState<'' | AssertionState>('')
  const [applied, setApplied] = useState<{ profile_id?: string; state?: AssertionState }>({})
  const page = usePaged((cursor, signal) => cell.assertions({ ...applied, cursor }, { signal }), [applied.profile_id, applied.state, cell])
  const [creating, setCreating] = useState(false)
  const [retracting, setRetracting] = useState<AssertionOut | null>(null)

  return (
    <div className="grid gap-5">
      <form
        className={`${card} flex flex-wrap items-end gap-3 p-4`}
        onSubmit={(e) => {
          e.preventDefault()
          setApplied({ profile_id: profileId.trim() || undefined, state: state || undefined })
        }}
        aria-label="Filtrar asserções"
      >
        <div className="min-w-[14rem] flex-1">
          <TextInput label="ID do perfil" value={profileId} onChange={setProfileId} mono />
        </div>
        <div className="w-full sm:w-48">
          <Select
            label="Estado"
            value={state}
            onChange={setState}
            options={[
              { value: '', label: 'Todos' },
              { value: 'applied', label: 'Aplicadas' },
              { value: 'pending', label: 'Pendentes' },
              { value: 'rejected', label: 'Recusadas' },
            ]}
          />
        </div>
        <button type="submit" className={btnSecondary}>
          Filtrar
        </button>
        {canWrite && (
          <button type="button" className={btnPrimary} onClick={() => setCreating(true)}>
            Nova asserção
          </button>
        )}
      </form>

      {(page.status === 'loading' || page.status === 'idle') && <Loading label="Carregando as asserções…" />}
      {page.status === 'error' && <ErrorState error={page.error} onRetry={page.reload} />}
      {page.status === 'success' && page.items.length === 0 && <Empty title="Nenhuma asserção com esses filtros.">
          Asserções nascem quando um identificador prova que é da mesma pessoa que outro: um login, um código de verificação, um sistema que diz "este telefone é deste cliente". Exemplo do que você verá:{' '}
          <span className="text-ink">Telefone •••• 4471 e E-mail m•••@••••.com · Login · Aplicada · 29/09/2026 17:07</span>.
        </Empty>}
      {page.status === 'success' && page.items.length > 0 && (
        <>
          <div className={`${card} overflow-x-auto`}>
            <table className="w-full min-w-[60rem] border-collapse">
              <caption className="sr-only">Asserções de identidade</caption>
              <thead>
                <tr>
                  <th scope="col" className={th}>Identificadores</th>
                  <th scope="col" className={th}>Método</th>
                  <th scope="col" className={th}>Estado</th>
                  <th scope="col" className={`${th} text-right`}>Confiança</th>
                  <th scope="col" className={th}>Nível</th>
                  <th scope="col" className={th}>Quando e quem</th>
                  {canWrite && <th scope="col" className={`${th} text-right`}>Ação</th>}
                </tr>
              </thead>
              <tbody>
                {page.items.map((a) => (
                  <tr key={a.assertion_id}>
                    <td className={`${td} ${mono}`}>
                      {a.handle_a}
                      <span className="block">{a.handle_b}</span>
                    </td>
                    <td className={td}>{assertionMethod(a.method)}</td>
                    <td className={td}>
                      {a.retracted_at ? <Chip tone="danger">Retirada {fmtDateTime(a.retracted_at)}</Chip> : <Chip tone={STATE_TONE[a.state]}>{assertionState(a.state)}</Chip>}
                    </td>
                    <td className={`${td} text-right tabular-nums`}>{fmtPct(a.confidence)}</td>
                    <td className={`${td} ${mono}`}>{verification(a.level_at_capture)}</td>
                    <td className={`${td} text-[0.8125rem]`}>
                      {fmtDateTime(a.created_at)}
                      {a.actor && <span className={`${mono} block text-ink2`}>{a.actor}</span>}
                      {a.reason && <span className="block text-ink2">{a.reason}</span>}
                    </td>
                    {canWrite && (
                      <td className={`${td} text-right`}>
                        {!a.retracted_at && (
                          <button type="button" className={`${btnQuiet} ${danger.text}`} onClick={() => setRetracting(a)} aria-label={`Retirar a asserção ${a.assertion_id}`}>
                            Retirar
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <LoadMore hasMore={page.hasMore} loading={page.loadingMore} error={page.moreError} onClick={page.loadMore} />
        </>
      )}

      {creating && <CreateAssertion onClose={() => setCreating(false)} onDone={page.reload} />}
      {retracting && <RetractAssertion assertion={retracting} onClose={() => setRetracting(null)} onDone={page.reload} />}
    </div>
  )
}

function CreateAssertion({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { cell } = useSpace()
  const [a, setA] = useState<HandleIn>(emptyHandle('phone_e164'))
  const [b, setB] = useState<HandleIn>(emptyHandle('email'))
  const [method, setMethod] = useState<AssertionMethod>('explicit_identify')
  const [kind, setKind] = useState<SubjectKind>('person')
  const [reason, setReason] = useState('')
  const act = useAction<OperationOut>()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    act.run(() => cell.createAssertion({ handles: [cleanHandle(a), cleanHandle(b)], method, subject_kind: kind, ...(reason.trim() ? { reason: reason.trim() } : {}) }), onDone)
  }
  return (
    <Dialog open onClose={onClose} title="Nova asserção" wide>
      {act.result ? (
        <div className="grid gap-4">
          <OperationNotice op={act.result} />
          <div className="flex justify-end">
            <button type="button" className={btnPrimary} onClick={onClose}>
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={submit} className="grid gap-4">
          <p className={small}>Diz à memória que os dois identificadores são do mesmo sujeito. Os valores vão no corpo da requisição e voltam mascarados.</p>
          <HandleInput legend="Primeiro identificador" value={a} onChange={setA} />
          <HandleInput legend="Segundo identificador" value={b} onChange={setB} />
          <div className="grid gap-3 sm:grid-cols-2">
            <Select label="Método" value={method} onChange={setMethod} options={ASSERTION_METHODS.map((m) => ({ value: m, label: assertionMethod(m) }))} />
            <Select label="Tipo de sujeito" value={kind} onChange={setKind} options={SUBJECT_KINDS.map((k) => ({ value: k, label: subjectKind(k) }))} />
          </div>
          <ReasonField value={reason} onChange={setReason} required={false} />
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Cancelar
            </button>
            <button type="submit" className={btnPrimary} disabled={act.busy || !handleOk(a) || !handleOk(b)}>
              {act.busy ? 'Enviando…' : 'Criar asserção'}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}

function RetractAssertion({ assertion, onClose, onDone }: { assertion: AssertionOut; onClose: () => void; onDone: () => void }) {
  const { cell } = useSpace()
  const [reason, setReason] = useState('')
  const act = useAction<OperationOut>()
  return (
    <Dialog open onClose={onClose} title="Retirar a asserção">
      {act.result ? (
        <div className="grid gap-4">
          <OperationNotice op={act.result} />
          <p className={small}>Se a asserção era a única ligação entre os identificadores, o perfil se separa quando a operação for aplicada.</p>
          <div className="flex justify-end">
            <button type="button" className={btnPrimary} onClick={onClose}>
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            act.run(() => cell.retractAssertion(assertion.assertion_id, reason.trim()), onDone)
          }}
        >
          <p className="text-ink">
            A asserção <span className={mono}>{assertion.assertion_id}</span> deixa de valer. Ela continua no histórico, com a data da retirada.
          </p>
          <ReasonField value={reason} onChange={setReason} />
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Cancelar
            </button>
            <button type="submit" className={btnDanger} disabled={act.busy || !reasonOk(reason)}>
              {act.busy ? 'Retirando…' : 'Retirar a asserção'}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}

function MergeAndUnmerge({ canWrite }: { canWrite: boolean }) {
  const { cell } = useSpace()
  const [a, setA] = useState('')
  const [b, setB] = useState('')
  const [force, setForce] = useState(false)
  const [reason, setReason] = useState('')
  const merge = useAction<OperationOut>()

  const [target, setTarget] = useState('')
  const [profile, setProfile] = useState<{ status: 'idle' | 'loading' | 'success' | 'error'; data?: ProfileOut; error?: unknown }>({ status: 'idle' })
  // The applied assertions of the profile: the proofs that joined its identifiers, to undo one by one.
  const [joins, setJoins] = useState<{ status: 'idle' | 'loading' | 'success' | 'error'; items: AssertionOut[]; error?: unknown }>({ status: 'idle', items: [] })
  const [by, setBy] = useState<'handles' | 'assertions'>('handles')
  const [picked, setPicked] = useState<string[]>([])
  const [pickedJoins, setPickedJoins] = useState<string[]>([])
  const [unReason, setUnReason] = useState('')
  const unmerge = useAction<OperationOut>()

  const loadProfile = async (e: FormEvent) => {
    e.preventDefault()
    setPicked([])
    setPickedJoins([])
    unmerge.reset()
    setProfile({ status: 'loading' })
    setJoins({ status: 'loading', items: [] })
    const id = target.trim()
    try {
      setProfile({ status: 'success', data: await cell.profile(id) })
    } catch (error) {
      setProfile({ status: 'error', error })
      setJoins({ status: 'idle', items: [] })
      return
    }
    try {
      setJoins({ status: 'success', items: (await cell.assertions({ profile_id: id, state: 'applied' })).items.filter((a) => !a.retracted_at) })
    } catch (error) {
      setJoins({ status: 'error', items: [], error })
    }
  }

  const mergeReady = a.trim() && b.trim() && a.trim() !== b.trim() && (!force || reasonOk(reason))

  return (
    <div className="grid gap-6">
      <Section title="Unir dois perfis" description="Os dois perfis viram um só. Perfis verificados e organizações são protegidos: unir assim mesmo pede a opção forçada, com motivo.">
        {canWrite ? (
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              merge.run(() => cell.merge([a.trim(), b.trim()], force, reason.trim()))
            }}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <TextInput label="ID do primeiro perfil" value={a} onChange={setA} mono />
              <TextInput label="ID do segundo perfil" value={b} onChange={setB} mono />
            </div>
            <label className="flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] text-ink">
              <input type="checkbox" className="h-5 w-5 accent-[#5e7c00]" checked={force} onChange={(e) => setForce(e.target.checked)} />
              Forçar a união, mesmo com perfil verificado ou organização
            </label>
            <ReasonField value={reason} onChange={setReason} required={force} />
            {Boolean(merge.error) && <ErrorState error={merge.error} compact />}
            {merge.result && <OperationNotice op={merge.result} />}
            <div>
              <button type="submit" className={btnPrimary} disabled={merge.busy || !mergeReady}>
                {merge.busy ? 'Enviando…' : 'Unir os perfis'}
              </button>
            </div>
          </form>
        ) : (
          <p className={small}>Exige o papel Segurança e privacidade ou Integração.</p>
        )}
      </Section>

      <Section title="Separar um perfil" description="Escolha os identificadores que saem do perfil, ou as ligações que se desfazem. Os fatos, episódios e padrões presos a cada identificador vão junto, porque a memória guarda a origem de cada item.">
        <form className="flex flex-wrap items-end gap-3" onSubmit={loadProfile}>
          <div className="min-w-[16rem] flex-1">
            <TextInput label="ID do perfil" value={target} onChange={setTarget} mono />
          </div>
          <button type="submit" className={btnSecondary} disabled={!target.trim() || profile.status === 'loading'}>
            Carregar identificadores
          </button>
        </form>
        <div className="mt-4">
          {profile.status === 'loading' && <Loading label="Carregando o perfil…" />}
          {profile.status === 'error' && <ErrorState error={profile.error} compact />}
          {profile.status === 'success' && profile.data && (
            <form
              className="grid gap-4"
              onSubmit={(e) => {
                e.preventDefault()
                const id = profile.data!.profile_id
                unmerge.run(() => (by === 'handles' ? cell.unmerge(id, picked, unReason.trim()) : cell.unmergeAssertions(id, pickedJoins, unReason.trim())))
              }}
            >
              <fieldset>
                <legend className="mb-1 text-[0.8125rem] font-semibold text-ink">Separar por</legend>
                <div className="flex flex-wrap gap-x-5">
                  {(
                    [
                      ['handles', 'Identificadores que saem'],
                      ['assertions', 'Ligações que se desfazem'],
                    ] as const
                  ).map(([value, text]) => (
                    <label key={value} className="flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] text-ink lg:min-h-9">
                      <input
                        type="radio"
                        name="unmerge-by"
                        className="h-4 w-4 accent-[#5e7c00]"
                        checked={by === value}
                        onChange={() => {
                          setBy(value)
                          unmerge.reset()
                        }}
                      />
                      {text}
                    </label>
                  ))}
                </div>
              </fieldset>
              {by === 'assertions' ? (
                <Joins profile={profile.data} joins={joins} picked={pickedJoins} onPick={setPickedJoins} />
              ) : (
              <fieldset>
                <legend className="mb-2 text-[0.8125rem] font-semibold text-ink">
                  Identificadores de <span className="font-mono">{profile.data.pseudonym}</span> que saem
                </legend>
                {profile.data.handles.length === 0 && <p className={small}>Nenhum identificador visível para o seu papel.</p>}
                <ul className="grid gap-1">
                  {profile.data.handles.map((h) => (
                    <li key={h.handle_id}>
                      <label className="flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] text-ink lg:min-h-9">
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-[#5e7c00]"
                          checked={picked.includes(h.handle_id)}
                          onChange={(e) => setPicked((cur) => (e.target.checked ? [...cur, h.handle_id] : cur.filter((x) => x !== h.handle_id)))}
                        />
                        {handleType(h.type)} <span className={mono}>{h.value}</span>
                        {h.status !== 'active' && <Chip>{handleStatus(h.status)}</Chip>}
                      </label>
                    </li>
                  ))}
                </ul>
              </fieldset>
              )}
              <ReasonField value={unReason} onChange={setUnReason} required={false} />
              {Boolean(unmerge.error) && <ErrorState error={unmerge.error} compact />}
              {unmerge.result && <OperationNotice op={unmerge.result} />}
              {canWrite && by === 'handles' && (
                <div>
                  <button type="submit" className={btnDanger} disabled={unmerge.busy || picked.length === 0 || picked.length === profile.data.handles.length}>
                    {unmerge.busy ? 'Enviando…' : 'Separar os identificadores escolhidos'}
                  </button>
                  {picked.length > 0 && picked.length === profile.data.handles.length && <p className={`${small} mt-1.5`}>Deixe ao menos um identificador no perfil.</p>}
                </div>
              )}
              {canWrite && by === 'assertions' && (
                <div>
                  <button type="submit" className={btnDanger} disabled={unmerge.busy || pickedJoins.length === 0}>
                    {unmerge.busy ? 'Enviando…' : 'Desfazer as ligações escolhidas'}
                  </button>
                </div>
              )}
            </form>
          )}
        </div>
      </Section>
    </div>
  )
}

/**
 * The proofs that joined the identifiers of a profile. Undone, an assertion stops counting and stays in
 * the history with the date; the profile splits wherever no other proof holds the identifiers together.
 */
function Joins({ profile, joins, picked, onPick }: { profile: ProfileOut; joins: { status: string; items: AssertionOut[]; error?: unknown }; picked: string[]; onPick: (ids: string[]) => void }) {
  const byId = new Map(profile.handles.map((h) => [h.handle_id, h]))
  const name = (id: string) => {
    const h = byId.get(id)
    return h ? `${handleType(h.type)} ${h.value}` : 'um identificador fora do alcance do seu papel'
  }
  return (
    <fieldset>
      <legend className="mb-1 text-[0.8125rem] font-semibold text-ink">
        Ligações de <span className="font-mono">{profile.pseudonym}</span> que se desfazem
      </legend>
      <p className={`${small} mb-2`}>Cada ligação é uma asserção: a prova de que dois identificadores são da mesma pessoa. O perfil se separa onde nenhuma outra prova segura os dois juntos.</p>
      {joins.status === 'loading' && <Loading label="Carregando as ligações…" />}
      {joins.status === 'error' && <ErrorState error={joins.error} compact />}
      {joins.status === 'success' && joins.items.length === 0 && <p className={small}>Nenhuma asserção aplicada liga identificadores deste perfil.</p>}
      <ul className="grid gap-1">
        {joins.items.map((a) => (
          <li key={a.assertion_id}>
            <label className="flex min-h-11 cursor-pointer items-start gap-2.5 py-1 text-[0.875rem] text-ink lg:min-h-9">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 shrink-0 accent-[#5e7c00]"
                checked={picked.includes(a.assertion_id)}
                onChange={(e) => onPick(e.target.checked ? [...picked, a.assertion_id] : picked.filter((x) => x !== a.assertion_id))}
              />
              <span className="min-w-0">
                {name(a.handle_a)} com {name(a.handle_b)}
                <span className={`${mono} block text-ink2`}>
                  {assertionMethod(a.method)} · {fmtDateTime(a.created_at)} · {a.assertion_id}
                </span>
              </span>
            </label>
          </li>
        ))}
      </ul>
    </fieldset>
  )
}

function Block({ canWrite }: { canWrite: boolean }) {
  const { cell } = useSpace()
  const [handle, setHandle] = useState<HandleIn>(emptyHandle())
  const [reason, setReason] = useState('')
  const act = useAction<OperationOut>()
  return (
    <Section
      title="Bloquear um identificador"
      description="Um identificador bloqueado não liga mais ninguém a ninguém: serve para o telefone da loja, o e-mail genérico da empresa ou um número reciclado pela operadora."
    >
      {canWrite ? (
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            act.run(() => cell.blockHandle(cleanHandle(handle), reason.trim()))
          }}
        >
          <HandleInput legend="Identificador" value={handle} onChange={setHandle} />
          <ReasonField value={reason} onChange={setReason} required={false} />
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          {act.result && <OperationNotice op={act.result} />}
          <div>
            <button type="submit" className={btnDanger} disabled={act.busy || !handleOk(handle)}>
              {act.busy ? 'Bloqueando…' : 'Bloquear o identificador'}
            </button>
          </div>
        </form>
      ) : (
        <p className={small}>Exige o papel Segurança e privacidade ou Integração.</p>
      )}
    </Section>
  )
}

const LINK_METHODS: { value: LinkMethod; label: string }[] = [
  { value: 'declared', label: 'Declarado' },
  { value: 'system_import', label: 'Importado de sistema' },
  { value: 'login', label: 'Login' },
  { value: 'co_occurrence', label: 'Apareceram juntos' },
]

function Links({ canWrite }: { canWrite: boolean }) {
  const { cell, space } = useSpace()
  const [person, setPerson] = useState<HandleIn>(emptyHandle('email'))
  const [org, setOrg] = useState<HandleIn>(emptyHandle('email_domain'))
  const [orgKind, setOrgKind] = useState<SubjectKind>('account')
  const [roleName, setRoleName] = useState('')
  const [contacts, setContacts] = useState(false)
  const [method, setMethod] = useState<LinkMethod>('declared')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const create = useAction<LinkOut>()

  const [linkId, setLinkId] = useState('')
  const [endAt, setEndAt] = useState('')
  const end = useAction<LinkOut>()

  return (
    <div className="grid gap-6">
      <Section title="Ligar uma pessoa a uma conta ou parceiro" description="O vínculo diz o papel da pessoa na organização (comprador, motorista, contato técnico) e por quanto tempo vale. É ele que leva o contexto da conta para a conversa da pessoa.">
        {canWrite ? (
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              create.run(() =>
                cell.createLink({
                  person: cleanHandle(person),
                  organization: { ...cleanHandle(org), subject_kind: orgKind },
                  role: roleName.trim(),
                  can_see_contacts: contacts,
                  method,
                  ...(from ? { valid_from: dayToISO(from) } : {}),
                  ...(to ? { valid_to: dayToISO(to, true) } : {}),
                }),
              )
            }}
          >
            <HandleInput legend="Pessoa" value={person} onChange={setPerson} />
            <HandleInput legend="Organização" value={org} onChange={setOrg} />
            <div className="grid gap-3 sm:grid-cols-2">
              <Select
                label="A organização é"
                value={orgKind}
                onChange={setOrgKind}
                options={[
                  { value: 'account', label: 'Conta (cliente)' },
                  { value: 'partner', label: 'Parceiro' },
                ]}
              />
              <TextInput label="Papel" value={roleName} onChange={setRoleName} hint="buyer, driver, broker, technical_contact, owner" mono />
              <Select label="Como se sabe" value={method} onChange={setMethod} options={LINK_METHODS} />
              <TextInput label="Vale de" type="date" value={from} onChange={setFrom} />
              <TextInput label="Vale até" type="date" value={to} onChange={setTo} min={from || undefined} />
            </div>
            <label className="flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] text-ink">
              <input type="checkbox" className="h-5 w-5 accent-[#5e7c00]" checked={contacts} onChange={(e) => setContacts(e.target.checked)} />
              A pessoa pode ver os contatos da organização
            </label>
            {Boolean(create.error) && <ErrorState error={create.error} compact />}
            {create.result && (
              <p role="status" className="rounded-lg border border-accent/40 bg-accent-wash px-3 py-2.5 text-[0.875rem] text-ink">
                <strong>Vínculo criado.</strong> <span className={mono}>{create.result.link_id}</span> · {create.result.role} · desde {fmtDateTime(create.result.valid_from)}
              </p>
            )}
            <div>
              <button type="submit" className={btnPrimary} disabled={create.busy || !handleOk(person) || !handleOk(org) || !roleName.trim()}>
                {create.busy ? 'Criando…' : 'Criar o vínculo'}
              </button>
            </div>
          </form>
        ) : (
          <p className={small}>Exige o papel Segurança e privacidade ou Integração.</p>
        )}
      </Section>

      <Section title="Encerrar um vínculo" description="O vínculo fica no histórico com a data de fim. Os vínculos de um cliente aparecem na memória dele, em Identificadores e vínculos.">
        {canWrite ? (
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              end.run(() => cell.endLink(linkId.trim(), endAt ? (dayToISO(endAt, true) ?? null) : null))
            }}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <TextInput label="ID do vínculo" value={linkId} onChange={setLinkId} mono />
              <TextInput label="Termina em" type="date" value={endAt} onChange={setEndAt} hint="Vazio: termina agora." />
            </div>
            {Boolean(end.error) && <ErrorState error={end.error} compact />}
            {end.result && (
              <p role="status" className="rounded-lg border border-accent/40 bg-accent-wash px-3 py-2.5 text-[0.875rem] text-ink">
                <strong>Vínculo encerrado</strong> em {fmtDateTime(end.result.valid_to)}.
              </p>
            )}
            <div>
              <button type="submit" className={btnSecondary} disabled={end.busy || !linkId.trim()}>
                {end.busy ? 'Encerrando…' : 'Encerrar o vínculo'}
              </button>
            </div>
          </form>
        ) : (
          <p className={small}>Exige o papel Segurança e privacidade ou Integração.</p>
        )}
        <p className={`${small} mt-4`}>
          Para ver os vínculos de um cliente, abra a memória dele em{' '}
          <Link className="font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4" to={paths.customers(space.space_id)}>
            Clientes
          </Link>
          .
        </p>
      </Section>
    </div>
  )
}

/** A parent names both organizations by an id: a registry or a system id, never an e-mail domain. */
const ORG_IDS: HandleType[] = ['system_id', 'org_registry_hmac']
const PARENT_METHODS: { value: ParentMethod; label: string }[] = [
  { value: 'declared', label: 'Declarado' },
  { value: 'system_import', label: 'Importado de sistema' },
]

/**
 * An organization under its parent: one level, the subsidiaries are sibling accounts, and a parent is
 * never a merge. The subsidiary's context then carries the parent's own facts.
 */
function Parents({ canWrite }: { canWrite: boolean }) {
  const { cell, space } = useSpace()
  const [child, setChild] = useState<HandleIn>(emptyHandle('system_id'))
  const [parent, setParent] = useState<HandleIn>(emptyHandle('system_id'))
  const [kind, setKind] = useState<SubjectKind>('account')
  const [method, setMethod] = useState<ParentMethod>('declared')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const create = useAction<ParentOut>()

  const [parentId, setParentId] = useState('')
  const [endAt, setEndAt] = useState('')
  const end = useAction<ParentOut>()

  return (
    <div className="grid gap-6">
      <Section
        title="Colocar uma organização sob a matriz"
        description="Um nível só: a matriz não tem matriz, e uma subsidiária não tem subsidiárias. As subsidiárias de uma matriz são contas irmãs, cada uma com a própria memória, e nenhuma se une à matriz. O contexto da subsidiária passa a trazer os fatos que a matriz guarda nos próprios IDs."
      >
        {canWrite ? (
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              create.run(() =>
                cell.setParent({
                  organization: { ...cleanHandle(child), subject_kind: kind },
                  parent: { ...cleanHandle(parent), subject_kind: kind },
                  method,
                  ...(from ? { valid_from: dayToISO(from) } : {}),
                  ...(to ? { valid_to: dayToISO(to, true) } : {}),
                }),
              )
            }}
          >
            <HandleInput legend="Subsidiária" value={child} onChange={setChild} types={ORG_IDS} />
            <HandleInput legend="Matriz" value={parent} onChange={setParent} types={ORG_IDS} />
            <div className="grid gap-3 sm:grid-cols-2">
              <Select
                label="As duas são"
                value={kind}
                onChange={setKind}
                options={[
                  { value: 'account', label: 'Contas (clientes)' },
                  { value: 'partner', label: 'Parceiros' },
                ]}
              />
              <Select label="Como se sabe" value={method} onChange={setMethod} options={PARENT_METHODS} />
              <TextInput label="Vale de" type="date" value={from} onChange={setFrom} />
              <TextInput label="Vale até" type="date" value={to} onChange={setTo} min={from || undefined} />
            </div>
            {Boolean(create.error) && <ErrorState error={create.error} compact />}
            {create.result && (
              <p role="status" className="rounded-lg border border-accent/40 bg-accent-wash px-3 py-2.5 text-[0.875rem] text-ink">
                <strong>Matriz registrada.</strong> <span className={mono}>{create.result.parent_link_id}</span> · desde {fmtDateTime(create.result.valid_from)}
                {create.result.org_profile_id && (
                  <>
                    {' · '}
                    <Link className="font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4" to={paths.profile(space.space_id, create.result.org_profile_id)}>
                      abrir a subsidiária
                    </Link>
                  </>
                )}
              </p>
            )}
            <div>
              <button type="submit" className={btnPrimary} disabled={create.busy || !handleOk(child) || !handleOk(parent)}>
                {create.busy ? 'Registrando…' : 'Registrar a matriz'}
              </button>
            </div>
          </form>
        ) : (
          <p className={small}>Exige o papel Segurança e privacidade ou Integração.</p>
        )}
      </Section>

      <Section title="Encerrar a matriz" description="A ligação fica no histórico com a data de fim, e as leituras seguintes da subsidiária deixam de trazer os fatos da matriz. Uma organização tem uma matriz por vez: para trocar, encerre a atual.">
        {canWrite ? (
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              end.run(() => cell.endParent(parentId.trim(), endAt ? (dayToISO(endAt, true) ?? null) : null))
            }}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <TextInput label="ID da ligação com a matriz" value={parentId} onChange={setParentId} mono />
              <TextInput label="Termina em" type="date" value={endAt} onChange={setEndAt} hint="Vazio: termina agora." />
            </div>
            {Boolean(end.error) && <ErrorState error={end.error} compact />}
            {end.result && (
              <p role="status" className="rounded-lg border border-accent/40 bg-accent-wash px-3 py-2.5 text-[0.875rem] text-ink">
                <strong>Matriz encerrada</strong> em {fmtDateTime(end.result.valid_to)}.
              </p>
            )}
            <div>
              <button type="submit" className={btnSecondary} disabled={end.busy || !parentId.trim()}>
                {end.busy ? 'Encerrando…' : 'Encerrar a matriz'}
              </button>
            </div>
          </form>
        ) : (
          <p className={small}>Exige o papel Segurança e privacidade ou Integração.</p>
        )}
        <p className={`${small} mt-4`}>
          A matriz e as subsidiárias de uma organização aparecem na memória dela, em Identificadores e vínculos, em{' '}
          <Link className="font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4" to={paths.customers(space.space_id)}>
            Clientes
          </Link>
          .
        </p>
      </Section>
    </div>
  )
}
