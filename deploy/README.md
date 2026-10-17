# deploy

How a Niadra cell runs on AWS: Kubernetes (k3s) on one machine, RDS PostgreSQL, S3, KMS, Secrets
Manager and CloudWatch Logs, with everything that serves traffic inside the cluster. The images are built
from [`../server`](../server) (server, models) and [`../web`](../web) (site with the Console, the site
API). Growing adds machines to the same cluster; nothing in the chart changes.

```
                      Route 53 (<domain>)
                              |
                    one fixed address (80, 443)
                              |
   k3s machine -- Traefik -- ingest, read, admin, insights, control, site, site-api
                             workers (one deployment per queue class, KEDA on queue depth)
                             loops, models, PgBouncer, RabbitMQ, Valkey
                             cert-manager, External Secrets, Prometheus, Grafana, Fluent Bit
                              |
         RDS PostgreSQL, S3, ECR, Secrets Manager, KMS, SNS, CloudWatch Logs
```

| Path | What it is |
|---|---|
| `aws/cluster.yaml` | the CloudFormation stack of a cell: network, the k3s machine and its fixed address, RDS PostgreSQL, the cell, release and audit buckets, the cell's master key in KMS, the alerts topic, the log group; for the default cell also the four image repositories and the DNS zone. A dedicated cell (below) is the same template under another name |
| `k8s/helmfile.yaml.gotmpl` | everything installed in the cluster, in order |
| `k8s/charts/niadra` | the platform's chart, installed twice: `base` (configuration, secrets, broker, cache, connection pooler, network policies, certificate issuer) and `app` (database setup, the deployables, ingress, metrics, alerts, dashboard, nightly backups, the sandbox tenant, `helm test`) |
| `k8s/values/` | values of the third-party charts: Prometheus and Grafana, Fluent Bit |
| `k8s/rabbitmq-operator` | the broker operator's released manifest with our resources, applied with `kubectl apply --server-side -k` |
| `scripts/` | stack, deploy, the local cell and the operator's reports (below) |
| `tests/` | checks that the API ingress sends every public path to the deployable that serves it and that the database grants match the server's; `cluster-default.json`, the default cell's stack as CloudFormation resolves it, which `scripts/check.sh` compares against |

## What a cell is

- **Kubernetes from the first machine.** Every deployable is a deployment with its own resources and
  autoscaler; each worker pool scales on its queue's depth (KEDA), and the pools that can wait scale to
  zero. The measure pool keeps one worker, so a session's measurement lands right after its memory.
- **Memory requests are the budget.** The scheduler places a pod only where its memory request fits, so
  every long-running container asks for what it holds, measured on the machine (the comments beside
  each value). `scripts/requests_budget.py` adds every release up at its minimum and fails when a
  container asks for nothing, when the sum passes 85% of the node's allocatable memory (about 5,940 MiB
  of the 8 GiB machine), or when the read path could not reach its autoscaler's maximum in the rest. A
  pending `read` replica is placed before the others (PriorityClass `niadra-read`, which never evicts).
- **State outside the cluster** where a managed service is cheaper to run than to operate: RDS
  (backups, point-in-time recovery), S3, Secrets Manager, CloudWatch Logs. RabbitMQ and Valkey run in
  the cluster. Valkey is two stores: `valkey`, the cache, evicts its least used entries when full;
  `valkey-state` (the hot layer, key revocations, rate and daily limits) never evicts, and an alert
  fires if it refuses a write (`templates/data.yaml`).
- **No secret in any repository or image.** `scripts/bootstrap-secrets.sh` generates them once into
  Secrets Manager (`<cell>/app`); External Secrets copies each one only into the Secret of the processes
  that read it. The machine reaches AWS with its instance role, never with keys.
- **The master key never leaves KMS.** The cell's key (`alias/<cell>-cell`) wraps each space's data key;
  a process asks KMS once per data key and keeps it in memory. The machine's role may only use the key
  and the account may only administer it, so using it takes a change to the key policy first, which
  CloudTrail records. KMS rotates the key material every year.
- **The audit trail is written once.** The daily root of each space's receipt chain is also copied to
  `<cell>-audit-<account>`, a bucket with Object Lock in compliance mode: for five years nobody, the
  account's root user included, can delete a copy, and the bucket policy refuses any write that could
  replace one. The machine's role writes and reads there, and cannot delete.
- **Plain Kubernetes.** Nothing in `k8s/` depends on AWS except the secret store's provider, the log
  output and Grafana's CloudWatch data source.

### Databases

One RDS instance holds three kinds of database: `niadra_control` (the control plane), `niadra_cell` (the
cell's own state: the directory of space databases, wrapped data keys, the configuration snapshot,
health) and one database per space, `s_<space id>`. The cell creates, migrates and drops the space
databases itself (the `loops` deployment, from the control plane's snapshot), so a new space needs no
deploy; the chart only makes the roles and the first databases (`files/db-init.sql`) and runs the
migrations on every deploy.

- **PgBouncer** (`templates/data.yaml`, values `pgbouncer`) sits between the cell and the database in
  transaction mode. Hundreds of small databases would otherwise need hundreds of connections, and the
  db.t4g.micro allows 79: each process opens at most three client connections per database and role,
  PgBouncer keeps at most `maxDbConnections` (2) server connections per database (`cellDbConnections`,
  4, for the cell's and the control plane's own) and closes one idle for `serverIdleTimeout` (10 s). The
  workers and the loops log in as `niadra_worker`, whose pool gets `workerDbConnections` (1) of a
  database's two, so background work never holds the connection a batch's acknowledgement needs. The
  userlist is written from the roles' secrets when the pod starts, so a password rotation also needs
  `kubectl -n niadra rollout restart deployment/pgbouncer`. PgBouncer reaches the database over TLS 1.3
  (the parameter group sets `ssl_min_protocol_version = TLSv1.3`). Migrations, provisioning and the
  backups connect to the database directly (`NIADRA_DB_DIRECT_HOST`).
- **Backups.** RDS keeps its automated backups (`BackupRetentionPeriod` in `aws/cluster.yaml` is 1 day;
  raise it to 35, and `backup.instanceRetentionDays` with it, where the AWS account's plan allows), but
  it only restores the whole instance. So the `niadra-backup` CronJob
  (`templates/ops.yaml`) runs `pg_dump` of the cell database and of every space database up to 50 GiB
  each night at 02:30 UTC into the cell bucket (`backups/<database>/`), where they expire after 35 days;
  on Sundays it also restores each latest dump into a scratch database and counts rows. Restoring one
  space, checking a dump by hand and deleting a space are runbooks in the
  [server's README](../server/README.md); run them in the `loops` pod
  (`kubectl -n niadra exec deploy/loops -- python -m niadra.entrypoints.ops ...`).

### Growing

`python3 scripts/stack_render.py cost [InstanceType=... DatabaseClass=...]` prints a monthly estimate
from AWS on-demand list prices. To survive the loss of a machine or a zone: two more k3s servers in two
other zones join the cluster (`--server https://<first>:6443` with the first server's token; it already
runs embedded etcd), a network load balancer goes in front of the three, the database becomes Aurora or
Multi-AZ, and the chart's `replicas` and `rabbitmq.replicas` go to 2 and 3.

## Deploy your own

Needs an AWS account, the AWS CLI signed in (`NIADRA_PROFILE`, default `niadra`; `NIADRA_REGION`, default
`us-east-2`), `rsync`, `jq` and Python 3 with PyYAML. No SSH port is open: everything on the machine goes
through Systems Manager.

1. **The stack.** `NIADRA_DOMAIN=<domain> NIADRA_ALERT_EMAIL=<address> scripts/stack.sh` creates or
   updates the stack and prints its outputs. Point the domain's nameservers at the `NameServers` output;
   until then the cell answers on `sslip.io` names. The address gets the alerts once it confirms the SNS
   subscription.
2. **The AI endpoint.** The extraction and decision models are reached through an OpenAI-compatible
   endpoint. Its settings live in one secret, `<cell>/platform/llm`, with five properties, all of which
   must exist (External Secrets fails on a missing one; an empty value reaches the server as unset):

   ```
   aws secretsmanager create-secret --name niadra/platform/llm --secret-string '{
     "api_key": "<key>", "base_url": "<https://endpoint/v1>", "model": "<extraction model id>",
     "decisions_base_url": "<endpoint of the decision model>", "decisions_model": "<decision model id>"}'
   ```

   The chart passes them to the cell as `NIADRA_LLM_API_KEY`, `NIADRA_LLM_BASE_URL`, `NIADRA_LLM_MODEL`,
   `NIADRA_DECISIONS_BASE_URL` and `NIADRA_DECISIONS_MODEL` (`templates/secrets.yaml`).

   The site's admin panel signs in one account, read from the optional secret `<cell>/admin/site` with the
   properties `email` and `password_hash` (an scrypt hash; [`../web/README.md`](../web/README.md) shows how to
   make one). Without it the panel refuses every sign-in, and the rest of the site keeps working.
3. **The first release.** `scripts/deploy.sh` (with `NIADRA_DOMAIN=<domain>` once the nameservers point
   at the zone) packs `../server`, `../web` and `../deploy` into one archive in the release bucket; the
   machine builds the images, pushes them to ECR, generates the cell's secrets (`<cell>/app`), installs
   the RabbitMQ operator and runs `helmfile apply`. Chart hooks create the database roles and databases
   and run `serve setup` (migrations, broker topology, bucket rules) before any deployable starts; when
   any of them fails, the deploy stops before the pods change. Then `helm test` runs the end-to-end check
   of the sandbox and `scripts/latency.sh --gate` probes the read inside the region.
4. **Continuous deploy.** From then on the machine pulls: `scripts/cd.sh` runs from a systemd timer a
   minute after each run ends, reads the `release` branch of `NIADRA_CD_REPOSITORY` (set when running
   `deploy.sh`; default `https://github.com/ainiadra/niadra.git`) and, when it moved, runs the same deploy.
   A person moves `release` to a green commit of `main`; no CI run deploys, and GitHub holds no AWS
   credential. For a private repository, use its SSH address and create the read-only deploy key with
   `NIADRA_GITHUB_REPOSITORY=<owner>/<repo> scripts/github-keys.sh`.

The first install also creates the sandbox's synthetic tenant, whose admin password and keys go straight
to Secrets Manager (`<cell>/tenant/bootstrap`): the company "Niadra", the project "Atendimento e cobrança"
(spaces `atendimento` and `atendimento-sandbox`) and the admin `e2e@niadra.com`, which the end-to-end check
uses.

Without `NIADRA_DOMAIN`, the hosts are names under `sslip.io` that resolve to the machine's fixed address
(`<a-b-c-d>.sslip.io`, `api.`, `control.`, `grafana.`), with certificates proven over HTTP; the SDKs need
`NIADRA_BASE_URL` there, since a wildcard certificate is only possible in the cell's own zone.

| Host | What answers |
|---|---|
| `<domain>` (and `www.`) | the site, the Console at `/console`, the contact form at `/api` |
| `<space>.<region>.api.<domain>` | the data API and MCP at the address the SDKs derive from a key (wildcard record and certificate), routed per path to ingest, read, insights or admin |
| `api.<domain>` | the same API at the cell's own address |
| `control.api.<domain>` | the control plane |
| `grafana.<domain>` | dashboards and alerts (user `admin`, password in `<cell>/app`) |
| `admin.<domain>` | the site's admin panel (ingress `site-admin`): the page from `site`, `/api/admin/*` from `site-api`, which answers that host only |
| `docs.<domain>` | only when the stack gets `NIADRA_DOCS_TARGET` (and the docs host's proofs): a CNAME to where [`../docs`](../docs) is published |

Each image is tagged `src-<digest>` from what it is built of (its build context, target and build
arguments; `scripts/image_tags.py`), never from the release, and a tag the registry holds is not built
again. So a release that changes only `web` rolls only `site` and `site-api`, one that changes `server`
rolls the server's deployments (and `models` when `server/models` changed), and one that changes only
`deploy` rolls what the chart changed. When the end-to-end check or the latency gate fails, `on-ops.sh`
rolls the `niadra` release back to the revision of the release that passed last, but only when the new
release changed no migration history (`server/migrations`) and no shape of the stored space document
(`server/openapi/space-config.json`); either way the deploy counts as failed.

Changes to `aws/cluster.yaml` are not continuous: run `scripts/stack.sh`. The database's parameter group
applies a dynamic parameter at once; a static one waits for a reboot, which `scripts/stack.sh` reports as
`pending-reboot`. Reboot in a quiet moment, never together with a deploy:

```
aws rds reboot-db-instance --db-instance-identifier niadra
aws rds wait db-instance-available --db-instance-identifier niadra
```

CloudFormation reads `AmiId` from Parameter Store again on every update: if a newer Ubuntu image was
published, the same update replaces the machine (the database and the buckets stay; `scripts/deploy.sh`
brings the cluster back). Compare the parameter's value with the machine's `ImageId` first.

## Operating it

```
scripts/stack.sh                       create or update the stack; prints its outputs
scripts/deploy.sh                      build, push and install a release from this repository, then `helm test`
scripts/cd-status.sh [n]               the continuous deploy: commit live, last failure, the last runs' output
scripts/github-keys.sh                 create or rotate the deploy key the machine reads a private repository with
scripts/check.sh                       what CI checks here: scripts, chart, stack template
scripts/latency.sh [rates]             latency of context() inside the region, from a laptop
scripts/contacts.sh [n]                the site's latest contact requests
scripts/admin-check.sh                 the site's admin panel end to end
scripts/console-access.sh [--app]      the Console login of the sandbox tenant the end-to-end check uses
uv run scripts/demo_seed.py            a demo space with synthetic customers, agents and objects; re-runnable
scripts/ai-crawlers.sh [days]          which AI crawlers and search engines read the site
scripts/pause.sh, scripts/resume.sh    stop and start the machine and the database
scripts/cell.sh up|status|route|down <name>   a cell dedicated to one customer (below)
uv run scripts/types_check.py ...      object types end to end through the Python SDK, against a local cell left running (--keep)
uv run scripts/replay_check.py ...     the replay and the tool counterfactual end to end, the same way
uv run scripts/chaos_check.py ...      both SDKs with Niadra down mid-conversation, the same way
```

`NIADRA_STACK` names the cell the scripts act on (default `niadra`). To empty the default cell for a fresh
install, `source scripts/lib.sh; on_ops scripts/reset-cell.sh`; to empty every space and keep tenants,
people, projects, sources and keys, `source scripts/lib.sh; on_ops scripts/reset-spaces.sh`. Both leave the
continuous deploy stopped: move `release`, then `systemctl start niadra-cd.timer` on the machine.

**Logs** go to CloudWatch Logs, group `/<cell>/cluster`, one stream per pod, 30 days; Grafana queries them
next to the metrics. **Alerts**: Prometheus in the cluster, rules in `k8s/charts/niadra/files/alerts.yaml`.
Alertmanager sends severities `page` and `ticket` (Niadra's rules) and `critical` and `warning`
(kube-prometheus-stack's) to the SNS topic `<cell>-alerts`, grouped by rule and severity and repeated every
4 hours, and to a webhook when `NIADRA_ALERTS_WEBHOOK_URL` is set at deploy; `info`, `Watchdog` and alerts
without a severity stay in Grafana. Every deploy that passes, and a weekly job, add a test alert
(`NiadraDeployCanary`, a ticket ending after 5 minutes), so the path to the e-mail stays exercised.

The read has objectives, by the server's time: 99% of context reads under 100 ms, 99% of batches
acknowledged under 80 ms and 99.95% of requests without a 5xx. The ratios outside each objective are
recorded (`files/slo-rules.yaml`) and the alerts burn its error budget over two windows at once: 14.4 times
for an hour and 5 minutes pages, 6 times for six hours and 30 minutes opens a ticket; the p95 rules are
tickets. `HotReadTouchedDatabase` opens a ticket when warm context reads keep running database statements.

### When a worker pool or the database stops

Run the commands on the machine (`source scripts/lib.sh; on_ops '<command>'`).

- **A worker pool stops.** Kubernetes restarts a killed pod by itself. Meanwhile writes and reads keep
  answering: ingest acknowledges into the intake and reads come from the hot layer; memory waits.
  `IntakeBacklog` (the realtime pool) and `QueueWithoutConsumer` (any pool) page after 5 minutes. Look with
  `kubectl -n niadra get pods` and `kubectl -n niadra describe deployment workers-<pool>`; restart with
  `kubectl -n niadra rollout restart deployment/workers-<pool>`. Nothing is replayed by hand.
- **The database is unreachable.** Writes answer 503 or run past the client's timeout, and the SDKs send
  them again with the same idempotency key; a read whose resolution is cached still answers. `ServerErrors`
  fires after 10 minutes of 5xx. Look with `aws rds describe-db-instances --db-instance-identifier niadra`
  and `kubectl -n niadra logs deploy/pgbouncer --tail 50`. When it answers again, nothing is replayed by
  hand; a lost instance follows "Databases" above.

## Dedicated cells

A customer can get servers, a database and keys of its own: a dedicated cell, the same template as a second
stack `niadra-<name>` in the same account and Region, with the parameter `CellName`. Every name derives from
it: the network and the machine, the RDS instance (its disk, and the machine's, encrypted with the cell's own
key), the master key `alias/niadra-<name>-cell`, the buckets `niadra-<name>-{cell,releases,audit}-<account>`,
the log group, the topic and the secrets under `niadra-<name>/`, the only ones its machine's role reads. The
image repositories and the DNS zone stay the default cell's. `scripts/check.sh` renders the template for the
default cell, compares it with `tests/cluster-default.json`, and checks that a dedicated cell's names all
derive from its own.

The cell runs only the cell: the chart with `controlPlane.url` set (the default cell's
`https://control.api.<domain>`) leaves out the control plane, the site, the Console and the sandbox tenant. It
pulls its configuration with a credential of its own, which the control plane knows only by its SHA-256. Its
own names are `niadra-<name>.cells.<domain>` and `grafana.niadra-<name>.cells.<domain>`; the SDKs reach its
spaces at the usual `<space>.<region>.api.<domain>`, through a record per space that beats the wildcard.

```
NIADRA_DOMAIN=<domain> scripts/cell.sh up acme --tenant acme             the monthly estimate and the plan; nothing is created
NIADRA_DOMAIN=<domain> scripts/cell.sh up acme --tenant acme --confirm   stack, the cell in the control plane, secrets, deploy
NIADRA_DOMAIN=<domain> scripts/cell.sh status acme                       the stack, its outputs, the estimate, the space records
NIADRA_DOMAIN=<domain> scripts/cell.sh route acme --confirm              point the addresses of its spaces at it
NIADRA_DOMAIN=<domain> scripts/cell.sh down acme --confirm               refused while it serves a space
```

`up --confirm` creates the stack, registers the cell in the control plane as dedicated to the tenant (with
the SHA-256 of a credential generated on the laptop), writes `niadra-<name>/app` with that credential and the
platform's key pepper, copies `platform/llm` and the deploy key under the cell's name, and deploys; from then
on the cell deploys every release by itself. Every new project of the tenant in the region lands there;
`control_cli place-project` places one by hand (the server's README, "a cell dedicated to one customer"). `down`
refuses while the control plane places a space on the cell; RDS keeps a final snapshot, and the cell and
audit buckets and the master key stay.

## Checking the key, the audit lock and the alerts

- after the next daily anchor, `GET https://api.<domain>/v1/receipts/verify?day=<yesterday>` (with a key
  that has the admin scope) answers `"anchor_locked": true`;
- `aws kms get-key-rotation-status --key-id <CellKeyArn>` shows rotation on, every 365 days;
- `aws s3api get-object-retention --bucket niadra-audit-<account> --key audit/<space id>/<day>.json` shows
  `COMPLIANCE` until five years after that day;
- a test alert reaches the topic, and the e-mail arrives within a minute:
  ```
  kubectl -n monitoring exec alertmanager-monitoring-kube-prometheus-alertmanager-0 -c alertmanager -- \
    amtool alert add NiadraTestAlert severity=ticket --annotation=summary="test of the alert route" \
    --alertmanager.url=http://localhost:9093
  ```

## A cell on this computer

`scripts/local-e2e.sh` runs the deploy's end-to-end check against a server checkout, each deployable started
as production starts it, behind a path router that stands for the API ingress, with PostgreSQL 17 sized
like the db.t4g.micro, PgBouncer with the chart's own configuration, RabbitMQ, two Valkey stores (a Redis
server), S3 (moto) and the checkout's models server.

```
brew install postgresql@17 pgbouncer rabbitmq redis uv helm
export NIADRA_LLM_API_KEY=... NIADRA_LLM_BASE_URL=... NIADRA_LLM_MODEL=... NIADRA_DECISIONS_MODEL=...
scripts/local-e2e.sh ../server                     the check; exits with the number of failed steps
scripts/local-e2e.sh ../server --keep              the same, and the cell stays up (api at 127.0.0.1:19709)
scripts/local-e2e.sh ../server --decisions-down    the decision model unreachable; the cell must still work
scripts/local-e2e.sh <old checkout> ../server      an upgrade: each checkout runs on the data the one before left
```

Without `NIADRA_LLM_API_KEY` in the environment it reads the five settings from Secrets Manager
(`LOCAL_E2E_LLM_SECRET`, default `niadra/bench/llm`). The header of `scripts/local-e2e.sh` lists the other
options (`--burst`, `--history`, `--between`).

## Checks

```
scripts/check.sh
uv run --directory ../server --with pyyaml pytest ../deploy/tests
```

`scripts/check.sh` is what CI runs ([`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml)):
shellcheck, the chart rendered in both parts, on a domain, under sslip.io and as a dedicated cell, the
alert rules (promtool), the Alertmanager routes (amtool), the memory requests of every release against the
node, the default cell's stack against `tests/cluster-default.json`, and cfn-lint (installed, or through
`uvx`). Without helmfile, amtool or promtool it skips those steps on a laptop; CI requires them. The tests in
`tests/` import the server package, so they run in the server's environment.
