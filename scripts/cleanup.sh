#!/usr/bin/env bash
set -euo pipefail

[[ -f .sandbox.env ]] || { echo "No .sandbox.env; nothing to clean" >&2; exit 0; }
# shellcheck disable=SC1091
source .sandbox.env

aca sandbox delete -l "name=$SANDBOX_LABEL" --yes >/dev/null 2>&1 || true
aca sandboxgroup volume delete --name "$VOLUME_NAME" >/dev/null 2>&1 || true
aca sandboxgroup disk delete --id "$DISK_IMAGE_ID" >/dev/null 2>&1 || true
aca sandboxgroup delete -g "$RESOURCE_GROUP" --name "$SANDBOX_GROUP" --yes
az group delete --name "$RESOURCE_GROUP" --yes --no-wait
rm -f .sandbox.env

