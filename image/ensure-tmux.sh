#!/usr/bin/env bash
set -euo pipefail

session="${TMUX_SESSION:-copilot}"
workspace="${TMUX_WORKDIR:-/mnt/data}"

mkdir -p "$workspace"
if ! tmux has-session -t "$session" 2>/dev/null; then
  tmux new-session -d -s "$session" -c "$workspace"
fi

