# Niadra web

Everything Niadra serves in a browser, in one Node project:

| Part | Where | What it is |
|---|---|---|
| Site | `src/` (pages, components, i18n, blog) | niadra.com: React 19, TypeScript, Vite 7, Tailwind v4 and react-router. Every page is prerendered to HTML in Portuguese (root) and English (`/en`). |
| Console | `src/console/` | The governance console at `/console`, loaded only when opened. It talks to the control plane and the data API of [../server](../server). See [src/console/README.md](src/console/README.md). |
| Admin panel | `admin.html`, `src/admin/` | A private panel on the admin host (contact requests and access counts), its own Vite entry (`vite.admin.config.ts`). |
| Site API | `server/` | A small Node HTTP API with Postgres: the contact form (`POST /api/contato`), the access counter (UDP from Caddy) and the admin panel's API (`/api/admin/*`). |

The developer documentation lives in [../docs](../docs) (docs.niadra.com), the platform server in
[../server](../server) and the infrastructure in [../deploy](../deploy).

## Requirements

Node 22 and npm. For the Python snippet check, [uv](https://docs.astral.sh/uv/) (or Python 3.12 with
`pip install niadra`). For the site API, a Postgres 16.

## Run

```bash
npm ci
npm run dev                                  # site and Console on http://localhost:5173
npx vite --config vite.admin.config.ts       # admin panel on http://localhost:5174
```

For the Console in development, create `public/config.json` on your machine only (it is not
committed): `{ "controlURL": "http://localhost:8080", "apiURL": "http://localhost:8000" }`.

The site API, with a local Postgres:

```bash
docker run -d --rm --name niadra-pg -e POSTGRES_USER=niadra_site -e POSTGRES_PASSWORD=dev \
  -e POSTGRES_DB=niadra -p 127.0.0.1:55432:5432 postgres:16-alpine
printf 'DATABASE_URL=postgres://niadra_site:dev@127.0.0.1:55432/niadra\nALLOWED_ORIGINS=http://localhost:5173\nADMIN_HOST=localhost:5174\n' > .env.api
npm run build:api && npm run api             # 127.0.0.1:8787; both Vite servers forward /api to it
```

### Site API environment

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | required | Postgres connection string |
| `HOST`, `PORT` | `127.0.0.1`, `8787` | Listen address (`0.0.0.0` in the image) |
| `ALLOWED_ORIGINS` | none | Extra origins allowed to post the form, comma separated (`https://niadra.com` and `www` are built in) |
| `SITE_HOST` | `niadra.com` | The host whose accesses are counted |
| `ADMIN_HOST` | `admin.<SITE_HOST>` | The only host where `/api/admin/*` answers |
| `ADMIN_EMAIL` | none | The admin panel's account |
| `ADMIN_PASSWORD_HASH` | none | scrypt hash of that account's password, `scrypt$16384$8$1$<salt>$<digest>` (base64url) |
| `ACCESS_LOG_PORT` | `8788` | UDP port of the access counter; `0` turns it off |

Without `ADMIN_EMAIL` and `ADMIN_PASSWORD_HASH` the admin panel refuses every sign-in. To make a
hash without the password reaching the shell history:

```bash
read -rs ADMIN_PASSWORD && export ADMIN_PASSWORD
node -e 'const c=require("node:crypto");const s=c.randomBytes(16);const d=c.scryptSync(process.env.ADMIN_PASSWORD,s,32,{N:16384,r:8,p:1});console.log(`scrypt$16384$8$1$${s.toString("base64url")}$${d.toString("base64url")}`)'
unset ADMIN_PASSWORD
```

## Check

The same steps CI runs (`.github/workflows/web.yml` at the monorepo root), from this directory:

```bash
npm ci
npm run audit:prod                           # high or critical advisories in runtime dependencies
npx tsc -b                                   # types
npx vitest run                               # tests (site, Console, admin, site API)
npm run build                                # browser build, prerender, admin build
uv run --no-project --python 3.12 --with niadra scripts/python-snippets.py
```

`npm run build` writes `dist/` (pages as HTML and Markdown, `sitemap.xml`, `robots.txt`, `llms.txt`,
`llms-full.txt`, RSS feeds, 404 pages), `dist-ssr/` (the prerender bundle) and `dist-admin/`. The
prerender stops on any internal link to an address that does not exist, and the Markdown plugin
(`build/markdown.ts`) refuses a post with a missing field, an unknown icon, an em dash or a `#` title.
The last step runs the home snippet and every Python block of the blog against the published `niadra`
package and its in-memory emulator.

## Content

- **Copy.** All page text is in `src/i18n/pt.ts` and `src/i18n/en.ts`; the English dictionary has the
  type of the Portuguese one, so a missing key fails the build. Internal links use `Link` from
  `src/i18n/link.tsx`, which adds `/en` when needed. `src/copy.test.ts` holds the writing rules a test
  can check (no em or en dash, never "bot", no internal planning ids, none of the promises the site
  does not make, no model or model provider named).
- **Blog.** Each post is a pair `src/content/blog/<id>.pt.md` and `<id>.en.md` sharing the `id` in the
  frontmatter (`id`, `lang`, `slug`, `title`, `description`, `order`, `date`, `category`, `icon`,
  `cover`, `keywords`). Questions under `## Perguntas frequentes` / `## Frequently asked questions`
  become FAQPage structured data. `src/blog/posts.ts` lists the posts.
- **Benchmark.** `/benchmark` reads the result files in `src/content/benchmark/runs` and
  `src/content/benchmark/typed`, imported by `scripts/import-benchmark.mjs` and
  `scripts/import-typed-benchmark.mjs` from the benchmark results published in
  [niadra-sdk-python](https://github.com/ainiadra/niadra-sdk-python).
- **Context example.** The context shown in the demonstration is the exact text the server's compiler
  produced for a synthetic customer: [../docs](../docs) generates it (`tooling/pack`, `npm run sync`)
  and `node scripts/pack-example.mjs` copies it into `src/content/pack-example.json` (`NIADRA_DOCS_DIR`
  overrides `../docs`). A test fails if the page shows anything else.

## Style

Tailwind only, single light theme, no hand-written CSS. `src/index.css` holds the generated color
tokens (OKLCH, named by function: `bg`, `ink`, `line`, `edge`, `accent`, `brand`) and is not edited by
hand; every text and background pair meets WCAG AA. Shared class compositions are in `src/ui.ts`.
The visual direction in short: an enterprise product, 1px hairlines, flat surfaces, 44px touch
targets, no entrance animation (visible content is the resting state), no generic "AI" imagery.

## Images

The `Dockerfile` builds two images from this directory:

| Target | What it runs |
|---|---|
| `site` | Caddy on `:8080` with the pages, the Console and, on `ADMIN_HOST` only, the admin panel (`deploy/Caddyfile`). On start, `deploy/entrypoint.sh` writes the Console's `/config.json` and its CSP from `SITE_ORIGIN`, `NIADRA_CONTROL_URL` and `NIADRA_API_URL` (or `NIADRA_CELLS`). |
| `site-api` | The site API on `:8787` (TCP) and the access counter on `:8788` (UDP). |

```bash
docker build --target site -t niadra-site . && docker run --rm -p 8080:8080 \
  -e SITE_ORIGIN=http://localhost:8080 -e NIADRA_CONTROL_URL=http://localhost:8001 \
  -e NIADRA_API_URL=http://localhost:8000 niadra-site
```

The deployment of both images is in [../deploy](../deploy). After a deploy to the domain,
`deploy/indexnow.sh` notifies IndexNow of the pages that changed; its key is public by design and
served at `public/2cb4b61fc760b200811620abd9314331.txt`.

## Layout

```
src/
  i18n/            pt.ts, en.ts, index.ts (language from the URL), link.tsx
  pages/           Home, Products, Product, Memory, Resources, Pricing, Enterprise, Blog, Post, Benchmark, Comparison, Integrations, About
  components/      shared site components
  content/         blog posts, benchmark results, comparison data, integration samples
  console/         the Console (screens, api, lib, ui, test)
  admin/           the admin panel
  entry-server.tsx the prerender entry
build/markdown.ts  Markdown to HTML at build time, with frontmatter validation
scripts/           prerender, benchmark import, context example copy, Python snippet check
server/            the site API: api.ts, contact.ts, access/ (counter), admin/ (sign-in and routes)
deploy/            Caddyfile, entrypoint.sh, console-config.sh, indexnow.sh
Dockerfile         the site and site-api images
```
