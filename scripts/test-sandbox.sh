#!/usr/bin/env bash
set -euo pipefail

[[ -f .sandbox.env ]] || { echo "Run ./scripts/deploy.sh first" >&2; exit 1; }
# shellcheck disable=SC1091
source .sandbox.env

selector="name=$SANDBOX_LABEL"
marker="volume-ok-$(date +%s)"

aca sandbox exec -l "$selector" -c \
  "ensure-tmux && tmux has-session -t copilot && copilot --version && gh --version && az version --output tsv && terraform version && python --version && pip --version && uv --version && jq --version && yq --version"

aca sandbox exec -l "$selector" -c \
  "printf '%s\n' '$marker' > /mnt/data/sandbox-volume-test"

aca sandbox delete -l "$selector" --yes
aca sandbox create \
  --disk-id "$DISK_IMAGE_ID" \
  --cpu 2000m \
  --memory 4096Mi \
  --entrypoint /usr/local/bin/container-entrypoint \
  --label "$selector"
aca sandbox mount \
  -l "$selector" \
  --volume "$VOLUME_NAME" \
  --path /mnt/data

actual="$(aca sandbox exec -l "$selector" -c "cat /mnt/data/sandbox-volume-test" | tr -d '\r')"
[[ "$actual" == *"$marker"* ]] || {
  echo "Volume persistence failed: expected $marker, got $actual" >&2
  exit 1
}

aca sandbox exec -l "$selector" -c \
  "ensure-tmux && tmux new-window -d -t copilot -n reconnect-test 'sleep 300' && tmux list-windows -t copilot"

echo "All checks passed. Interactive attach:"
echo "  aca sandbox exec -l '$selector' -c 'tmux attach -t copilot'"
