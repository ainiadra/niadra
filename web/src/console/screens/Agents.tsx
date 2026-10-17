import { useEffect, useId, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ApiError } from '../api/http'
import type { Audience, IssuedKeyOut, SourceOut, Verification } from '../api/types'
import { explainCeilingRefusal } from '../lib/ceiling'
import { audience, channel, purposes, verification } from '../lib/labels'
import { fmtDate } from '../lib/format'
import { paths } from '../paths'
import { useSpace } from '../session'
import { Chip, PageHeader, useSources } from '../ui/bits'
import { Dialog } from '../ui/Dialog'
import { Field, Select, TextInput, useAction } from '../ui/forms'
import { Icon } from '../ui/Icon'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, Loading } from '../ui/States'
import { btnPrimary, btnSecondary, card, danger, input, small, td, th } from '../ui/styles'
import { Term } from '../ui/Term'
import { SecretDialog } from './AgentDetail'

/** Every agent and system that talks to the memory is a source, with its own keys and audience. */
export function Agents() {
  const { space, hasRole } = useSpace()
  useTitle('Agentes e chaves', space.environment === 'sandbox')
  const sources = useSources()
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<{ source: SourceOut; issued: IssuedKeyOut | null } | null>(null)
  const canCreate = hasRole('admin')

  const newSource = canCreate && (
    <button type="button" data-tour="agents.new" className={btnPrimary} onClick={() => setCreating(true)}>
      <Icon name="plus" size={16} />
      Nova fonte
    </button>
  )

  return (
    <>
      <PageHeader
        screen="agents"
        actions={newSource}
      />
      {sources.status === 'loading' && <Loading label="Carregando as fontes deste espaço…" />}
      {sources.status === 'error' && <ErrorState error={sources.error} onRetry={sources.reload} />}
      {sources.status === 'success' && sources.list.length === 0 && (
        <Empty title="Nenhuma fonte neste espaço." action={newSource || undefined}>
          {canCreate
            ? 'A primeira fonte leva menos de um minuto: nome, audiência, canal, finalidades e o teto de verificação. A chave sai na mesma hora, para o SDK.'
            : 'Quem tem o papel Administração cria as fontes deste espaço.'}
        </Empty>
      )}
      {sources.status === 'success' && sources.list.length > 0 && (
        <div className={`${card} overflow-x-auto`}>
          <table data-tour="agents.list" className="w-full min-w-[60rem] border-collapse">
            <caption className="sr-only">Fontes do espaço</caption>
            <thead>
              <tr>
                <th scope="col" className={th}>Fonte</th>
                <th scope="col" className={th}>Fornecedor</th>
                <th scope="col" className={th}>Canal</th>
                <th scope="col" className={th}>
                  <Term k="finalidade">Finalidades</Term>
                </th>
                <th scope="col" className={th}>
                  <Term k="audiencia">Audiência</Term>
                </th>
                <th scope="col" className={th}>
                  <Term k="tetoVerificacao">Teto de verificação</Term>
                </th>
                <th scope="col" className={th}>Estado</th>
              </tr>
            </thead>
            <tbody>
              {sources.list.map((s) => {
                const to = paths.agent(space.space_id, s.source_id)
                return (
                  <tr key={s.source_id} className="cursor-pointer transition-colors hover:bg-bg" onClick={() => navigate(to)}>
                    <td className={td}>
                      <Link to={to} onClick={(e) => e.stopPropagation()} className="inline-flex min-h-11 items-center font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4 lg:min-h-0">
                        {s.name}
                      </Link>
                      {s.critical && <span className="block text-[0.8125rem] text-ink2">crítica: fora de experimentos</span>}
                    </td>
                    <td className={td}>{s.vendor ?? 'próprio'}</td>
                    <td className={td}>{channel(s.channel)}</td>
                    <td className={td}>{purposes(s.purposes)}</td>
                    <td className={td}>{audience(s.audience)}</td>
                    <td className={`${td} font-mono`}>{verification(s.verification_ceiling)}</td>
                    <td className={td}>{s.revoked_at ? <Chip tone="danger">Acesso cortado em {fmtDate(s.revoked_at)}</Chip> : <Chip tone="ok">Ativa</Chip>}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {creating && (
        <NewSourceDialog
          onClose={() => setCreating(false)}
          onDone={(result) => {
            setCreating(false)
            sources.reload()
            if (result.issued) setCreated(result)
            else navigate(paths.agent(space.space_id, result.source.source_id))
          }}
        />
      )}
      {created?.issued && (
        <SecretDialog issued={created.issued} onClose={() => navigate(paths.agent(space.space_id, created.source.source_id))}>
          <p className="mt-4 text-[0.875rem] text-ink">
            A fonte <strong>{created.source.name}</strong> foi criada. No servidor do agente, a variável <span className="font-mono text-[0.8125rem]">NIADRA_API_KEY</span> recebe este segredo, e o
            SDK deriva o endereço do espaço a partir dele.
          </p>
        </SecretDialog>
      )}
    </>
  )
}

/** The audiences a source is created with, and the ceilings each one may declare. */
const AUDIENCES: { value: Audience; label: string; hint: string }[] = [
  { value: 'customer_agent', label: 'Agente de atendimento', hint: 'Fala com o cliente: WhatsApp, voz, chat, e-mail.' },
  { value: 'internal_agent', label: 'Agente interno', hint: 'Trabalha sem o cliente na conversa: cobrança, pedidos, tickets, CRM.' },
  { value: 'human', label: 'Atendente humano', hint: 'A mesa de atendimento de pessoas, pela ferramenta que a empresa já usa.' },
  { value: 'analyst', label: 'Análise', hint: 'Leituras agregadas para análise, nunca o contexto de um cliente.' },
]

const CEILINGS: Record<string, { value: Verification; label: string }[]> = {
  customer_agent: [
    { value: 'V2', label: 'V2: atestado pelo canal' },
    { value: 'V3', label: 'V3: verificado por código' },
    { value: 'V1', label: 'V1: plausível pelo canal' },
    { value: 'V0', label: 'V0: autodeclarado' },
  ],
  internal_agent: [
    { value: 'no_customer', label: 'Sem cliente: tarefa interna' },
    { value: 'V4', label: 'V4: documento conferido' },
    { value: 'V3', label: 'V3: verificado por código' },
    { value: 'V2', label: 'V2: atestado pelo canal' },
    { value: 'V1', label: 'V1: plausível pelo canal' },
    { value: 'V0', label: 'V0: autodeclarado' },
  ],
  human: [
    { value: 'V3', label: 'V3: verificado por código' },
    { value: 'V4', label: 'V4: documento conferido' },
    { value: 'V2', label: 'V2: atestado pelo canal' },
    { value: 'V1', label: 'V1: plausível pelo canal' },
  ],
  analyst: [{ value: 'V0', label: 'V0: sem contexto de cliente' }],
}

const CEILING_HINTS: Record<string, string> = {
  customer_agent: 'O nível mais alto que a fonte pode declarar, e uma chave vazada não passa dele. V2 é o padrão de agente automatizado; V3, de quem faz verificação por código (OTP).',
  internal_agent: 'Tarefa sem o cliente na conversa: o contexto sai sem os itens que exigem a presença do cliente. Um sistema de registro que confere documento pode declarar até V4.',
  human: 'V4, documento conferido, vale só para a mesa de atendimento humano e para agentes internos, como um sistema de registro.',
  analyst: 'Fontes de análise leem agregados, nunca o contexto de um cliente.',
}

const CHANNELS = ['whatsapp', 'voice', 'chat', 'email', 'sms', 'app', 'web', 'crm', 'erp', 'helpdesk', 'tickets', 'billing', 'orders'] as const
const NO_CHANNEL = 'none'

const NAME_MAX = 200

/** The control plane checks the ceiling against the audience again when the source is created (check_ceiling). */
const isCeilingRefusal = (e: unknown): e is ApiError => e instanceof ApiError && e.code === 'verification_not_allowed'

function NewSourceDialog({ onClose, onDone }: { onClose: () => void; onDone: (r: { source: SourceOut; issued: IssuedKeyOut | null }) => void }) {
  const { control, space } = useSpace()
  const [name, setName] = useState('')
  const [who, setWho] = useState<Audience>('customer_agent')
  const [ceiling, setCeiling] = useState<Verification>('V2')
  const [channelValue, setChannel] = useState<string>('whatsapp')
  const [vendor, setVendor] = useState('')
  const [purposes, setPurposes] = useState('')
  const [ops, setOps] = useState('')
  const [confirms, setConfirms] = useState(false)
  const [navigation, setNavigation] = useState(true)
  const [critical, setCritical] = useState(false)
  const [issueKey, setIssueKey] = useState(true)
  const [touched, setTouched] = useState(false)
  const act = useAction<{ source: SourceOut; issued: IssuedKeyOut | null }>()
  const ids = { confirms: useId(), navigation: useId(), critical: useId(), issue: useId(), nameErr: useId(), ceiling: useId() }
  // A refused ceiling is said on its field, for the audience and level that were sent; another choice clears it.
  const [refused, setRefused] = useState<{ who: Audience; ceiling: Verification; message: string } | null>(null)
  const ceilingProblem = refused && refused.who === who && refused.ceiling === ceiling ? refused.message : null
  useEffect(() => {
    if (refused) document.getElementById(ids.ceiling)?.focus()
  }, [refused, ids.ceiling])

  const purposeList = [...new Set(purposes.split(/[,\n]/).map((p) => p.trim()).filter(Boolean))]
  const nameError = !name.trim() ? 'Dê um nome à fonte, como o agente é conhecido no seu time.' : name.trim().length > NAME_MAX ? `No máximo ${NAME_MAX} caracteres.` : null
  const purposeError = purposeList.length > 32 ? 'No máximo 32 finalidades.' : purposeList.some((p) => p.length > NAME_MAX) ? 'Cada finalidade tem no máximo 200 caracteres.' : null
  // The operations an internal agent may record (guides/internal-agents): a closed list, and the ones whose
  // declared action closes an open item before the system confirms it (domain/objects/state.py closes_items).
  const trusted = who === 'internal_agent' ? [...new Set(ops.split(/[,\n]/).map((p) => p.trim()).filter(Boolean))] : []
  const opsError = trusted.length > 64 ? 'No máximo 64 operações.' : trusted.some((p) => p.length > NAME_MAX) ? 'Cada operação tem no máximo 200 caracteres.' : null
  const audienceHint = AUDIENCES.find((a) => a.value === who)?.hint

  const chooseAudience = (a: Audience) => {
    setWho(a)
    setCeiling(CEILINGS[a]![0]!.value)
    if (a === 'internal_agent' && ['whatsapp', 'voice', 'chat', 'sms'].includes(channelValue)) setChannel('erp')
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (nameError || purposeError || opsError) return
    const sent = { who, ceiling }
    act.run(async () => {
      const source = await control
        .createSource({
          space_id: space.space_id,
          name: name.trim(),
          audience: who,
          vendor: vendor.trim() || null,
          channel: channelValue === NO_CHANNEL ? null : channelValue,
          purposes: purposeList,
          verification_ceiling: ceiling,
          confirms_before_acting: confirms,
          navigation_enabled: navigation,
          trusted_action_ops: trusted,
          critical,
        })
        .catch((error: unknown) => {
          if (isCeilingRefusal(error)) {
            setRefused({ ...sent, message: explainCeilingRefusal(error.detail) ?? 'O plano de controle não aceita este teto para esta audiência. Escolha um nível abaixo.' })
          }
          throw error
        })
      // The source exists even if the key fails; the person can issue it on the source's page.
      const issued = issueKey ? await control.issueKey(source.source_id, null).catch(() => null) : null
      return { source, issued }
    }, onDone)
  }

  return (
    <Dialog open onClose={onClose} title="Nova fonte" wide>
      <form onSubmit={submit} className="grid gap-4" noValidate>
        <p className="text-ink">
          A fonte é como um agente, fornecedor ou sistema aparece para a memória: tudo o que ele grava leva o nome dela, e tudo o que ele lê passa pela política com a audiência e as finalidades
          dela. Estas regras ficam fixas depois de criadas.
        </p>
        <Field label="Nome" hint="Como o seu time chama este agente: Agente de voz, Cobrança, Help desk.">
          {(id) => (
            <>
              <input
                id={id}
                className={input}
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoComplete="off"
                maxLength={NAME_MAX + 20}
                aria-invalid={touched && Boolean(nameError)}
                aria-describedby={touched && nameError ? ids.nameErr : undefined}
                required
              />
              {touched && nameError && (
                <p id={ids.nameErr} className={`mt-1.5 text-[0.8125rem] font-semibold ${danger.text}`}>
                  {nameError}
                </p>
              )}
            </>
          )}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Select label="Audiência" value={who} onChange={chooseAudience} options={AUDIENCES.map(({ value, label }) => ({ value, label }))} hint={audienceHint} />
          <Select
            label="Canal"
            value={channelValue}
            onChange={setChannel}
            options={[...CHANNELS.map((c) => ({ value: c as string, label: channel(c) })), { value: NO_CHANNEL, label: 'Nenhum' }]}
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            id={ids.ceiling}
            label="Teto de verificação"
            value={ceiling}
            onChange={setCeiling}
            options={CEILINGS[who]!}
            hint={CEILING_HINTS[who]}
            problem={ceilingProblem}
          />
          <TextInput label="Fornecedor (opcional)" value={vendor} onChange={setVendor} hint="Deixe vazio quando o agente é do seu time." maxLength={NAME_MAX} />
        </div>
        <TextInput
          label="Finalidades"
          value={purposes}
          onChange={setPurposes}
          hint={purposeError ?? 'Separadas por vírgula, como estão na política: atendimento, cobranca. A política libera cada categoria por finalidade.'}
          aria-invalid={Boolean(purposeError)}
        />
        {who === 'internal_agent' && (
          <TextInput
            label="Operações confiáveis (opcional)"
            value={ops}
            onChange={setOps}
            hint={
              opsError ??
              'Separadas por vírgula, como o agente registra: credit, refund. Com a lista, a fonte só registra essas operações, e a ação declarada de uma delas fecha a pendência na hora, antes de o sistema confirmar. Vazia, a fonte registra qualquer operação, e a pendência espera o sistema confirmar.'
            }
            aria-invalid={Boolean(opsError)}
          />
        )}
        <fieldset className="grid gap-1">
          <legend className="mb-1 text-[0.8125rem] font-semibold text-ink">Comportamento</legend>
          <Check id={ids.navigation} checked={navigation} onChange={setNavigation} label="Consulta ao histórico ligada" hint="O agente busca no histórico do cliente, com o cliente amarrado fora do modelo." />
          <Check
            id={ids.confirms}
            checked={confirms}
            onChange={setConfirms}
            label="Confirma antes de agir"
            hint="Uma pergunta de confirmação do agente não conta como repetição na medição."
          />
          <Check id={ids.critical} checked={critical} onChange={setCritical} label="Fonte crítica" hint="Fica fora de grupos de controle e de experimentos." />
        </fieldset>
        <div className="rounded-lg border border-line bg-bg px-3 py-1">
          <Check
            id={ids.issue}
            checked={issueKey}
            onChange={setIssueKey}
            label="Emitir a primeira chave agora"
            hint="Com os escopos que a audiência permite. O segredo aparece uma vez, logo depois de criar."
          />
        </div>
        {Boolean(act.error) && !isCeilingRefusal(act.error) && <ErrorState error={act.error} compact />}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onClose}>
            Cancelar
          </button>
          <button type="submit" className={btnPrimary} disabled={act.busy}>
            {act.busy ? 'Criando…' : issueKey ? 'Criar e emitir a chave' : 'Criar fonte'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

/** A checkbox whose whole line is the target (44 px on touch), with what it changes said under it. */
function Check({ id, checked, onChange, label, hint: help }: { id: string; checked: boolean; onChange: (v: boolean) => void; label: string; hint: string }) {
  return (
    <div className="py-1">
      <label htmlFor={id} className="flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] font-semibold text-ink lg:min-h-8">
        <input id={id} type="checkbox" className="h-5 w-5 shrink-0 accent-[#5e7c00]" checked={checked} onChange={(e) => onChange(e.target.checked)} aria-describedby={`${id}-hint`} />
        {label}
      </label>
      <p id={`${id}-hint`} className={`${small} pl-[1.875rem]`}>
        {help}
      </p>
    </div>
  )
}
