#!/usr/bin/env bash
# Stops the machine and the database to save cost; everything comes back with scripts/resume.sh.
# While paused the site and the APIs are down; the disk, the fixed address and the backups stay (and
# keep costing a little). AWS starts a stopped database again by itself after 7 days.
#   scripts/pause.sh
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"

aws ec2 stop-instances --instance-ids "$(output InstanceId)" --query 'StoppingInstances[0].CurrentState.Name' --output text
aws rds stop-db-instance --db-instance-identifier "$STACK" --query DBInstance.DBInstanceStatus --output text
