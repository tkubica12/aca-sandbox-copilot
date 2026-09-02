#!/usr/bin/env python3
"""HTTP worker for Connector Namespace Service Bus trigger callbacks."""

from __future__ import annotations

import base64
import binascii
import json
import os
import subprocess
import sys
import tempfile
import threading
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schedule import schedule_task

DATA_ROOT = Path(os.environ.get("SCHEDULER_DATA_ROOT", "/mnt/data")).resolve()
TASK_ROOT = DATA_ROOT / "tasks"
LOG_ROOT = DATA_ROOT / "scheduler" / "logs"
RUN_LOCK = threading.Lock()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_case_insensitive(mapping: dict[str, Any], key: str) -> Any:
    wanted = key.casefold()
    return next((value for name, value in mapping.items() if name.casefold() == wanted), None)


def decode_message_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("Service Bus item must be an object.")
    content = get_case_insensitive(item, "ContentData")
    if content is None:
        return item
    if not isinstance(content, str):
        raise ValueError("ContentData must be a string.")
    try:
        raw = base64.b64decode(content, validate=True).decode("utf-8")
        task = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        task = json.loads(content)
    if not isinstance(task, dict):
        raise ValueError("Decoded task must be an object.")
    return task


def extract_tasks(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("Callback payload must be an object.")
    if get_case_insensitive(payload, "version") is not None:
        return [payload]
    if get_case_insensitive(payload, "ContentData") is not None:
        return [decode_message_item(payload)]
    body = get_case_insensitive(payload, "body")
    if not isinstance(body, dict):
        raise ValueError("Callback payload has no body object.")
    values = get_case_insensitive(body, "value")
    items = values if isinstance(values, list) else [body]
    return [decode_message_item(item) for item in items]


def validate_task(task: dict[str, Any]) -> dict[str, Any]:
    if task.get("version") != 1:
        raise ValueError("Task version must be 1.")
    task_id = task.get("id")
    try:
        uuid.UUID(task_id)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("Task id must be a UUID.") from error
    task_type = task.get("type")
    if task_type == "prompt":
        if not isinstance(task.get("prompt"), str) or not task["prompt"].strip():
            raise ValueError("Prompt task requires a non-empty prompt.")
    elif task_type == "script":
        if not isinstance(task.get("script"), str) or not task["script"].strip():
            raise ValueError("Script task requires a script path.")
        if not isinstance(task.get("args", []), list) or not all(
            isinstance(value, str) for value in task.get("args", [])
        ):
            raise ValueError("Script args must be an array of strings.")
    else:
        raise ValueError("Task type must be prompt or script.")
    recurrence = task.get("recurrence")
    if recurrence is not None:
        if not isinstance(recurrence, dict):
            raise ValueError("Recurrence must be null or an object.")
        if recurrence.get("frequency") not in {"daily", "weekly"}:
            raise ValueError("Recurrence frequency must be daily or weekly.")
        if not isinstance(recurrence.get("interval"), int) or recurrence["interval"] < 1:
            raise ValueError("Recurrence interval must be a positive integer.")
    return task


def script_command(task: dict[str, Any]) -> list[str]:
    relative = Path(task["script"])
    if relative.is_absolute():
        raise ValueError("Script path must be relative to /mnt/data/tasks.")
    script = (TASK_ROOT / relative).resolve()
    if TASK_ROOT not in script.parents or not script.is_file():
        raise ValueError("Script must exist below /mnt/data/tasks.")
    if script.suffix == ".py":
        return ["python3", str(script), *task.get("args", [])]
    if script.suffix == ".sh":
        return ["bash", str(script), *task.get("args", [])]
    raise ValueError("Only .py and .sh scripts are allowed.")


def next_occurrence(task: dict[str, Any]) -> datetime | None:
    recurrence = task.get("recurrence")
    if not recurrence:
        return None
    base_value = task.get("scheduled_at")
    try:
        base = datetime.fromisoformat(base_value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        base = utc_now()
    days = recurrence["interval"] * (7 if recurrence["frequency"] == "weekly" else 1)
    candidate = base.astimezone(timezone.utc) + timedelta(days=days)
    now = utc_now()
    while candidate <= now:
        candidate += timedelta(days=days)
    return candidate


def write_result(task_id: str, result: dict[str, Any]) -> None:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    target = LOG_ROOT / f"{task_id}.json"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=LOG_ROOT, delete=False
    ) as handle:
        json.dump(result, handle, indent=2)
        temporary = Path(handle.name)
    temporary.replace(target)


def execute_task(task: dict[str, Any]) -> dict[str, Any]:
    task = validate_task(task)
    started = utc_now()
    command = (
        ["copilot", "--allow-all-tools", "-p", task["prompt"]]
        if task["type"] == "prompt"
        else script_command(task)
    )
    completed = subprocess.run(
        command,
        cwd=DATA_ROOT,
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("TASK_TIMEOUT_SECONDS", "900")),
        check=False,
    )
    result: dict[str, Any] = {
        "id": task["id"],
        "type": task["type"],
        "started_at": started.isoformat(),
        "finished_at": utc_now().isoformat(),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    if completed.returncode == 0:
        following = next_occurrence(task)
        if following:
            recurring_task = {
                **task,
                "scheduled_at": following.isoformat().replace("+00:00", "Z"),
            }
            result["next_sequence_number"] = schedule_task(recurring_task, following)
            result["next_scheduled_at"] = recurring_task["scheduled_at"]
    write_result(task["id"], result)
    if completed.returncode != 0:
        raise RuntimeError(f"Task {task['id']} exited with {completed.returncode}.")
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "SandboxTaskWorker/1.0"

    def reply(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path != "/health":
            self.reply(404, {"error": "not found"})
            return
        self.reply(200, {"status": "ok"})

    def do_POST(self) -> None:
        if self.path not in {"/", "/tasks"}:
            self.reply(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024 * 1024:
                raise ValueError("Request body must be between 1 byte and 1 MiB.")
            payload = json.loads(self.rfile.read(length))
            with RUN_LOCK:
                results = [execute_task(task) for task in extract_tasks(payload)]
            self.reply(200, {"results": results})
        except (ValueError, json.JSONDecodeError) as error:
            self.reply(400, {"error": str(error)})
        except Exception as error:
            traceback.print_exc()
            self.reply(500, {"error": str(error)})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.log_date_time_string()} {fmt % args}", flush=True)


def main() -> None:
    TASK_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("WORKER_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Sandbox task worker listening on {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
