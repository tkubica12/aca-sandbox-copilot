# Architecture and security

## Purpose

The system hosts GitHub Copilot CLI in an isolated Azure Container Apps
Sandbox while preserving a durable workspace between compute suspensions. It
supports three ways to start work:

1. an interactive user attached to the persistent `tmux` session;
2. a prompt or approved script scheduled for future execution;
3. an email received by an optional AgentMail inbox.

All automated paths converge on the same HTTP task worker in the sandbox.

## Components

| Component | Responsibility |
| --- | --- |
| Sandbox Group | Security and lifecycle boundary for the sandbox, disk, secrets, identity, and egress policy |
| Sandbox | Runs the image, Copilot CLI, `tmux`, and the task worker |
| DataDisk | Persists the workspace, runtime bootstrap, task definitions, and execution results |
| Service Bus | Durably stores immediate and scheduled task messages |
| Connector Namespace | Polls Service Bus and invokes the protected sandbox worker |
| Azure Function | Optional public AgentMail webhook bridge |
| AgentMail | Optional managed inbox and inbound email event source |

## Sandbox lifecycle

The sandbox uses disk-mode auto-suspend. When idle, compute stops while the
DataDisk remains available. A request to the worker's `OnDemand` port resumes
the sandbox and reruns `/usr/local/bin/container-entrypoint`.

The entrypoint:

1. restores the persistent `tmux` session;
2. reloads non-secret runtime settings from
   `/mnt/data/scheduler/runtime.json`;
3. starts the HTTP worker on port 8080.

The worker port is not anonymous. Only the Connector Namespace managed identity
is included in its Entra object allowlist.

## Scheduled task flow

```text
Copilot or user
    │
    ▼
schedule-task
    │  managed identity
    ▼
Service Bus scheduled message
    │
    ▼
Connector Namespace trigger
    │  Entra token, activationMode=OnDemand
    ▼
Sandbox worker
    ├─ prompt → copilot --allow-all-tools -p
    └─ script → approved .py or .sh below /mnt/data/tasks
```

Recurring schedules are expanded into a finite set of Service Bus scheduled
messages while managed identity is available. This is necessary because the
current preview does not restore the platform-managed identity environment
after a disk-mode resume.

## AgentMail flow

```text
Allowlisted sender
    │
    ▼
AgentMail receive/reply allowlists
    │  message.received webhook
    ▼
Azure Function, Flex Consumption
    ├─ Function key validation
    ├─ Svix signature and timestamp validation
    ├─ inbox validation
    └─ sender allowlist validation
    │  managed identity
    ▼
Service Bus reference task
    │
    ▼
Connector Namespace → OnDemand sandbox worker
    │
    ├─ fetch canonical message from AgentMail
    ├─ revalidate inbox, message, thread, and sender
    ├─ select extracted newly authored text
    └─ construct a bounded Copilot prompt
```

The Function does not copy email bodies or attachments into Service Bus. It
sends only a reference:

```json
{
  "version": 1,
  "id": "deterministic UUID derived from event_id",
  "type": "agentmail",
  "scheduled_at": "2026-09-02T08:00:00Z",
  "agentmail": {
    "event_id": "evt_...",
    "inbox_id": "agent@agentmail.to",
    "message_id": "<message-id>",
    "thread_id": "thd_..."
  }
}
```

This keeps Service Bus messages small, avoids truncation of large webhook
payloads, and ensures the worker processes the canonical stored message.
Attachment metadata is exposed to Copilot, but attachment content is not
processed.

## Trust boundaries

### Inbound webhook

The Function endpoint uses two independent controls:

- an Azure Functions key sent by AgentMail as a write-only
  `x-functions-key` header;
- the AgentMail Svix signature over the unmodified request body.

Only `message.received` events for the configured inbox and exact allowlisted
sender addresses are dispatched.

### Email authorization

The exact-address allowlist is enforced in three places:

1. AgentMail inbox-scoped `receive` and `reply` allowlists;
2. the Azure Function before Service Bus dispatch;
3. the worker after fetching the canonical message.

Both AgentMail list types are required because replies are evaluated against
the reply list rather than the receive list.

An allowlisted sender is authorized to request work, but quoted or forwarded
content remains untrusted reference data. The worker prefers
`extracted_text`, bounds body size, and wraps the content in a fixed prompt.

### Outbound secrets

The GitHub PAT and AgentMail API key are stored as separate Sandbox Group
secrets. They are never written to:

- the image;
- process environment;
- command arguments;
- Service Bus;
- the DataDisk.

Full-inspection egress transforms inject authorization headers only for the
required API destinations. AgentMail access is limited to `GET` requests below
`/v0/inboxes/*`.

The Function does not receive the AgentMail API key. It only holds its webhook
signing secret in Function settings and sends Service Bus messages with its
system-assigned managed identity.

## Idempotency and failure handling

The Function derives the task UUID deterministically from AgentMail's
`event_id`. If AgentMail retries a webhook or Service Bus redelivers a message,
the worker finds the existing successful result on the DataDisk and does not
run Copilot again.

Basic Service Bus does not provide broker-side duplicate detection, so
idempotency is implemented by the worker.

The Function acknowledges a webhook only after Service Bus accepts the task.
Invalid signatures and malformed events are rejected; valid events outside the
configured inbox or allowlist are acknowledged without dispatch.

## Preview constraints

- ACA Sandboxes and Connector Namespace APIs are preview services and may
  change.
- Connector Namespace currently requires a Listen-only Service Bus connection
  string. The credential is isolated in Connector Namespace and is not exposed
  to the sandbox.
- The current sandbox preview has no guest heartbeat, lease, busy flag, or
  readiness-to-suspend API. Task execution therefore remains inside the
  synchronous connector callback.
- Auto-suspend must be configured above the longest expected task duration.
