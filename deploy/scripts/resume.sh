#!/usr/bin/env bash
# Starts the database and the machine again after scripts/pause.sh, and waits until both are up.
# k3s brings every pod back by itself; no deploy is needed.
#   scripts/resume.sh
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"

aws rds start-db-instance --db-instance-identifier "$STACK" --query DBInstance.DBInstanceStatus --output text
aws ec2 start-instances --instance-ids "$(output InstanceId)" --query 'StartingInstances[0].CurrentState.Name' --output text
aws rds wait db-instance-available --db-instance-identifier "$STACK"
aws ec2 wait instance-status-ok --instance-ids "$(output InstanceId)"
echo "up; the pods take a few minutes to be ready"
