#!/usr/bin/env python3
"""Delete the sandbox deployment."""

from __future__ import annotations

from azure.core.exceptions import ResourceNotFoundError

from common import AzureClients, Config


def main() -> None:
    config = Config.from_env()
    with AzureClients.create(config) as clients:
        if not clients.resources.resource_groups.check_existence(config.resource_group):
            print(f"Resource group {config.resource_group} does not exist.")
            return

        try:
            clients.groups.get_group(config.sandbox_group)
        except ResourceNotFoundError:
            pass
        else:
            print(f"Deleting sandbox group {config.sandbox_group}...")
            clients.groups.begin_delete_group(config.sandbox_group).result()

        print(f"Deleting resource group {config.resource_group}...")
        clients.resources.resource_groups.begin_delete(
            config.resource_group
        ).result()
        print("Cleanup complete.")


if __name__ == "__main__":
    main()
