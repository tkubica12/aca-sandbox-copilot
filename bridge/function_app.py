"""Azure Function that validates AgentMail webhooks and queues sandbox tasks."""

from __future__ import annotations

import json
import logging
import os

import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage, TransportType
from svix.webhooks import Webhook, WebhookVerificationError

from bridge_core import IgnoredEvent, build_task, parse_allowed_senders

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="agentmail", methods=["POST"])
def agentmail_webhook(req: func.HttpRequest) -> func.HttpResponse:
    secret = os.environ["AGENTMAIL_WEBHOOK_SECRET"]
    try:
        payload = Webhook(secret).verify(req.get_body(), dict(req.headers))
    except WebhookVerificationError:
        logging.warning("Rejected AgentMail webhook with an invalid Svix signature.")
        return func.HttpResponse(status_code=401)

    try:
        task = build_task(
            payload,
            expected_inbox=os.environ["AGENTMAIL_INBOX_ID"],
            allowed_senders=parse_allowed_senders(
                os.environ["AGENTMAIL_ALLOWED_SENDERS"]
            ),
        )
    except IgnoredEvent as error:
        logging.info("Acknowledged AgentMail webhook without dispatch: %s", error)
        return func.HttpResponse(status_code=204)
    except ValueError as error:
        logging.warning("Rejected malformed AgentMail webhook: %s", error)
        return func.HttpResponse(str(error), status_code=400)

    namespace = os.environ["SERVICE_BUS_NAMESPACE"]
    if "." not in namespace:
        namespace = f"{namespace}.servicebus.windows.net"
    credential = DefaultAzureCredential()
    try:
        with ServiceBusClient(
            namespace,
            credential,
            transport_type=TransportType.AmqpOverWebsocket,
        ) as client:
            with client.get_queue_sender(os.environ["SERVICE_BUS_QUEUE"]) as sender:
                sender.send_messages(
                    ServiceBusMessage(
                        json.dumps(task, separators=(",", ":")),
                        content_type="application/json",
                        message_id=task["id"],
                    )
                )
    finally:
        credential.close()
    logging.info("Queued AgentMail event %s as task %s.", payload["event_id"], task["id"])
    return func.HttpResponse(status_code=204)
