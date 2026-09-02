#!/usr/bin/env python3
"""Run the clean Copilot-scheduled suspend and wake end-to-end test."""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from common import (
    AzureClients,
    Config,
    exec_checked,
    matching_credentials,
    matching_disk_images,
    matching_sandboxes,
)


def normalized_state(value: Any) -> str:
    return str(value).rsplit(".", 1)[-1].lower()


def wait_for_state(
    clients: AzureClients,
    sandbox_id: str,
    expected: str,
    timeout: int,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = normalized_state(clients.group.get_sandbox(sandbox_id).state)
        if state == expected.lower():
            return
        if state in {"failed", "deleting"}:
            raise RuntimeError(f"Sandbox entered terminal state {state}.")
        time.sleep(3)
    raise TimeoutError(f"Sandbox did not reach {expected} within {timeout} seconds.")


def queue_counts(config: Config, clients: AzureClients) -> tuple[int, int, int]:
    queue = clients.servicebus.queues.get(
        config.resource_group,
        config.service_bus_namespace,
        config.service_bus_queue,
    )
    details = queue.count_details
    return (
        details.active_message_count or 0,
        details.scheduled_message_count or 0,
        details.dead_letter_message_count or 0,
    )


def wait_for_queue(
    config: Config,
    clients: AzureClients,
    *,
    active: int,
    scheduled: int,
    dead_letter: int,
    timeout: int = 120,
) -> None:
    expected = (active, scheduled, dead_letter)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if queue_counts(config, clients) == expected:
            return
        time.sleep(3)
    raise TimeoutError(
        f"Queue counts did not become {expected}; current counts are "
        f"{queue_counts(config, clients)}."
    )


def wait_for_markers(
    sandbox: Any,
    prompt_marker: str,
    script_marker: str,
    timeout: int = 480,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = sandbox.exec(
            "test \"$(cat /mnt/data/i-was-here.txt 2>/dev/null)\" = "
            f"{prompt_marker!r} && "
            "test \"$(cat /mnt/data/this-is-another-file.txt 2>/dev/null)\" = "
            f"{script_marker!r}"
        )
        if result.exit_code == 0:
            return
        time.sleep(5)
    raise TimeoutError("Both scheduled task marker files were not created.")


def main() -> None:
    config = Config.from_env()
    if config.auto_suspend_seconds <= 0:
        raise RuntimeError("SANDBOX_AUTO_SUSPEND_SECONDS must be positive for this test.")

    with AzureClients.create(config) as clients:
        sandboxes = matching_sandboxes(config, clients)
        images = matching_disk_images(config, clients)
        credentials = matching_credentials(config, clients)
        if len(sandboxes) != 1 or len(images) != 1 or len(credentials) != 1:
            raise RuntimeError(
                "Expected exactly one project sandbox, disk image, and credential. "
                "Run scripts/deploy.py first."
            )

        sandbox_id = sandboxes[0].id
        sandbox = clients.group.get_sandbox_client(sandbox_id)
        sandbox.ensure_running()

        print("Checking image tools, packaged scheduler, skill, tmux, and worker...")
        exec_checked(
            sandbox,
            [
                "bash",
                "-lc",
                "copilot --version && gh --version | head -1 && az version "
                "--query '\"azure-cli\"' -o tsv && terraform version | head -1 "
                "&& python3 --version && uv --version && jq --version && yq --version "
                "&& tmux -V && test -x /usr/local/bin/schedule-task "
                "&& test -x /usr/local/bin/sandbox-task-worker "
                "&& test -f /root/.copilot/skills/scheduler/SKILL.md "
                "&& test -f /mnt/data/scheduler/runtime.json "
                "&& test \"$(stat -c %a /mnt/data/scheduler/runtime.json)\" = 600 "
                "&& test -f /mnt/data/scheduler/identity.json "
                "&& test \"$(stat -c %a /mnt/data/scheduler/identity.json)\" = 600 "
                "&& tmux has-session -t copilot "
                "&& pgrep -af '/opt/copilot-scheduler/worker.py' "
                "&& curl -fsS http://127.0.0.1:8080/health",
            ],
        )

        callback_url = (
            f"https://{sandbox_id}--{config.worker_port}."
            f"{config.location}.adcproxy.io"
        )
        print("Checking that the worker port rejects anonymous requests...")
        try:
            urllib.request.urlopen(callback_url, timeout=30)
        except urllib.error.HTTPError as error:
            if error.code not in {401, 403}:
                raise
        else:
            raise RuntimeError("Worker callback unexpectedly allowed anonymous access.")

        active, scheduled, dead_letter = queue_counts(config, clients)
        if (active, scheduled, dead_letter) != (0, 0, 0):
            raise RuntimeError(
                "The end-to-end test requires an empty queue; recreate the deployment. "
                f"Current counts: {(active, scheduled, dead_letter)}."
            )

        token = secrets.token_hex(8)
        prompt_marker = f"PROMPT_OK:{token}"
        script_marker = f"SCRIPT_OK:{token}"
        scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=6)
        at = scheduled_at.isoformat().replace("+00:00", "Z")

        exec_checked(
            sandbox,
            [
                "bash",
                "-lc",
                "rm -f /mnt/data/i-was-here.txt "
                "/mnt/data/this-is-another-file.txt "
                "/mnt/data/tasks/e2e-touch.sh; "
                "rm -f /mnt/data/scheduler/logs/*.json; "
                "mkdir -p /mnt/data/tasks /mnt/data/scheduler/logs",
            ],
        )

        print(f"Asking Copilot CLI to schedule two tasks for {at}...")
        copilot_prompt = f"""
Read and follow the installed scheduler skill at
/root/.copilot/skills/scheduler/SKILL.md. Work from /mnt/data.

Schedule exactly two one-time tasks for {at}. Do not execute either task now.

1. Schedule a prompt task whose prompt instructs the future Copilot invocation
   to create /mnt/data/i-was-here.txt containing exactly:
   {prompt_marker}
2. First create /mnt/data/tasks/e2e-touch.sh as a safe Bash script which writes
   exactly {script_marker} to /mnt/data/this-is-another-file.txt. Then schedule
   that script as a script task with no arguments.

Use the schedule-task command from the skill for both scheduled messages.
"""
        copilot_output = exec_checked(
            sandbox,
            ["copilot", "--allow-all-tools", "-p", copilot_prompt],
        )
        print(copilot_output.strip().encode("ascii", "backslashreplace").decode())

        if exec_checked(
            sandbox,
            ["bash", "-lc", "test -f /mnt/data/tasks/e2e-touch.sh && echo ready"],
        ).strip() != "ready":
            raise RuntimeError("Copilot did not create the scheduled script.")

        print("Verifying that both future messages exist in Service Bus...")
        wait_for_queue(
            config,
            clients,
            active=0,
            scheduled=2,
            dead_letter=0,
        )

        print("Waiting for the sandbox to auto-suspend...")
        wait_for_state(
            clients,
            sandbox_id,
            "stopped",
            config.auto_suspend_seconds + 240,
        )
        if datetime.now(timezone.utc) >= scheduled_at:
            raise RuntimeError("Sandbox did not suspend before the tasks became due.")

        print("Sandbox is stopped; waiting for the Service Bus trigger to wake it...")
        wake_timeout = max(
            300,
            int((scheduled_at - datetime.now(timezone.utc)).total_seconds()) + 300,
        )
        wait_for_state(clients, sandbox_id, "running", wake_timeout)

        sandbox = clients.group.get_sandbox_client(sandbox_id)
        print("Sandbox woke; waiting for both scheduled tasks to finish...")
        wait_for_markers(sandbox, prompt_marker, script_marker)

        prompt_value = exec_checked(
            sandbox, ["cat", "/mnt/data/i-was-here.txt"]
        ).strip()
        script_value = exec_checked(
            sandbox, ["cat", "/mnt/data/this-is-another-file.txt"]
        ).strip()
        if prompt_value != prompt_marker or script_value != script_marker:
            raise RuntimeError(
                f"Unexpected marker contents: {prompt_value!r}, {script_value!r}."
            )

        exec_checked(
            sandbox,
            [
                "bash",
                "-lc",
                "test -f /root/.copilot/skills/scheduler/SKILL.md "
                "&& pgrep -af '/opt/copilot-scheduler/worker.py' "
                "&& test \"$(find /mnt/data/scheduler/logs -name '*.json' "
                "-type f | wc -l)\" -eq 2",
            ],
        )
        lifecycle = clients.group.get_sandbox(sandbox_id).lifecycle
        auto_suspend = lifecycle.auto_suspend if lifecycle else None
        if (
            not auto_suspend
            or not auto_suspend.enabled
            or auto_suspend.interval != config.auto_suspend_seconds
            or auto_suspend.mode != "Disk"
        ):
            raise RuntimeError(
                f"Worker did not restore the auto-suspend policy: {lifecycle}."
            )
        print("Verifying that Service Bus consumed both messages...")
        wait_for_queue(
            config,
            clients,
            active=0,
            scheduled=0,
            dead_letter=0,
            timeout=180,
        )

        print("Cleaning test artifacts...")
        exec_checked(
            sandbox,
            [
                "bash",
                "-lc",
                "rm -f /mnt/data/i-was-here.txt "
                "/mnt/data/this-is-another-file.txt "
                "/mnt/data/tasks/e2e-touch.sh "
                "/mnt/data/scheduler/logs/*.json",
            ],
        )
        print("Clean Copilot schedule -> suspend -> wake end-to-end test passed.")


if __name__ == "__main__":
    main()
