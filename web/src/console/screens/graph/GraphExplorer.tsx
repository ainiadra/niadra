import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import type { GraphNode, GraphNodeKind, GraphOut } from '../../api/graph'
import { ApiError } from '../../api/http'
import { countByKind, fromResponse, looseProfiles, merge, objectsShown, search, visibleEdges, visibleIds, type Graph } from '../../lib/graph'
import { initialLayout, placeNew, EXPAND_STEPS, type Layout } from '../../lib/graphLayout'
import { nodeBox, weight } from '../../lib/graphView'
import { displayLabel, NODE_KINDS, NODE_KIND_MEANS, nodeKind, nodeKindPlural, objectsLine } from '../../lib/graphLabels'
import { useResource } from '../../lib/useResource'
import { Icon } from '../../ui/Icon'
import { Empty, ErrorState, Loading } from '../../ui/States'
import { btnSecondary, input, small } from '../../ui/styles'
import { GraphCanvas, type GraphCamera, type Simulation } from './GraphCanvas'
import { GraphList } from './GraphList'
import { canExpand, NodeCard } from './NodeCard'
import { KindSwatch } from './shapes'

/** On the map of the space, agents and systems start hidden: they touch everyone and would knot the drawing. */
const SOURCES: GraphNodeKind[] = ['agent', 'system']
const SHOWN_MATCHES = 8

export interface ExplorerProps {
  /** `customer`: around one profile, with the second level on demand. `overview`: the space's most connected items. */
  mode: 'customer' | 'overview'
  load: (depth: 1 | 2, signal: AbortSignal) => Promise<GraphOut>
  /** The graph around a profile the person clicked, to merge into the canvas. */
  expand: (ref: string, signal: AbortSignal) => Promise<GraphOut>
  space: string
  /** The empty state of this screen: what is missing and what creates it. */
  empty: ReactNode
  tour?: string
}

/**
 * Where the card sits, inside the drawing: a sheet over the lower part on a phone, floating at the right from
 * 640 px, never taller than the drawing (the card scrolls inside). The slot lets the pointer through where
 * the card is not. It slides in with the node it is about (a new node, a new slide), and stays put when
 * motion is reduced.
 */
function CardSlot({ children }: { children: ReactNode }) {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (cb: () => void) => window.setTimeout(cb, 16)
    const cancel = typeof cancelAnimationFrame === 'function' ? cancelAnimationFrame : window.clearTimeout
    const h = raf(() => setShown(true))
    return () => cancel(h)
  }, [])
  return (
    <div
      className={`pointer-events-none absolute inset-x-2 bottom-2 flex max-h-[56%] flex-col justify-end transition-[opacity,transform] duration-200 ease-out motion-reduce:transition-none sm:inset-x-auto sm:inset-y-3 sm:right-3 sm:max-h-none sm:w-[19rem] sm:justify-start ${
        shown ? 'translate-y-0 opacity-100 sm:translate-x-0' : 'translate-y-2 opacity-0 motion-reduce:translate-y-0 motion-reduce:opacity-100 sm:translate-x-2 sm:translate-y-0 motion-reduce:sm:translate-x-0'
      }`}
    >
      <div className="pointer-events-auto flex min-h-0 flex-col">{children}</div>
    </div>
  )
}

/** Below 640 px the list is the way in: a phone reads a table better than it pans a map. */
const narrow = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 639px)').matches
const reducedMotion = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

/**
 * The relationship graph with everything around it: the notes the server sent (cut, withheld), the search,
 * the legend that also filters, the canvas or the list, the card of the node under the pointer or the focus,
 * and the controls of the view. The same `Graph` feeds the canvas and the list.
 */
export function GraphExplorer({ mode, load, expand, space, empty, tour }: ExplorerProps) {
  const first = useResource((signal) => load(1, signal), [load])
  const [graph, setGraph] = useState<Graph | null>(null)
  const [depth, setDepth] = useState<1 | 2>(1)
  const [deepening, setDeepening] = useState<{ loading: boolean; error: unknown }>({ loading: false, error: null })
  const layout = useRef<Layout>({ nodes: new Map(), stepsLeft: 0 })
  /**
   * What the canvas's loop reads at every frame: the lines and options of the simulation, the canvas aspect,
   * and what it owes (a fit once the layout rests, the growth of the boxes once per drawing). A ref, so a
   * frame never waits for a render.
   */
  const sim = useRef<Simulation>({ edges: [], options: {}, aspect: 1, fitWhenSettled: true, grown: false })
  const camera = useRef<GraphCamera>(null)
  const reduced = useMemo(reducedMotion, [])
  const [showList, setShowList] = useState(narrow)
  const [hidden, setHidden] = useState<Set<GraphNodeKind>>(() => new Set(mode === 'overview' ? SOURCES : []))
  // The map of the space draws what has a tie among profiles; the customers with none come back on demand, or
  // with the agents and systems, which is what they are tied to.
  const [looseShown, setLooseShown] = useState(false)
  // The list, narrowed to the customers with no tie, from the note's link.
  const [listOnlyLoose, setListOnlyLoose] = useState(false)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [expanding, setExpanding] = useState<{ id: string | null; error: unknown; errorId: string | null }>({ id: null, error: null, errorId: null })
  const searchId = useId()

  /** A fresh drawing: the layout starts over and the canvas settles it, then fits it. */
  const startLayout = useCallback((g: Graph) => {
    layout.current = initialLayout([...g.nodes.keys()], g.center)
    sim.current.fitWhenSettled = true
    sim.current.grown = false
  }, [])
  /** The layout runs again, warm, from where it is: after an expansion or a filter. */
  const resettle = useCallback(() => {
    layout.current = { nodes: layout.current.nodes, stepsLeft: EXPAND_STEPS }
    sim.current.fitWhenSettled = true
  }, [])

  // A fresh answer (the first load, or a return to one level) replaces the canvas.
  useEffect(() => {
    if (!first.data) return
    const g = fromResponse(first.data)
    setGraph(g)
    setDepth(1)
    setActiveId(null)
    setExpanding({ id: null, error: null, errorId: null })
    startLayout(g)
  }, [first.data, startLayout])

  const byKind = useMemo(() => (graph ? visibleIds(graph, hidden) : new Set<string>()), [graph, hidden])
  const loose = useMemo(() => (graph && mode === 'overview' ? looseProfiles(graph, byKind) : new Set<string>()), [graph, byKind, mode])
  const sourcesShown = !SOURCES.every((k) => hidden.has(k))
  const looseDrawn = looseShown || sourcesShown
  const visible = useMemo(() => {
    if (looseDrawn || loose.size === 0) return byKind
    const ids = new Set(byKind)
    for (const id of loose) ids.delete(id)
    return ids
  }, [byKind, loose, looseDrawn])
  const edges = useMemo(() => (graph ? visibleEdges(graph, visible) : []), [graph, visible])
  /**
   * What the simulation sees: only the visible nodes and the lines between them (a hidden kind pulls nothing),
   * each node with the box of its mark and label, agents and systems of the map on a ring, customers with no
   * tie drawn gathered loosely to one side. Written into the ref the canvas reads, without restarting it.
   */
  useMemo(() => {
    if (!graph) return
    const degrees = new Map<string, number>()
    for (const e of edges) {
      degrees.set(e.from, (degrees.get(e.from) ?? 0) + 1)
      degrees.set(e.to, (degrees.get(e.to) ?? 0) + 1)
    }
    const boxes = new Map<string, { width: number; height: number }>()
    const ring = new Set<string>()
    const block = new Set<string>()
    const nodes = [...graph.nodes.values()].filter((n) => visible.has(n.id))
    // Companies and partners first in the loose block, so they make its first row.
    const order = (n: GraphNode) => (n.kind === 'account' || n.kind === 'partner' ? 0 : 1)
    for (const n of [...nodes].sort((a, b) => order(a) - order(b))) {
      boxes.set(n.id, nodeBox(n, weight(n, degrees.get(n.id) ?? 0, n.id === graph.center)))
      // Around one customer everything hangs from the customer; the ring and the loose block are the map's.
      if (mode !== 'overview') continue
      if (n.kind === 'agent' || n.kind === 'system') ring.add(n.id)
      else if (loose.has(n.id)) block.add(n.id)
    }
    sim.current.edges = edges.map((e) => ({ from: e.from, to: e.to, kind: e.kind }))
    sim.current.options = { only: visible, boxes, ring, loose: block }
  }, [graph, edges, visible, mode, loose])
  const counts = useMemo(() => (graph ? countByKind(graph) : null), [graph])
  const matches = useMemo(() => (graph ? search(graph, query, visible) : []), [graph, query, visible])
  const matchIds = useMemo(() => new Set(matches.map((n) => n.id)), [matches])

  // Hiding or showing a kind lets the rest settle again, warm, and refits; the boxes grow again for the zoom
  // the new drawing lands on (showing the sources widens it a lot).
  const filtered = useRef(visible)
  useEffect(() => {
    if (filtered.current === visible) return
    filtered.current = visible
    sim.current.grown = false
    resettle()
  }, [visible, resettle])

  const doExpand = async (id: string) => {
    if (!graph) return
    const node = graph.nodes.get(id)
    if (!node || !canExpand(node) || graph.expanded.has(id) || expanding.id) return
    setExpanding({ id, error: null, errorId: null })
    try {
      const r = await expand(node.ref!, new AbortController().signal)
      const next = merge(graph, r, id)
      setGraph(next.graph)
      // The new nodes grow out of the one that was expanded; once they settle, the whole graph is in view again.
      layout.current = placeNew(layout.current, next.added, id)
      if (next.added.length > 0) sim.current.fitWhenSettled = true
      setExpanding({ id: null, error: null, errorId: null })
    } catch (error) {
      setExpanding({ id: null, error, errorId: id })
    }
  }

  const deepen = async () => {
    if (!graph || !graph.center) return
    setDeepening({ loading: true, error: null })
    try {
      const r = await load(2, new AbortController().signal)
      const next = merge(graph, r, graph.center)
      setGraph(next.graph)
      layout.current = placeNew(layout.current, next.added, graph.center)
      if (next.added.length > 0) sim.current.fitWhenSettled = true
      setDepth(2)
      setDeepening({ loading: false, error: null })
    } catch (error) {
      setDeepening({ loading: false, error })
    }
  }

  const redraw = () => {
    if (!graph) return
    startLayout(graph)
  }

  /** A search hit: the canvas lights it and glides to it. */
  const goTo = (id: string) => {
    setActiveId(id)
    setShowList(false)
  }

  if (first.status === 'loading' || first.status === 'idle') return <Loading label="Montando o mapa de relações…" />
  if (first.status === 'error' || !graph) {
    const err = first.error
    if (err instanceof ApiError && err.status === 403) {
      return (
        <Empty title="O seu papel não vê as relações deste espaço.">
          O mapa segue as mesmas regras de leitura da memória de um cliente. Quem tem o papel Administração libera a leitura de perfis para o seu papel em Pessoas e papéis.
        </Empty>
      )
    }
    return <ErrorState error={err} onRetry={first.reload} />
  }

  const populated = graph.nodes.size > (graph.center ? 1 : 0)
  if (!populated) return <>{empty}</>

  const cardNode = graph.nodes.get(hoverId ?? activeId ?? '') ?? null
  const card = cardNode && (
    <NodeCard
      graph={graph}
      node={cardNode}
      space={space}
      visible={visible}
      expanding={expanding.id === cardNode.id}
      expandError={expanding.errorId === cardNode.id ? expanding.error : null}
      onExpand={doExpand}
      onClose={() => {
        setActiveId(null)
        setHoverId(null)
      }}
    />
  )
  const activeNode = activeId ? graph.nodes.get(activeId) : null
  // The customer of the screen has more objects than the 25 the map shows: the note says so.
  const centerNode = graph.center ? graph.nodes.get(graph.center) : null
  const centerObjects = centerNode ? objectsLine(centerNode, objectsShown(graph, centerNode.id)) : null
  const objectsCut = centerNode && centerObjects?.startsWith('mostrando') ? centerObjects : null

  return (
    <div data-tour={tour} className="grid gap-4">
      {(graph.truncated || graph.withheld > 0 || objectsCut) && (
        <p role="status" className={`${small} rounded-lg border border-line bg-surface px-3 py-2.5 text-ink`}>
          {graph.truncated && (
            <>
              {mode === 'customer' ? `Mostrando os ${graph.nodes.size} itens mais próximos deste cliente.` : `Mostrando os ${graph.nodes.size} itens mais ligados do espaço.`} Há mais relações que não
              couberam: clique num cliente para trazer as dele.{' '}
            </>
          )}
          {graph.withheld > 0 && <>{graph.withheld === 1 ? '1 item oculto pelo seu nível de acesso. ' : `${graph.withheld} itens ocultos pelo seu nível de acesso. `}</>}
          {objectsCut && <>Objetos do cliente: {objectsCut}.</>}
        </p>
      )}
      {mode === 'overview' && loose.size > 0 && !looseDrawn && (
        <p role="status" className={`${small} rounded-lg border border-line bg-surface px-3 py-2.5 text-ink`}>
          {loose.size === 1 ? '1 cliente sem vínculo com empresa ou parceiro não aparece aqui.' : `${loose.size} clientes sem vínculo com empresa ou parceiro não aparecem aqui.`}{' '}
          <button
            type="button"
            className="font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4 hover:decoration-ink"
            onClick={() => {
              setListOnlyLoose(true)
              setShowList(true)
            }}
          >
            Ver na lista
          </button>
        </p>
      )}

      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="relative min-w-0 lg:w-[22rem]">
          <label htmlFor={searchId} className="mb-1.5 block text-[0.8125rem] font-semibold text-ink">
            Procurar um item pelo nome
          </label>
          <input
            id={searchId}
            className={input}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="p_eyxl5q, Pedido PED-90039, sac-voz"
            autoComplete="off"
            spellCheck={false}
            aria-describedby={`${searchId}-n`}
          />
          <p id={`${searchId}-n`} className="sr-only">
            Os itens encontrados ganham um anel no mapa e aparecem numa lista abaixo do campo.
          </p>
          {query.trim() && (
            <div className="mt-2" aria-live="polite">
              {matches.length === 0 ? (
                <p className={small}>Nenhum item com esse nome no mapa. O mapa só tem o que já foi trazido; abra um cliente para trazer as relações dele.</p>
              ) : (
                <ul className="grid gap-1" aria-label="Itens encontrados">
                  {matches.slice(0, SHOWN_MATCHES).map((n) => (
                    <li key={n.id}>
                      <button type="button" onClick={() => goTo(n.id)} className="inline-flex min-h-11 items-center gap-2 rounded-lg px-2 text-left text-[0.8125rem] text-ink hover:bg-bg lg:min-h-9">
                        <span className="font-semibold">{displayLabel(n)}</span>
                        <span className="text-ink2">{nodeKind(n.kind)}</span>
                      </button>
                    </li>
                  ))}
                  {matches.length > SHOWN_MATCHES && <li className={small}>e mais {matches.length - SHOWN_MATCHES}. Escreva mais letras para afinar.</li>}
                </ul>
              )}
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Como ver as relações" className="inline-flex rounded-lg border border-edge bg-surface p-0.5">
            <button type="button" aria-pressed={!showList} onClick={() => setShowList(false)} className={`inline-flex h-10 items-center gap-1.5 rounded-md px-3 text-[0.8125rem] font-semibold lg:h-8 ${showList ? 'text-ink2 hover:text-ink' : 'bg-ink text-bg'}`}>
              <Icon name="graph" size={15} />
              Ver o grafo
            </button>
            <button type="button" aria-pressed={showList} onClick={() => setShowList(true)} className={`inline-flex h-10 items-center gap-1.5 rounded-md px-3 text-[0.8125rem] font-semibold lg:h-8 ${showList ? 'bg-ink text-bg' : 'text-ink2 hover:text-ink'}`}>
              <Icon name="list" size={15} />
              Ver como lista
            </button>
          </div>
          {mode === 'overview' && loose.size > 0 && !sourcesShown && (
            <button type="button" className={btnSecondary} aria-pressed={looseShown} onClick={() => setLooseShown((v) => !v)}>
              {looseShown ? 'Esconder clientes sem vínculo' : 'Mostrar clientes sem vínculo'}
            </button>
          )}
          {mode === 'overview' && (
            <button
              type="button"
              className={btnSecondary}
              aria-pressed={!SOURCES.every((k) => hidden.has(k))}
              onClick={() =>
                setHidden((cur) => {
                  const next = new Set(cur)
                  if (SOURCES.every((k) => next.has(k))) SOURCES.forEach((k) => next.delete(k))
                  else SOURCES.forEach((k) => next.add(k))
                  return next
                })
              }
            >
              {SOURCES.every((k) => hidden.has(k)) ? 'Mostrar agentes e sistemas' : 'Esconder agentes e sistemas'}
            </button>
          )}
          {mode === 'customer' &&
            (depth === 1 ? (
              <button type="button" className={btnSecondary} onClick={deepen} disabled={deepening.loading}>
                {deepening.loading ? 'Trazendo o segundo nível…' : 'Mostrar mais um nível'}
              </button>
            ) : (
              <button type="button" className={btnSecondary} onClick={first.reload}>
                Voltar a um nível
              </button>
            ))}
        </div>
      </div>
      {deepening.error !== null && deepening.error !== undefined && <ErrorState error={deepening.error} compact onRetry={deepen} />}

      <div role="group" aria-label="Legenda e filtro por tipo" className="flex flex-wrap gap-1.5">
        {NODE_KINDS.filter((k) => counts![k] > 0).map((k) => {
          const off = hidden.has(k)
          return (
            <button
              key={k}
              type="button"
              aria-pressed={!off}
              title={NODE_KIND_MEANS[k]}
              onClick={() =>
                setHidden((cur) => {
                  const next = new Set(cur)
                  if (next.has(k)) next.delete(k)
                  else next.add(k)
                  return next
                })
              }
              className={`inline-flex min-h-11 items-center gap-1.5 rounded-lg border px-2.5 text-[0.8125rem] font-semibold transition-colors lg:min-h-8 ${
                off ? 'border-line bg-bg text-ink2 line-through decoration-ink2' : 'border-edge bg-surface text-ink hover:bg-bg'
              }`}
            >
              <span className={off ? 'opacity-40' : ''}>
                <KindSwatch kind={k} />
              </span>
              <span>{counts![k] === 1 ? nodeKind(k) : nodeKindPlural(k)}</span>{' '}
              <span className="font-mono text-[0.75rem] text-ink2">{counts![k]}</span>
            </button>
          )
        })}
        <span className={`${small} self-center pl-1`}>Clique num tipo para esconder ou mostrar.</span>
      </div>
      <p className={`${small} -mt-2`}>
        Como ler: {mode === 'customer' ? 'o anel lima marca o cliente desta tela; a' : 'a'} linha cheia é uma relação registrada, e a palavra sobre ela diz qual. A linha tracejada é uma sugestão de que dois perfis
        são a mesma pessoa, ainda sem confirmação. Quanto mais ligado um item, maior ele aparece.{mode === 'overview' && ' Os agentes e sistemas começam escondidos: eles falam com quase todo mundo e cobririam o desenho; mostre-os para vê-los em volta.'}
      </p>

      {showList ? (
        <>
          {listOnlyLoose && loose.size > 0 && (
            <p className={small}>
              {loose.size === 1 ? 'Mostrando só o cliente sem vínculo com empresa ou parceiro.' : `Mostrando só os ${loose.size} clientes sem vínculo com empresa ou parceiro.`}{' '}
              <button type="button" className="font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4 hover:decoration-ink" onClick={() => setListOnlyLoose(false)}>
                Ver todos
              </button>
            </p>
          )}
          <GraphList graph={graph} visible={listOnlyLoose && loose.size > 0 ? loose : byKind} space={space} />
        </>
      ) : (
        <>
          {/* The one dark surface of the Console: the memory drawn as a living network (lib/graphTheme), with its controls docked under it on the same ground. */}
          <div className="flex h-[clamp(480px,calc(100dvh-24rem),760px)] flex-col overflow-hidden rounded-xl border border-line bg-[#10130a]">
            <div className="relative min-h-0 flex-1">
              <GraphCanvas
                ref={camera}
                graph={graph}
                layout={layout}
                sim={sim}
                visible={visible}
                edges={edges}
                activeId={activeId}
                onActive={(id) => {
                  setActiveId(id)
                  // A choice replaces a preview: the card shows what was chosen until the pointer moves on.
                  setHoverId(null)
                }}
                onHover={setHoverId}
                onExpand={doExpand}
                matches={matchIds}
                label={mode === 'customer' ? 'Mapa de relações do cliente' : 'Mapa de relações do espaço'}
                reduced={reduced}
              />
              {/* One card, inside the drawing: a sheet over the lower part on a phone, floating at the right from 640 px. */}
              {card && <CardSlot key={cardNode.id}>{card}</CardSlot>}
            </div>
            <div className="flex flex-wrap gap-1.5 border-t border-[#31362a] px-3 py-2.5">
              <button type="button" className={`${btnSecondary} px-3`} onClick={() => camera.current?.fit()}>
                <Icon name="fit" size={15} />
                Centralizar
              </button>
              <button type="button" className={`${btnSecondary} w-11 px-0 lg:w-9`} onClick={() => camera.current?.zoom(1.25)} aria-label="Aproximar">
                <Icon name="plus" size={16} />
              </button>
              <button type="button" className={`${btnSecondary} w-11 px-0 lg:w-9`} onClick={() => camera.current?.zoom(0.8)} aria-label="Afastar">
                <Icon name="minus" size={16} />
              </button>
              <button type="button" className={`${btnSecondary} px-3`} onClick={redraw}>
                Refazer o desenho
              </button>
            </div>
          </div>
        </>
      )}
      <p className="sr-only" aria-live="polite">
        {activeNode ? `${nodeKind(activeNode.kind)} ${displayLabel(activeNode)} selecionado.` : ''}
      </p>
    </div>
  )
}
