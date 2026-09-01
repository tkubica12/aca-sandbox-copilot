#!/usr/bin/env python3
"""Run end-to-end checks and verify DataDisk persistence."""

from __future__ import annotations

import secrets

from common import (
    AzureClients,
    Config,
    create_sandbox,
    exec_checked,
    matching_credentials,
    matching_disk_images,
    matching_sandboxes,
)


def main() -> None:
    config = Config.from_env()
    with AzureClients.create(config) as clients:
        sandboxes = matching_sandboxes(config, clients)
        images = matching_disk_images(config, clients)
        credentials = matching_credentials(config, clients)
        if len(sandboxes) != 1 or len(images) != 1 or len(credentials) != 1:
            raise RuntimeError(
                "Expected exactly one project sandbox, disk image, and credential. "
                "Run scripts/deploy.py first."
            )

        sandbox = clients.group.get_sandbox_client(sandboxes[0].id)
        print("Checking installed tools...")
        exec_checked(
            sandbox,
            [
                "bash",
                "-lc",
                "copilot --version && gh --version | head -1 && az version "
                "--query '\"azure-cli\"' -o tsv && terraform version | head -1 "
                "&& python3 --version && uv --version && jq --version && yq --version "
                "&& tmux -V",
            ],
        )

        print("Checking persistent tmux session...")
        exec_checked(
            sandbox,
            [
                "bash",
                "-lc",
                "tmux has-session -t copilot && "
                "test \"$(tmux display-message -p -t copilot '#{session_name}')\" = copilot",
            ],
        )

        marker = f"persistent-{secrets.token_hex(8)}"
        exec_checked(
            sandbox,
            ["bash", "-lc", f"printf '%s' {marker} > /mnt/data/persistence-test"],
        )

        print("Replacing sandbox while preserving DataDisk...")
        sandbox.begin_delete().result()
        sandbox = create_sandbox(
            config,
            clients,
            disk_id=images[0].id,
            credential_id=credentials[0]["id"],
        )

        persisted = exec_checked(
            sandbox, ["cat", "/mnt/data/persistence-test"]
        ).strip()
        if persisted != marker:
            raise RuntimeError("DataDisk marker did not survive sandbox replacement.")
        exec_checked(
            sandbox,
            ["bash", "-lc", "tmux has-session -t copilot"],
        )

        print("Checking authenticated Copilot request...")
        copilot_output = exec_checked(
            sandbox,
            ["copilot", "-p", "Reply exactly: COPILOT_OK"],
        )
        if "COPILOT_OK" not in copilot_output:
            raise RuntimeError(f"Unexpected Copilot response: {copilot_output}")
        print("All sandbox, Copilot, tmux, and DataDisk checks passed.")


if __name__ == "__main__":
    main()
