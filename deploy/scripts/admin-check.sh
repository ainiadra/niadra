#!/usr/bin/env bash
# Checks the site's admin panel end to end from a laptop, the way its operator uses it: the page on its
# host with the private headers, the API refusing other hosts and requests without a session, the
# sign-in with the password from Secrets Manager (NIADRA_ADMIN_SECRET, default
# <cell>/admin/founder-password), both tabs with data, and the sign-out. The password goes from the AWS
# CLI into Python's memory and to the sign-in over HTTPS; nothing prints it.
#
# Usage:  NIADRA_DOMAIN=<domain> scripts/admin-check.sh      (admin.<domain>)
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
DOMAIN="${NIADRA_DOMAIN:?set NIADRA_DOMAIN, the domain of the site}"

aws secretsmanager get-secret-value --secret-id "${NIADRA_ADMIN_SECRET:-$STACK/admin/founder-password}" \
  --query SecretString --output text |
    python3 "$(dirname "$0")/admin_check.py" "$DOMAIN"
