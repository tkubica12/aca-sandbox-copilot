#!/usr/bin/env python3
"""Shared configuration and Azure Container Apps Sandboxes SDK helpers."""

from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from azure.containerapps.sandbox import (
    DATA_PLANE_API_VERSION,
    DATA_PLANE_SCOPE,
    SandboxGroupClient,
    SandboxGroupManagementClient,
    SandboxVolume,
    endpoint_for_region,
)
from azure.core.exceptions import HttpResponseError
from azure.core.pipeline import Pipeline
from azure.core.pipeline.policies import (
    BearerTokenCredentialPolicy,
    ContentDecodePolicy,
    HeadersPolicy,
    ProxyPolicy,
    RedirectPolicy,
    RequestIdPolicy,
    RetryPolicy,
    UserAgentPolicy,
)
from azure.core.pipeline.transport import RequestsTransport
from azure.core.rest import HttpRequest
from azure.identity import AzureCliCredential
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.resource.resources.models import GenericResource
from azure.mgmt.servicebus import ServiceBusManagementClient

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DATA_OWNER_ROLE_ID = "c24cf47c-5077-412d-a19c-45202126392c"
SERVICE_BUS_SENDER_ROLE_ID = "69a216fc-b8fb-44d8-bc22-1f3c2cd27a39"
SERVICE_BUS_RECEIVER_ROLE_ID = "4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0"
CONNECTOR_API_VERSION = "2026-05-01-preview"
SANDBOX_API_VERSION = "2026-02-01-preview"
ADC_PROXY_AUDIENCE = "https://auth.adcproxy.io/"
PROJECT_LABELS = {"managed-by": "aca-sandbox-copilot"}


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        raise RuntimeError(f"Missing {path}. Copy .env.sample to .env and configure it.")
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"{name} is required in {ENV_PATH}")
    return value or ""


def current_subscription_id() -> str:
    configured = env("AZURE_SUBSCRIPTION_ID")
    if configured:
        return configured
    try:
        result = subprocess.run(
            ["az", "account", "show", "--query", "id", "--output", "tsv"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise RuntimeError(
            "AZURE_SUBSCRIPTION_ID is unset and the active Azure CLI subscription "
            "could not be read. Run 'az login' or set it in .env."
        ) from error
    subscription_id = result.stdout.strip()
    if not subscription_id:
        raise RuntimeError("Azure CLI returned no active subscription.")
    return subscription_id


@dataclass(frozen=True)
class Config:
    subscription_id: str
    location: str
    resource_group: str
    sandbox_group: str
    sandbox_name: str
    disk_name: str
    volume_name: str
    credential_name: str
    image: str
    cpu: str
    memory: str
    root_disk_size: str
    data_disk_size: str
    auto_suspend_seconds: int
    token: str
    service_bus_namespace: str
    service_bus_queue: str
    connector_namespace: str
    connector_location: str
    connector_connection: str
    connector_trigger: str
    worker_port: int

    @classmethod
    def from_env(cls, *, require_token: bool = False) -> "Config":
        load_env()
        subscription_id = current_subscription_id()
        location = env("AZURE_LOCATION", "swedencentral")
        config = cls(
            subscription_id=subscription_id,
            location=location,
            resource_group=env("AZURE_RESOURCE_GROUP", "rg-copilot-sandbox"),
            sandbox_group=env("SANDBOX_GROUP", "copilot-sandbox-group"),
            sandbox_name=env("SANDBOX_NAME", "copilot-cli"),
            disk_name=env("SANDBOX_DISK_NAME", "copilot-image"),
            volume_name=env("SANDBOX_VOLUME_NAME", "copilot-data"),
            credential_name=env("SANDBOX_CREDENTIAL_NAME", "github-copilot"),
            image=env(
                "SANDBOX_IMAGE",
                "ghcr.io/tkubica12/aca-sandbox-copilot:latest",
            ),
            cpu=env("SANDBOX_CPU", "2000m"),
            memory=env("SANDBOX_MEMORY", "4096Mi"),
            root_disk_size=env("SANDBOX_ROOT_DISK", "20Gi"),
            data_disk_size=env("SANDBOX_VOLUME_SIZE", "1Gi"),
            auto_suspend_seconds=int(env("SANDBOX_AUTO_SUSPEND_SECONDS", "60")),
            token=env("COPILOT_GITHUB_TOKEN", required=require_token),
            service_bus_namespace=(
                env("SERVICE_BUS_NAMESPACE")
                or f"copilot-sbx-{subscription_id[:8]}"
            ),
            service_bus_queue=env("SERVICE_BUS_QUEUE", "copilot-tasks"),
            connector_namespace=env("CONNECTOR_NAMESPACE", "copilot-scheduler"),
            connector_location=env("CONNECTOR_LOCATION") or location,
            connector_connection=env("CONNECTOR_CONNECTION", "servicebus"),
            connector_trigger=env("CONNECTOR_TRIGGER", "copilot-task-trigger"),
            worker_port=int(env("WORKER_PORT", "8080")),
        )
        if require_token and not config.token.startswith("github_pat_"):
            raise RuntimeError(
                "COPILOT_GITHUB_TOKEN must be a fine-grained token beginning with "
                "'github_pat_'."
            )
        return config

    @property
    def group_scope(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.App/sandboxGroups/{self.sandbox_group}"
        )

    @property
    def service_bus_scope(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.ServiceBus/namespaces/{self.service_bus_namespace}"
        )

    @property
    def connector_scope(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.Web/connectorGateways/{self.connector_namespace}"
        )


class ArmRestClient:
    """Small ARM client for Connector Namespace preview resources."""

    def __init__(self, credential: AzureCliCredential) -> None:
        policies = [
            RequestIdPolicy(),
            HeadersPolicy(),
            UserAgentPolicy(sdk_moniker="aca-sandbox-copilot/1.0"),
            ProxyPolicy(),
            ContentDecodePolicy(),
            RedirectPolicy(),
            RetryPolicy(),
            BearerTokenCredentialPolicy(
                credential, "https://management.azure.com/.default"
            ),
        ]
        self._pipeline = Pipeline(RequestsTransport(), policies=policies)

    def close(self) -> None:
        self._pipeline.__exit__(None, None, None)

    def request(
        self,
        method: str,
        resource_id: str,
        api_version: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> Any:
        options: dict[str, Any] = {"params": {"api-version": api_version}}
        if body is not None:
            options["json"] = body
        request = HttpRequest(
            method, f"https://management.azure.com{resource_id}", **options
        )
        response = self._pipeline.run(request).http_response
        if response.status_code >= 400:
            raise HttpResponseError(response=response)
        content = response.text()
        return None if not content else json.loads(content)


class ProviderCredentialClient:
    """Data-plane provider credential operations missing from SDK 0.1.0b4."""

    def __init__(self, config: Config, credential: AzureCliCredential) -> None:
        self._endpoint = endpoint_for_region(config.location).rstrip("/")
        self._path = (
            f"/subscriptions/{config.subscription_id}/resourceGroups/{config.resource_group}"
            f"/sandboxGroups/{config.sandbox_group}/connections"
        )
        policies = [
            RequestIdPolicy(),
            HeadersPolicy(),
            UserAgentPolicy(sdk_moniker="aca-sandbox-copilot/1.0"),
            ProxyPolicy(),
            ContentDecodePolicy(),
            RedirectPolicy(),
            RetryPolicy(retry_on_status_codes=[403], retry_status=10),
            BearerTokenCredentialPolicy(credential, DATA_PLANE_SCOPE),
        ]
        self._pipeline = Pipeline(RequestsTransport(), policies=policies)

    def close(self) -> None:
        self._pipeline.__exit__(None, None, None)

    def _request(
        self,
        method: str,
        path: str = "",
        *,
        body: dict[str, Any] | None = None,
        force: bool = False,
    ) -> Any:
        params: dict[str, str] = {"api-version": DATA_PLANE_API_VERSION}
        if force:
            params["force"] = "true"
        request_options: dict[str, Any] = {"params": params}
        if body is not None:
            request_options["json"] = body
        request = HttpRequest(
            method, f"{self._endpoint}{self._path}{path}", **request_options
        )
        response = self._pipeline.run(request).http_response
        if response.status_code >= 400:
            raise HttpResponseError(response=response)
        if response.status_code == 204:
            return None
        return response.json()

    def list(self) -> list[dict[str, Any]]:
        result = self._request("GET")
        return result if isinstance(result, list) else result.get("value", [])

    def create_github_copilot(self, name: str, token: str) -> dict[str, Any]:
        return self._request(
            "POST",
            body={
                "name": name,
                "parameterValueSetName": "pat",
                "parameterValueSetValues": {"token": token},
                "type": "github-copilot",
            },
        )

    def delete(self, credential_id: str) -> None:
        self._request("DELETE", f"/{credential_id}", force=True)


@dataclass
class AzureClients:
    credential: AzureCliCredential
    resources: ResourceManagementClient
    authorization: AuthorizationManagementClient
    servicebus: ServiceBusManagementClient
    groups: SandboxGroupManagementClient
    group: SandboxGroupClient
    connections: ProviderCredentialClient
    arm: ArmRestClient

    @classmethod
    def create(cls, config: Config) -> "AzureClients":
        credential = AzureCliCredential(process_timeout=30)
        return cls(
            credential=credential,
            resources=ResourceManagementClient(credential, config.subscription_id),
            authorization=AuthorizationManagementClient(
                credential, config.subscription_id
            ),
            servicebus=ServiceBusManagementClient(
                credential, config.subscription_id
            ),
            groups=SandboxGroupManagementClient(
                credential,
                subscription_id=config.subscription_id,
                resource_group=config.resource_group,
            ),
            group=SandboxGroupClient(
                endpoint_for_region(config.location),
                credential,
                subscription_id=config.subscription_id,
                resource_group=config.resource_group,
                sandbox_group=config.sandbox_group,
            ),
            connections=ProviderCredentialClient(config, credential),
            arm=ArmRestClient(credential),
        )

    def close(self) -> None:
        self.arm.close()
        self.connections.close()
        self.group.close()
        self.groups.close()
        self.authorization.close()
        self.servicebus.close()
        self.resources.close()
        self.credential.close()

    def __enter__(self) -> "AzureClients":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def token_claim(token: str, name: str) -> str:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    value = claims.get(name)
    if not value:
        raise RuntimeError(f"Azure access token does not contain the {name!r} claim.")
    return str(value)


def identity_principal_id(identity: Any) -> str:
    value = (
        identity.get("principalId")
        if isinstance(identity, dict)
        else getattr(identity, "principal_id", None)
    )
    if not value:
        raise RuntimeError("Azure resource has no system identity principal ID.")
    return str(value)


def ensure_role_assignment(
    config: Config,
    clients: AzureClients,
    *,
    scope: str,
    principal_id: str,
    role_id: str,
    principal_type: str,
) -> None:
    role_definition_id = (
        f"/subscriptions/{config.subscription_id}/providers/Microsoft.Authorization"
        f"/roleDefinitions/{role_id}"
    )
    assignment_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{scope}:{principal_id}:{role_id}")
    )
    try:
        clients.authorization.role_assignments.create(
            scope,
            assignment_id,
            {
                "role_definition_id": role_definition_id,
                "principal_id": principal_id,
                "principal_type": principal_type,
            },
        )
    except HttpResponseError as error:
        if error.status_code != 409:
            raise


def ensure_data_owner(config: Config, clients: AzureClients) -> None:
    access_token = clients.credential.get_token(
        "https://management.azure.com/.default"
    ).token
    principal_id = token_claim(access_token, "oid")
    principal_type = (
        "ServicePrincipal"
        if token_claim(access_token, "idtyp").lower() == "app"
        else "User"
    )
    ensure_role_assignment(
        config,
        clients,
        scope=config.group_scope,
        principal_id=principal_id,
        role_id=DATA_OWNER_ROLE_ID,
        principal_type=principal_type,
    )


def create_connector_namespace(config: Config, clients: AzureClients) -> str:
    gateway = clients.resources.resources.begin_create_or_update_by_id(
        config.connector_scope,
        CONNECTOR_API_VERSION,
        GenericResource(
            location=config.connector_location,
            tags=PROJECT_LABELS,
            identity={"type": "SystemAssigned"},
            properties={},
        ),
    ).result()
    return identity_principal_id(gateway.identity)


def configure_connector(
    config: Config,
    clients: AzureClients,
    *,
    sandbox_id: str,
    connector_principal_id: str,
    service_bus_connection_string: str,
) -> str:
    connection_scope = (
        f"{config.connector_scope}/connections/{config.connector_connection}"
    )
    trigger_scope = f"{config.connector_scope}/triggerConfigs/{config.connector_trigger}"
    for resource_scope in (trigger_scope, connection_scope):
        try:
            clients.arm.request(
                "DELETE", resource_scope, CONNECTOR_API_VERSION
            )
        except HttpResponseError as error:
            if error.status_code != 404:
                raise
    clients.arm.request(
        "PUT",
        connection_scope,
        CONNECTOR_API_VERSION,
        body={
            "properties": {
                "displayName": "Copilot task queue",
                "connectorName": "servicebus",
                "parameterValues": {
                    "connectionString": service_bus_connection_string
                },
            }
        },
    )
    callback_url = (
        f"https://{sandbox_id}--{config.worker_port}."
        f"{config.location}.adcproxy.io"
    )
    clients.arm.request(
        "PUT",
        trigger_scope,
        CONNECTOR_API_VERSION,
        body={
            "properties": {
                "state": "Enabled",
                "description": "Dispatch scheduled Service Bus tasks to Copilot Sandbox.",
                "connectionDetails": {
                    "connectorName": "servicebus",
                    "connectionName": config.connector_connection,
                },
                "operationName": "GetMessagesFromQueue",
                "parameters": [
                    {"name": "queueName", "value": config.service_bus_queue},
                    {"name": "maxMessageCount", "value": "1"},
                    {"name": "queueType", "value": "Main"},
                ],
                "notificationDetails": {
                    "callbackUrl": callback_url,
                    "httpMethod": "POST",
                    "body": "@triggerBody()",
                    "authentication": {
                        "type": "ManagedServiceIdentity",
                        "audience": ADC_PROXY_AUDIENCE,
                    },
                },
                "metadata": {
                    "sandboxGroupName": config.sandbox_group,
                    "sandboxId": sandbox_id,
                    "recurrenceFrequency": "Second",
                    "recurrenceInterval": "10",
                },
            }
        },
    )
    register_worker_port(
        config,
        clients,
        sandbox_id=sandbox_id,
        connector_principal_id=connector_principal_id,
    )
    return callback_url


def register_worker_port(
    config: Config,
    clients: AzureClients,
    *,
    sandbox_id: str,
    connector_principal_id: str,
) -> None:
    endpoint = endpoint_for_region(config.location).rstrip("/")
    resource = (
        f"/subscriptions/{config.subscription_id}/resourceGroups/{config.resource_group}"
        f"/sandboxGroups/{config.sandbox_group}/sandboxes/{sandbox_id}/ports"
    )
    token = clients.credential.get_token(DATA_PLANE_SCOPE).token
    tenant_id = token_claim(
        clients.credential.get_token("https://management.azure.com/.default").token,
        "tid",
    )
    request = HttpRequest(
        "PUT",
        f"{endpoint}{resource}",
        params={"api-version": SANDBOX_API_VERSION},
        headers={"Authorization": f"Bearer {token}"},
        json={
            "ports": [
                {
                    "port": config.worker_port,
                    "url": (
                        f"https://{sandbox_id}--{config.worker_port}."
                        f"{config.location}.adcproxy.io"
                    ),
                    "auth": {
                        "anonymous": False,
                        "entraId": {
                            "enabled": True,
                            "objectIds": [connector_principal_id],
                            "tenantIds": [tenant_id],
                        },
                    },
                    "activationMode": "OnDemand",
                    "protocol": "Http",
                }
            ]
        },
    )
    response = RequestsTransport().send(request)
    if response.status_code >= 400:
        raise HttpResponseError(response=response)


def wait_for_data_plane(clients: AzureClients, timeout: int = 180) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            list(clients.group.list_volumes())
            return
        except HttpResponseError as error:
            if error.status_code != 403 or time.monotonic() >= deadline:
                raise
            time.sleep(5)


def matching_sandboxes(config: Config, clients: AzureClients) -> list[Any]:
    return [
        sandbox
        for sandbox in clients.group.list_sandboxes()
        if sandbox.id == config.sandbox_name
        or sandbox.labels.get("name") == config.sandbox_name
    ]


def matching_disk_images(config: Config, clients: AzureClients) -> list[Any]:
    return [
        image
        for image in clients.group.list_disk_images()
        if image.name == config.disk_name
        or image.labels.get("name") == config.disk_name
    ]


def matching_credentials(
    config: Config, clients: AzureClients
) -> list[dict[str, Any]]:
    return [
        item
        for item in clients.connections.list()
        if item.get("name") == config.credential_name
    ]


def create_sandbox(
    config: Config,
    clients: AzureClients,
    *,
    disk_id: str,
    credential_id: str,
):
    if config.auto_suspend_seconds == 0:
        body = {
            "sourcesRef": {"diskImage": {"id": disk_id}},
            "resources": {
                "cpu": config.cpu,
                "memory": config.memory,
                "disk": config.root_disk_size,
            },
            "lifecycle": {"autoSuspendPolicy": {"enabled": False}},
            "labels": {**PROJECT_LABELS, "name": config.sandbox_name},
            "environment": {
                "SERVICE_BUS_NAMESPACE": (
                    f"{config.service_bus_namespace}.servicebus.windows.net"
                ),
                "SERVICE_BUS_QUEUE": config.service_bus_queue,
                "WORKER_PORT": str(config.worker_port),
            },
            "connections": [credential_id],
            "volumes": [
                SandboxVolume(
                    volume_name=config.volume_name,
                    mountpoint="/mnt/data",
                    read_only=False,
                )._to_dict()
            ],
            "entrypoint": ["/usr/local/bin/container-entrypoint"],
        }
        created = clients.group._dp_put(
            f"{clients.group._group_path}/sandboxes", body
        )
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            sandbox = clients.group.get_sandbox(created["id"])
            state = str(sandbox.state).lower()
            if state.endswith("running"):
                return clients.group.get_sandbox_client(created["id"])
            if state.endswith(("failed", "deleting")):
                raise RuntimeError(
                    f"Sandbox {created['id']} entered terminal state {sandbox.state}."
                )
            time.sleep(3)
        raise TimeoutError(f"Sandbox {created['id']} did not become ready.")

    return clients.group.begin_create_sandbox(
        disk=None,
        disk_id=disk_id,
        disk_size=config.root_disk_size,
        cpu=config.cpu,
        memory=config.memory,
        labels={**PROJECT_LABELS, "name": config.sandbox_name},
        auto_suspend_seconds=config.auto_suspend_seconds,
        auto_suspend_mode="Disk",
        volumes=[
            SandboxVolume(
                volume_name=config.volume_name,
                mountpoint="/mnt/data",
                read_only=False,
            )
        ],
        connections=[credential_id],
        environment={
            "SERVICE_BUS_NAMESPACE": (
                f"{config.service_bus_namespace}.servicebus.windows.net"
            ),
            "SERVICE_BUS_QUEUE": config.service_bus_queue,
            "WORKER_PORT": str(config.worker_port),
        },
        entrypoint=["/usr/local/bin/container-entrypoint"],
    ).result()


def exec_checked(sandbox: Any, command: list[str]) -> str:
    result = sandbox.exec(shlex.join(command))
    if result.exit_code != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.exit_code}: {' '.join(command)}\n"
            f"{result.stderr or result.stdout}"
        )
    return result.stdout
