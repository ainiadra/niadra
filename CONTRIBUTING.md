# Contributing to Niadra

Thank you for helping. Niadra is a monorepo of five components, each with its own toolchain and its own
CI workflow in [`.github/workflows`](.github/workflows). A pull request runs only the workflows of the
components it touches.

| Component | Stack | Checks (run inside the component's directory) |
|---|---|---|
| [`server/`](server) | Python 3.13, uv, PostgreSQL 17, RabbitMQ, Redis | `uv sync`, `uv run ruff check src tests migrations`, `uv run ruff format --check src tests migrations`, `uv run mypy`, `uv run lint-imports`, `uv run pytest -o addopts="" -q` |
| [`web/`](web) | React, TypeScript, Vite, Tailwind | `npm ci`, `npx tsc -b`, `npx vitest run`, `npm run build` |
| [`deploy/`](deploy) | Helm, helmfile, CloudFormation, Bash, Python | `scripts/check.sh` |
| [`spec/`](spec) | JSON Schema, Python, uv | `uv sync --frozen`, `uv run ruff check .`, `uv run pytest -q` |
| [`docs/`](docs) | Mintlify, TypeScript | `npm ci`, `npm run check` |

Each component's README says how to run it locally. The server's integration tests need PostgreSQL 17,
Redis and RabbitMQ; [`server/README.md`](server/README.md) explains how to start them.

## How we work

- **One change per pull request**, with a description of what changes, why, and how you checked it.
- **Tests come with the change.** A bug fix starts with a test that fails without it.
- **Contracts are code.** The public API models in `server/src/niadra/contracts` generate the OpenAPI
  that the SDKs, the Console, the specs and the docs are tested against. A change to the contract updates
  the spec and the docs in the same pull request.
- **Identifiers, code and commit messages are in English.** Product content in Portuguese (the website's
  Portuguese pages and the Portuguese documentation) is kept in step with its English counterpart.
- **No comments that restate the code.** Names carry the meaning; the reasoning goes in the pull request
  or in the component's README.
- **Never commit real customer data, keys or credentials**, not even in tests. Use the synthetic fixtures
  and `example.com` addresses.

## Reporting bugs and proposing features

Open an issue with the templates. For anything security related, follow [SECURITY.md](SECURITY.md) instead.

## License

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE).
