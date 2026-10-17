#!/usr/bin/env bash
# Whether the models image carries the OCR engine (models.ocr in values.yaml): prints 1 when it does,
# nothing otherwise, as `--build-arg OCR=` of on-ops.sh expects. check.sh holds this reading against
# the rendered chart.
#   scripts/models-ocr.sh
set -euo pipefail
sed -nE 's/^  ocr: *true *(#.*)?$/1/p' "$(dirname "$0")/../k8s/charts/niadra/values.yaml"
