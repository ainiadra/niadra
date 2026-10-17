import { useId, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { describeError } from '../api/describe'
import { ApiError } from '../api/http'
import type { DataIssue } from '../api/objectTypes'
import type { CoverageOut, DeliveryView, DiffOut, DocumentOut, MappingDoc, ReceiptKind, SourceCoverage, SourceOut, WebhookEndpointDoc } from '../api/types'
import { dataURLFor } from '../config'
import {
  collectorTokenId,
  ENDPOINT_ID,
  ENDPOINT_ID_RULE,
  isSiemStream,
  newSigningSecret,
  SECRET_TAKEN,
  secretInUse,
  suggestedEndpointId,
  urlProblem,
  webhookSecretRef,
  writeWebhookSecret,
} from '../lib/endpoints'
import { fmtDateTime, fmtInt } from '../lib/format'
import { audience, channel, dataIssue, inferenceLimit, intakeKind, OUTBOUND_EVENTS, receiptKind, RECEIPT_KINDS, SIEM_FORMATS, siemFormat } from '../lib/labels'
import { Retrying, usePaged, useResource, type Resource } from '../lib/useResource'
import { paths } from '../paths'
import { useSpace } from '../session'
import { Chip, PageHeader, SourceName, useSources } from '../ui/bits'
import { Dialog } from '../ui/Dialog'
import { Field, Section, Select, useAction } from '../ui/forms'
import { Icon } from '../ui/Icon'
import { useTitle } from '../ui/layout'
import { DiffReason, diffReasonOk, DiffSent, freeId, useCanPropose } from '../ui/proposal'
import { Empty, ErrorState, LoadMore, Loading } from '../ui/States'
import { btnPrimary, btnQuiet, btnSecondary, card, danger, h2, hint, input, label, mono, small, td, th } from '../ui/styles'
import { Term } from '../ui/Term'

/**
 * How each system talks to the memory: the inbound webhook and its
 * versioned mapping, the outbound endpoints that receive warnings, how much of the traffic arrives with
 * context read, and the receipts that stream to the company's SIEM. Mappings and endpoints are
 * configuration: a new endpoint or SIEM stream is proposed here, the rest changes in Configuração.
 */
export function Systems() {
  const { space, control, cell, config, hasRole } = useSpace()
  useTitle('Sistemas', space.environment === 'sandbox')
  const sources = useSources()
  const mappings = useResource((signal) => control.configDocument(space.space_id, 'mappings', { signal }), [space.space_id, control])
  const endpoints = useResource((signal) => control.configDocument(space.space_id, 'webhook-endpoints', { signal }), [space.space_id, control])
  const coverage = useResource((signal) => cell.coverage(7, { signal }), [cell])
  const apiBase = dataURLFor(config, space)
  // A new endpoint or SIEM stream writes its signing secret into the cell vault (the Security role) and
  // proposes the endpoint (the roles that write webhook-endpoints): the Administration does both.
  const canPropose = useCanPropose('webhook-endpoints', ['integration'])
  const canCreate = canPropose && hasRole('security')
  const [creating, setCreating] = useState<SourceOut | null>(null)
  const [creatingStream, setCreatingStream] = useState(false)
  const known = Array.isArray(endpoints.data?.document) ? (endpoints.data!.document as WebhookEndpointDoc[]) : []
  // Every endpoint belongs to a source, and the control plane refuses one whose source was cut.
  const owners = sources.list.filter((s) => !s.revoked_at)

  return (
    <>
      <PageHeader
        screen="systems"
      />
      {canPropose && !canCreate && (
        <p className={`${small} mb-4`}>
          Criar um endpoint, ou um fluxo de comprovantes para o SIEM, grava a assinatura dele no cofre, o que pede também o papel Segurança e privacidade. Quem administra o Console faz as duas partes.
        </p>
      )}
      {sources.status === 'loading' && <Loading label="Carregando as fontes…" />}
      {sources.status === 'error' && <ErrorState error={sources.error} onRetry={sources.reload} />}
      {sources.status === 'success' && sources.list.length === 0 && <Empty title="Nenhuma fonte neste espaço." />}
      {sources.status === 'success' && sources.list.length > 0 && (
        <>
          <ul className="grid gap-4" data-tour="systems.inbound">
            {sources.list.map((s, i) => (
              <SourceSystem
                key={s.source_id}
                source={s}
                apiBase={apiBase}
                mappings={mappings}
                endpoints={endpoints}
                coverage={coverage}
                spaceId={space.space_id}
                onNewEndpoint={canCreate && !s.revoked_at ? () => setCreating(s) : undefined}
                first={i === 0}
              />
            ))}
          </ul>
          <div className="mt-6">
            <SiemStreams endpoints={endpoints} spaceId={space.space_id} onNew={canCreate && owners.length > 0 ? () => setCreatingStream(true) : undefined} />
          </div>
          {hasRole('integration') && (
            <>
              <KeptBatches />
              <DataNotes spaceId={space.space_id} />
            </>
          )}
        </>
      )}
      {creating && <NewEndpointDialog source={creating} known={known} onClose={() => setCreating(null)} onSent={endpoints.reload} />}
      {creatingStream && <NewSiemDialog owners={owners} known={known} onClose={() => setCreatingStream(false)} onSent={endpoints.reload} />}
    </>
  )
}

function SourceSystem({
  source: s,
  apiBase,
  mappings,
  endpoints,
  coverage,
  spaceId,
  onNewEndpoint,
  first = false,
}: {
  source: SourceOut
  apiBase: string | null
  mappings: Resource<DocumentOut>
  endpoints: Resource<DocumentOut>
  coverage: Resource<CoverageOut>
  spaceId: string
  onNewEndpoint?: () => void
  /** The first card carries the anchor the tutorial points at. */
  first?: boolean
}) {
  const mapping = (Array.isArray(mappings.data?.document) ? (mappings.data!.document as MappingDoc[]) : []).find((m) => m.source_id === s.source_id)
  // A SIEM stream never receives warnings: it is listed with the receipts, below the sources.
  const mine = (Array.isArray(endpoints.data?.document) ? (endpoints.data!.document as WebhookEndpointDoc[]) : []).filter((e) => e.source_id === s.source_id && !isSiemStream(e))
  const cov = coverage.data?.items.find((b) => b.source_id === s.source_id)
  const inbound = apiBase ? `${apiBase}/v1/ingest/webhook/${s.source_id}` : null
  const [copied, setCopied] = useState(false)

  return (
    <li className={`${card} px-4 py-4 sm:px-5`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className={h2}>
          <Link to={paths.agent(spaceId, s.source_id)} className="inline-flex min-h-11 items-center underline decoration-accent decoration-2 underline-offset-4 lg:min-h-0">
            {s.name}
          </Link>
        </h2>
        <p className="text-[0.8125rem] text-ink2">
          {[s.vendor, channel(s.channel), audience(s.audience)].filter(Boolean).join(' · ')}
          {s.revoked_at && (
            <span className="ml-2">
              <Chip tone="danger">Acesso cortado</Chip>
            </span>
          )}
        </p>
      </div>

      <div className="mt-4 grid gap-5 lg:grid-cols-3">
        <div className="min-w-0">
          <h3 className="text-[0.8125rem] font-semibold text-ink">
            Entrada por <Term k="webhook">webhook</Term>
          </h3>
          {inbound ? (
            <>
              <p className={`${mono} mt-1 break-all text-ink`}>{inbound}</p>
              <button
                type="button"
                className={`${btnQuiet} -ml-2 mt-1`}
                onClick={() =>
                  navigator.clipboard.writeText(inbound).then(
                    () => setCopied(true),
                    () => setCopied(false),
                  )
                }
              >
                <Icon name={copied ? 'check' : 'copy'} size={15} />
                {copied ? 'Copiado' : 'Copiar endereço'}
              </button>
            </>
          ) : (
            <p className={`${small} mt-1`}>Sem endereço de API para esta região no config.json.</p>
          )}
          <div className="mt-2">
            {mappings.status === 'loading' && <p className={small}>Carregando o mapeamento…</p>}
            {mappings.status === 'error' && <p className={small}>{mappings.error instanceof Retrying ? WAITING : 'O mapeamento não carregou.'}</p>}
            {mappings.status === 'success' &&
              (mapping ? (
                <p className={small}>
                  Mapeamento versão <span className={mono}>{mapping.version}</span> · {fmtInt(mapping.rules.length)} {mapping.rules.length === 1 ? 'regra' : 'regras'}
                  {mapping.auth ? ' · autenticação configurada' : ' · sem autenticação configurada'}
                </p>
              ) : (
                <p className={small}>Sem mapeamento: eventos por webhook desta fonte ficam guardados crus, sem virar memória.</p>
              ))}
          </div>
        </div>

        <div className="min-w-0">
          <h3 className="text-[0.8125rem] font-semibold text-ink">
            <Term k="endpoint">Endpoints</Term> que recebem <Term k="aviso">avisos</Term>
          </h3>
          {endpoints.status === 'loading' && <p className={`${small} mt-1`}>Carregando…</p>}
          {endpoints.status === 'error' && <p className={`${small} mt-1`}>{endpoints.error instanceof Retrying ? WAITING : 'Os endpoints não carregaram.'}</p>}
          {endpoints.status === 'success' && mine.length === 0 && <p className={`${small} mt-1`}>Nenhum endpoint desta fonte.</p>}
          {mine.length > 0 && (
            <ul className="mt-1 grid gap-2">
              {mine.map((e) => (
                <li key={e.endpoint_id}>
                  <p className={`${mono} break-all text-ink`}>{e.url}</p>
                  <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[0.8125rem] text-ink2">
                    {e.enabled ? <Chip tone="ok">Ligado</Chip> : <Chip>Desligado</Chip>}
                    <span className={mono}>{e.endpoint_id}</span> · {e.event_types.join(', ')} · {e.secret_refs.length === 2 ? 'assinatura em rotação' : 'uma assinatura'}
                  </p>
                  <TestSend endpoint={e} />
                </li>
              ))}
            </ul>
          )}
          {onNewEndpoint && endpoints.status === 'success' && (
            <button type="button" className={`${btnQuiet} -ml-2 mt-1`} onClick={onNewEndpoint} aria-label={`Novo endpoint para ${s.name}`}>
              <Icon name="plus" size={15} />
              Novo endpoint
            </button>
          )}
        </div>

        <div className="min-w-0" data-tour={first ? 'systems.coverage' : undefined}>
          <h3 className="text-[0.8125rem] font-semibold text-ink">
            <Term k="cobertura">Cobertura</Term> nos últimos 7 dias
          </h3>
          {coverage.status === 'loading' && <p className={`${small} mt-1`}>Carregando…</p>}
          {coverage.status === 'error' && <p className={`${small} mt-1`}>{coverage.error instanceof Retrying ? WAITING : 'A cobertura não carregou.'}</p>}
          {coverage.status === 'success' && <Coverage cov={cov} />}
        </div>
      </div>
    </li>
  )
}

/** A part of the card whose read the server asked to try later: the Console tries again by itself. */
const WAITING = 'A região pediu um instante; o Console tenta de novo sozinho.'

/** The deliveries of one endpoint in Avisos, where a failed or dead one is sent again by hand. */
const deliveriesOf = (spaceId: string, endpointId: string) => `${paths.alerts(spaceId)}?aba=deliveries&endpoint=${encodeURIComponent(endpointId)}`

/** One signed delivery marked as a test, sent once to an endpoint that is on, to check the receiver before real events flow. */
function TestSend({ endpoint }: { endpoint: WebhookEndpointDoc }) {
  const { cell, space, hasRole } = useSpace()
  const send = useAction<DeliveryView>()
  if (!endpoint.enabled || !hasRole('integration')) return null
  return (
    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
      <button
        type="button"
        className={`${btnQuiet} -ml-2`}
        disabled={send.busy}
        onClick={() => void send.run(() => cell.testEndpoint(endpoint.endpoint_id))}
        aria-label={`Enviar um teste para ${endpoint.endpoint_id}`}
      >
        {send.busy ? 'Enviando o teste…' : 'Enviar teste'}
      </button>
      <span role="status" className="text-[0.8125rem] text-ink">
        {send.result && (
          <>
            Teste enviado, uma tentativa.{' '}
            <Link to={deliveriesOf(space.space_id, endpoint.endpoint_id)} className="font-semibold underline underline-offset-2">
              Ver o resultado nas entregas
            </Link>
          </>
        )}
      </span>
      {send.error != null && (
        <span role="alert" className={`text-[0.8125rem] font-semibold ${danger.text}`}>
          O teste não saiu: {describeError(send.error).title}
        </span>
      )}
    </span>
  )
}

/** The signing secret, shown once, for the system that receives. */
function SigningSecretOnce({ secret, refError }: { secret: string; refError: unknown }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="grid gap-2 rounded-xl border border-line p-3">
      <p className="text-[0.875rem] font-semibold text-ink">Assinatura do endpoint</p>
      <p className={small}>Guarde agora no sistema que recebe: a Niadra não mostra esta assinatura de novo. Cada entrega vem assinada com ela, no padrão Standard Webhooks.</p>
      <p className={`${mono} break-all rounded-lg bg-surface px-3 py-2 text-ink`}>{secret}</p>
      <div>
        <button
          type="button"
          className={btnSecondary}
          onClick={() =>
            navigator.clipboard.writeText(secret).then(
              () => setCopied(true),
              () => setCopied(false),
            )
          }
        >
          <Icon name={copied ? 'check' : 'copy'} size={15} />
          {copied ? 'Copiada' : 'Copiar a assinatura'}
        </button>
      </div>
      {Boolean(refError) && <p className={small}>O plano de controle não registrou a versão da assinatura. Ela já está no cofre e assina as entregas.</p>}
    </div>
  )
}

/** The problem of an endpoint id against the document: the id itself, or a secret another endpoint names. */
function idProblemOf(id: string, endpoints: WebhookEndpointDoc[], refs: string[]): string | null {
  if (!ENDPOINT_ID.test(id)) return ENDPOINT_ID_RULE
  if (endpoints.some((e) => e.endpoint_id === id)) return 'Outro endpoint já usa este ID.'
  if (refs.some((ref) => secretInUse(ref, endpoints))) return SECRET_TAKEN
  return null
}

type Sent = { diff: DiffOut; secret: string; endpoint: WebhookEndpointDoc; refError: unknown }

/**
 * Adds a webhook endpoint to a source without JSON. The Console draws the signing
 * secret, writes it straight into the cell vault, registers its version on the control plane and proposes
 * the endpoint as a diff with a reason. The secret is shown once, for the receiving system; the endpoint
 * receives nothing until the diff is approved.
 */
function NewEndpointDialog({ source, known, onClose, onSent }: { source: SourceOut; known: WebhookEndpointDoc[]; onClose: () => void; onSent: () => void }) {
  const { cell, control, space } = useSpace()
  const [id, setId] = useState(() => freeId(suggestedEndpointId(source.name), known.map((e) => e.endpoint_id)))
  const [url, setUrl] = useState('')
  const [types, setTypes] = useState<string[]>(['trigger.fired'])
  const [reason, setReason] = useState('')
  // The document as the checks see it: the one on screen, then the one read right before sending.
  const [latest, setLatest] = useState(known)
  const [tried, setTried] = useState(false)
  const act = useAction<Sent | null>()
  const first = useRef<HTMLInputElement>(null)
  const idProblem = idProblemOf(id, latest, [webhookSecretRef(space.space_id, id)])
  const uProblem = url.trim() ? urlProblem(url) : tried ? 'Informe o endereço que recebe os avisos.' : null
  const typesProblem = types.length === 0 ? 'Escolha ao menos um tipo de evento.' : null
  const ok = !idProblem && url.trim() !== '' && !urlProblem(url) && !typesProblem && diffReasonOk(reason)
  const sent = act.result

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setTried(true)
    if (!ok) return
    act.run(async () => {
      // The document of now: an endpoint approved while the dialog was open is kept, and its id and secrets are taken.
      const now = await control.configDocument(space.space_id, 'webhook-endpoints')
      if (!Array.isArray(now.document)) throw new Error('the webhook-endpoints document is not a list')
      const current = now.document as WebhookEndpointDoc[]
      if (idProblemOf(id, current, [webhookSecretRef(space.space_id, id)])) {
        setLatest(current)
        return null
      }
      const secret = newSigningSecret()
      const signing = await writeWebhookSecret(cell, control, space.space_id, id, secret)
      const endpoint: WebhookEndpointDoc = { endpoint_id: id, url: url.trim(), source_id: source.source_id, event_types: types, secret_refs: [signing.ref], enabled: true, format: 'standard' }
      const diff = await control.submitDiff(space.space_id, 'webhook-endpoints', [...current, endpoint], reason.trim())
      return { diff, secret, endpoint, refError: signing.refError }
    }, onSent)
  }

  return (
    <Dialog open onClose={onClose} title={`Novo endpoint para ${source.name}`} wide initialFocus={first}>
      {sent ? (
        <div className="grid gap-4">
          <DiffSent diff={sent.diff} title={`Endpoint ${sent.endpoint.endpoint_id} enviado para aprovação.`}>
            <p className={small}>O endpoint não recebe nada antes da aprovação.</p>
          </DiffSent>
          <SigningSecretOnce secret={sent.secret} refError={sent.refError} />
          <div className="flex justify-end">
            <button type="button" className={btnPrimary} onClick={onClose}>
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <form className="grid gap-4" onSubmit={submit} noValidate>
          <p className={small}>
            O endpoint recebe os eventos que você escolher, assinados, e só o que a audiência desta fonte ({audience(source.audience)}) pode ler. A Niadra gera a assinatura e grava no cofre da
            região; você a vê uma vez, no fim.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="ID do endpoint">
              {(fid) => (
                <>
                  <input
                    id={fid}
                    ref={first}
                    className={`${idProblem ? input.replace('border-edge', danger.border) : input} font-mono`}
                    value={id}
                    onChange={(e) => setId(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    aria-invalid={idProblem ? true : undefined}
                    aria-describedby={`${fid}-note`}
                  />
                  <p id={`${fid}-note`} className={idProblem ? `mt-1.5 text-[0.8125rem] font-semibold ${danger.text}` : hint}>
                    {idProblem ?? 'As regras de aviso escolhem o endpoint por este ID.'}
                  </p>
                </>
              )}
            </Field>
            <Field label="Endereço (HTTPS)">
              {(fid) => (
                <>
                  <input
                    id={fid}
                    type="url"
                    inputMode="url"
                    className={`${uProblem ? input.replace('border-edge', danger.border) : input} font-mono`}
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="https://"
                    aria-invalid={uProblem ? true : undefined}
                    aria-describedby={`${fid}-note`}
                  />
                  <p id={`${fid}-note`} className={uProblem ? `mt-1.5 text-[0.8125rem] font-semibold ${danger.text}` : hint}>
                    {uProblem ?? 'Endereços privados, de loopback e de metadados da nuvem são recusados na entrega.'}
                  </p>
                </>
              )}
            </Field>
          </div>
          <fieldset className="min-w-0" aria-describedby="endpoint-types-note">
            <legend className={label}>Tipos de evento</legend>
            <p id="endpoint-types-note" className={typesProblem && tried ? `text-[0.8125rem] font-semibold ${danger.text}` : 'text-[0.8125rem] leading-5 text-ink2'}>
              {typesProblem && tried ? typesProblem : 'Os tipos que a Niadra envia hoje. Uma regra de aviso só chega a um endpoint que assina trigger.fired.'}
            </p>
            <div className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-2">
              {OUTBOUND_EVENTS.map((ev) => (
                <label key={ev.type} className="flex min-h-11 cursor-pointer items-start gap-2.5 py-1 text-[0.875rem] leading-5 text-ink">
                  <input
                    type="checkbox"
                    className="mt-px h-5 w-5 shrink-0 accent-[#5e7c00]"
                    checked={types.includes(ev.type)}
                    onChange={(e) => setTypes((cur) => (e.target.checked ? [...cur, ev.type] : cur.filter((x) => x !== ev.type)))}
                  />
                  <span className="min-w-0">
                    {ev.label} <span className={`${mono} block text-ink2`}>{ev.type}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <DiffReason value={reason} onChange={setReason} />
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Cancelar
            </button>
            <button type="submit" className={btnPrimary} disabled={act.busy || !diffReasonOk(reason)}>
              {act.busy ? 'Enviando…' : 'Gerar a assinatura e enviar para aprovação'}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}

/**
 * The receipts that stream to the company's SIEM: an endpoint with format `ocsf` or `hec` receives
 * every receipt of the space seconds after the chain holds it, signed like any webhook, in batches of up to
 * 200, retried for 24 hours and then left for a resend by hand (app/notify.py enqueue_receipts).
 */
function SiemStreams({ endpoints, spaceId, onNew }: { endpoints: Resource<DocumentOut>; spaceId: string; onNew?: () => void }) {
  const list = (Array.isArray(endpoints.data?.document) ? (endpoints.data!.document as WebhookEndpointDoc[]) : []).filter(isSiemStream)
  const create =
    onNew && endpoints.status === 'success' ? (
      <button type="button" className={btnPrimary} onClick={onNew}>
        <Icon name="plus" size={16} />
        Novo fluxo para o SIEM
      </button>
    ) : undefined
  return (
    <Section
      tour="systems.siem"
      title="Comprovantes no SIEM"
      description="Cada comprovante do espaço segue em segundos para o SIEM da empresa, como evento OCSF 1.3 de API Activity, com ids e hashes, nunca o que foi lido. As entregas saem em lotes de até 200, assinadas no padrão Standard Webhooks; a que falha é tentada de novo por 24 horas e depois espera o reenvio à mão."
      actions={list.length > 0 ? create : undefined}
    >
      {endpoints.status === 'loading' && <Loading label="Carregando os fluxos…" />}
      {endpoints.status === 'error' && <ErrorState error={endpoints.error} onRetry={endpoints.reload} compact />}
      {endpoints.status === 'success' && list.length === 0 && (
        <Empty title="Nenhum fluxo de comprovantes para o SIEM." action={create}>
          A exportação contínua leva a tabela de comprovantes ao bucket da empresa, na frequência do agendamento. Um fluxo leva cada comprovante ao SIEM em segundos, e entra como mudança de configuração, que vale depois de
          aprovada.
        </Empty>
      )}
      {list.length > 0 && (
        <ul className="divide-y divide-line2 rounded-xl border border-line">
          {list.map((e) => (
            <li key={e.endpoint_id} className="grid gap-1 px-4 py-3">
              <p className={`${mono} break-all text-ink`}>{e.url}</p>
              <p className="flex flex-wrap items-center gap-1.5 text-[0.8125rem] text-ink2">
                {e.enabled ? <Chip tone="ok">Ligado</Chip> : <Chip>Desligado</Chip>}
                <span className="font-semibold text-ink">{siemFormat(e.format)}</span>
                <span className={mono}>{e.endpoint_id}</span>
              </p>
              <p className={small}>
                {e.event_types.includes('*') ? 'todos os tipos de comprovante' : e.event_types.map((k) => receiptKind(k as ReceiptKind)).join(', ')} ·{' '}
                {e.authorization_ref ? 'token do coletor no cabeçalho Authorization' : 'sem token do coletor'} · {e.secret_refs.length === 2 ? 'assinatura em rotação' : 'assinatura Standard Webhooks'} · fonte{' '}
                <SourceName id={e.source_id} />
              </p>
              <div>
                <Link to={deliveriesOf(spaceId, e.endpoint_id)} className={`${btnQuiet} -ml-2`} aria-label={`Entregas e reenvio de ${e.endpoint_id}`}>
                  Entregas e reenvio
                </Link>
                <TestSend endpoint={e} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

type Scheme = 'splunk' | 'bearer' | 'raw'

/**
 * How the collector token goes in the Authorization header. The vault keeps the whole header value, which
 * app/notify.py sends as it is: an HTTP Event Collector reads `Splunk <token>`.
 */
const SCHEMES: { value: Scheme; label: string; prefix: string }[] = [
  { value: 'splunk', label: 'Splunk e o token (HTTP Event Collector)', prefix: 'Splunk ' },
  { value: 'bearer', label: 'Bearer e o token', prefix: 'Bearer ' },
  { value: 'raw', label: 'O valor inteiro, como digitado', prefix: '' },
]
const SCHEME_OF: Record<'ocsf' | 'hec', Scheme> = { ocsf: 'bearer', hec: 'splunk' }

/** What stops a collector token from going out: a line break would break the header, and a doubled scheme would too. */
function tokenProblem(token: string, scheme: Scheme): string | null {
  const t = token.trim()
  if (!t) return null
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(t)) return 'O token não pode ter quebra de linha nem caractere de controle.'
  if (t.length > 8000) return 'No máximo 8.000 caracteres.'
  const word = SCHEMES.find((s) => s.value === scheme)!.prefix.trim()
  if (word && t.toLowerCase().startsWith(`${word.toLowerCase()} `)) return `O token já começa com ${word}: tire o prefixo, ou escolha O valor inteiro.`
  return null
}

type StreamSent = Sent & { withToken: boolean }

/**
 * Adds a SIEM stream without JSON, with what the endpoint form does: the
 * Console draws the Standard Webhooks secret, writes it straight into the cell vault, registers its
 * version, and proposes the stream as a diff of the document read again right before sending. An optional
 * collector token goes to the vault beside it and the stream names it as `authorization_ref`.
 */
function NewSiemDialog({ owners, known, onClose, onSent }: { owners: SourceOut[]; known: WebhookEndpointDoc[]; onClose: () => void; onSent: () => void }) {
  const { cell, control, space } = useSpace()
  const base = useId()
  const [id, setId] = useState(() => freeId('siem', known.map((e) => e.endpoint_id)))
  const [format, setFormat] = useState<'ocsf' | 'hec'>('ocsf')
  const [url, setUrl] = useState('')
  const [all, setAll] = useState(true)
  const [kinds, setKinds] = useState<ReceiptKind[]>([])
  const [owner, setOwner] = useState(owners[0]?.source_id ?? '')
  // Null follows the format: an HTTP Event Collector reads `Splunk <token>`, most others a bearer token.
  const [scheme, setScheme] = useState<Scheme | null>(null)
  const [token, setToken] = useState('')
  const [reason, setReason] = useState('')
  const [latest, setLatest] = useState(known)
  const [tried, setTried] = useState(false)
  const act = useAction<StreamSent | null>()
  const first = useRef<HTMLInputElement>(null)
  const how = scheme ?? SCHEME_OF[format]
  const header = token.trim() ? `${SCHEMES.find((s) => s.value === how)!.prefix}${token.trim()}` : ''
  // The signing secret, and the collector token when there is one, take names from the id in the vault.
  const refsOf = (value: string) => [webhookSecretRef(space.space_id, id), ...(value ? [webhookSecretRef(space.space_id, collectorTokenId(id))] : [])]
  const idProblem = idProblemOf(id, latest, refsOf(header))
  const uProblem = url.trim() ? urlProblem(url) : tried ? 'Informe o endereço do SIEM ou do coletor.' : null
  const kindsProblem = !all && kinds.length === 0 ? 'Escolha ao menos um tipo de comprovante.' : null
  const tProblem = tokenProblem(token, how)
  const ok = !idProblem && url.trim() !== '' && !urlProblem(url) && !kindsProblem && !tProblem && Boolean(owner) && diffReasonOk(reason)
  const sent = act.result
  const radio = 'mt-px h-5 w-5 shrink-0 accent-[#5e7c00]'
  const choice = 'flex min-h-11 cursor-pointer items-start gap-2.5 py-1 text-[0.875rem] leading-5 text-ink'

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setTried(true)
    if (!ok) return
    const value = header
    act.run(async () => {
      // The document of now: an endpoint approved while the dialog was open is kept, and its id and secrets are taken.
      const now = await control.configDocument(space.space_id, 'webhook-endpoints')
      if (!Array.isArray(now.document)) throw new Error('the webhook-endpoints document is not a list')
      const current = now.document as WebhookEndpointDoc[]
      if (idProblemOf(id, current, refsOf(value))) {
        setLatest(current)
        return null
      }
      const secret = newSigningSecret()
      const signing = await writeWebhookSecret(cell, control, space.space_id, id, secret)
      const auth = value ? await writeWebhookSecret(cell, control, space.space_id, collectorTokenId(id), value, 'collector_token') : null
      const endpoint: WebhookEndpointDoc = {
        endpoint_id: id,
        url: url.trim(),
        source_id: owner,
        event_types: all ? ['*'] : RECEIPT_KINDS.filter((k) => kinds.includes(k)),
        secret_refs: [signing.ref],
        enabled: true,
        format,
        ...(auth ? { authorization_ref: auth.ref } : {}),
      }
      const diff = await control.submitDiff(space.space_id, 'webhook-endpoints', [...current, endpoint], reason.trim())
      return { diff, secret, endpoint, refError: signing.refError ?? auth?.refError ?? null, withToken: Boolean(auth) }
    }, onSent)
    // The token stays in the form only until it is sent, as every credential the Console writes.
    setToken('')
  }

  return (
    <Dialog open onClose={onClose} title="Novo fluxo de comprovantes para o SIEM" wide initialFocus={first}>
      {sent ? (
        <div className="grid gap-4">
          <DiffSent diff={sent.diff} title={`Fluxo ${sent.endpoint.endpoint_id} enviado para aprovação.`}>
            <p className={small}>O fluxo não envia nada antes da aprovação. A primeira entrega sai com o primeiro comprovante depois dela e aparece nas entregas do fluxo, com a resposta do SIEM.</p>
          </DiffSent>
          <SigningSecretOnce secret={sent.secret} refError={sent.refError} />
          {sent.withToken && <p className={small}>O token do coletor foi gravado no cofre da região e segue no cabeçalho Authorization de cada entrega. Ele não aparece de novo em lugar nenhum.</p>}
          <div className="flex flex-wrap justify-end gap-2">
            <Link to={deliveriesOf(space.space_id, sent.endpoint.endpoint_id)} className={btnSecondary}>
              Ver as entregas do fluxo
            </Link>
            <button type="button" className={btnPrimary} onClick={onClose}>
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <form className="grid gap-4" onSubmit={submit} noValidate>
          <p className={small}>
            Cada comprovante do espaço segue em segundos para este endereço, como evento OCSF 1.3 de API Activity (classe 6003), com ids e hashes, nunca o que foi lido. A Niadra gera a assinatura, no padrão
            Standard Webhooks, e grava no cofre da região; você a vê uma vez, no fim.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="ID do endpoint">
              {(fid) => (
                <>
                  <input
                    id={fid}
                    ref={first}
                    className={`${idProblem ? input.replace('border-edge', danger.border) : input} font-mono`}
                    value={id}
                    onChange={(e) => setId(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    aria-invalid={idProblem ? true : undefined}
                    aria-describedby={`${fid}-note`}
                  />
                  <p id={`${fid}-note`} className={idProblem ? `mt-1.5 text-[0.8125rem] font-semibold ${danger.text}` : hint}>
                    {idProblem ?? 'Cada entrega leva este ID, e as entregas em Avisos se filtram por ele.'}
                  </p>
                </>
              )}
            </Field>
            <Field label="Endereço (HTTPS)">
              {(fid) => (
                <>
                  <input
                    id={fid}
                    type="url"
                    inputMode="url"
                    className={`${uProblem ? input.replace('border-edge', danger.border) : input} font-mono`}
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="https://"
                    aria-invalid={uProblem ? true : undefined}
                    aria-describedby={`${fid}-note`}
                  />
                  <p id={`${fid}-note`} className={uProblem ? `mt-1.5 text-[0.8125rem] font-semibold ${danger.text}` : hint}>
                    {uProblem ?? 'Endereços privados, de loopback e de metadados da nuvem são recusados na entrega.'}
                  </p>
                </>
              )}
            </Field>
          </div>
          <fieldset className="min-w-0">
            <legend className={label}>Formato</legend>
            <div className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
              {SIEM_FORMATS.map((f) => (
                <label key={f.value} className={choice}>
                  <input
                    type="radio"
                    name={`${base}-format`}
                    className={radio}
                    checked={format === f.value}
                    onChange={() => setFormat(f.value)}
                    aria-labelledby={`${base}-${f.value}`}
                    aria-describedby={`${base}-${f.value}-means`}
                  />
                  <span className="min-w-0">
                    <span id={`${base}-${f.value}`} className="font-semibold">
                      {f.title}
                    </span>
                    <span id={`${base}-${f.value}-means`} className="block text-[0.8125rem] text-ink2">
                      {f.means}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset className="min-w-0" aria-describedby={`${base}-kinds-note`}>
            <legend className={label}>Tipos de comprovante</legend>
            <p id={`${base}-kinds-note`} className={kindsProblem && tried ? `text-[0.8125rem] font-semibold ${danger.text}` : 'text-[0.8125rem] leading-5 text-ink2'}>
              {kindsProblem && tried ? kindsProblem : 'Um fluxo recebe só comprovantes; os avisos e os eventos vão para os endpoints de cada fonte.'}
            </p>
            <div className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-2">
              <label className={choice}>
                <input type="radio" name={`${base}-kinds`} className={radio} checked={all} onChange={() => setAll(true)} />
                <span className="min-w-0">
                  Todos os tipos de comprovante <span className="block text-[0.8125rem] text-ink2">Inclusive um tipo que a Niadra criar depois.</span>
                </span>
              </label>
              <label className={choice}>
                <input type="radio" name={`${base}-kinds`} className={radio} checked={!all} onChange={() => setAll(false)} />
                <span className="min-w-0">Só os tipos escolhidos</span>
              </label>
            </div>
            {!all && (
              <div className="mt-2 grid gap-x-4 gap-y-1 border-t border-line2 pt-2 sm:grid-cols-2">
                {RECEIPT_KINDS.map((k) => (
                  <label key={k} className={choice}>
                    <input
                      type="checkbox"
                      className={radio}
                      checked={kinds.includes(k)}
                      onChange={(e) => setKinds((cur) => (e.target.checked ? [...cur, k] : cur.filter((x) => x !== k)))}
                    />
                    <span className="min-w-0">
                      {receiptKind(k)} <span className={`${mono} block text-ink2`}>{k}</span>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </fieldset>
          <Select
            label="Fonte dona do endpoint"
            value={owner}
            onChange={setOwner}
            options={owners.map((s) => ({ value: s.source_id, label: `${s.name}${s.vendor ? ` (${s.vendor})` : ''}` }))}
            hint="O fluxo leva os comprovantes do espaço inteiro, de todas as fontes. A fonte só registra de quem é o endpoint, como em todo endpoint."
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Token do coletor (opcional)">
              {(fid) => (
                <>
                  <input
                    id={fid}
                    type="password"
                    className={`${tProblem ? input.replace('border-edge', danger.border) : input} font-mono`}
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    aria-invalid={tProblem ? true : undefined}
                    aria-describedby={`${fid}-note`}
                  />
                  <p id={`${fid}-note`} className={tProblem ? `mt-1.5 text-[0.8125rem] font-semibold ${danger.text}` : hint}>
                    {tProblem ?? 'Vai para o cofre da região e segue no cabeçalho Authorization de cada entrega. Não aparece de novo.'}
                  </p>
                </>
              )}
            </Field>
            <Select label="Como o token vai no cabeçalho" value={how} onChange={setScheme} options={SCHEMES.map(({ value, label: text }) => ({ value, label: text }))} />
          </div>
          <DiffReason value={reason} onChange={setReason} />
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Cancelar
            </button>
            <button type="submit" className={btnPrimary} disabled={act.busy || !diffReasonOk(reason)}>
              {act.busy ? 'Enviando…' : 'Gerar a assinatura e enviar para aprovação'}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}

/** What the source sent and what arrived: a source that went quiet is what coverage exists to show. */
function Coverage({ cov }: { cov: SourceCoverage | undefined }) {
  if (!cov) return <p className={`${small} mt-1`}>Sem dados de cobertura para esta fonte.</p>
  const lost = cov.sent > cov.received ? cov.sent - cov.received : 0
  return (
    <div className="mt-1">
      {cov.received === 0 ? (
        <p className="flex flex-wrap items-center gap-2 text-ink">
          <Chip tone="danger">Silenciosa</Chip> nenhum evento recebido
        </p>
      ) : (
        <p className="text-ink">
          <strong>{fmtInt(cov.received)}</strong> eventos recebidos
          {cov.sent > 0 && <> de {fmtInt(cov.sent)} enviados</>}
        </p>
      )}
      <p className={small}>
        {[lost > 0 && `${fmtInt(lost)} não chegaram`, cov.rejected > 0 && `${fmtInt(cov.rejected)} recusados`, cov.unmapped > 0 && `${fmtInt(cov.unmapped)} sem mapeamento`, cov.sent === 0 && 'sem contagem de envio do SDK']
          .filter(Boolean)
          .join(' · ') || 'nada recusado'}
      </p>
      <p className={`${mono} text-ink2`}>último evento {cov.last_event_at ? fmtDateTime(cov.last_event_at) : 'nenhum'}</p>
    </div>
  )
}

/** Shown in Tipos, next to the type they speak of. */
const TYPE_NOTES = new Set(['drift', 'type_undeclared', 'field_held'])

const isOff = (error: unknown) => error instanceof ApiError && error.status === 404

/**
 * Batches of the company's data that failed every attempt to be taken in (`row_failed`): kept aside,
 * sealed, never deleted (the server's `domain/ingest/intake.py`). Kind, source and times only; a retry puts
 * them back for a worker to store, and one that fails again comes back to this list.
 */
function KeptBatches() {
  const { cell } = useSpace()
  const kept = useResource((signal) => cell.objectTypes.failedIntake({ signal }), [cell])
  const retry = useAction<{ retried: number }>()
  const [done, setDone] = useState<number | null>(null)
  const run = (ids: string[] | null) =>
    retry.run(
      () => cell.objectTypes.retryIntake(ids),
      (r) => {
        setDone(r.retried)
        kept.reload()
      },
    )
  return (
    <section className="mt-8" aria-labelledby="kept-batches">
      <h2 id="kept-batches" className={h2}>
        Lotes guardados para nova tentativa
      </h2>
      <p className={`${hint} mt-1`}>
        Um lote que não pôde ser guardado depois de várias tentativas fica aqui, inteiro e lacrado: nada se perde. Tentar de novo devolve o lote à fila; se falhar outra vez, ele volta para esta lista.
      </p>
      <div className="mt-3">
        {(kept.status === 'loading' || kept.status === 'idle') && <Loading label="Carregando os lotes guardados…" />}
        {kept.status === 'error' && !isOff(kept.error) && <ErrorState error={kept.error} onRetry={kept.reload} compact />}
        {kept.status === 'error' && isOff(kept.error) && <Empty title="Nenhum lote guardado." />}
        {kept.status === 'success' && kept.data && kept.data.items.length === 0 && (
          <Empty title="Nenhum lote guardado: tudo o que chegou foi guardado na memória." />
        )}
        {kept.status === 'success' && kept.data && kept.data.items.length > 0 && (
          <>
            <div className={`${card} overflow-x-auto`}>
              <table className="w-full min-w-[40rem] border-collapse">
                <caption className="sr-only">Lotes guardados para nova tentativa</caption>
                <thead>
                  <tr>
                    {['Lote', 'Fonte', 'Recebido em', 'Guardado em', 'Tentativas', ''].map((h, i) => (
                      <th key={h || i} scope="col" className={th}>
                        {h ? h : <span className="sr-only">Ação</span>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {kept.data.items.map((b) => (
                    <tr key={b.intake_id}>
                      <td className={td}>{intakeKind(b.kind)}</td>
                      <td className={td}>
                        <SourceName id={b.source_id} />
                      </td>
                      <td className={`${td} whitespace-nowrap`}>{fmtDateTime(b.received_at)}</td>
                      <td className={`${td} whitespace-nowrap`}>{fmtDateTime(b.failed_at)}</td>
                      <td className={`${td} tabular-nums`}>{fmtInt(b.attempts)}</td>
                      <td className={td}>
                        <button type="button" className={btnQuiet} disabled={retry.busy} onClick={() => run([b.intake_id])}>
                          Tentar de novo
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button type="button" className={btnSecondary} disabled={retry.busy} onClick={() => run(null)}>
                {retry.busy ? 'Devolvendo à fila…' : 'Tentar todos de novo'}
              </button>
              {kept.data.kept > kept.data.items.length && (
                <span className={small}>
                  Mostrando {fmtInt(kept.data.items.length)} de {fmtInt(kept.data.kept)} lotes guardados; Tentar todos de novo inclui todos.
                </span>
              )}
              {done !== null && (
                <span className={small} role="status">
                  {done === 1 ? '1 lote voltou à fila.' : `${fmtInt(done)} lotes voltaram à fila.`}
                </span>
              )}
            </div>
          </>
        )}
        {retry.error !== null && <ErrorState error={retry.error} compact />}
      </div>
    </section>
  )
}

/**
 * The open data issues not about a type (those live in Tipos): values, sources, rules, the day's AI ceiling,
 * kept batches and inference limits, each with what it means. Notes, never tasks: archiving one only says it was seen.
 */
function DataNotes({ spaceId }: { spaceId: string }) {
  const { cell } = useSpace()
  const notes = usePaged((cursor, signal) => cell.objectTypes.openIssues(cursor, { signal }), [cell, spaceId])
  const shown = notes.items.filter((i) => !TYPE_NOTES.has(i.kind))
  return (
    <section className="mt-8" aria-labelledby="data-notes">
      <h2 id="data-notes" className={h2}>
        Avisos dos dados
      </h2>
      <p className={`${hint} mt-1`}>
        O que a Niadra notou nos dados que chegam: valores, fontes, regras, o teto de IA do dia e os limites do que ela infere sozinha. São avisos, não tarefas. Campos e tipos novos aparecem em <Link to={paths.objectTypes(spaceId)}>Tipos</Link>.
      </p>
      <div className="mt-3">
        {(notes.status === 'loading' || notes.status === 'idle') && <Loading label="Carregando os avisos…" />}
        {notes.status === 'error' && !isOff(notes.error) && <ErrorState error={notes.error} onRetry={notes.reload} compact />}
        {notes.status === 'error' && isOff(notes.error) && <Empty title="Os avisos de dado estão desligados neste espaço." />}
        {notes.status === 'success' && shown.length === 0 && !notes.hasMore && <Empty title="Nenhum aviso sobre os dados." />}
        {shown.length > 0 && (
          <ul className="grid gap-3" aria-label="Avisos dos dados">
            {shown.map((i) => (
              <DataNote key={i.issue_id} issue={i} onDone={notes.reload} />
            ))}
          </ul>
        )}
        <LoadMore hasMore={notes.hasMore} loading={notes.loadingMore} error={notes.moreError} onClick={notes.loadMore} />
      </div>
    </section>
  )
}

function DataNote({ issue: i, onDone }: { issue: DataIssue; onDone: () => void }) {
  const { cell } = useSpace()
  const ack = useAction<unknown>()
  const about = dataIssue(i.kind)
  // An inference limit names the limit in `field`: it is said in words, and only the type stays as a name.
  const limit = i.kind === 'inference_limit' && i.field ? inferenceLimit(i.field) : null
  const where = [i.object_type, limit ? null : i.field].filter(Boolean).join(' · ')
  return (
    <li className={`${card} px-4 py-3`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="min-w-0 font-semibold text-ink">{about.title}</p>
        <Chip>{i.occurrences === 1 ? 'Visto 1 vez' : `Visto ${fmtInt(i.occurrences)} vezes`}</Chip>
      </div>
      <p className={`${small} mt-1`}>{about.means}</p>
      {limit && <p className={`${small} mt-1 text-ink`}>{limit}.</p>}
      <p className={`${small} mt-1`}>
        {where && (
          <>
            <span className={mono}>{where}</span>
            {' · '}
          </>
        )}
        {i.source ? <>de {i.source} · </> : null}
        desde {fmtDateTime(i.opened_at)}; a última vez em {fmtDateTime(i.last_seen_at)}.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" className={btnSecondary} disabled={ack.busy} onClick={() => ack.run(() => cell.objectTypes.acknowledge(i.issue_id), onDone)}>
          {ack.busy ? 'Arquivando…' : 'Arquivar o aviso'}
        </button>
        <span className={small}>Uma ocorrência nova abre outro aviso.</span>
      </div>
      {ack.error !== null && <ErrorState error={ack.error} compact />}
    </li>
  )
}
