# Operations, configuration, and testing

## Prerequisites

- Python 3.10 or later
- Azure CLI authenticated with `az login`
- the preview `aca` CLI for interactive shell access
- permission to create resource groups, role assignments, Service Bus,
  Azure Functions, storage, Sandbox Groups, and Connector Namespace resources
- a fine-grained GitHub PAT with the **Copilot Requests** account permission
- optionally, an AgentMail API key

Install the deployment dependencies:

```console
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Use `.venv\Scripts\python.exe` instead of `.venv/bin/python` on Windows.

## Configuration

Copy `.env.sample` to `.env`. The file is ignored by Git.

### Core settings

| Setting | Purpose | Default |
| --- | --- | --- |
| `COPILOT_GITHUB_TOKEN` | Fine-grained GitHub PAT used to populate the Sandbox Group secret | required initially |
| `AZURE_SUBSCRIPTION_ID` | Target subscription; active Azure CLI subscription when empty | active subscription |
| `AZURE_LOCATION` | Sandbox and Azure resource region | `swedencentral` |
| `AZURE_RESOURCE_GROUP` | Resource group | `rg-copilot-sandbox` |
| `SANDBOX_GROUP` | Sandbox Group name | `copilot-sandbox-group` |
| `SANDBOX_NAME` | Sandbox label/name | `copilot-cli` |
| `SANDBOX_IMAGE` | Public GHCR image and tag | `latest` |
| `SANDBOX_AUTO_SUSPEND_SECONDS` | Idle interval before disk-mode suspend | `60` |
| `SERVICE_BUS_QUEUE` | Task queue | `copilot-tasks` |
| `WORKER_PORT` | Protected HTTP worker port | `8080` |

For production-like use, increase `SANDBOX_AUTO_SUSPEND_SECONDS` above the
longest expected synchronous task duration. The 60-second default is optimized
for the repository's short end-to-end test.

### AgentMail settings

Create an API key in the AgentMail Console. A scoped key needs:

- inbox and message read access;
- webhook read/write access;
- list read/write access.

Configure:

| Setting | Purpose |
| --- | --- |
| `AGENTMAIL_API_KEY` | AgentMail API key beginning with `am_` |
| `AGENTMAIL_INBOX_ID` | Existing inbox to use; optional when deployment should create one |
| `AGENTMAIL_USERNAME` | Username for a newly created inbox |
| `AGENTMAIL_DOMAIN` | Domain for a newly created inbox |
| `AGENTMAIL_ALLOWED_SENDERS` | Comma-separated exact sender addresses |
| `AGENTMAIL_FUNCTION_APP` | Globally unique Function App name |
| `AGENTMAIL_STORAGE_ACCOUNT` | Globally unique storage account name |
| `AGENTMAIL_MAX_BODY_CHARS` | Maximum text passed to Copilot |

Example:

```text
AGENTMAIL_API_KEY=am_...
AGENTMAIL_INBOX_ID=my-agent@agentmail.to
AGENTMAIL_ALLOWED_SENDERS=owner@example.com,operator@example.com
```

Do not use domain entries in `AGENTMAIL_ALLOWED_SENDERS`; this integration
accepts exact addresses only.

## Deployment

```console
.venv/bin/python scripts/deploy.py
```

Deployment is idempotent. It creates or updates:

- the resource group and Basic Service Bus namespace;
- the Sandbox Group, DataDisk, secrets, and disk image;
- the sandbox and its protected OnDemand worker port;
- Connector Namespace and its Service Bus trigger;
- when AgentMail is enabled, receive/reply allowlists, a Flex Consumption
  Function, Function key, webhook, managed identity role, and AgentMail egress
  transform.

When using an immutable image tag, update `SANDBOX_IMAGE` before deployment.
The test detects images that predate AgentMail worker support.

## Interactive access

```console
aca --resource-group rg-copilot-sandbox \
  --region swedencentral \
  sandbox shell \
  --group copilot-sandbox-group \
  --selector name=copilot-cli \
  --command "tmux attach -t copilot"
```

Detach with `Ctrl-b`, then `d`.

## Scheduling

Times must be future ISO 8601 timestamps with an explicit offset. UTC with `Z`
is recommended.

Prompt:

```console
schedule-task prompt \
  --at 2026-09-02T16:00:00Z \
  --prompt "Inspect open issues and summarize blockers"
```

Approved script:

```console
schedule-task script \
  --at 2026-09-02T16:00:00Z \
  --script report.py \
  --arg weekly
```

Scripts must be `.py` or `.sh` files below `/mnt/data/tasks`. Absolute paths and
arbitrary shell command strings are rejected.

Finite recurrence:

```console
schedule-task prompt \
  --at 2026-09-07T06:00:00Z \
  --every weekly \
  --occurrences 52 \
  --prompt "Prepare the weekly repository report"
```

Recurrence supports `daily` and `weekly`, with 1 to 366 occurrences.

## Results and logs

| Path | Contents |
| --- | --- |
| `/mnt/data/scheduler/logs/<task-id>.json` | Exit status, timestamps, stdout, and stderr |
| `/mnt/data/scheduler/worker.log` | HTTP worker lifecycle and request log |
| `/mnt/data/scheduler/runtime.json` | Non-secret resume settings and Copilot placeholder |
| `/mnt/data/tasks` | User-approved scheduled scripts |

## Testing

Run the local unit suite:

```console
.venv/bin/python -m unittest discover -s tests -v
```

It validates AgentMail webhook task construction, sender filtering, Function
signature handling, worker schema validation, and safe prompt construction.

Run the deployed end-to-end test:

```console
.venv/bin/python scripts/test.py
```

The test verifies:

- packaged tools, `tmux`, worker, and scheduler skill;
- secret-backed GitHub and AgentMail egress rules;
- absence of the AgentMail API key from environment and DataDisk;
- Function identity and Function-key protection;
- Entra protection on the sandbox worker;
- prompt and script scheduling;
- auto-suspend, Connector Namespace wake-up, task execution, and queue drain.

The test requires an empty Service Bus queue and cleans its own scheduled-task
artifacts.

### AgentMail end-to-end validation

After deployment, send an email from an exact allowlisted address to the
configured inbox. Use a harmless, observable request such as creating a marker
file in `/mnt/data`. Confirm:

1. the Function accepts the webhook;
2. Service Bus receives and drains the reference task;
3. the sandbox transitions from `Stopped` to `Running`;
4. the expected result appears in `/mnt/data`;
5. a replay of the same AgentMail `event_id` does not rerun Copilot.

Do not use production instructions or sensitive email content for validation.

## Cleanup

```console
.venv/bin/python scripts/cleanup.py
```

Cleanup removes the managed AgentMail webhook before deleting Azure resources.
It intentionally leaves the AgentMail inbox and its allowlists in place.

## Troubleshooting

### Function returns 401 or 403

Confirm AgentMail sends the configured `x-functions-key` header and that the
current webhook signing secret is present in Function settings.

### Function returns 500 before Service Bus receives a message

Check Application Insights exceptions and verify the Function managed identity
has the **Azure Service Bus Data Sender** role.

### Worker returns 400 for `agentmail`

The sandbox is likely using an old immutable image. Publish or select an image
containing the `agentmail` task type and redeploy.

### Sandbox wakes but cannot fetch the email

Verify the AgentMail Sandbox Group secret and the GET-only egress transform for
`api.agentmail.to`. The key must have message read permission for the selected
inbox.

### Scheduler reports missing Copilot authentication

Use a clean deployment and confirm the GitHub PAT secret-backed egress
transforms exist for GitHub and Copilot API hosts. The persisted runtime file
contains only the required placeholder, never the PAT itself.
