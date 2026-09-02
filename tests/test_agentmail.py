from __future__ import annotations

import importlib.util
import base64
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))

import azure.functions as func
from svix.webhooks import Webhook

from bridge_core import IgnoredEvent, build_task, parse_allowed_senders
import function_app


def load_worker():
    path = ROOT / "image" / "scheduler" / "worker.py"
    spec = importlib.util.spec_from_file_location("sandbox_worker", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class BridgeCoreTests(unittest.TestCase):
    def payload(self, sender: str = "Trusted <trusted@example.com>"):
        return {
            "event_type": "message.received",
            "event_id": "evt_123",
            "message": {
                "inbox_id": "agent@agentmail.to",
                "message_id": "<message@example.com>",
                "thread_id": "thd_123",
                "from": sender,
            },
        }

    def test_builds_stable_reference_task(self):
        now = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
        task = build_task(
            self.payload(),
            expected_inbox="agent@agentmail.to",
            allowed_senders=parse_allowed_senders("TRUSTED@example.com"),
            now=now,
        )
        repeated = build_task(
            self.payload(),
            expected_inbox="agent@agentmail.to",
            allowed_senders=frozenset({"trusted@example.com"}),
            now=now,
        )
        self.assertEqual(task["id"], repeated["id"])
        self.assertEqual(task["type"], "agentmail")
        self.assertNotIn("text", task)
        self.assertEqual(task["scheduled_at"], "2026-09-02T08:00:00Z")

    def test_ignores_sender_outside_allowlist(self):
        with self.assertRaises(IgnoredEvent):
            build_task(
                self.payload("attacker@example.net"),
                expected_inbox="agent@agentmail.to",
                allowed_senders=frozenset({"trusted@example.com"}),
            )

    def test_rejects_non_address_allowlist_entry(self):
        with self.assertRaises(ValueError):
            parse_allowed_senders("example.com")


class WorkerAgentMailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = load_worker()

    def test_validates_agentmail_reference(self):
        task = {
            "version": 1,
            "id": "ecfcc440-94e5-5b7b-bc21-a9b8b93d3d18",
            "type": "agentmail",
            "agentmail": {
                "event_id": "evt_123",
                "inbox_id": "agent@agentmail.to",
                "message_id": "<message@example.com>",
                "thread_id": "thd_123",
            },
        }
        self.assertEqual(self.worker.validate_task(task), task)

    def test_builds_prompt_from_extracted_text(self):
        prompt = self.worker.agentmail_prompt(
            {
                "inbox_id": "agent@agentmail.to",
                "message_id": "<message@example.com>",
                "thread_id": "thd_123",
                "from": "Trusted <trusted@example.com>",
                "subject": "Run report",
                "text": "quoted history",
                "extracted_text": "Create the report.",
                "attachments": [{"filename": "input.csv", "content_type": "text/csv"}],
            },
            expected_inbox="agent@agentmail.to",
            expected_message="<message@example.com>",
            expected_thread="thd_123",
            allowed_senders=frozenset({"trusted@example.com"}),
            max_body_chars=1000,
        )
        self.assertIn("Create the report.", prompt)
        self.assertNotIn("quoted history", prompt)
        self.assertIn("input.csv", prompt)


class FunctionTests(unittest.TestCase):
    def signed_request(self) -> func.HttpRequest:
        payload = {
            "event_type": "message.received",
            "event_id": "evt_123",
            "message": {
                "inbox_id": "agent@agentmail.to",
                "message_id": "<message@example.com>",
                "thread_id": "thd_123",
                "from": "trusted@example.com",
            },
        }
        body = json.dumps(payload, separators=(",", ":"))
        timestamp = datetime.now(timezone.utc)
        signature = Webhook(os.environ["AGENTMAIL_WEBHOOK_SECRET"]).sign(
            "msg_123", timestamp, body
        )
        return func.HttpRequest(
            method="POST",
            url="https://example.test/api/agentmail",
            headers={
                "svix-id": "msg_123",
                "svix-timestamp": str(int(timestamp.timestamp())),
                "svix-signature": signature,
            },
            params={},
            route_params={},
            body=body.encode(),
        )

    @patch.dict(
        os.environ,
        {
            "AGENTMAIL_WEBHOOK_SECRET": (
                "whsec_" + base64.b64encode(b"test-secret-value").decode()
            ),
            "AGENTMAIL_INBOX_ID": "agent@agentmail.to",
            "AGENTMAIL_ALLOWED_SENDERS": "trusted@example.com",
            "SERVICE_BUS_NAMESPACE": "example.servicebus.windows.net",
            "SERVICE_BUS_QUEUE": "tasks",
        },
        clear=False,
    )
    @patch.object(function_app, "DefaultAzureCredential")
    @patch.object(function_app, "ServiceBusClient")
    def test_signed_webhook_queues_reference(self, service_bus, credential):
        client = MagicMock()
        sender = MagicMock()
        service_bus.return_value.__enter__.return_value = client
        client.get_queue_sender.return_value.__enter__.return_value = sender

        response = function_app.agentmail_webhook(self.signed_request())

        self.assertEqual(response.status_code, 204)
        sender.send_messages.assert_called_once()
        queued = sender.send_messages.call_args.args[0]
        self.assertIn('"type":"agentmail"', str(queued))
        credential.return_value.close.assert_called_once()

    @patch.dict(
        os.environ,
        {
            "AGENTMAIL_WEBHOOK_SECRET": (
                "whsec_" + base64.b64encode(b"test-secret-value").decode()
            ),
        },
        clear=False,
    )
    @patch.object(function_app, "ServiceBusClient")
    def test_invalid_signature_never_opens_service_bus(self, service_bus):
        request = func.HttpRequest(
            method="POST",
            url="https://example.test/api/agentmail",
            headers={},
            params={},
            route_params={},
            body=b"{}",
        )
        response = function_app.agentmail_webhook(request)
        self.assertEqual(response.status_code, 401)
        service_bus.assert_not_called()


if __name__ == "__main__":
    unittest.main()
