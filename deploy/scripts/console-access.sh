#!/usr/bin/env bash
# The Console login of the sandbox tenant (company "Niadra", project "Atendimento e cobrança",
# synthetic data), read from Secrets Manager. Printed only in the terminal that runs it; nothing
# goes to a file or a log.
#   - e2e@niadra.com (<cell>/tenant/bootstrap): the account the end-to-end check uses; e-mail,
#     password and the second-factor code of this moment.
#
# Usage:  scripts/console-access.sh
#         scripts/console-access.sh --app   also the otpauth:// address of the test account
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"

echo "Console   https://$(hosts_base)/console"
echo
echo "Test account (the end-to-end check's)"
aws secretsmanager get-secret-value --secret-id "$STACK/tenant/bootstrap" \
  --query SecretString --output text | python3 -c '
import base64, hashlib, hmac, json, struct, sys, time, urllib.parse
d = json.load(sys.stdin)
secret = d["totp_secret"]
def code(t):
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", int(t) // 30), hashlib.sha1).digest()
    o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)
now = time.time()
print("E-mail    " + d["admin_email"])
print("Password  " + d["password"])
print("Code      %s  (valid %d s more; run again after that)" % (code(now), 30 - int(now) % 30))
if "--app" in sys.argv[1:]:
    label = urllib.parse.quote("Niadra:" + d["admin_email"])
    print("App       otpauth://totp/%s?secret=%s&issuer=Niadra" % (label, secret))
' "$@"
