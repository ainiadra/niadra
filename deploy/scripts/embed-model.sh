#!/usr/bin/env bash
# The embedding model the chart runs (models.embedModel in values.yaml), which on-ops.sh bakes into the
# models image; prints nothing when the chart serves the hashing embedder. check.sh holds this reading
# against the rendered chart.
#   scripts/embed-model.sh
set -euo pipefail
sed -nE 's/^  embedModel: *"?([A-Za-z0-9._-]*)"?.*$/\1/p' "$(dirname "$0")/../k8s/charts/niadra/values.yaml"
