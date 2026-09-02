#!/usr/bin/env bash
set -euo pipefail

/usr/local/bin/ensure-tmux
mkdir -p /mnt/data/scheduler/logs /mnt/data/tasks

if (($#)); then
  exec "$@"
fi

exec sandbox-task-worker >>/mnt/data/scheduler/worker.log 2>&1
