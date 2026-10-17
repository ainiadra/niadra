/**
 * Reads the OpenAPI 3.1 files for the checks of the reference: the method and path pairs, broken
 * references, the description keys the translations cover, and examples that break their schema.
 */
/* eslint-disable @typescript-eslint/no-explicit-any */
export type Json = any
export type Spec = Json

function resolveRef(spec: Spec, ref: string): Json {
  if (!ref.startsWith('#/')) throw new Error(`external reference not supported: ${ref}`)
  let cur: Json = spec
  for (const part of ref.slice(2).split('/')) {
    cur = cur?.[part.replace(/~1/g, '/').replace(/~0/g, '~')]
    if (cur === undefined) throw new Error(`broken reference: ${ref}`)
  }
  return cur
}

const refName = (ref: string) => ref.split('/').pop() ?? ref

/** Local schemas (`$defs`) that a body carries inside it, resolved by `#/$defs/Name`. */
type Defs = Record<string, Json>
const withDefs = (defs: Defs, s: Json): Defs => (s && typeof s === 'object' && s.$defs ? { ...defs, ...s.$defs } : defs)

function resolveAny(spec: Spec, ref: string, defs: Defs): Json {
  if (ref.startsWith('#/$defs/')) {
    const hit = defs[ref.slice(8)]
    if (hit === undefined) throw new Error(`broken reference: ${ref}`)
    return hit
  }
  return resolveRef(spec, ref)
}

/** Unwraps $ref and single-item allOf (the way to put a description on a $ref). */
function deref(spec: Spec, s: Json, defs: Defs = {}): { schema: Json; name?: string; description?: string } {
  let description: string | undefined = s?.description
  let name: string | undefined
  let cur = s
  for (let i = 0; i < 10 && cur; i++) {
    if (cur.$ref) {
      name = refName(cur.$ref)
      cur = resolveAny(spec, cur.$ref, defs)
      description ??= cur.description
      continue
    }
    if (Array.isArray(cur.allOf) && cur.allOf.length === 1 && !cur.properties) {
      description ??= cur.description
      cur = cur.allOf[0]
      continue
    }
    break
  }
  return { schema: cur ?? {}, name, description: description ?? cur?.description }
}

/** What the documentation adds to the contract: handwritten examples, and the address when the file has none. */
export type OpExtra = { request?: Json; responses?: Record<string, Json>; params?: Record<string, Json> }

/** FastAPI's default descriptions become text in the page language through the `res:*:<description>` key. */
export const GENERIC_RESPONSES = new Set(['Successful Response', 'Validation Error', 'The request does not match the contract.'])

/** Every method + path pair of the file, to check against the documentation map. */
export function operationKeys(spec: Spec): string[] {
  const out: string[] = []
  for (const [path, item] of Object.entries<Json>(spec.paths ?? {})) {
    for (const m of ['get', 'post', 'put', 'patch', 'delete']) if (item?.[m]) out.push(`${m} ${path}`)
  }
  return out
}

/** Every $ref in the file points to something that exists. Returns the broken ones. */
export function brokenRefs(spec: Spec): string[] {
  const broken: string[] = []
  const walk = (v: Json, defs: Defs) => {
    if (!v || typeof v !== 'object') return
    const d = withDefs(defs, v)
    if (typeof v.$ref === 'string') {
      try {
        resolveAny(spec, v.$ref, d)
      } catch {
        broken.push(v.$ref)
      }
    }
    for (const x of Object.values(v)) walk(x, d)
  }
  walk(spec, {})
  return broken
}

/** Every description key the contract has (schemas, fields, routes, parameters, responses). */
export function translationKeys(spec: Spec): string[] {
  const keys = new Set<string>()
  const schemas: Record<string, Json> = { ...(spec.components?.schemas ?? {}) }
  const walk = (v: Json) => {
    if (!v || typeof v !== 'object') return
    if (v.$defs) Object.assign(schemas, v.$defs)
    for (const x of Object.values(v)) walk(x)
  }
  walk(spec.paths)
  for (const [name, s] of Object.entries<Json>(schemas)) {
    if (s.description) keys.add(name)
    for (const [f, v] of Object.entries<Json>(s.properties ?? {})) if (v?.description) keys.add(`${name}.${f}`)
  }
  for (const [path, item] of Object.entries<Json>(spec.paths ?? {})) {
    for (const m of ['get', 'post', 'put', 'patch', 'delete']) {
      const o = item?.[m]
      if (!o) continue
      const key = `${m} ${path}`
      if (o.description) keys.add(`op:${key}`)
      for (const p of o.parameters ?? []) if (p.description) keys.add(`param:${key}:${p.name}`)
      for (const [st, r] of Object.entries<Json>(o.responses ?? {})) {
        if (r.description && GENERIC_RESPONSES.has(r.description)) keys.add(`res:*:${r.description}`)
        else if (r.description) keys.add(`res:${key}:${st}`)
      }
    }
  }
  return [...keys]
}

/** The example fits the schema: every field exists and every required one is present. Returns the first problem. */
export function exampleProblem(spec: Spec, schema: Json, ex: Json, defs0: Defs = {}, at = ''): string | undefined {
  const defs = withDefs(defs0, schema)
  let { schema: s } = deref(spec, schema, defs)
  const alts: Json[] | undefined = s.anyOf ?? s.oneOf
  if (alts && ex !== null) {
    const nonNull = alts.filter((a) => a?.type !== 'null')
    const probs = nonNull.map((a) => exampleProblem(spec, a, ex, defs, at))
    return probs.includes(undefined) ? undefined : probs[0]
  }
  s = s ?? {}
  if (Array.isArray(ex)) {
    for (let i = 0; i < ex.length; i++) {
      const p = exampleProblem(spec, s.items ?? {}, ex[i], defs, `${at}[${i}]`)
      if (p) return p
    }
    return undefined
  }
  if (ex && typeof ex === 'object' && s.properties) {
    for (const [k, v] of Object.entries(ex)) {
      if (!(k in s.properties)) {
        if (s.additionalProperties) continue
        return `${at}.${k} is not in the schema`
      }
      const p = exampleProblem(spec, s.properties[k], v, defs, `${at}.${k}`)
      if (p) return p
    }
    for (const r of s.required ?? []) if (!(r in ex)) return `${at}.${r} is required`
  }
  return undefined
}
