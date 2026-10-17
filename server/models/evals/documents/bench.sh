#!/bin/bash
# The document reader's eval inside the models image, as production builds it (`--build-arg OCR=1`):
# one document per container, no network, 2 CPUs like the cell's node, ONNX on 2 threads like values.yaml.
# Appends one JSON line per document to results/widened.jsonl; then run score.py.
#
#   python generate.py [--stress]      (see its docstring)
#   ./bench.sh [files...]              (default: every file in testset/)
#   python score.py --require-keys 76 --require-rows 18
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
image="${NIADRA_EVAL_IMAGE:-niadra-models:document-eval}"
if [ -z "${NIADRA_EVAL_NO_BUILD:-}" ]; then
  docker build -q -t "$image" --build-arg OCR=1 "$here/../.." >/dev/null
fi
mkdir -p "$here/results"
if [ "$#" -gt 0 ]; then files=("$@"); else
  files=()
  while IFS= read -r f; do files+=("$f"); done < <(cd "$here/testset" && ls | grep -v ground_truth)
fi
for f in "${files[@]}"; do
  docker run --rm --network none --cpus=2 --memory=2g -e NIADRA_MODELS_ONNX_THREADS=2 \
    -v "$here/testset:/data:ro" -v "$here/run_one.py:/eval/run_one.py:ro" \
    "$image" python /eval/run_one.py "/data/$f" 2>"$here/results/$f.log" \
    | sed -n 's/^@@RESULT@@//p' >> "$here/results/widened.jsonl"
  echo "$f done"
done
