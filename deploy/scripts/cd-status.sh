#!/usr/bin/env bash
# The continuous deploy seen from a laptop: the commits live now, the last set that failed (if any),
# the timer and the end of the last runs' output.
#   scripts/cd-status.sh        (the last 60 lines)
#   scripts/cd-status.sh 300
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
N=${1:-60}
[[ $N =~ ^[0-9]+$ ]] || { echo "usage: $0 [lines]"; exit 1; }

SCRIPT="$(mktemp)"
trap 'rm -f "$SCRIPT"' EXIT
cat > "$SCRIPT" <<REMOTE
echo "deployed commit: \$(cat /var/lib/niadra/deployed 2>/dev/null || echo none yet)"
echo "failed commit:   \$(cat /var/lib/niadra/failed 2>/dev/null || echo none)"
systemctl list-timers niadra-cd.timer --no-pager | head -2
journalctl -u niadra-cd --no-pager -o short-iso -n $N
REMOTE
ops_query "$SCRIPT"
