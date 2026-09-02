"""Provision AgentMail and its Azure Function webhook bridge."""

from __future__ import annotations

import json
import secrets
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from common import (
    PROJECT_LABELS,
    SERVICE_BUS_SENDER_ROLE_ID,
    AzureClients,
    Config,
    ensure_role_assignment,
)

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = ROOT / "bridge"
WEBHOOK_CLIENT_ID = "aca-sandbox-copilot-agentmail"


def az_json(*args: str, subscription: str | None = None) -> Any:
    command = ["az", *args, "--only-show-errors", "--output", "json"]
    if subscription:
        command.extend(("--subscription", subscription))
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout) if completed.stdout.strip() else None


class AgentMailClient:
    def __init__(self, api_key: str, base_url: str = "https://api.agentmail.to/v0"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "aca-sandbox-copilot/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"AgentMail {method} {path} failed with {error.code}: {detail}"
            ) from error
        return None if not content else json.loads(content)

    def ensure_inbox(self, config: Config) -> str:
        if config.agentmail_inbox_id:
            encoded = urllib.parse.quote(config.agentmail_inbox_id, safe="")
            inbox = self.request("GET", f"/inboxes/{encoded}")
        else:
            inbox = self.request(
                "POST",
                "/inboxes",
                body={
                    "username": config.agentmail_username,
                    "domain": config.agentmail_domain,
                    "display_name": "Copilot Sandbox",
                    "client_id": f"{WEBHOOK_CLIENT_ID}-inbox",
                },
            )
        inbox_id = inbox.get("inbox_id") if isinstance(inbox, dict) else None
        if not isinstance(inbox_id, str) or not inbox_id:
            raise RuntimeError("AgentMail did not return an inbox_id.")
        return inbox_id

    def sync_allowlists(self, inbox_id: str, allowed_senders: tuple[str, ...]) -> None:
        encoded_inbox = urllib.parse.quote(inbox_id, safe="")
        desired = {sender.casefold() for sender in allowed_senders}
        for direction in ("receive", "reply"):
            path = f"/inboxes/{encoded_inbox}/lists/{direction}/allow"
            entries: list[dict[str, Any]] = []
            page_token: str | None = None
            while True:
                query = {"limit": "100"}
                if page_token:
                    query["page_token"] = page_token
                response = self.request("GET", path, query=query)
                if not isinstance(response, dict):
                    raise RuntimeError("AgentMail list response must be an object.")
                entries.extend(
                    item for item in response.get("entries", []) if isinstance(item, dict)
                )
                page_token = response.get("next_page_token")
                if not page_token:
                    break
            current = {
                str(item.get("entry", "")).casefold(): item
                for item in entries
            }
            for entry, item in current.items():
                if entry not in desired and not item.get("read_only", False):
                    self.request(
                        "DELETE",
                        f"{path}/{urllib.parse.quote(entry, safe='')}",
                    )
            for entry in sorted(desired - current.keys()):
                self.request("POST", path, body={"entry": entry})

    def replace_webhook(
        self,
        *,
        inbox_id: str,
        url: str,
        function_key: str,
    ) -> str:
        self.delete_managed_webhooks(inbox_id)
        encoded_inbox = urllib.parse.quote(inbox_id, safe="")
        webhook = self.request(
            "POST",
            f"/inboxes/{encoded_inbox}/webhooks",
            body={
                "url": url,
                "event_types": ["message.received"],
                "client_id": WEBHOOK_CLIENT_ID,
                "headers": {"x-functions-key": function_key},
            },
        )
        secret = webhook.get("secret") if isinstance(webhook, dict) else None
        if not isinstance(secret, str) or not secret:
            raise RuntimeError("AgentMail did not return a webhook signing secret.")
        return secret

    def delete_managed_webhooks(self, inbox_id: str) -> None:
        encoded_inbox = urllib.parse.quote(inbox_id, safe="")
        path = f"/inboxes/{encoded_inbox}/webhooks"
        page_token: str | None = None
        webhook_ids: list[str] = []
        while True:
            query = {"limit": "100"}
            if page_token:
                query["page_token"] = page_token
            response = self.request("GET", path, query=query)
            webhook_ids.extend(
                webhook["webhook_id"]
                for webhook in response.get("webhooks", [])
                if webhook.get("client_id") == WEBHOOK_CLIENT_ID
            )
            page_token = response.get("next_page_token")
            if not page_token:
                break
        for webhook_id in webhook_ids:
            self.request("DELETE", f"{path}/{webhook_id}")


def bridge_archive() -> Path:
    temporary = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    temporary.close()
    target = Path(temporary.name)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in BRIDGE_ROOT.iterdir():
            if path.is_file() and path.name != "local.settings.json":
                archive.write(path, path.name)
    return target


def deploy_function(config: Config, clients: AzureClients, inbox_id: str) -> str:
    try:
        az_json(
            "storage",
            "account",
            "show",
            "--resource-group",
            config.resource_group,
            "--name",
            config.agentmail_storage_account,
            subscription=config.subscription_id,
        )
    except subprocess.CalledProcessError:
        az_json(
            "storage",
            "account",
            "create",
            "--resource-group",
            config.resource_group,
            "--name",
            config.agentmail_storage_account,
            "--location",
            config.location,
            "--sku",
            "Standard_LRS",
            "--allow-blob-public-access",
            "false",
            "--tags",
            *(f"{key}={value}" for key, value in PROJECT_LABELS.items()),
            subscription=config.subscription_id,
        )
    try:
        az_json(
            "functionapp",
            "show",
            "--resource-group",
            config.resource_group,
            "--name",
            config.agentmail_function_app,
            subscription=config.subscription_id,
        )
    except subprocess.CalledProcessError:
        az_json(
            "functionapp",
            "create",
            "--resource-group",
            config.resource_group,
            "--name",
            config.agentmail_function_app,
            "--storage-account",
            config.agentmail_storage_account,
            "--flexconsumption-location",
            config.location,
            "--runtime",
            "python",
            "--runtime-version",
            "3.11",
            subscription=config.subscription_id,
        )
    identity = az_json(
        "functionapp",
        "identity",
        "assign",
        "--resource-group",
        config.resource_group,
        "--name",
        config.agentmail_function_app,
        subscription=config.subscription_id,
    )
    principal_id = identity.get("principalId")
    if not principal_id:
        raise RuntimeError("Function App system identity has no principal ID.")
    ensure_role_assignment(
        config,
        clients,
        scope=config.service_bus_scope,
        principal_id=principal_id,
        role_id=SERVICE_BUS_SENDER_ROLE_ID,
        principal_type="ServicePrincipal",
    )
    az_json(
        "functionapp",
        "config",
        "appsettings",
        "set",
        "--resource-group",
        config.resource_group,
        "--name",
        config.agentmail_function_app,
        "--settings",
        f"SERVICE_BUS_NAMESPACE={config.service_bus_namespace}.servicebus.windows.net",
        f"SERVICE_BUS_QUEUE={config.service_bus_queue}",
        f"AGENTMAIL_INBOX_ID={inbox_id}",
        f"AGENTMAIL_ALLOWED_SENDERS={','.join(config.agentmail_allowed_senders)}",
        subscription=config.subscription_id,
    )
    archive = bridge_archive()
    try:
        az_json(
            "functionapp",
            "deployment",
            "source",
            "config-zip",
            "--resource-group",
            config.resource_group,
            "--name",
            config.agentmail_function_app,
            "--src",
            str(archive),
            "--build-remote",
            "true",
            subscription=config.subscription_id,
        )
    finally:
        archive.unlink(missing_ok=True)
    function_key = secrets.token_urlsafe(32)
    az_json(
        "functionapp",
        "keys",
        "set",
        "--resource-group",
        config.resource_group,
        "--name",
        config.agentmail_function_app,
        "--key-type",
        "functionKeys",
        "--key-name",
        "agentmail-webhook",
        "--key-value",
        function_key,
        subscription=config.subscription_id,
    )
    return function_key


def configure_agentmail(config: Config, clients: AzureClients) -> tuple[str, str]:
    agentmail = AgentMailClient(config.agentmail_api_key)
    inbox_id = config.agentmail_inbox_id
    if not inbox_id:
        raise RuntimeError("AgentMail inbox must be prepared before the bridge.")
    function_key = deploy_function(config, clients, inbox_id)
    webhook_url = (
        f"https://{config.agentmail_function_app}.azurewebsites.net/api/agentmail"
    )
    webhook_secret = agentmail.replace_webhook(
        inbox_id=inbox_id,
        url=webhook_url,
        function_key=function_key,
    )
    az_json(
        "functionapp",
        "config",
        "appsettings",
        "set",
        "--resource-group",
        config.resource_group,
        "--name",
        config.agentmail_function_app,
        "--settings",
        f"AGENTMAIL_WEBHOOK_SECRET={webhook_secret}",
        subscription=config.subscription_id,
    )
    az_json(
        "functionapp",
        "restart",
        "--resource-group",
        config.resource_group,
        "--name",
        config.agentmail_function_app,
        subscription=config.subscription_id,
    )
    return inbox_id, webhook_url


def prepare_agentmail(config: Config, clients: AzureClients) -> Config:
    agentmail = AgentMailClient(config.agentmail_api_key)
    inbox_id = agentmail.ensure_inbox(config)
    agentmail.sync_allowlists(inbox_id, config.agentmail_allowed_senders)
    print("Creating AgentMail Sandbox Group secret...")
    clients.group.upsert_secret(
        config.agentmail_secret_name,
        {"api-key": config.agentmail_api_key},
    )
    return replace(config, agentmail_inbox_id=inbox_id)


def cleanup_agentmail(config: Config) -> None:
    if config.agentmail_enabled:
        client = AgentMailClient(config.agentmail_api_key)
        inbox_id = client.ensure_inbox(config)
        client.delete_managed_webhooks(inbox_id)
