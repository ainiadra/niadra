#!/usr/bin/env bash
# Lists the latest contact requests the site's form stored (database niadra_site). The query runs in
# a one-off pod in the cluster; the output comes straight here, never through a log.
#
# Usage:  scripts/contacts.sh        (the 20 newest)
#         scripts/contacts.sh 100    (the 100 newest)
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
N=${1:-20}
[[ $N =~ ^[0-9]+$ ]] || { echo "usage: $0 [count]"; exit 1; }

SCRIPT="$(mktemp)"
trap 'rm -f "$SCRIPT"' EXIT
cat > "$SCRIPT" <<REMOTE
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=\$PATH:/usr/local/bin
$(site_psql)
psql_site <<'SQL'
select id,
       to_char(created_at at time zone 'UTC', 'YYYY-MM-DD HH24:MI') as at_utc,
       name, email, email_domain as domain,
       case domain_has_mx when true then 'yes' when false then 'no' else '?' end as receives_email,
       company, interest, lang,
       coalesce(nullif(utm_source, ''), split_part(split_part(referrer, '://', 2), '/', 1), 'direct') as source,
       landing_page,
       coalesce(heard_from, '-') as said_they_came_from,
       message
from contact_requests order by id desc limit $N;
SQL
REMOTE
LC_ALL=C.UTF-8 ops_query "$SCRIPT"
