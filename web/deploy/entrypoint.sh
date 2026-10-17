#!/bin/sh
# Start of the site container: writes the Console's /config.json from the environment, works out
# the Console's connect-src (the control plane and each data API) and starts Caddy.
#   SITE_ORIGIN          https://niadra.com
#   NIADRA_CONTROL_URL   https://control.niadra.com
#   NIADRA_API_URL       https://api.niadra.com (or NIADRA_CELLS, see console-config.sh)
#   ADMIN_HOST           admin.niadra.com (default: admin. and the site's host)
#   ACCESS_LOG_ADDR      site-api:8788, the access counter (UDP)
set -eu

: "${SITE_ORIGIN:?set SITE_ORIGIN}"
sh /usr/local/bin/console-config.sh /config/console-config.json

origin() { printf '%s' "$1" | sed -E 's#^(https?://[^/]+).*#\1#'; }
connect="$(origin "$NIADRA_CONTROL_URL")"
[ -n "${NIADRA_API_URL:-}" ] && connect="$connect $(origin "$NIADRA_API_URL")"
old_ifs=$IFS
IFS=','
for pair in ${NIADRA_CELLS:-}; do connect="$connect $(origin "${pair#*=}")"; done
IFS=$old_ifs
export NIADRA_CONNECT_SRC="$connect"
SITE_HOST="$(printf '%s' "$SITE_ORIGIN" | sed -E 's#^https?://([^/:]+).*#\1#')"
export SITE_HOST
# The admin panel's host and the access counter's address (deploy/Caddyfile); the chart sets both.
export ADMIN_HOST="${ADMIN_HOST:-admin.$SITE_HOST}"
export ACCESS_LOG_ADDR="${ACCESS_LOG_ADDR:-site-api:8788}"

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
