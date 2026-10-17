#!/usr/bin/env bash
# Latency of context() as an agent sees it, measured inside the region: POST /v1/context at the
# space's SDK address, through TLS and the edge, with the sandbox tenant's WhatsApp key (synthetic
# data only). Each rate runs 30 s at a constant arrival rate over 20 conversations, the way turns
# arrive, and prints p50, p90, p95, p99 and max, the server's own time (Server-Timing) and the
# delivery paths. The key never leaves the machine.
#   scripts/latency.sh                    10 and 25 calls per second, from a laptop
#   scripts/latency.sh 5,50               other rates
#   scripts/latency.sh --gate <release>   on the machine, after the deploy's check (scripts/on-ops.sh)
# 24/09/2026 on the single m7i-flex.large: p95 45 ms at 10/s and 54 ms at 25/s, p99 under 80 ms.
#
# The gate runs 10 calls per second and fails when the server's p95 passes
# 100 ms, or rises more than 30% and more than 10 ms over the live release's: a few milliseconds between
# two runs of the same release are noise, never a regression. It writes <release>/latency.json: the probe's
# numbers, its window and the p95 the cell's Prometheus recorded for that window, which the next release
# is held to. With no customer traffic the production p95 does not exist; the probe is the measure.
set -euo pipefail
GATE=""
if [ "${1:-}" = --gate ]; then
  GATE="${2:?usage: $0 --gate <release dir>}"
  RATES=10
else
  RATES="${1:-10,25}"
fi
[[ $RATES =~ ^[0-9]+(,[0-9]+)*$ ]] || { echo "usage: $0 [rate,rate...] | --gate <release dir>"; exit 1; }
GATE_P95_MS=100
GATE_RISE=1.3
GATE_RISE_MS=10
PROMETHEUS_URL=http://monitoring-kube-prometheus-prometheus.monitoring:9090

SCRIPT="$(mktemp)"
trap 'rm -f "$SCRIPT"' EXIT
cat > "$SCRIPT" <<REMOTE
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=\$PATH:/usr/local/bin
KEY=\$(kubectl -n niadra get secret niadra-sandbox-bootstrap -o jsonpath='{.data.bootstrap\\.json}' 2>/dev/null | base64 -d | jq -r '.keys.whatsapp' 2>/dev/null || true)
if [ -z "\$KEY" ] || [ "\$KEY" = null ]; then echo "probe-skip: no sandbox key in this cell"; exit 0; fi
kubectl -n niadra exec -i deploy/loops -- env PROBE_KEY="\$KEY" PROBE_RATES="$RATES" PROBE_JSON="${GATE:+1}" \
  PROBE_PROMETHEUS="$PROMETHEUS_URL" python - <<'PROBE'
import asyncio, json, os, statistics, time, uuid
import httpx
key = os.environ["PROBE_KEY"]
parts = key.split("_")  # nia_sk_<live|test>_<region>_<space>_<key_id>_<secret>
region, space = parts[3], parts[4]
base = f"https://{space}.{region}.api.niadra.com"
headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}
subject = {"type": "phone_e164", "value": "+5511900001234"}

def pct(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]

def report(name, total, server, paths):
    print(f"{name}: n={len(total)} p50={pct(total,50):.1f} p90={pct(total,90):.1f} p95={pct(total,95):.1f} p99={pct(total,99):.1f} max={max(total):.1f} ms"
          f" | server p50={pct(server,50):.1f} p95={pct(server,95):.1f} ms | paths={dict((p, paths.count(p)) for p in set(paths))}")

async def call(client, body):
    t = time.perf_counter()
    r = await client.post(f"{base}/v1/context", json=body, headers=headers)
    ms = (time.perf_counter() - t) * 1000
    st = r.headers.get("server-timing", "")
    # The first entry is the server's whole time (app); the read's stages follow it.
    srv = float(st.split("dur=")[1].split(",")[0]) if "dur=" in st else float("nan")
    path = r.json().get("path") if r.status_code == 200 else f"http{r.status_code}"
    return ms, srv, path

async def recorded_p95(start, end):
    """The p95 of the cell's own histogram over the probe's window, once the scraper read its end."""
    await asyncio.sleep(20)
    window = int(end - start) + 15
    query = ('histogram_quantile(0.95, sum by (le) (increase('
             f'niadra_http_request_duration_seconds_bucket{{route="/v1/context"}}[{window}s])))')
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{os.environ['PROBE_PROMETHEUS']}/api/v1/query", params={"query": query, "time": end + 15})
        result = r.json()["data"]["result"]
        value = float(result[0]["value"][1]) if result else float("nan")
        return None if value != value else round(value * 1000, 1)
    except Exception:
        return None

async def main():
    for rate in [int(r) for r in os.environ.get("PROBE_RATES", "10,25").split(",")]:
        results = []
        async with httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=50)) as client:
            convs = [f"rate{rate}-{i}-{uuid.uuid4().hex[:6]}" for i in range(20)]
            for c in convs:
                await call(client, {"subject": subject, "conversation_id": c})
            tasks = []
            started = time.time()
            start = time.perf_counter()
            for n in range(rate * 30):
                target = start + n / rate
                delay = target - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
                async def one(c=convs[n % len(convs)]):
                    results.append(await call(client, {"subject": subject, "conversation_id": c}))
                tasks.append(asyncio.create_task(one()))
            await asyncio.gather(*tasks)
            ended = time.time()
        total, server, paths = [r[0] for r in results], [r[1] for r in results], [r[2] for r in results]
        report(f"{rate} calls/s for 30 s, 20 conversations", total, server, paths)
        if os.environ.get("PROBE_JSON"):
            measured = [s for s in server if s == s]
            print("probe-json: " + json.dumps({
                "rate": rate, "n": len(total), "p95_ms": round(pct(total, 95), 1),
                "server_p50_ms": round(pct(measured, 50), 1) if measured else None,
                "server_p95_ms": round(pct(measured, 95), 1) if measured else None,
                "server_p99_ms": round(pct(measured, 99), 1) if measured else None,
                "paths": dict((p, paths.count(p)) for p in set(paths)),
                "window": {"start": round(started, 3), "end": round(ended, 3)},
                "prometheus_p95_ms": await recorded_p95(started, ended),
            }))
asyncio.run(main())
PROBE
REMOTE

if [ -z "$GATE" ]; then
  # shellcheck source=lib.sh
  source "$(dirname "$0")/lib.sh"
  ops_query "$SCRIPT" | grep -v "^\\s*\$"
  exit 0
fi

# On the machine: the probe runs here, and the live release's numbers are the bar.
OUT="$(bash "$SCRIPT")"
printf '%s\n' "$OUT" | sed -n '/^probe-json: /!s/^/    /p'
if [[ $OUT == *probe-skip* ]]; then
  exit 0
fi
printf '%s\n' "$OUT" | sed -n 's/^probe-json: //p' | tail -n 1 >"$GATE/latency.json"
python3 - "$GATE/latency.json" "$(readlink -f /opt/niadra/current 2>/dev/null || true)" "$GATE_P95_MS" "$GATE_RISE" \
  "$GATE_RISE_MS" <<'PY'
import json, os, sys
mine = json.load(open(sys.argv[1]))
live, ceiling, rise, floor = sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
p95 = mine.get("server_p95_ms")
before = None
if live and os.path.exists(os.path.join(live, "latency.json")):
    before = json.load(open(os.path.join(live, "latency.json"))).get("server_p95_ms")
live_p95 = f"{before} ms" if before is not None else "not measured"
print(f"    server p95 {p95} ms; the live release's {live_p95}; Prometheus over the window"
      f" {mine.get('prometheus_p95_ms')} ms")
if p95 is None:
    sys.exit("    the probe got no server time")
if p95 > ceiling:
    sys.exit(f"    server p95 {p95} ms is over {ceiling:g} ms")
if before and p95 > before * rise and p95 - before > floor:
    sys.exit(f"    server p95 {p95} ms is more than {round((rise - 1) * 100)}% and {floor:g} ms over the live"
             f" release's {before} ms")
PY
