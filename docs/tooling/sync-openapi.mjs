/**
 * Brings the server's OpenAPI contract into the documentation.
 *
 * Runs the server exporter (../server, or NIADRA_SERVER_DIR) and writes its output to
 * tooling/openapi/cell.json and control.json, from which `npm run build` generates the
 * reference. There is no public /openapi.json: the contract enters this directory
 * and is reviewed in the diff, like any other text.
 *
 *   node tooling/sync-openapi.mjs            syncs what it can; without the exporter, keeps the files
 *   node tooling/sync-openapi.mjs --strict   fails if the exporter does not exist or does not run
 *
 * After syncing, run `npm run build` and `npm test`: the test fails if a contract route
 * has no page in tooling/api-pages.ts (and vice versa).
 */
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SERVER = process.env.NIADRA_SERVER_DIR ?? join(ROOT, '..', 'server')
const OUT = join(ROOT, 'tooling', 'openapi')
const TARGETS = ['cell', 'control']
const strict = process.argv.includes('--strict')

const exporter = join(SERVER, 'src', 'niadra', 'entrypoints', 'openapi.py')
if (!existsSync(exporter)) {
  const msg = `exporter not found (${exporter}); the files in tooling/openapi stay as they are`
  if (strict) {
    console.error(msg)
    process.exit(1)
  }
  console.log(msg)
  process.exit(0)
}

let failed = 0
for (const target of TARGETS) {
  const run = spawnSync('uv', ['run', 'python', '-m', 'niadra.entrypoints.openapi', '--target', target], {
    cwd: SERVER,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  })
  if (run.status !== 0) {
    console.error(`${target}: the exporter failed (${run.error?.message ?? `exit code ${run.status}`})\n${run.stderr ?? ''}`)
    failed++
    continue
  }
  let spec
  try {
    spec = JSON.parse(run.stdout)
  } catch {
    console.error(`${target}: the output is not JSON`)
    failed++
    continue
  }
  if (!String(spec.openapi ?? '').startsWith('3.1')) {
    console.error(`${target}: expected OpenAPI 3.1, got ${spec.openapi}`)
    failed++
    continue
  }
  const file = join(OUT, `${target}.json`)
  const next = JSON.stringify(spec, null, 2) + '\n'
  const prev = existsSync(file) ? readFileSync(file, 'utf8') : ''
  writeFileSync(file, next)
  const paths = Object.keys(spec.paths ?? {}).length
  console.log(`${target}: ${prev === next ? 'unchanged' : 'updated'} (${paths} paths)`)
}

if (failed && strict) process.exit(1)
