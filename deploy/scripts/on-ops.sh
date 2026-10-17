#!/usr/bin/env bash
# Runs on the k3s machine with a release unpacked, started by scripts/deploy.sh (from a laptop) or
# by cd.sh (continuous deploy, from the release branch):
#   on-ops.sh /opt/niadra/releases/<version>
# The environment carries the version, the registry, the stack's outputs and the public hosts.
# Builds and pushes the images whose inputs changed, makes sure the secrets exist, installs or upgrades everything
# with helmfile and runs the end-to-end check; when the check fails, it rolls the deployables back if
# no migration changed (rollback_after_failed_check below).
set -euo pipefail
RELEASE="$1"
: "${NIADRA_VERSION:?}" "${NIADRA_REGISTRY:?}" "${NIADRA_REGION:?}"

# One deploy at a time, whichever way it started.
exec 9>/run/niadra-deploy.lock
flock 9

echo "==> node memory"
# The node keeps memory back for k3s, containerd, the image builds and the system, so the scheduler
# never places pods past what the machine has (a rollout then waits for room instead), and the
# kubelet evicts a pod before the kernel's OOM killer starves k3s. Without it, restarting every
# deployment at once on 24/09/2026 ran the 8 GB machine out of memory and k3s lost its lease.
# On 25/09/2026 the system outside the pods held about 1,475 MiB (k3s 1,289, anonymous memory), so
# the 1.5 GiB stays; the pods get about 5,940 MiB, and scripts/requests_budget.py keeps their requests
# within a share of it.
K3S_CONFIG=/etc/rancher/k3s/config.yaml
cat > "$K3S_CONFIG.new" <<'K3S'
kubelet-arg:
  - "system-reserved=memory=512Mi"
  - "kube-reserved=memory=1Gi"
  - "eviction-hard=memory.available<300Mi,nodefs.available<5%,imagefs.available<5%"
K3S
if cmp -s "$K3S_CONFIG.new" "$K3S_CONFIG"; then
  rm -f "$K3S_CONFIG.new"
else
  mv -f "$K3S_CONFIG.new" "$K3S_CONFIG"
  systemctl restart k3s
  for _ in $(seq 1 60); do kubectl get --raw /readyz >/dev/null 2>&1 && break; sleep 3; done
  kubectl get --raw /readyz >/dev/null
  # The ingress controller can stay unready after the API it watches went away; a new pod syncs.
  kubectl -n kube-system rollout status deployment/traefik --timeout=90s >/dev/null 2>&1 ||
    kubectl -n kube-system delete pod -l app.kubernetes.io/name=traefik >/dev/null
  echo "    k3s restarted with $(kubectl get node -o jsonpath='{.items[0].status.allocatable.memory}') allocatable"
fi

echo "==> ingress"
# The ingress controller k3s ships, configured through its HelmChartConfig. The machine's first boot writes
# it (aws/cluster.yaml); from then on this is its source. Traefik asks for CPU, so it is no longer
# BestEffort: that class shares one weight (cpu.weight 1) against the pods' 59, and on 30/09/2026 a
# temporary cell like production had Traefik waiting 100 to 290 ms per second for a core while the workers
# were busy, which added tens of ms to the p95 of every request through the edge.
# A new configuration makes k3s reinstall Traefik: the edge stops for a few seconds.
TRAEFIK_CONFIG=/var/lib/rancher/k3s/server/manifests/traefik-config.yaml
cat > "$TRAEFIK_CONFIG.new" <<'TRAEFIK'
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata: {name: traefik, namespace: kube-system}
spec:
  valuesContent: |-
    service:
      spec: {externalTrafficPolicy: Local}
    logs:
      access: {enabled: true, format: json}
    resources:
      requests: {cpu: 50m}
TRAEFIK
if cmp -s "$TRAEFIK_CONFIG.new" "$TRAEFIK_CONFIG"; then
  rm -f "$TRAEFIK_CONFIG.new"
else
  mv -f "$TRAEFIK_CONFIG.new" "$TRAEFIK_CONFIG"
  for _ in $(seq 1 60); do
    [ "$(kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik -o jsonpath='{.items[*].status.qosClass}')" = Burstable ] && break
    sleep 5
  done
  kubectl -n kube-system rollout status deployment/traefik --timeout=180s >/dev/null
  echo "    traefik $(kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik -o jsonpath='{.items[*].status.qosClass}')"
fi

# When the end-to-end check fails, the `niadra` release (the deployables) goes back to the revision of
# the release that passed it last (/opt/niadra/current), but only when this release changed no
# migration history (server/migrations: control, cell and space) and no shape of the stored space
# document (server/openapi/space-config.json). `helm rollback` reverts pods and
# configuration, never a schema, and runs no migration (the setup hooks are pre-install and pre-upgrade
# only), so after a migration a rollback would put old code on a schema it never ran against: that
# release is fixed forward instead. The base part (configuration, secrets, broker, pooler) is never rolled
# back. Either way the deploy fails.
rollback_after_failed_check() {
  local live target
  live="$(readlink -f /opt/niadra/current 2>/dev/null || true)"
  if [ -z "$live" ] || [ ! -d "$live/server/migrations" ]; then
    echo "==> no automatic rollback: no live release to compare the migrations with" >&2
    return 0
  fi
  if ! diff -rq -x __pycache__ "$RELEASE/server/migrations" "$live/server/migrations" >/dev/null; then
    echo "==> no automatic rollback: this release changes a migration history (server/migrations)" >&2
    return 0
  fi
  # The space documents the control plane stores in the cells are like a schema: a release that changed
  # their shape may have written documents the live release's servers cannot read (03/10/2026: a new field
  # in every stored document, and the rolled-back cells dropped those spaces). Fixed forward, never rolled
  # back.
  if ! cmp -s "$RELEASE/server/openapi/space-config.json" "$live/server/openapi/space-config.json"; then
    echo "==> no automatic rollback: this release changes the stored space document (server/openapi/space-config.json)" >&2
    return 0
  fi
  # The revision that passed the check with it (on-ops.sh records it).
  target="$(cat "$live/helm-revision" 2>/dev/null || true)"
  if [ -z "$target" ]; then
    echo "==> no automatic rollback: $(basename "$live") recorded no helm revision" >&2
    return 0
  fi
  echo "==> no migration changed: rolling niadra back to revision $target ($(basename "$live"))"
  if helm rollback niadra "$target" -n niadra --wait --timeout 15m; then
    echo "==> rolled back; this deploy still counts as failed"
  else
    echo "==> the rollback failed too: look at \`helm history niadra -n niadra\`" >&2
  fi
}

echo "==> images $NIADRA_VERSION"
aws ecr get-login-password --region "$NIADRA_REGION" | docker login -u AWS --password-stdin "$NIADRA_REGISTRY" >/dev/null
# Each image is tagged by what it is built of, never by the release (scripts/image_tags.py): a release
# that changes one component rolls only the pods of that component's images, and one that changes
# only the chart rolls what the chart changed. A tag the registry already holds is not built again.
build() {
  local name="$1" tag="$2" ref
  shift 2
  ref="$NIADRA_REGISTRY/niadra/$name:$tag"
  if docker manifest inspect "$ref" >/dev/null 2>&1; then
    echo "    niadra/$name:$tag (in the registry)"
    return
  fi
  docker build -q -t "$ref" "$@" >/dev/null
  docker push -q "$ref" >/dev/null
  echo "    niadra/$name:$tag (built)"
}
SITE_FLAG=()
# A dedicated cell (NIADRA_CONTROL_URL set) runs no site: the default cell builds and serves it.
[ -z "${NIADRA_CONTROL_URL:-}" ] || SITE_FLAG=(--no-site)
TAGS="$(python3 "$RELEASE/deploy/scripts/image_tags.py" "$RELEASE" --live /opt/niadra/current "${SITE_FLAG[@]}")"
declare -A TAG
: >"$RELEASE/images"
while read -r name tag state digest; do
  TAG[$name]="$tag"
  # What the next release compares its images with, once this one is live (image_tags.py).
  echo "$name $tag $digest" >>"$RELEASE/images"
  if [ "$state" = kept ]; then
    echo "    niadra/$name:$tag (unchanged since the live release)"
    continue
  fi
  case "$name" in
    server) build server "$tag" "$RELEASE/server" ;;
    # The embedding model the chart runs, downloaded and checked while the image builds, never by a
    # pod; and the OCR engine, when the chart asks for it (models.ocr), which fails the build if it
    # cannot load. image_tags.py reads the same two scripts, so a change to either is a new tag.
    models)
      build models "$tag" --build-arg EMBED_MODEL="$(bash "$RELEASE/deploy/scripts/embed-model.sh")" \
        --build-arg OCR="$(bash "$RELEASE/deploy/scripts/models-ocr.sh")" "$RELEASE/server/models"
      ;;
    site | site-api) build "$name" "$tag" --target "$name" "$RELEASE/web" ;;
  esac
done <<<"$TAGS"
export NIADRA_SERVER_TAG="${TAG[server]}" NIADRA_MODELS_TAG="${TAG[models]}"
export NIADRA_SITE_TAG="${TAG[site]:-}" NIADRA_SITE_API_TAG="${TAG[site-api]:-}"
# The images live in ECR; the local copies only serve the next build. A deploy can leave four new
# ones, and on 24/09 a day of deploys filled the disk: the kubelet evicted the cell's pods. So every
# image goes once pushed, and the build cache keeps at most 3 GB.
docker image prune -af >/dev/null
docker builder prune -af --keep-storage 3gb >/dev/null

echo "==> secrets"
bash "$RELEASE/deploy/scripts/bootstrap-secrets.sh" "${NIADRA_SECRETS_PREFIX:-niadra}/app"
/usr/local/bin/niadra-ecr-login >/dev/null

# The base part of the chart writes the Grafana password into the monitoring namespace.
for namespace in niadra monitoring; do
  kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done

cd "$RELEASE/deploy/k8s"
# The broker operator's manifest issues its webhook certificate through cert-manager.
echo "==> cert-manager"
helmfile -l name=cert-manager apply --suppress-diff --skip-diff-on-install

# The cluster resolves its own domain through public resolvers. The VPC resolver keeps a zone's old
# delegation for up to two days after the nameservers move (it answered NXDOMAIN for api.<domain>
# from the registrar's zone after the switch to Route 53), and the end-to-end check calls the
# public addresses from inside the cluster.
if [ -n "${NIADRA_DOMAIN:-}" ]; then
  echo "==> DNS for $NIADRA_DOMAIN"
  dns="$(kubectl apply -f - <<YAML
apiVersion: v1
kind: ConfigMap
metadata: {name: coredns-custom, namespace: kube-system}
data:
  niadra.server: |
    $NIADRA_DOMAIN:53 {
      forward . 1.1.1.1 8.8.8.8
      cache 30
    }
YAML
)"
  if [[ $dns != *unchanged* ]]; then
    kubectl -n kube-system rollout restart deployment/coredns
    kubectl -n kube-system rollout status deployment/coredns --timeout=120s
  fi
fi

echo "==> broker operator"
# The released manifest with our resources (k8s/rabbitmq-operator): the release asks for 500Mi.
kubectl apply --server-side -k rabbitmq-operator >/dev/null
kubectl -n rabbitmq-system rollout status deployment/rabbitmq-cluster-operator --timeout=300s

echo "==> helmfile"
helmfile apply --suppress-diff --skip-diff-on-install
# helmfile diffs against the last release, not the cluster: a Deployment deleted by hand (reset-spaces.sh
# deletes `loops`) stays gone when the release did not change. Apply each missing one from the manifest,
# server-side as helm's own field manager: a client-side apply would own its fields, and the next helm
# upgrade would fail on the conflict (05/10).
for d in $(helm get manifest niadra -n niadra | awk '/^kind: Deployment$/{k=1;next} k&&/^  name: /{print $2;k=0}'); do
  kubectl -n niadra get deploy "$d" >/dev/null 2>&1 && continue
  echo "    $d missing: applied again from the release's manifest"
  helm get manifest niadra -n niadra | python3 -c '
import sys
name = sys.argv[1]
for doc in sys.stdin.read().split("\n---"):
    if "\nkind: Deployment" in doc and f"\n  name: {name}\n" in doc:
        print("---" + doc)' "$d" | kubectl -n niadra apply --server-side --field-manager=helm -f - >/dev/null
done

# The check calls the public addresses: on a new host name (the switch to the domain), the
# certificates are still being issued and the ingress answers with its self-signed one.
echo "==> certificates"
kubectl -n niadra wait certificate --all --for=condition=Ready --timeout=10m

echo "==> end-to-end check"
if ! helm test niadra -n niadra --timeout 15m --logs; then
  rollback_after_failed_check
  exit 1
fi

# The read's latency gate: the in-region probe after the check; a server
# p95 over 100 ms, or 30% and 10 ms over the live release's, fails the deploy as the check does. Its numbers stay in
# the release directory ($RELEASE/latency.json), the next release's bar. A release that changes the compiler or the
# template version compiles every pack again right after it goes live (and a new prompt re-extracts), so the first
# probe can land on that load: a failed probe is run once more 2 minutes later, both runs in this log, and only a
# second failure fails the deploy.
echo "==> latency"
if ! bash "$RELEASE/deploy/scripts/latency.sh" --gate "$RELEASE"; then
  echo "    latency gate: run 1 failed; run 2 in 120 s (a release that recompiles every pack loads the cell right after it goes live)"
  sleep 120
  echo "==> latency (run 2)"
  if ! bash "$RELEASE/deploy/scripts/latency.sh" --gate "$RELEASE"; then
    echo "    latency gate: run 2 failed too"
    rollback_after_failed_check
    exit 1
  fi
  echo "    latency gate: run 2 passed"
fi

# A test alert through the cell's Alertmanager to the alert e-mail: a ticket with this release, which
# ends after 5 minutes (a weekly job repeats it, templates/monitoring.yaml). Not a check: nothing here can
# read the inbox, and a deploy never fails for it.
echo "==> alert canary"
alertmanager="$(kubectl -n monitoring get pod -l app.kubernetes.io/name=alertmanager -o name 2>/dev/null | head -n 1)"
if [ -n "$alertmanager" ] && kubectl -n monitoring exec "$alertmanager" -c alertmanager -- amtool alert add \
  NiadraDeployCanary severity=ticket "release=$NIADRA_VERSION" \
  --annotation="summary=\"Deploy test alert: $NIADRA_VERSION is live and the path to the alert e-mail works. It ends by itself in 5 minutes.\"" \
  --start="$(date -u +%Y-%m-%dT%H:%M:%SZ)" --end="$(date -u -d '+5 minutes' +%Y-%m-%dT%H:%M:%SZ)" \
  --alertmanager.url=http://localhost:9093 >/dev/null; then
  echo "    NiadraDeployCanary (ticket, release $NIADRA_VERSION) until $(date -u -d '+5 minutes' +%H:%MZ)"
else
  echo "    the test alert could not be added; alerting itself is unchanged" >&2
fi

# The revision that passed, for the rollback of a later release (helm skips an upgrade that renders
# nothing new, so it can be older than this release).
helm history niadra -n niadra --max 1 -o json | python3 -c 'import json, sys; print(json.load(sys.stdin)[-1]["revision"])' \
  >"$RELEASE/helm-revision" || rm -f "$RELEASE/helm-revision"
ln -sfn "$RELEASE" /opt/niadra/current

# Continuous deploy (cd.sh): the environment of this deploy, without the version and the image tags
# (every deploy works them out), for the next one;
# the script itself, replaced by a rename so a run in progress keeps reading its own copy; and the
# timer. The cached deploy key goes, so a rotation in Secrets Manager takes effect on the next run.
# Images of earlier releases that no pod runs any more, and releases older than the last five.
k3s crictl rmi --prune >/dev/null 2>&1 || true
find /opt/niadra/releases -mindepth 1 -maxdepth 1 -type d | sort | head -n -5 | xargs -r rm -rf
echo "    disk: $(df -h / | awk 'NR==2 {print $5}') used"

echo "==> continuous deploy"
mkdir -p /etc/niadra
env | grep -E '^NIADRA_' | grep -vE '^NIADRA_(VERSION|[A-Z_]+_TAG)=' | sort | sed -E "s/^([A-Z0-9_]+)=(.*)$/export \1='\2'/" > /etc/niadra/deploy.env.new
chmod 600 /etc/niadra/deploy.env.new && mv -f /etc/niadra/deploy.env.new /etc/niadra/deploy.env
install -m 0755 "$RELEASE/deploy/scripts/cd.sh" /opt/niadra/cd.sh.new && mv -f /opt/niadra/cd.sh.new /opt/niadra/cd.sh
cat > /etc/systemd/system/niadra-cd.service <<'UNIT'
[Unit]
Description=Niadra continuous deploy (the release branch on GitHub)
After=network-online.target k3s.service
[Service]
Type=oneshot
ExecStart=/bin/bash /opt/niadra/cd.sh
TimeoutStartSec=2h
UNIT
cat > /etc/systemd/system/niadra-cd.timer <<'UNIT'
[Unit]
Description=Look for new release commits a minute after the last look ends
[Timer]
OnBootSec=2min
OnUnitInactiveSec=1min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now niadra-cd.timer >/dev/null
rm -f /etc/niadra/github/deploy
ls -1dt /opt/niadra/releases/* | tail -n +6 | xargs -r rm -rf
echo "==> deployed $NIADRA_VERSION"
