#!/usr/bin/env bash
set -euo pipefail

/usr/local/bin/ensure-tmux
mkdir -p /mnt/data/scheduler/logs /mnt/data/tasks

for runtime_environment in \
  /mnt/data/scheduler/runtime.json \
  /root/.sandbox-identity.json; do
  if [[ -f "$runtime_environment" ]]; then
    eval "$(jq -r 'to_entries[] | "export \(.key)=\(.value | @sh)"' "$runtime_environment")"
  fi
done

if (($#)); then
  exec "$@"
fi

exec sandbox-task-worker >>/mnt/data/scheduler/worker.log 2>&1
