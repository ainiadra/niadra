#!/usr/bin/env bash
# IndexNow: tells Bing, Yandex, Naver, Seznam and Yep which pages changed since the last notice.
# Bing's index is what Copilot uses and one of the sources of ChatGPT's search.
# Runs after a deploy to niadra.com (deploy/scripts/deploy.sh at the monorepo root calls it); builds the site
# locally to know each page's hash.
#   deploy/indexnow.sh
set -euo pipefail
cd "$(dirname "$0")/.."

DOMAIN=niadra.com
# The key is public by design (public/2cb4b61fc760b200811620abd9314331.txt).
KEY=2cb4b61fc760b200811620abd9314331
STATE=deploy/indexnow-state.json

npm run build >/dev/null
# Only pages that changed or left since the last notice, as the protocol asks. The state file keeps
# the hash of every page already announced, and is updated only when IndexNow confirms receipt.
urls=$(python3 - "$STATE" <<'PY'
import json, os, sys
now = json.load(open('dist-ssr/hashes.json'))
state = json.load(open(sys.argv[1])) if os.path.exists(sys.argv[1]) else {}
print(json.dumps([u for u, h in now.items() if state.get(u) != h] + [u for u in state if u not in now]))
PY
)
if [[ $urls == "[]" ]]; then
  echo "IndexNow: no page changed since the last notice"
  exit 0
fi
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST https://api.indexnow.org/indexnow \
  -H 'Content-Type: application/json; charset=utf-8' \
  -d "{\"host\":\"$DOMAIN\",\"key\":\"$KEY\",\"keyLocation\":\"https://$DOMAIN/$KEY.txt\",\"urlList\":$urls}")
count=$(python3 -c 'import json,sys; print(len(json.loads(sys.argv[1])))' "$urls")
echo "IndexNow: $code for $count page(s) (200 or 202 = received)"
if [[ $code == 200 || $code == 202 ]]; then
  cp dist-ssr/hashes.json "$STATE"
  echo "commit $STATE so the next notice only carries new changes"
fi
