# shellcheck shell=bash
# Shared by the scripts that run on the operator's computer. Needs the AWS CLI, signed in:
#   aws login --profile niadra
# NIADRA_PROFILE, NIADRA_REGION and NIADRA_STACK change the defaults below. NIADRA_STACK names the cell:
# `niadra`, the default cell, or a dedicated one (`niadra-<name>`, scripts/cell.sh).

export AWS_PROFILE="${NIADRA_PROFILE:-niadra}"
export AWS_REGION="${NIADRA_REGION:-us-east-2}"
STACK="${NIADRA_STACK:-niadra}"
INFRA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(dirname "$INFRA")"
export ROOT

# One output of the cluster stack. Empty, never "None", when the stack does not have it (a conditional
# output such as CellHost).
output() {
  stack_output "$STACK" "$1"
}

# The same, of a named stack (scripts/cell.sh reads the default cell's and a dedicated cell's).
stack_output() {
  local value
  value="$(aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text)" || return
  if [ "$value" = None ]; then value=""; fi
  echo "$value"
}

# Runs a shell script on the k3s machine through Systems Manager (no SSH port is open). Fails if the
# script fails. Two ways to see its output:
#   on_ops <script file> [timeout seconds]     followed live, and kept in CloudWatch Logs
#   ops_query <script file>                    printed at the end, never written to a log (for
#                                              output with personal data, like contact requests)
_send() {
  local script="$1" timeout="$2" logs="$3"
  aws ssm send-command --instance-ids "$(output InstanceId)" --document-name AWS-RunShellScript \
    --timeout-seconds 600 \
    ${logs:+--cloud-watch-output-config "CloudWatchOutputEnabled=true,CloudWatchLogGroupName=$(output LogGroup)"} \
    --parameters "$(python3 -c '
import json, sys
# The agent runs commands with sh (dash on Ubuntu); the scripts are bash.
body = "bash <<\"__NIADRA_SCRIPT__\"\n" + open(sys.argv[1]).read() + "\n__NIADRA_SCRIPT__\n"
print(json.dumps({"commands": [body], "executionTimeout": [sys.argv[2]]}))' "$script" "$timeout")" \
    --query Command.CommandId --output text
}

_wait() {
  local command="$1" instance state
  instance="$(output InstanceId)"
  while true; do
    sleep 5
    state="$(aws ssm get-command-invocation --command-id "$command" --instance-id "$instance" \
      --query Status --output text 2>/dev/null || echo Pending)"
    case "$state" in Pending | InProgress | Delayed) ;; *) break ;; esac
  done
  echo "$state"
}

on_ops() {
  local command tail state
  command="$(_send "$1" "${2:-3600}" yes)"
  aws logs tail "$(output LogGroup)" --log-stream-name-prefix "$command/" --follow --format short &
  tail=$!
  state="$(_wait "$command")"
  # The agent ships output to CloudWatch in batches; give the last one time to arrive.
  sleep 20
  kill "$tail" 2>/dev/null || true
  wait "$tail" 2>/dev/null || true
  echo "machine: $state"
  if [ "$state" != Success ]; then
    aws ssm get-command-invocation --command-id "$command" --instance-id "$(output InstanceId)" \
      --query StandardErrorContent --output text | tail -n 40
    return 1
  fi
}

ops_query() {
  local command state
  command="$(_send "$1" 600 "")"
  state="$(_wait "$command")"
  aws ssm get-command-invocation --command-id "$command" --instance-id "$(output InstanceId)" \
    --query '[StandardOutputContent,StandardErrorContent]' --output text
  [ "$state" = Success ]
}

# For a script run by on_ops or ops_query: the definition of `psql_site`, which runs the SQL on its
# standard input against the site's database (the credentials come from the site API's secret), in
# a one-off pod. `kubectl run -i` loses the attach to a short-lived pod when run through SSM, so the
# pod waits and the SQL goes in through `kubectl exec -i`; the pod ends by itself after 10 minutes.
site_psql() {
  cat <<'KUBECTL'
psql_site() {
  local pod="psql-$RANDOM" rc=0
  kubectl -n niadra run "$pod" --quiet --restart=Never --image=postgres:17-alpine \
    --overrides='{"spec":{"activeDeadlineSeconds":600,"containers":[{"name":"psql","image":"postgres:17-alpine",
      "command":["sleep","600"],"envFrom":[{"secretRef":{"name":"niadra-site-api"}}],
      "securityContext":{"runAsUser":70,"runAsNonRoot":true,"allowPrivilegeEscalation":false}}]}}' >/dev/null
  kubectl -n niadra wait pod "$pod" --for=condition=Ready --timeout=120s >/dev/null
  kubectl -n niadra exec -i "$pod" -- \
    sh -c 'psql "${DATABASE_URL%%\?*}?sslmode=require" -v ON_ERROR_STOP=1 -P pager=off -f -' || rc=$?
  kubectl -n niadra delete pod "$pod" --wait=false >/dev/null
  return "$rc"
}
KUBECTL
}

# The public names: the domain once its nameservers point at the zone, until then names under
# sslip.io that resolve to the machine's fixed address.
hosts_base() {
  if [ -n "${NIADRA_DOMAIN:-}" ]; then
    echo "$NIADRA_DOMAIN"
  else
    local address
    address="$(output PublicAddress)"
    echo "${address//./-}.sslip.io"
  fi
}
