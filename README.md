# GitHub Copilot CLI on Azure Container Apps Sandboxes

Run a persistent GitHub Copilot CLI workspace inside
[Azure Container Apps Sandboxes](https://sandboxes.azure.com). The repository
provides a ready-to-use Ubuntu image, Python deployment automation, durable
storage, scheduled tasks, and optional email-triggered work through AgentMail.

This project targets **ACA Sandboxes**, not regular Azure Container Apps or
Container Apps dynamic sessions.

## What it provides

- GitHub Copilot CLI in a persistent `tmux` session
- Azure CLI, GitHub CLI, Terraform, Python, Node.js, and common engineering tools
- a 1 GiB persistent DataDisk mounted at `/mnt/data`
- automatic suspension when idle and on-demand wake-up for scheduled work
- durable prompt and script scheduling through Azure Service Bus
- optional AgentMail integration for starting Copilot tasks by email
- secret-backed outbound authentication without placing API keys on disk

## How it works

```text
Interactive user ───────────────────────────────┐
                                                │
Scheduled task → Service Bus ────────────────┐  │
                                             ▼  ▼
AgentMail → Azure Function → Service Bus → ACA Sandbox
                                             │
                                             ▼
                                  task worker → Copilot CLI
                                             │
                                             ▼
                                      /mnt/data
```

The sandbox keeps its workspace on the DataDisk and suspends compute while
idle. A Connector Namespace consumes Service Bus messages and calls an
Entra-protected `OnDemand` worker port, which wakes the sandbox before
dispatching a task.

AgentMail is an optional internet-facing trigger. A Flex Consumption Azure
Function validates the AgentMail webhook and places a reference to the email on
Service Bus. The sandbox then fetches the canonical message and starts Copilot.

See [Architecture](docs/architecture.md) for the complete data flows and
security model.

## Quick start

Prerequisites:

- Python 3.10 or later
- Azure CLI authenticated with `az login`
- permission to create Azure resources and role assignments
- a fine-grained GitHub PAT with the **Copilot Requests** account permission

Create your local configuration:

```console
cp .env.sample .env
```

Set at least:

```text
COPILOT_GITHUB_TOKEN=github_pat_...
```

Deploy on macOS or Linux:

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

Connect to the persistent Copilot session:

```console
aca --resource-group rg-copilot-sandbox \
  --region swedencentral \
  sandbox shell \
  --group copilot-sandbox-group \
  --selector name=copilot-cli \
  --command "tmux attach -t copilot"
```

Detach without stopping the session with `Ctrl-b`, then `d`.

## Schedule work

Inside the sandbox:

```console
schedule-task prompt \
  --at 2026-09-07T06:00:00Z \
  --prompt "Prepare the repository report"

schedule-task script \
  --at 2026-09-07T07:00:00Z \
  --script report.py \
  --arg weekly
```

Daily and weekly recurrence is also supported. See
[Operations and testing](docs/operations.md) for the full command reference.

## Enable AgentMail

Create an AgentMail API key with inbox, message, webhook, and list permissions,
then add the following values to `.env`:

```text
AGENTMAIL_API_KEY=am_...
AGENTMAIL_INBOX_ID=agent@agentmail.to
AGENTMAIL_ALLOWED_SENDERS=you@example.com
```

Redeploy with `scripts/deploy.py`. Emails from the exact allowlisted addresses
will then create Copilot tasks. See [Architecture](docs/architecture.md) for
the trust boundaries and [Operations and testing](docs/operations.md) for
configuration and validation.

## Container image

GitHub Actions publishes the public amd64 image:

```text
ghcr.io/tkubica12/aca-sandbox-copilot:latest
```

`main` also publishes immutable UTC tags such as `20260902-101511`.

## Documentation

- [Architecture and security](docs/architecture.md)
- [Operations, configuration, and testing](docs/operations.md)

## Cleanup

```console
.venv/bin/python scripts/cleanup.py
```

On Windows, use `.venv\Scripts\python.exe`.
