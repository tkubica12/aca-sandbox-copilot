#!/usr/bin/env python3
"""Deploy the Copilot CLI sandbox with the official Sandbox Python SDK."""

from __future__ import annotations
from azure.mgmt.servicebus.models import (
    SBAuthorizationRule,
    SBNamespace,
    SBQueue,
    SBSku,
)

from common import (
    DATA_OWNER_ROLE_ID,
    PROJECT_LABELS,
    SERVICE_BUS_RECEIVER_ROLE_ID,
    SERVICE_BUS_SENDER_ROLE_ID,
    AzureClients,
    Config,
    configure_connector,
    create_connector_namespace,
    create_sandbox,
    ensure_data_owner,
    ensure_role_assignment,
    identity_principal_id,
    matching_disk_images,
    matching_sandboxes,
    wait_for_data_plane,
    write_runtime_environment,
)
from agentmail_bridge import configure_agentmail, prepare_agentmail


def main() -> None:
    config = Config.from_env()
    with AzureClients.create(config) as clients:
        print(f"Creating resource group {config.resource_group}...")
        clients.resources.resource_groups.create_or_update(
            config.resource_group,
            {
                "location": config.location,
                "tags": {**PROJECT_LABELS, "SecurityControl": "ignore"},
            },
        )

        print(f"Creating Service Bus namespace {config.service_bus_namespace}...")
        service_bus = clients.servicebus.namespaces.begin_create_or_update(
            config.resource_group,
            config.service_bus_namespace,
            SBNamespace(
                location=config.location,
                sku=SBSku(name="Basic", tier="Basic"),
                disable_local_auth=False,
                tags=PROJECT_LABELS,
            ),
        ).result()
        if service_bus.disable_local_auth:
            raise RuntimeError(
                "Service Bus local authentication remains disabled. The Connector "
                "Namespace preview connector requires a connection string."
            )
        clients.servicebus.queues.create_or_update(
            config.resource_group,
            config.service_bus_namespace,
            config.service_bus_queue,
            SBQueue(),
        )
        clients.servicebus.namespaces.create_or_update_authorization_rule(
            config.resource_group,
            config.service_bus_namespace,
            "connector-listen",
            SBAuthorizationRule(rights=["Listen"]),
        )
        clients.servicebus.namespaces.create_or_update_authorization_rule(
            config.resource_group,
            config.service_bus_namespace,
            "test-send",
            SBAuthorizationRule(rights=["Send"]),
        )
        connector_keys = clients.servicebus.namespaces.list_keys(
            config.resource_group,
            config.service_bus_namespace,
            "connector-listen",
        )
        if not connector_keys.primary_connection_string:
            raise RuntimeError("Service Bus connector rule returned no connection string.")

        print(f"Creating sandbox group {config.sandbox_group}...")
        group = clients.groups.begin_create_group(
            config.sandbox_group,
            config.location,
            identity={"type": "SystemAssigned"},
            tags=PROJECT_LABELS,
        ).result()
        if group.location.replace(" ", "").lower() != config.location.lower():
            raise RuntimeError(
                f"Sandbox group exists in {group.location}, not {config.location}."
            )

        print("Granting SandboxGroup Data Owner role...")
        ensure_data_owner(config, clients)
        wait_for_data_plane(clients)

        print(f"Creating Connector Namespace {config.connector_namespace}...")
        connector_principal_id = create_connector_namespace(config, clients)
        ensure_role_assignment(
            config,
            clients,
            scope=config.group_scope,
            principal_id=connector_principal_id,
            role_id=DATA_OWNER_ROLE_ID,
            principal_type="ServicePrincipal",
        )
        group_principal_id = identity_principal_id(group.identity)
        for role_id in (SERVICE_BUS_SENDER_ROLE_ID, SERVICE_BUS_RECEIVER_ROLE_ID):
            ensure_role_assignment(
                config,
                clients,
                scope=config.service_bus_scope,
                principal_id=group_principal_id,
                role_id=role_id,
                principal_type="ServicePrincipal",
            )

        for sandbox in matching_sandboxes(config, clients):
            print(f"Deleting previous sandbox {sandbox.id}...")
            clients.group.get_sandbox_client(sandbox.id).begin_delete().result()

        existing_secrets = {secret.id for secret in clients.group.list_secrets()}
        if config.token:
            if not config.token.startswith("github_pat_"):
                raise RuntimeError(
                    "COPILOT_GITHUB_TOKEN must begin with 'github_pat_'."
                )
            print("Creating GitHub Copilot Sandbox Group secret...")
            clients.group.upsert_secret(
                config.secret_name,
                {"token": config.token},
            )
        elif (
            config.secret_name not in existing_secrets
            or "token" not in clients.group.list_secret_keys(config.secret_name)
        ):
            raise RuntimeError(
                "Set COPILOT_GITHUB_TOKEN for the initial deployment. It may be "
                "left blank later while the Sandbox Group secret exists."
            )

        if config.agentmail_enabled:
            print("Preparing AgentMail inbox and sender allowlists...")
            config = prepare_agentmail(config, clients)

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
            entrypoint=["/usr/local/bin/container-entrypoint"],
        ).result()

        print(f"Creating sandbox {config.sandbox_name}...")
        sandbox = create_sandbox(
            config,
            clients,
            disk_id=image.id,
        )
        write_runtime_environment(
            config,
            sandbox,
            sandbox_id=sandbox.sandbox_id,
        )
        callback_url = configure_connector(
            config,
            clients,
            sandbox_id=sandbox.sandbox_id,
            connector_principal_id=connector_principal_id,
            service_bus_connection_string=connector_keys.primary_connection_string,
        )
        if config.agentmail_enabled:
            print("Deploying AgentMail Azure Function bridge...")
            inbox_id, webhook_url = configure_agentmail(config, clients)
            print(f"AgentMail inbox: {inbox_id}")
            print(f"AgentMail webhook: {webhook_url}")
        print(f"Sandbox ready: {sandbox.sandbox_id}")
        print(f"Trigger callback: {callback_url}")


if __name__ == "__main__":
    main()
