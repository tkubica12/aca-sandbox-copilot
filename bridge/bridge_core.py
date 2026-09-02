"""Pure AgentMail webhook validation and Service Bus task construction."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from email.parser import Parser
from email.policy import default as email_policy
from typing import Any

TASK_NAMESPACE = uuid.UUID("d79921e9-5254-48f1-b8cd-8330796c9cca")


class IgnoredEvent(Exception):
    """A valid webhook event that should be acknowledged without dispatch."""


def parse_allowed_senders(value: str) -> frozenset[str]:
    senders: set[str] = set()
    for item in value.split(","):
        candidate = item.strip().casefold()
        if candidate:
            if candidate.count("@") != 1 or any(char.isspace() for char in candidate):
                raise ValueError(
                    "AGENTMAIL_ALLOWED_SENDERS accepts exact email addresses only."
                )
            senders.add(candidate)
    if not senders:
        raise ValueError("AGENTMAIL_ALLOWED_SENDERS must contain at least one address.")
    return frozenset(senders)


def sender_address(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("AgentMail event sender must be a string.")
    header = Parser(policy=email_policy).parsestr(f"From: {value}\n")["from"]
    addresses = header.addresses if header else ()
    if len(addresses) != 1 or not addresses[0].addr_spec:
        raise ValueError("AgentMail event must contain exactly one sender address.")
    return addresses[0].addr_spec.casefold()


def build_task(
    payload: dict[str, Any],
    *,
    expected_inbox: str,
    allowed_senders: frozenset[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    if payload.get("event_type") != "message.received":
        raise IgnoredEvent("Only message.received events dispatch tasks.")
    event_id = payload.get("event_id")
    message = payload.get("message")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("AgentMail event_id is required.")
    if not isinstance(message, dict):
        raise ValueError("AgentMail message object is required.")

    inbox_id = message.get("inbox_id")
    message_id = message.get("message_id")
    thread_id = message.get("thread_id")
    for name, value in (
        ("inbox_id", inbox_id),
        ("message_id", message_id),
        ("thread_id", thread_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"AgentMail message {name} is required.")
    if inbox_id.casefold() != expected_inbox.casefold():
        raise IgnoredEvent("Event belongs to a different AgentMail inbox.")

    sender = sender_address(message.get("from"))
    if sender not in allowed_senders:
        raise IgnoredEvent("Sender is not allowlisted.")

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "version": 1,
        "id": str(uuid.uuid5(TASK_NAMESPACE, event_id)),
        "type": "agentmail",
        "scheduled_at": timestamp.isoformat().replace("+00:00", "Z"),
        "agentmail": {
            "event_id": event_id,
            "inbox_id": inbox_id,
            "message_id": message_id,
            "thread_id": thread_id,
        },
    }
