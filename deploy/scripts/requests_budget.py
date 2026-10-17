"""The memory the cluster reserves with everything at its minimum, against the node's allocatable memory.

The scheduler places a pod only where its memory request fits, so the requests are the node's real
budget: a request below what the process holds lets a scale-up or a rollout take memory the machine
does not have (24/09/2026), and one far above it leaves autoscaled replicas pending while the memory
sits unused (25/09/2026). This adds up every long-running container of every release as the deploy
renders it (helmfile template), plus the broker's operator (k8s/rabbitmq-operator) and the k3s add-ons,
each deployment at its autoscaler's minimum, and fails when:
  - a long-running container states no memory request (the scheduler would not count it);
  - the sum passes MAX_SHARE of the node's allocatable memory;
  - the read path cannot reach its autoscaler's maximum in what is left.
Jobs, CronJobs and test pods run for minutes and come out of what is left.

  python3 scripts/requests_budget.py <helmfile template output> <kustomize output or kustomization.yaml>
"""

from __future__ import annotations

import sys
from typing import Any

import yaml

# m7i-flex.large (8 GiB) less scripts/on-ops.sh's reservations (512Mi system, 1Gi k3s, eviction at
# 300Mi): the kubelet reported 6082820Ki allocatable on 25/09/2026.
ALLOCATABLE_MI = 6082820 / 1024
# What the minimum may reserve; the rest is for scale-ups (the read path first), one rollout's new
# pods, the models pod reading an image (about 1 GiB above its request for seconds) and the jobs.
MAX_SHARE = 0.85
# k3s add-ons outside helmfile, with the requests of their k3s manifests; traefik, the local-path
# provisioner and the service load balancer ask for no memory, so their working set on 25/09/2026 counts.
K3S_ADDONS_MI = {
    "kube-system/coredns": 70,
    "kube-system/metrics-server": 70,
    "kube-system/traefik, local-path-provisioner, svclb (no memory request; measured)": 48,
}
K3S_ADDONS_CPU_M = 250
READ_PATH = "read"
# The node's vCPUs in millicores: nothing is reserved for CPU (on-ops.sh reserves memory only). The CPU
# requests are what the scheduler counts, so every pod at its autoscaler's maximum must fit, or a
# scale-up stays pending while the node still has cycles to spare.
ALLOCATABLE_CPU_M = 2000


def mebibytes(quantity: str | int | float | None) -> float:
    if quantity is None:
        return 0.0
    text = str(quantity)
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024, "k": 1e3 / 2**20, "M": 1e6 / 2**20, "G": 1e9 / 2**20}
    for suffix, factor in units.items():
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * factor
    return float(text) / 2**20


def memory_request(container: dict[str, Any]) -> str | None:
    return ((container.get("resources") or {}).get("requests") or {}).get("memory")


def millicores(quantity: str | int | float | None) -> float:
    if quantity is None:
        return 0.0
    text = str(quantity)
    return float(text[:-1]) if text.endswith("m") else float(text) * 1000


def pod_cpu(pod: dict[str, Any]) -> float:
    total = sum(millicores(((c.get("resources") or {}).get("requests") or {}).get("cpu")) for c in pod.get("containers", []))
    inits = [millicores(((c.get("resources") or {}).get("requests") or {}).get("cpu")) for c in pod.get("initContainers", []) or []]
    return max([total, *inits])


def pod_request(pod: dict[str, Any], owner: str, missing: list[str]) -> float:
    total = 0.0
    for container in pod.get("containers", []):
        request = memory_request(container)
        if request is None:
            missing.append(f"{owner}/{container['name']}")
        total += mebibytes(request)
    inits = [mebibytes(memory_request(c)) for c in pod.get("initContainers", []) or []]
    return max([total, *inits])


def main(rendered_path: str, operator_path: str) -> int:
    docs = [d for d in yaml.safe_load_all(open(rendered_path)) if isinstance(d, dict) and d.get("kind")]
    operator_docs = [d for d in yaml.safe_load_all(open(operator_path)) if isinstance(d, dict)]
    if operator_docs and operator_docs[0].get("kind") == "Kustomization":
        # Without kubectl: the resources the patch writes into the operator's only container.
        patch = yaml.safe_load(operator_docs[0]["patches"][0]["patch"])[0]
        container = {"name": "operator", "resources": patch["value"]}
        operator_docs = [{"kind": "Deployment", "metadata": {"name": "rabbitmq-cluster-operator",
                          "namespace": "rabbitmq-system"},
                          "spec": {"template": {"spec": {"containers": [container]}}}}]
    docs += [d for d in operator_docs if d.get("kind") == "Deployment"]

    def key(doc: dict[str, Any]) -> tuple[str, str]:
        return doc["metadata"].get("namespace", "niadra"), doc["metadata"]["name"]

    scale: dict[tuple[str, str], tuple[int, int]] = {}
    for doc in docs:
        spec = doc.get("spec") or {}
        namespace = doc["metadata"].get("namespace", "niadra")
        if doc["kind"] == "HorizontalPodAutoscaler":
            scale[(namespace, spec["scaleTargetRef"]["name"])] = (spec.get("minReplicas", 1), spec["maxReplicas"])
        elif doc["kind"] == "ScaledObject":
            scale[(namespace, spec["scaleTargetRef"]["name"])] = (
                spec.get("minReplicaCount", 0), spec.get("maxReplicaCount", 100))

    # The config reloader the Prometheus operator adds beside Prometheus and Alertmanager.
    reloader = 0.0
    for doc in docs:
        if doc["kind"] == "Deployment" and doc["metadata"]["name"].endswith("prometheus-operator"):
            for arg in doc["spec"]["template"]["spec"]["containers"][0].get("args", []):
                if arg.startswith("--config-reloader-memory-request="):
                    reloader = mebibytes(arg.split("=", 1)[1])

    missing: list[str] = []
    cpu_rows: list[tuple[str, int, float]] = []
    rows: list[tuple[str, int, float]] = []
    per_pod: dict[str, float] = {}
    skipped: list[str] = []
    for doc in docs:
        kind, spec = doc["kind"], doc.get("spec") or {}
        namespace, name = key(doc)
        owner = f"{namespace}/{name}"
        if kind in ("Deployment", "StatefulSet"):
            default = spec.get("replicas", 1)
            replicas = scale.get((namespace, name), (default, default))[0]
            request = pod_request(spec["template"]["spec"], owner, missing)
            cpu_rows.append((owner, scale.get((namespace, name), (default, default))[1], pod_cpu(spec["template"]["spec"])))
        elif kind == "DaemonSet":
            replicas, request = 1, pod_request(spec["template"]["spec"], owner, missing)
            cpu_rows.append((owner, 1, pod_cpu(spec["template"]["spec"])))
        elif kind == "RabbitmqCluster":
            replicas = spec.get("replicas", 1)
            request = mebibytes(((spec.get("resources") or {}).get("requests") or {}).get("memory"))
            cpu_rows.append((owner, replicas, millicores(((spec.get("resources") or {}).get("requests") or {}).get("cpu"))))
            if not request:
                missing.append(owner)
        elif kind in ("Prometheus", "Alertmanager"):
            replicas = spec.get("replicas", 1)
            cpu_rows.append((owner, replicas, millicores(((spec.get("resources") or {}).get("requests") or {}).get("cpu"))))
            request = mebibytes(((spec.get("resources") or {}).get("requests") or {}).get("memory"))
            if not request:
                missing.append(owner)
            if not reloader:
                missing.append(f"{owner}/config-reloader")
            request += reloader
        elif kind in ("Job", "CronJob", "Pod"):
            skipped.append(f"{kind} {owner}")
            continue
        else:
            continue
        per_pod[name] = request
        rows.append((owner, replicas, request))

    total = sum(replicas * request for _, replicas, request in rows) + sum(K3S_ADDONS_MI.values())
    limit = MAX_SHARE * ALLOCATABLE_MI
    left = ALLOCATABLE_MI - total

    print(f"    {'workload':<58} {'pods':>4} {'MiB each':>9} {'MiB':>6}")
    for owner, replicas, request in sorted(rows):
        if replicas:
            print(f"    {owner:<58} {replicas:>4} {request:>9.0f} {replicas * request:>6.0f}")
    for owner, request in K3S_ADDONS_MI.items():
        print(f"    {owner:<58} {1:>4} {request:>9.0f} {request:>6.0f}")
    print(f"    at the minimum: {total:.0f} MiB of {ALLOCATABLE_MI:.0f} allocatable "
          f"({total / ALLOCATABLE_MI:.0%}; at most {MAX_SHARE:.0%}), {left:.0f} MiB left")
    print(f"    left out, they run for minutes: {len(skipped)} jobs and test pods")

    problems = []
    if missing:
        problems.append("no memory request: " + ", ".join(sorted(set(missing))))
    if total > limit:
        problems.append(f"the minimum reserves {total:.0f} MiB, more than {MAX_SHARE:.0%} of "
                        f"{ALLOCATABLE_MI:.0f} ({limit:.0f} MiB)")
    low, high = scale.get(("niadra", READ_PATH), (1, 1))
    read_growth = (high - low) * per_pod.get(READ_PATH, 0)
    if read_growth > left:
        problems.append(f"the read path needs {read_growth:.0f} MiB to reach {high} replicas and "
                        f"{left:.0f} MiB is left")
    else:
        print(f"    read path from {low} to {high} replicas: {read_growth:.0f} MiB of the {left:.0f} left")
    # CPU: every deployment at its autoscaler's maximum, plus the k3s add-ons: coredns and metrics-server
    # (100m each, their manifests) and Traefik (50m, the HelmChartConfig scripts/on-ops.sh writes).
    cpu_max = sum(replicas * request for _, replicas, request in cpu_rows) + K3S_ADDONS_CPU_M
    print(f"    CPU requested with every autoscaler at its maximum: {cpu_max:.0f}m of {ALLOCATABLE_CPU_M}m")
    for owner, replicas, request in sorted(cpu_rows, key=lambda r: -r[1] * r[2])[:8]:
        print(f"      {owner:<56} {replicas:>2} x {request:>4.0f}m")
    if cpu_max > ALLOCATABLE_CPU_M:
        problems.append(f"at their maximum the pods request {cpu_max:.0f}m of CPU, more than {ALLOCATABLE_CPU_M}m")
    if problems:
        print("    " + "\n    ".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
