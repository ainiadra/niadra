#!/usr/bin/env bash
# Which AI crawlers and search engines read the site, and who arrived from an AI answer, from the
# site's access log in CloudWatch Logs (the cell's log group, IP already masked).
#
# Usage:  scripts/ai-crawlers.sh        (last 7 days)
#         scripts/ai-crawlers.sh 30     (last 30 days; the log group keeps 30)
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
DAYS=${1:-7}
[[ $DAYS =~ ^[0-9]+$ ]] || { echo "usage: $0 [days]"; exit 1; }

since=$(( ($(date +%s) - DAYS * 86400) * 1000 ))
aws logs filter-log-events --log-group-name "$(output LogGroup)" --log-stream-name-prefix niadra.site- \
  --start-time "$since" --filter-pattern '"http.log.access"' --query 'events[].message' --output json |
  LC_ALL=C.UTF-8 python3 "$(dirname "$0")/ai_crawlers.py" "$DAYS"
