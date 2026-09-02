#!/usr/bin/env python3
"""Schedule durable Sandbox tasks through Azure Service Bus."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage, TransportType


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("Timestamp must include a UTC offset or Z.")
    parsed = parsed.astimezone(timezone.utc)
    if parsed <= datetime.now(timezone.utc):
        raise argparse.ArgumentTypeError("Timestamp must be in the future.")
    return parsed


def service_bus_settings() -> tuple[str, str]:
    namespace = os.environ.get("SERVICE_BUS_NAMESPACE", "")
    queue = os.environ.get("SERVICE_BUS_QUEUE", "")
    if not namespace or not queue:
        raise RuntimeError("SERVICE_BUS_NAMESPACE and SERVICE_BUS_QUEUE are required.")
    if "." not in namespace:
        namespace = f"{namespace}.servicebus.windows.net"
    return namespace, queue


def schedule_task(task: dict[str, Any], enqueue_at: datetime) -> int:
    namespace, queue = service_bus_settings()
    credential = DefaultAzureCredential()
    try:
        with ServiceBusClient(
            namespace,
            credential,
            transport_type=TransportType.AmqpOverWebsocket,
            connection_verify=os.environ.get("SSL_CERT_FILE"),
        ) as client:
            with client.get_queue_sender(queue) as sender:
                message = ServiceBusMessage(
                    json.dumps(task, separators=(",", ":")),
                    content_type="application/json",
                    message_id=task["id"],
                )
                return sender.schedule_messages(message, enqueue_at)[0]
    finally:
        credential.close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="type", required=True)
    for task_type in ("prompt", "script"):
        command = subparsers.add_parser(task_type)
        command.add_argument("--at", required=True, type=parse_utc)
        command.add_argument("--every", choices=("daily", "weekly"))
        command.add_argument("--interval", type=int, default=1)
        if task_type == "prompt":
            command.add_argument("--prompt", required=True)
        else:
            command.add_argument("--script", required=True)
            command.add_argument("--arg", action="append", default=[])
    return result


def main() -> None:
    args = parser().parse_args()
    if args.interval < 1:
        raise SystemExit("--interval must be at least 1")
    task: dict[str, Any] = {
        "version": 1,
        "id": str(uuid.uuid4()),
        "type": args.type,
        "scheduled_at": args.at.isoformat().replace("+00:00", "Z"),
        "recurrence": (
            {"frequency": args.every, "interval": args.interval}
            if args.every
            else None
        ),
    }
    if args.type == "prompt":
        task["prompt"] = args.prompt
    else:
        task["script"] = args.script
        task["args"] = args.arg
    sequence_number = schedule_task(task, args.at)
    print(json.dumps({"task": task, "sequence_number": sequence_number}, indent=2))


if __name__ == "__main__":
    main()
