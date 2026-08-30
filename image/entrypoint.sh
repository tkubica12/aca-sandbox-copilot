#!/usr/bin/env bash
set -euo pipefail

/usr/local/bin/ensure-tmux

if (($#)); then
  exec "$@"
fi

exec tail -f /dev/null

