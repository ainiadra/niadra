#!/usr/bin/env bash
# What CI checks in this component (.github/workflows/deploy.yml at the repository root), and what to
# run before a commit: the scripts parse (and pass shellcheck, when it is installed), the chart renders
# both parts on the domain, under sslip.io and as a dedicated cell, the alert rules pass promtool, the Alertmanager routes
# pass amtool, the memory requests of everything fit the node (scripts/requests_budget.py), the stack
# renders the default cell exactly as before (scripts/stack_render.py) and passes cfn-lint (installed, or
# through uvx).
#   scripts/check.sh
set -euo pipefail
INFRA="$(cd "$(dirname "$0")/.." && pwd)"
cd "$INFRA"

echo "==> scripts"
for f in scripts/*.sh; do bash -n "$f"; done
if command -v shellcheck >/dev/null; then
  shellcheck -S warning -x scripts/*.sh
else
  echo "    shellcheck not installed; syntax only"
fi

echo "==> chart"
RENDERED="$(mktemp -d)"
trap 'rm -rf "$RENDERED"' EXIT
render() {
  local part="$1" api="$2" control="$3" zone="$4"
  shift 4
  helm template niadra k8s/charts/niadra --namespace niadra \
    --set part="$part" --set image.registry=registry.example --set image.tags.server=server-check \
    --set image.tags.models=models-check --set image.tags.site=site-check --set image.tags.site-api=site-api-check \
    --set region=us-east-2 --set database.host=db.example --set database.masterSecret=arn:example \
    --set objectStorage.bucket=bucket-example --set tls.route53Zone="$zone" \
    --set 'hosts.site={site.example,www.site.example}' --set "hosts.api={$api}" \
    --set hosts.control="$control" --set hosts.grafana=grafana.example \
    --set keys.kmsKeyId=arn:aws:kms:us-east-2:111122223333:key/example --set audit.bucket=audit-example \
    "$@" >"$RENDERED/$part-${api%%,*}.yaml"
  echo "    $part on ${api%%,*}"
}
# On the domain, and under sslip.io before the domain moves.
for part in base app; do
  render "$part" 'api.niadra.com,*.us-east-2.api.niadra.com' control.api.niadra.com Z0000EXAMPLE
  render "$part" api.1-2-3-4.sslip.io control.1-2-3-4.sslip.io ''
done

echo "==> edge TLS"
# Every host of the edge takes TLS 1.3 or later: the default TLSOption renders in the app part, once,
# and so does every other TLSOption (the mTLS address of the webhook, under a wildcard API host).
for f in "$RENDERED"/app-*.yaml; do
  options="$(grep -c '^kind: TLSOption$' "$f" || true)"
  defaults="$(grep -A3 '^kind: TLSOption$' "$f" | grep -c '^  name: default$' || true)"
  modern="$(grep -c 'minVersion: VersionTLS13' "$f" || true)"
  if [ "$defaults" != 1 ] || [ "$modern" != "$options" ]; then
    echo "    $(basename "$f"): expected one default TLSOption, every TLSOption with minVersion VersionTLS13"
    exit 1
  fi
done
echo "    minVersion VersionTLS13"

echo "==> client certificates"
# The cell trusts a forwarded client certificate only because every route to it drops the header first,
# and only the mTLS address, which asks for a certificate, forwards one.
wild="$RENDERED/app-api.niadra.com.yaml"
if ! grep -A12 '^  name: api$' "$wild" | grep -q 'strip-client-cert@kubernetescrd' ||
  ! grep -A12 '^  name: webhooks-mtls$' "$wild" | grep -q 'strip-client-cert@kubernetescrd,niadra-pass-client-cert@kubernetescrd' ||
  ! grep -q 'host: "mtls.us-east-2.api.niadra.com"' "$wild" ||
  ! grep -A8 '^  name: mtls$' "$wild" | grep -q 'clientAuthType: RequireAnyClientCert'; then
  echo "    the API ingress must drop X-Forwarded-Tls-Client-Cert and only the mTLS address forward it"
  exit 1
fi
if grep -q 'webhooks-mtls' "$RENDERED/app-api.1-2-3-4.sslip.io.yaml"; then
  echo "    without a wildcard API host there is no mTLS address"
  exit 1
fi
echo "    mtls.us-east-2.api.niadra.com asks for a certificate; every other route drops the header"

echo "==> api routes"
# Turns are written on ingest by their whole paths; any deeper path (a turn by id, the search) reaches admin.
api="$RENDERED/app-api.niadra.com.yaml"
for exact in /v1/turns /v1/turns/promote; do
  if ! grep -A40 '^  name: api$' "$api" | grep -q "{path: $exact, pathType: Exact, backend: {service: {name: ingest,"; then
    echo "    $exact must go to ingest as an Exact path"
    exit 1
  fi
done
if grep -A40 '^  name: api$' "$api" | grep -q '{path: /v1/turns, pathType: Prefix'; then
  echo "    /v1/turns as a prefix would take the admin's turn reads to ingest"
  exit 1
fi
echo "    /v1/turns and /v1/turns/promote exact to ingest; the rest of /v1/turns to admin"

echo "==> site admin panel"
# admin.<site host> serves the site's admin panel: the page from the site, only /api/admin from the site API
# (no other API path on that host), with its own certificate; plain HTTP there is redirected too. The
# site's Caddy sends its access log to the site API's UDP port, which the Service exposes.
python3 - "$RENDERED/app-api.niadra.com.yaml" <<'PY'
import sys
import yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
by = {(d["kind"], d["metadata"]["name"]): d for d in docs}
problems = []
admin = by.get(("Ingress", "site-admin"))
if not admin:
    problems.append("no site-admin ingress")
else:
    rules = admin["spec"]["rules"]
    paths = {(p["path"], p["backend"]["service"]["name"]) for r in rules for p in r["http"]["paths"]}
    if [r["host"] for r in rules] != ["admin.site.example"] or paths != {("/api/admin", "site-api"), ("/", "site")}:
        problems.append(f"site-admin routes {sorted(paths)} on {[r['host'] for r in rules]}")
    if admin["spec"]["tls"] != [{"hosts": ["admin.site.example"], "secretName": "tls-site-admin"}]:
        problems.append(f"site-admin tls {admin['spec']['tls']}")
redirect = {r["host"] for r in by[("Ingress", "http-redirect")]["spec"]["rules"]}
if "admin.site.example" not in redirect:
    problems.append("admin.site.example is not redirected to HTTPS")
ports = by[("Service", "site-api")]["spec"]["ports"]
if {"name": "access-log", "port": 8788, "targetPort": "access-log", "protocol": "UDP"} not in ports:
    problems.append(f"site-api service ports {ports}")
env = {e["name"]: e["value"] for e in by[("Deployment", "site")]["spec"]["template"]["spec"]["containers"][0]["env"]}
if env.get("ACCESS_LOG_ADDR") != "site-api:8788" or env.get("ADMIN_HOST") != "admin.site.example":
    problems.append(f"site env {env}")
if problems:
    sys.exit("    " + "; ".join(problems))
PY
echo "    admin.site.example: the page from site, /api/admin from site-api, its own certificate; counter on UDP 8788"

echo "==> measurement"
# A session's measurement lands right after its memory only with a measure worker already running, and
# its delay has an alert for people (severity ticket) on the histogram the workers export.
app="$RENDERED/app-api.niadra.com.yaml"
min="$(awk '/^kind: ScaledObject$/{s=1} s&&/name: workers-measure$/{m=1} m&&/minReplicaCount:/{print $2; exit}' "$app")"
if [ "${min:-0}" -lt 1 ]; then
  echo "    the measure pool scales to ${min:-0}; it needs at least one worker"
  exit 1
fi
if ! grep -A4 'alert: MeasurementReady' "$app" | grep -q 'niadra_measurement_ready_seconds_bucket' ||
  ! grep -A4 'alert: MeasurementReady' "$app" | grep -q 'severity: ticket'; then
  echo "    no ticket alert on niadra_measurement_ready_seconds"
  exit 1
fi
echo "    measure pool at ${min} or more; MeasurementReady alert on the histogram"

echo "==> alert rules"
# The rules as the chart renders them: promtool loads both groups (the objectives' ratios and the alerts),
# the burn-rate alerts of the three objectives page and open tickets, the static p95s are tickets, and so
# is a context read that touches the database. CI installs promtool.
python3 - "$RENDERED/app-api.niadra.com.yaml" "$RENDERED/rules.yaml" <<'PY'
import sys
import yaml
rule = next(d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d["kind"] == "PrometheusRule")
groups = rule["spec"]["groups"]
yaml.safe_dump({"groups": groups}, open(sys.argv[2], "w"), sort_keys=False)
severity = {r["alert"]: r["labels"]["severity"] for g in groups for r in g["rules"] if "alert" in r}
recorded = {r["record"] for g in groups for r in g["rules"] if "record" in r}
want = {f"{slo}BudgetBurn{speed}": level for slo in ("ContextLatency", "BatchLatency", "Availability")
        for speed, level in (("Fast", "page"), ("Slow", "ticket"))}
want |= {"ContextLatencyP95Above100ms": "ticket", "IngestAckP95Above80ms": "ticket", "HotReadTouchedDatabase": "ticket"}
problems = [f"{name} is {severity.get(name)}, expected {level}" for name, level in want.items() if severity.get(name) != level]
ratios = {f"niadra:{sli}:rate{w}" for sli in ("context_slow_ratio", "batch_slow_ratio", "error_ratio")
          for w in ("5m", "30m", "1h", "6h")}
problems += [f"no recording rule {name}" for name in sorted(ratios - recorded)]
# The first read of a conversation runs statements by design: only warm reads may raise the alert.
hot = next((r["expr"] for g in groups for r in g["rules"] if r.get("alert") == "HotReadTouchedDatabase"), "")
if 'read="warm"' not in hot:
    problems.append("HotReadTouchedDatabase must count warm reads only (read=\"warm\")")
if problems:
    sys.exit("    " + "; ".join(problems))
PY
if command -v promtool >/dev/null; then
  promtool check rules "$RENDERED/rules.yaml" | sed -n 's/^  SUCCESS: /    /p'
elif [ -n "${CI:-}" ]; then
  echo "    promtool is required in CI"
  exit 1
else
  echo "    promtool not installed; the rules' syntax is not checked"
fi
echo "    burn rates: page at 14.4 times the budget, ticket at 6; static p95s and HotReadTouchedDatabase (warm reads) tickets"

echo "==> models image"
# on-ops.sh bakes the model embed-model.sh names into the image; the pod has to look in that directory.
model="$(bash scripts/embed-model.sh)"
dir="$(sed -nE 's/.*name: NIADRA_MODELS_ONNX_EMBED_DIR, value: "([^"]*)".*/\1/p' "$RENDERED/app-api.niadra.com.yaml")"
if [ "$dir" != "${model:+/models/$model}" ]; then
  echo "    on-ops.sh bakes '${model:-no model}' but the chart points the pod at '${dir:-no directory}'"
  exit 1
fi
echo "    ${model:-no model: the hashing embedder}"
# With OCR, reading an image takes the pod from about 600 MiB to 1.1 to 1.5 GiB for a moment.
if [ -n "$(bash scripts/models-ocr.sh)" ]; then
  limit="$(awk '/^kind: /{d=($0=="kind: Deployment")} d&&/^  name: models$/{m=1} m&&/limits: \{memory:/{sub(/.*memory: /, ""); sub(/\}.*/, ""); print; exit}' "$RENDERED/app-api.niadra.com.yaml")"
  case "$limit" in
    *Gi) mib=$((${limit%Gi} * 1024)) ;;
    *Mi) mib=${limit%Mi} ;;
    *) mib=0 ;;
  esac
  if [ "$mib" -lt 1792 ]; then
    echo "    OCR on with a memory limit of ${limit:-none}; it needs 1792Mi or more (the 1.5 GiB peak with room)"
    exit 1
  fi
  echo "    OCR on; memory limit $limit"
fi

echo "==> dedicated cell"
# A dedicated cell (scripts/cell.sh) runs only the cell: the control plane, the site, the Console and the
# sandbox tenant stay on the default cell, whose control address it pulls from; the site hosts are only
# the Console's origins.
for part in base app; do
  render "$part" 'niadra-acme.cells.niadra.com,*.us-east-2.api.niadra.com' control.api.niadra.com Z0000EXAMPLE \
    --set 'hosts.site={niadra.com,www.niadra.com}' --set hosts.grafana=grafana.niadra-acme.cells.niadra.com \
    --set controlPlane.url=https://control.api.niadra.com --set cellId=niadra-acme --set sandbox.enabled=false \
    --set secretStore.app=niadra-acme/app --set secretStore.llm=niadra-acme/platform/llm
done
python3 - "$RENDERED/base-niadra-acme.cells.niadra.com.yaml" "$RENDERED/app-niadra-acme.cells.niadra.com.yaml" <<'PY'
import sys
import yaml
docs = [d for path in sys.argv[1:] for d in yaml.safe_load_all(open(path)) if d]
names = {(d["kind"], d["metadata"]["name"]) for d in docs}
problems = [f"{kind} {name} runs on the default cell only" for kind, name in sorted(names) if (kind, name) in {
    ("Deployment", "control"), ("Deployment", "site"), ("Deployment", "site-api"), ("Ingress", "control"),
    ("Ingress", "site"), ("Ingress", "site-admin"), ("ExternalSecret", "niadra-control"), ("ExternalSecret", "niadra-site-api"),
    ("Job", "niadra-sandbox-tenant")}]
config = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "niadra-cell")["data"]
if config["NIADRA_CONTROL_URL"] != "https://control.api.niadra.com" or config["NIADRA_CELL_ID"] != "niadra-acme":
    problems.append("the cell must pull from the default cell's control plane as niadra-acme")
if config["NIADRA_ALLOWED_ORIGINS"] != "https://niadra.com,https://www.niadra.com":
    problems.append("the Console's origins must still reach the cell")
stores = {r["remoteRef"]["key"] for d in docs if d["kind"] == "ExternalSecret" for r in d["spec"]["data"]}
# arn:example is the database master secret this check passes; the cell's own secrets are under its name.
if any(not s.startswith("niadra-acme/") for s in stores - {"arn:example"}):
    problems.append(f"secrets outside niadra-acme/: {sorted(stores)}")
redirect = next(d for d in docs if d["kind"] == "Ingress" and d["metadata"]["name"] == "http-redirect")
hosts = {r["host"] for r in redirect["spec"]["rules"]}
if hosts != {"niadra-acme.cells.niadra.com", "*.us-east-2.api.niadra.com", "grafana.niadra-acme.cells.niadra.com"}:
    problems.append(f"http-redirect serves {sorted(hosts)}")
if problems:
    sys.exit("    " + "; ".join(problems))
PY
echo "    niadra-acme: the cell only, pulling from https://control.api.niadra.com, secrets under niadra-acme/"

echo "==> image tags"
# A release rolls only the pods whose image inputs changed (scripts/image_tags.py): on 28/09/2026 a
# site-only release retagged every image and rolled every back-end pod at once. Releases made of small
# sources and this component's own scripts and values go through image_tags.py as on-ops.sh calls it,
# and the chart renders each outcome: the pod templates that differ are exactly the ones expected.
TAGS_DIR="$RENDERED/image-tags"
release_tree() {
  local root="$TAGS_DIR/$1"
  mkdir -p "$root/server/src" "$root/server/models" "$root/web/src" "$root/deploy/k8s/charts/niadra"
  echo "server = 1" >"$root/server/src/app.py"
  echo "FROM scratch" >"$root/server/models/Dockerfile"
  echo "<h1>site</h1>" >"$root/web/src/index.html"
  cp -R scripts "$root/deploy/scripts"
  cp k8s/charts/niadra/values.yaml "$root/deploy/k8s/charts/niadra/values.yaml"
}
tags_of() {
  local release="$1" live="${2:-}"
  python3 scripts/image_tags.py "$TAGS_DIR/$release" ${live:+--live "$TAGS_DIR/$live"}
}
release_tree live
release_tree front && echo "<p>README</p>" >>"$TAGS_DIR/front/web/src/index.html"
release_tree back && echo "server = 2" >"$TAGS_DIR/back/server/src/app.py"
release_tree chart && echo "# a chart change" >>"$TAGS_DIR/chart/deploy/k8s/charts/niadra/values.yaml"
release_tree model && sed -i.bak -E 's/^(  embedModel: *).*$/\1"another-model"/' "$TAGS_DIR/model/deploy/k8s/charts/niadra/values.yaml"
for release in live front back chart model; do tags_of "$release" >"$TAGS_DIR/$release.tags"; done
# A live release without an `images` file lends no tag.
tags_of chart live >"$TAGS_DIR/chart-no-images.tags"
# Each release carries its tags in its `images` file (on-ops.sh writes it): the live release is the
# site change, and the next release changes the server only.
awk '{print $1, $2, $4}' "$TAGS_DIR/front.tags" >"$TAGS_DIR/front/images"
release_tree next && cp "$TAGS_DIR/front/web/src/index.html" "$TAGS_DIR/next/web/src/index.html" &&
  echo "server = 2" >"$TAGS_DIR/next/server/src/app.py"
tags_of next front >"$TAGS_DIR/back-after-front.tags"
render_tags() {
  local file="$1" args=()
  while read -r name tag _; do args+=(--set "image.tags.$name=$tag"); done <"$TAGS_DIR/$file.tags"
  helm template niadra k8s/charts/niadra --namespace niadra --set part=app --set image.registry=registry.example \
    --set release="$file" --set region=us-east-2 --set database.host=db.example \
    --set database.masterSecret=arn:example --set objectStorage.bucket=bucket-example \
    --set 'hosts.site={site.example}' --set 'hosts.api={api.example}' --set hosts.control=control.example \
    --set hosts.grafana=grafana.example --set keys.kmsKeyId=arn:aws:kms:us-east-2:111122223333:key/example \
    --set audit.bucket=audit-example "${args[@]}" >"$TAGS_DIR/$file.yaml"
}
for release in live front back chart model; do render_tags "$release"; done
python3 - "$TAGS_DIR" <<'PY'
import sys
from pathlib import Path

import yaml

tags_dir = Path(sys.argv[1])
BACK_END = {"admin", "control", "ingest", "insights", "read", "loops", "models"}


def tags(name):
    lines = (tags_dir / f"{name}.tags").read_text().splitlines()
    return {n: (t, s) for n, t, s, _ in (line.split() for line in lines)}


def pods(name):
    """Every pod template of the release: what a rollout compares."""
    found = {}
    for doc in yaml.safe_load_all((tags_dir / f"{name}.yaml").read_text()):
        if doc and doc["kind"] in ("Deployment", "StatefulSet", "DaemonSet"):
            found[doc["metadata"]["name"]] = doc["spec"]["template"]
    return found


def rolled(release, base="live"):
    before, after = pods(base), pods(release)
    return {n for n in after if after[n] != before.get(n)}


problems = []
live, front, back, chart, model = (tags(n) for n in ("live", "front", "back", "chart", "model"))
changed = lambda other: {n for n in live if live[n][0] != other[n][0]}  # noqa: E731
expect = {
    "front": (changed(front), {"site", "site-api"}),
    "back": (changed(back), {"server"}),
    "chart": (changed(chart), set()),
    "model": (changed(model), {"models"}),
}
for release, (got, want) in expect.items():
    if got != want:
        problems.append(f"a {release} change retags {sorted(got)}, expected {sorted(want)}")
if any(not t.startswith("src-") or s != "new" for t, s in live.values()):
    problems.append(f"without a live release every tag is src-<digest> and new: {live}")
if any(s == "kept" for _, s in tags("chart-no-images").values()):
    problems.append("a live release without an images file lends no tag")
after_front = tags("back-after-front")
want = {n: front[n][0] for n in ("models", "site", "site-api")}
if {n: t for n, (t, s) in after_front.items() if s == "kept"} != want or after_front["server"][1] != "new":
    problems.append(f"the images file carries the tags of unchanged images: {after_front}")
servers = {n for n, p in pods("back").items() if "/niadra/server:" in p["spec"]["containers"][0]["image"]}
for release, want in (("front", {"site", "site-api"}), ("back", servers), ("chart", set()), ("model", {"models"})):
    got = rolled(release)
    if got != want:
        problems.append(f"a {release} release rolls {sorted(got)}, expected {sorted(want)}")
if not BACK_END <= servers | {"models"} or {"site", "site-api"} & servers:
    problems.append(f"the server image runs {sorted(servers)}")
if problems:
    sys.exit("    " + "\n    ".join(problems))
print(f"    a site release rolls site and site-api; a back-end one the {len(servers)} server deployments;")
print("    a chart release no pod through its tags; the images file carries the live tags over")
PY

echo "==> alert routes"
# Alertmanager's configuration as the deploy renders it (helmfile, k8s/values/monitoring.yaml.gotmpl):
# amtool checks it, and each severity reaches the receiver the README names. With the topic,
# and with the topic and a webhook. CI installs helmfile and amtool; on a laptop without them the step
# is skipped.
if command -v helmfile >/dev/null && command -v amtool >/dev/null; then
  topic=arn:aws:sns:us-east-2:111122223333:niadra-alerts
  alertmanager_config() {
    local out="$RENDERED/alertmanager-$1.yaml"
    NIADRA_REGION=us-east-2 NIADRA_GRAFANA_HOST=grafana.example NIADRA_ALERTS_TOPIC_ARN="$2" \
      NIADRA_ALERTS_WEBHOOK_URL="$3" helmfile -q -f k8s/helmfile.yaml.gotmpl -l name=monitoring \
      write-values --skip-deps --output-file-template "$RENDERED/values-$1.yaml" >/dev/null
    python3 - "$RENDERED/values-$1.yaml" "$out" <<'PY'
import sys
import yaml
values = yaml.safe_load(open(sys.argv[1]))
yaml.safe_dump(values["alertmanager"]["config"], open(sys.argv[2], "w"), sort_keys=False)
PY
    amtool check-config "$out" >/dev/null
  }
  expect_route() {
    local config="$1" receiver="$2"
    shift 2
    if ! amtool config routes test --config.file="$config" --verify.receivers="$receiver" "$@" >/dev/null; then
      echo "    $* should reach $receiver, and reaches $(amtool config routes test --config.file="$config" "$@")"
      exit 1
    fi
  }
  for variant in topic webhook; do
    case "$variant" in
      topic) alertmanager_config topic "$topic" '' ;;
      webhook) alertmanager_config webhook "$topic" https://hooks.example/alerts ;;
    esac
    config="$RENDERED/alertmanager-$variant.yaml"
    for severity in page critical ticket warning; do
      expect_route "$config" notify alertname=AnyRule severity="$severity"
    done
    # The deploy's and the weekly test alert (on-ops.sh, templates/monitoring.yaml) reach the e-mail.
    expect_route "$config" notify alertname=NiadraDeployCanary severity=ticket release=check
    expect_route "$config" null alertname=AnyRule severity=info
    expect_route "$config" null alertname=AnyRule
    expect_route "$config" null alertname=Watchdog severity=none
    expect_route "$config" null alertname=InfoInhibitor severity=none
    # The receiver behind those routes: the topic, signed in the region; the webhook only when set.
    python3 - "$config" "$variant" "$topic" <<'PY'
import sys
import yaml
config, variant, topic = yaml.safe_load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
notify = next(r for r in config["receivers"] if r["name"] == "notify")
sns = notify.get("sns_configs", [])
hooks = notify.get("webhook_configs", [])
ok = (
    len(sns) == 1
    and sns[0]["topic_arn"] == topic
    and sns[0]["sigv4"] == {"region": "us-east-2"}
    and sns[0]["send_resolved"] is True
    and len(hooks) == (1 if variant == "webhook" else 0)
)
if not ok:
    sys.exit(f"    notify ({variant}): {notify}")
PY
    echo "    $variant: page, critical, ticket and warning to notify; info, Watchdog and no severity to null"
  done
elif [ -n "${CI:-}" ]; then
  echo "    helmfile and amtool are required in CI"
  exit 1
else
  echo "    helmfile or amtool not installed; skipped"
fi

echo "==> memory requests"
# Every release as the deploy renders it (helmfile template), with the broker's operator as kustomize
# builds it: every long-running container asks for memory, the minimum stays within its share of the
# node's allocatable memory, and the read path can reach its maximum (scripts/requests_budget.py).
# Needs helmfile, like the alert routes; kubectl builds the operator's manifest when installed.
if command -v helmfile >/dev/null; then
  NIADRA_REGION=us-east-2 NIADRA_GRAFANA_HOST=grafana.example NIADRA_REGISTRY=registry.example \
    NIADRA_VERSION=check NIADRA_SERVER_TAG=check NIADRA_MODELS_TAG=check NIADRA_SITE_TAG=check \
    NIADRA_SITE_API_TAG=check NIADRA_DB_HOST=db.example NIADRA_DB_MASTER_SECRET=arn:example \
    NIADRA_CELL_BUCKET=bucket-example NIADRA_SITE_HOSTS=site.example NIADRA_API_HOSTS=api.example \
    NIADRA_CONTROL_HOST=control.example NIADRA_ALERTS_TOPIC_ARN=arn:aws:sns:us-east-2:111122223333:niadra-alerts \
    NIADRA_ALERTS_WEBHOOK_URL='' NIADRA_KMS_KEY_ARN=arn:aws:kms:us-east-2:111122223333:key/example \
    NIADRA_AUDIT_BUCKET=audit-example \
    helmfile -q -f k8s/helmfile.yaml.gotmpl template >"$RENDERED/everything.yaml" 2>"$RENDERED/helmfile.log" || {
    cat "$RENDERED/helmfile.log"
    exit 1
  }
  operator=k8s/rabbitmq-operator/kustomization.yaml
  if command -v kubectl >/dev/null; then
    kubectl kustomize k8s/rabbitmq-operator >"$RENDERED/operator.yaml"
    operator="$RENDERED/operator.yaml"
  fi
  python3 scripts/requests_budget.py "$RENDERED/everything.yaml" "$operator"
elif [ -n "${CI:-}" ]; then
  echo "    helmfile is required in CI"
  exit 1
else
  echo "    helmfile not installed; skipped"
fi

echo "==> stack"
# The default cell renders exactly as tests/cluster-default.json, and a dedicated cell's names
# all derive from its own (scripts/stack_render.py, tests/cluster-default.json).
python3 scripts/stack_render.py check
if command -v cfn-lint >/dev/null; then
  cfn-lint aws/cluster.yaml
  echo "    aws/cluster.yaml"
elif command -v uvx >/dev/null; then
  uvx --quiet cfn-lint aws/cluster.yaml
  echo "    aws/cluster.yaml (cfn-lint through uvx)"
else
  echo "    cfn-lint not installed; skipped"
fi
