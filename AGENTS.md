# AGENTS.md

Guidance for coding agents working in this repository. Humans: see [CONTRIBUTING.md](CONTRIBUTING.md).

## What this is

Niadra is an open-source memory layer for AI agents: it builds one memory of each customer from every
channel and system and serves a short context to any agent before its model call. This monorepo holds
the server, the web app (website and Console), the deployment, the open specifications and the docs. The
SDKs live in [niadra-sdk-python](https://github.com/ainiadra/niadra-sdk-python) and
[niadra-sdk-ts](https://github.com/ainiadra/niadra-sdk-ts).

## Layout

- `server/`: Python 3.13 with uv. Layers `entrypoints -> adapters -> app -> ports -> config / contracts -> domain`,
  enforced by `import-linter`; `domain/` is pure. Read [`server/CODE_GUIDE.md`](server/CODE_GUIDE.md) first.
- `web/`: React 19, TypeScript, Vite, Tailwind v4. The Console is `web/src/console`.
- `deploy/`: Helm chart (`deploy/k8s/charts/niadra`), helmfile, CloudFormation (`deploy/aws`), scripts.
- `spec/`: normative prose in `spec/spec`, JSON Schemas in `spec/schemas`, test vectors in `spec/vectors`.
- `docs/`: Mintlify site; Portuguese at the root, English under `docs/en`; the API reference is generated.

## Rules

- Work inside one component when you can, and run that component's checks (table in CONTRIBUTING.md)
  before you say a change is done. Report the commands you ran and their results.
- A change to the server's public contract (`server/src/niadra/contracts`) regenerates the OpenAPI and
  updates `spec/` and `docs/` in the same change.
- Code, identifiers, logs and commit messages are in English. Do not add comments that restate the code.
- Never put real customer data, keys or credentials in code, tests or fixtures.
- The server never depends on a proprietary cloud service: PostgreSQL, RabbitMQ, a Redis-compatible
  cache, S3-compatible storage and configurable model endpoints, each behind a port in `server/src/niadra/ports`.
