---
name: scheduler
description: Schedule one-time or recurring work that wakes this Azure Container Apps Sandbox.
---

# Sandbox scheduler

Use `schedule-task` to schedule work through Azure Service Bus. Convert the
user's local date and time to an explicit future UTC ISO 8601 timestamp first.

Prompt task:

```bash
schedule-task prompt --at 2026-09-02T06:00:00Z --prompt 'Inspect open issues and summarize blockers'
```

Approved script below `/mnt/data/tasks`:

```bash
schedule-task script --at 2026-09-02T06:00:00Z --script report.py --arg weekly
```

Add `--every daily` or `--every weekly`; use `--interval N` for every N days
or weeks and `--occurrences N` to choose the scheduling horizon. The default is
52 occurrences. All occurrences are scheduled up front so they remain durable
while the sandbox is stopped.
Never schedule arbitrary shell text. Create a `.py` or `.sh` file below
`/mnt/data/tasks`, then schedule its relative path and argument array.

Task results are JSON files in `/mnt/data/scheduler/logs`.
