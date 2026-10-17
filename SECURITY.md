# Security policy

Niadra holds the memory of a company's customers, so we treat every report seriously.

## Reporting a vulnerability

Do not open a public issue. Report it privately, in English or Portuguese, through either channel:

- GitHub: the **Report a vulnerability** button in this repository's
  [Security tab](https://github.com/ainiadra/niadra/security/advisories/new).
- The form at [niadra.com/enterprise](https://niadra.com/en/enterprise?interesse=seguranca), with "Security" selected.

Include what you found, how to reproduce it, and the component and version (`server/`, `web/`, `deploy/`,
`spec/`, `docs/`, or one of the SDKs). We answer every report, keep you informed while we work on a fix, and
credit you in the advisory unless you prefer otherwise.

## Scope

This repository, the SDKs ([niadra-sdk-python](https://github.com/ainiadra/niadra-sdk-python),
[niadra-sdk-ts](https://github.com/ainiadra/niadra-sdk-ts)) and the services at `*.niadra.com`. Please do not
run load tests or automated scanners against the hosted services, and never access data that is not yours.

## Supported versions

Fixes land on `main`. Self-hosted deployments should track the latest commit of `main` and the latest SDK
releases.
