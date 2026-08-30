#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"

command -v az >/dev/null || { echo "az CLI is required" >&2; exit 1; }
command -v aca >/dev/null || { echo "aca CLI is required: curl -fsSL https://aka.ms/aca-cli-install | sh" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-copilot-sandbox}"
LOCATION="${LOCATION:-westus2}"
SANDBOX_GROUP="${SANDBOX_GROUP:-copilot-sandbox-group}"
VOLUME_NAME="${VOLUME_NAME:-copilot-data}"
VOLUME_SIZE="${VOLUME_SIZE:-1Gi}"
DISK_NAME="${DISK_NAME:-copilot-cli}"
SANDBOX_LABEL="${SANDBOX_LABEL:-copilot-cli}"
SANDBOX_CPU="${SANDBOX_CPU:-2000m}"
SANDBOX_MEMORY="${SANDBOX_MEMORY:-4096Mi}"
SANDBOX_DISK="${SANDBOX_DISK:-20480Mi}"
IMAGE="${IMAGE:-ghcr.io/tkubica12/aca-sandbox-copilot:latest}"
SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

if ! aca sandboxgroup get -g "$RESOURCE_GROUP" --name "$SANDBOX_GROUP" >/dev/null 2>&1; then
  aca sandboxgroup create \
    -g "$RESOURCE_GROUP" \
    --name "$SANDBOX_GROUP" \
    --location "$LOCATION" \
    -s "$SUBSCRIPTION_ID" \
    --set-config
else
  aca config set \
    --subscription "$SUBSCRIPTION_ID" \
    --resource-group "$RESOURCE_GROUP" \
    --region "$LOCATION"
  aca config sandbox set \
    --subscription "$SUBSCRIPTION_ID" \
    --resource-group "$RESOURCE_GROUP" \
    --region "$LOCATION" \
    --group "$SANDBOX_GROUP"
fi

aca doctor

if ! aca sandboxgroup volume list -o json | jq -e --arg name "$VOLUME_NAME" '.[] | select(.volumeName == $name)' >/dev/null; then
  aca sandboxgroup volume create \
    --name "$VOLUME_NAME" \
    --type DataDisk \
    --size "$VOLUME_SIZE"
fi

disk_id="$(
  aca sandboxgroup disk list -l "project=aca-sandbox-copilot" -o json \
    | jq -r '.[0].id // empty' \
    | head -n 1
)"
if [[ -z "$disk_id" ]]; then
  disk_id="$(
    aca sandboxgroup disk create \
      --image "$IMAGE" \
      --name "$DISK_NAME" \
      --label project=aca-sandbox-copilot \
      -o json \
      | jq -r '.id // .diskImage.id'
  )"
fi

aca sandbox delete -l "name=$SANDBOX_LABEL" --yes >/dev/null 2>&1 || true
DISK_IMAGE_ID="$disk_id"
create_sandbox

cat > .sandbox.env <<EOF
RESOURCE_GROUP=$RESOURCE_GROUP
LOCATION=$LOCATION
SANDBOX_GROUP=$SANDBOX_GROUP
VOLUME_NAME=$VOLUME_NAME
VOLUME_SIZE=$VOLUME_SIZE
DISK_NAME=$DISK_NAME
DISK_IMAGE_ID=$disk_id
SANDBOX_LABEL=$SANDBOX_LABEL
SANDBOX_CPU=$SANDBOX_CPU
SANDBOX_MEMORY=$SANDBOX_MEMORY
SANDBOX_DISK=$SANDBOX_DISK
IMAGE=$IMAGE
SUBSCRIPTION_ID=$SUBSCRIPTION_ID
EOF

echo "Sandbox ready. Run: ./scripts/test-sandbox.sh"
