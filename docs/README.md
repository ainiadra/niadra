# Niadra developer documentation

Niadra's public documentation, published at https://docs.niadra.com by Mintlify.
Portuguese at the root (`/quickstart`), English under `/en` (`/en/quickstart`). Both languages are
product content and are kept in step: every page exists at the same path in both.

The company website ([`../web`](../web)) only links here, from the Resources menu and the footer.

Tabs, in menu order: Home, Concepts, Guides, Integrations, SDKs, API reference and FAQ
(in Portuguese: Início, Conceitos, Guias, Integrações, SDKs, Referência da API, Perguntas frequentes).
The reference tab is generated from the server's contract ([`../server`](../server)).

## What is here

| Path | What it is | Who writes it |
|---|---|---|
| `docs.json` | Theme, colors, fonts, the menu of both languages | By hand; the reference groups are rewritten by `npm run build` |
| `index.mdx`, `quickstart.mdx`, `concepts/`, `guides/`, `integrations/`, `sdk/`, `security/`, `errors.mdx`, `conventions.mdx`, `api.mdx` | The guide in Portuguese | By hand |
| `integrations/` | One page per integration of the SDKs (voice, channels, agent frameworks, model SDKs, automation platforms), the MCP client configurations and the coding-assistant page; the code samples are the SDKs' `examples/` | By hand; when an adapter changes, change the page |
| `skill/niadra/SKILL.md` | The `niadra` skill a coding assistant loads to connect a repository to Niadra, served at `/skill/niadra/SKILL.md` | By hand |
| `llms.txt` | The documentation as a plain-text list of pages for AI agents, with the block for agents integrating Niadra and the link to the skill; it replaces the file Mintlify would generate | By hand; add a page here when it enters `docs.json` |
| `en/` | The same guide in English, with the same paths | By hand |
| `api/`, `en/api/` | One page per route, which only points to the operation in the OpenAPI | Generated |
| `openapi/{pt,en}/{cell,control}.json` | The translated contract, with examples, code samples and who can call each route | Generated |
| `tooling/openapi/{cell,control}.json` | The contract as it comes out of the server | `npm run sync` |
| `tooling/openapi/*.ts`, `tooling/api-pages.ts` | Translations, examples, code samples, permissions and the list of reference pages | By hand |
| `tooling/pack/examples.json` | The Context Pack the voice agent gets in the guide pages (`index`, `concepts/context`, both languages) and in the `POST /v1/context` example: the server's compiler output for a synthetic customer, from `tooling/pack/generate.py` | `npm run sync`; `npm run build` writes it into the pages below a marker comment |
| `tooling/build.ts`, `tooling/docs.test.ts` | The generator of the reference and the tests | By hand |
| `logo/`, `favicon.svg` | Brand assets | By hand |
| `faq.mdx`, `en/faq.mdx` | FAQ: the same questions as the website (home and the Memory page), copied from [`../web/src/i18n`](../web/src/i18n) | By hand; when it changes there, change it here |
| `style.css` | Neutral grays and the dark background `#111113` | By hand |

`.mintignore` keeps `tooling/`, `node_modules/` and this README out of the site.

## Running

Node 22 or later.

```bash
npm ci
npm run dev        # http://localhost:3000
```

## Checking

The same steps CI runs ([`../.github/workflows/docs.yml`](../.github/workflows/docs.yml)), from this directory:

```bash
npm ci
npm run check      # builds the reference, runs the tests, mint validate and mint broken-links
git diff --exit-code -- openapi api en/api docs.json   # the generated files are committed
```

The tests fail when:

- a contract route has no page in `tooling/api-pages.ts`, or a page is left without a route;
- a route has no rule for who can call it in `tooling/openapi/access.ts`;
- a handwritten example no longer fits the schema;
- the Portuguese for a new description is missing (the table is `tooling/openapi/pt.ts` and `tooling/openapi/extra/*.pt.ts`);
- the generated files are stale (run `npm run build`);
- a guide page shows a pack other than the one in `tooling/pack/examples.json`;
- a menu page does not exist, has a second `# ` title or uses a word or character the brand does not use.

## When the contract changes on the server

The reference is generated from the server in [`../server`](../server): its OpenAPI exporter
(`src/niadra/entrypoints/openapi.py`) and its Context Pack compiler. The server needs its own
environment (`uv sync` in `../server`).

```bash
npm run sync       # runs the exporter and the pack generator in ../server, then npm run build
npm test
```

`NIADRA_SERVER_DIR` points the sync at another checkout of the server. `npm run sync` also compiles
the Context Pack examples with the server's own functions (`tooling/pack/generate.py`, run with
`uv run` in the server's environment), so the pack on the pages is never written by hand. The
website copies `tooling/pack/examples.json` for its demonstration
([`../web/scripts/pack-example.mjs`](../web/scripts/pack-example.mjs)).

## Publishing

Mintlify publishes `main` through its GitHub app, with this `docs/` directory set as the docs root of
the monorepo. CI runs `npm run check` on every push and pull request that touches `docs/`, and fails when
the generated files are not committed. The domain `docs.niadra.com` is a CNAME to
`cname.mintlify.builders`, in the Route 53 zone of the cell stack
([`../deploy/aws/cluster.yaml`](../deploy/aws/cluster.yaml)).
