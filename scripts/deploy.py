#!/usr/bin/env python3
"""Deploy the Copilot CLI sandbox with the official Sandbox Python SDK."""

from __future__ import annotations

from common import (
    PROJECT_LABELS,
    AzureClients,
    Config,
    create_sandbox,
    ensure_data_owner,
    matching_credentials,
    matching_disk_images,
    matching_sandboxes,
    wait_for_data_plane,
)


def main() -> None:
    config = Config.from_env(require_token=True)
    with AzureClients.create(config) as clients:
        print(f"Creating resource group {config.resource_group}...")
        clients.resources.resource_groups.create_or_update(
            config.resource_group, {"location": config.location}
        )

        print(f"Creating sandbox group {config.sandbox_group}...")
        group = clients.groups.begin_create_group(
            config.sandbox_group,
            config.location,
            tags=PROJECT_LABELS,
        ).result()
        if group.location.replace(" ", "").lower() != config.location.lower():
            raise RuntimeError(
                f"Sandbox group exists in {group.location}, not {config.location}."
            )

        print("Granting SandboxGroup Data Owner role...")
        ensure_data_owner(config, clients)
        wait_for_data_plane(clients)

        for sandbox in matching_sandboxes(config, clients):
            print(f"Deleting previous sandbox {sandbox.id}...")
            clients.group.get_sandbox_client(sandbox.id).begin_delete().result()

        for credential in matching_credentials(config, clients):
            print(f"Deleting previous credential {credential['id']}...")
            clients.connections.delete(credential["id"])

        for image in matching_disk_images(config, clients):
            print(f"Deleting previous disk image {image.id}...")
            clients.group.begin_delete_disk_image(image.id).result()

        volumes = {
            volume.name: volume for volume in clients.group.list_volumes()
        }
        if config.volume_name not in volumes:
            print(f"Creating DataDisk volume {config.volume_name}...")
            clients.group.create_volume(
                config.volume_name,
                type="DataDisk",
                size=config.data_disk_size,
                labels=PROJECT_LABELS,
            )
        else:
            print(f"Using existing DataDisk volume {config.volume_name}.")

        print(f"Building disk image from {config.image}...")
        image = clients.group.begin_create_disk_image(
            config.image,
            name=config.disk_name,
            entrypoint=["/usr/local/bin/entrypoint.sh"],
        ).result()

        print("Creating GitHub Copilot provider credential...")
        provider_credential = clients.connections.create_github_copilot(
            config.credential_name, config.token
        )

        print(f"Creating sandbox {config.sandbox_name}...")
        sandbox = create_sandbox(
            config,
            clients,
            disk_id=image.id,
            credential_id=provider_credential["id"],
        )
        print(f"Sandbox ready: {sandbox.sandbox_id}")


if __name__ == "__main__":
    main()
