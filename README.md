# GitHub Copilot CLI on Azure Container Apps Sandboxes

Rich Ubuntu image for running GitHub Copilot CLI in **Azure Container Apps
Sandboxes** at [sandboxes.azure.com](https://sandboxes.azure.com). This is not
regular Azure Container Apps and not dynamic sessions.

The image includes:

- GitHub Copilot CLI and GitHub CLI
- Azure CLI and the preview `aca` Sandboxes CLI
- Terraform
- Bash, Python, pip, uv, Node.js
- jq, yq, ripgrep, fd, git, SSH, networking and build utilities
- tmux with a persistent `copilot` session rooted at `/mnt/data`
- authenticated HTTP task worker and Service Bus scheduling skill

## Image

GitHub Actions builds `image/Dockerfile` and publishes:

```text
ghcr.io/tkubica12/aca-sandbox-copilot:latest
```

Pushes to `main` publish `latest` and an immutable UTC timestamp tag such as
`20260831-192054`. Seconds keep multiple builds in one day distinct while the
tag stays short and sortable. Pull requests build without pushing. The GHCR
package is **public**, so the Sandbox service imports it without credentials.

Run locally:

```bash
docker build -t copilot-sandbox image
docker run --rm -d --name copilot-sandbox \
  -v copilot-data:/mnt/data copilot-sandbox
docker exec -it copilot-sandbox tmux attach -t copilot
```

Detach without stopping Copilot: `Ctrl-b`, then `d`. Reconnect with the same
`docker exec` command.

## Deploy to Azure Container Apps Sandboxes

Prerequisites:

- Python 3.10 or later
- Azure CLI authenticated with `az login`
- Permission to create resource groups and role assignments
- Fine-grained GitHub PAT with the **Copilot Requests** account permission

Create `.env` from `.env.sample` and set `COPILOT_GITHUB_TOKEN` to a token
starting with `github_pat_`. The `.env` file is ignored by Git.

```text
COPILOT_GITHUB_TOKEN=github_pat_...
```

Deploy:

```console
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/deploy.py
```

On Windows PowerShell:

```console
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\deploy.py
```

The deployment uses the official `azure-containerapps-sandbox` Python SDK.
The preview `aca` CLI is needed only for the interactive shell command below.

Defaults create:

- resource group `rg-copilot-sandbox` in Sweden Central
- sandbox group `copilot-sandbox-group`
- public custom disk from `ghcr.io/tkubica12/aca-sandbox-copilot:latest`
- 1 GiB persistent DataDisk mounted at `/mnt/data`
- sandbox `copilot-cli` with the GitHub Copilot credential attached
- Basic Service Bus namespace and `copilot-tasks` queue
- system-assigned Sandbox Group identity with Service Bus sender/receiver access
- Connector Namespace trigger with an Entra-protected on-demand worker port

The resource group is tagged `SecurityControl=ignore`. In the tested corporate
subscription this exempts the demo from the policy that otherwise forces
Service Bus local authentication off. The preview Connector Namespace Service
Bus connection currently requires a Listen-only connection string. That
credential is isolated in Connector Namespace and is never exposed to the
sandbox. Deployment stops if the policy exemption is not effective.

Connector Namespace is a regional preview and defaults to the Sandbox Group
region, `swedencentral`.

Change values in `.env` to override these defaults or select an immutable
timestamp image tag.

## Test

Run `.venv/bin/python scripts/test.py` on macOS/Linux or
`.venv\Scripts\python.exe scripts\test.py` on Windows.

The test checks the installed CLIs, tmux, worker authentication, Copilot,
DataDisk access, script and prompt dispatch, managed-identity scheduling, and
recurrence.

## Schedule work

Run these commands inside the sandbox. Times must be future ISO 8601 timestamps
with an offset; using UTC with `Z` is recommended.

```console
schedule-task prompt --at 2026-09-02T06:00:00Z \
  --prompt "Inspect open issues and summarize blockers"

schedule-task script --at 2026-09-02T06:00:00Z \
  --script report.py --arg weekly

schedule-task prompt --at 2026-09-07T06:00:00Z \
  --every weekly --prompt "Prepare the weekly repository report"
```

Scripts must be `.py` or `.sh` files below `/mnt/data/tasks`. Arbitrary shell
text and absolute paths are rejected. Results are persisted as JSON in
`/mnt/data/scheduler/logs`; worker logs are in
`/mnt/data/scheduler/worker.log`.

The message schema is:

```json
{
  "version": 1,
  "id": "UUID",
  "type": "prompt",
  "prompt": "Work to perform",
  "scheduled_at": "2026-09-02T06:00:00Z",
  "recurrence": null
}
```

For a script, replace `prompt` with `"script": "report.py"` and
`"args": ["weekly"]`. Recurrence is either `null` or
`{"frequency":"daily|weekly","interval":1}`. A successful recurring task
schedules its next occurrence.

Service Bus scheduled delivery is external to the sandbox. The Connector
Namespace polls the queue, consumes due messages, and posts the connector
payload to the sandbox's Entra-protected port. The Connector Namespace identity
is the only allowed port caller. The Sandbox Group identity schedules messages
without keys through `DefaultAzureCredential`.

Auto-suspend defaults to 60 seconds in disk mode because the attached DataDisk
does not support memory-mode suspension. On resume, the sandbox starts the
image entrypoint again, restores the tmux session, and runs the HTTP worker.
The worker port uses `activationMode=OnDemand`, so the Connector Namespace
callback wakes a stopped sandbox before delivering the due task.

The current preview exposes no guest heartbeat, lease, busy flag, or
readiness-to-suspend API. Instead, the worker uses its Sandbox Group managed
identity to disable auto-suspend before each task and restores the configured
disk-mode timeout afterward.

Non-secret runtime configuration and the provider-credential placeholder are
persisted in `/mnt/data/scheduler/runtime.json` because disk-mode restart does
not preserve the original process environment. The real GitHub token remains
outside the sandbox and is injected only by the provider credential proxy.

Disk restart currently drops the platform-managed identity environment. During
deployment, its sandbox-scoped endpoint and header are captured without leaving
the sandbox and stored in `/mnt/data/scheduler/identity.json` with mode `0600`.
The entrypoint reloads them after disk resume so scheduling and lifecycle
operations continue to use managed identity. The header is sensitive bootstrap
material, although it is not an Azure access token. Recreating the sandbox
generates a new file.

## Connect

```console
aca --resource-group rg-copilot-sandbox --region swedencentral sandbox shell --group copilot-sandbox-group --selector name=copilot-cli --command "tmux attach -t copilot"
```

Detach with `Ctrl-b`, then `d`. Reconnect with the same command.

## Clean up

Run `.venv/bin/python scripts/cleanup.py` on macOS/Linux or
`.venv\Scripts\python.exe scripts\cleanup.py` on Windows.

`DataDisk` is single-writer, full-POSIX storage and fits durable agent
workspaces. The sandbox uses a 20 GiB root disk, leaving disk-budget headroom
for the 1 GiB data disk at 2000m CPU.

## References

- [ACA Sandboxes CLI quickstart](https://learn.microsoft.com/azure/container-apps/sandboxes-quickstart-cli)
- [Sandbox disk images](https://sandboxes.azure.com/docs/sandboxes/disk-images)
- [Sandbox volumes](https://sandboxes.azure.com/docs/sandboxes/volumes)
- [Sandbox triggers](https://sandboxes.azure.com/docs/sandboxes/triggers)
- [Install GitHub Copilot CLI](https://docs.github.com/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli)