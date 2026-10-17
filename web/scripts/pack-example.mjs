/**
 * Copies the Context Pack example the documentation generates from the server's compiler
 * (../docs/tooling/pack/examples.json, written by its `npm run sync`) into
 * src/content/pack-example.json, which the demonstration of the site shows as it is.
 *
 *   NIADRA_DOCS_DIR=../docs node scripts/pack-example.mjs
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const DOCS = process.env.NIADRA_DOCS_DIR ?? join(ROOT, '..', 'docs')
const FROM = join(DOCS, 'tooling', 'pack', 'examples.json')
const TO = join(ROOT, 'src', 'content', 'pack-example.json')

if (!existsSync(FROM)) {
  console.error(`no pack examples at ${FROM}; run npm run sync in ../docs first`)
  process.exit(1)
}
const examples = JSON.parse(readFileSync(FROM, 'utf8'))
for (const lang of ['pt', 'en']) {
  if (typeof examples.packs?.[lang]?.voice?.text !== 'string') {
    console.error(`the examples have no voice pack for ${lang}`)
    process.exit(1)
  }
}
const next = JSON.stringify(examples, null, 2) + '\n'
const prev = existsSync(TO) ? readFileSync(TO, 'utf8') : ''
writeFileSync(TO, next)
console.log(`pack example: ${prev === next ? 'unchanged' : 'updated'} (compiler ${examples.compiler_version}, server ${examples.server_commit})`)
