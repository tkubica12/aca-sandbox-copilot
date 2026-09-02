#!/usr/bin/env python3
"""Delete the sandbox deployment."""

from __future__ import annotations

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from common import CONNECTOR_API_VERSION, AzureClients, Config


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
            try:
                print(f"Deleting Connector Namespace {config.connector_namespace}...")
                clients.resources.resources.begin_delete_by_id(
                    config.connector_scope, CONNECTOR_API_VERSION
                ).result()
            except HttpResponseError as error:
                if error.status_code != 404:
                    raise
            print(f"Deleting sandbox group {config.sandbox_group}...")
            clients.groups.begin_delete_group(config.sandbox_group).result()

        print(f"Deleting resource group {config.resource_group}...")
        clients.resources.resource_groups.begin_delete(
            config.resource_group
        ).result()
        print("Cleanup complete.")


if __name__ == "__main__":
    main()
