/**
 * Brings the Context Pack examples from the server's compiler into the documentation.
 *
 * Runs tooling/pack/generate.py inside the server's environment (NIADRA_SERVER_DIR, or
 * ../server) and writes its output to tooling/pack/examples.json, from which `npm run build`
 * writes the examples into the guide pages. The website (../web) copies the same file.
 *
 *   node tooling/sync-pack.mjs            syncs what it can; without the server, keeps the file
 *   node tooling/sync-pack.mjs --strict   fails if the server is missing or the generator fails
 */
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SERVER = process.env.NIADRA_SERVER_DIR ?? join(ROOT, '..', 'server')
const GENERATOR = join(ROOT, 'tooling', 'pack', 'generate.py')
const OUT = join(ROOT, 'tooling', 'pack', 'examples.json')
const strict = process.argv.includes('--strict')

if (!existsSync(join(SERVER, 'src', 'niadra', 'domain', 'compile'))) {
  const msg = `the server's compiler was not found in ${SERVER}; tooling/pack/examples.json stays as it is`
  if (strict) {
    console.error(msg)
    process.exit(1)
  }
  console.log(msg)
  process.exit(0)
}

const run = spawnSync('uv', ['run', 'python', GENERATOR], { cwd: SERVER, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 })
if (run.status !== 0) {
  console.error(`pack examples: the generator failed (${run.error?.message ?? `exit code ${run.status}`})\n${run.stderr ?? ''}`)
  process.exit(1)
}
const examples = JSON.parse(run.stdout)
const next = JSON.stringify(examples, null, 2) + '\n'
const prev = existsSync(OUT) ? readFileSync(OUT, 'utf8') : ''
writeFileSync(OUT, next)
console.log(`pack examples: ${prev === next ? 'unchanged' : 'updated'} (compiler ${examples.compiler_version}, server ${examples.server_commit})`)
