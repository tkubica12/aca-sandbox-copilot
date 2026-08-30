#!/usr/bin/env bash

create_sandbox() {
  local manifest
  manifest="$(mktemp)"
  trap 'rm -f "$manifest"' RETURN

  cat > "$manifest" <<EOF
diskId: $DISK_IMAGE_ID
resources:
  cpu: $SANDBOX_CPU
  memory: $SANDBOX_MEMORY
  disk: $SANDBOX_DISK
lifecycle:
  autoSuspendPolicy:
    enabled: true
    interval: 600
    mode: Disk
labels:
  name: $SANDBOX_LABEL
entrypoint:
  - /usr/local/bin/container-entrypoint
volumes:
  - volumeName: $VOLUME_NAME
    mountpoint: /mnt/data
    readOnly: false
EOF

  aca sandbox validate --file "$manifest"
  aca sandbox apply --file "$manifest"
}

