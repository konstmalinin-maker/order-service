"""Outbox → RabbitMQ, подтверждения, повторная доставка допустима."""

import json
import logging
import threading

import pika
from opentelemetry import propagate, trace

logger = logging.getLogger(__name__)


class EventPublisher:
    def __init__(self, url, queue="order_created"):
        self.url = url
        self.queue = queue

    def publish(self, event):
        parameters = pika.URLParameters(self.url)
        parameters.socket_timeout = 3
        parameters.blocked_connection_timeout = 3
        connection = pika.BlockingConnection(parameters)
        try:
            channel = connection.channel()
            channel.queue_declare(queue=self.queue, durable=True)
            channel.confirm_delivery()
            context = propagate.extract(event["headers"])
            with trace.get_tracer(__name__).start_as_current_span("OrderCreated publish", context=context):
                headers = {}
                propagate.inject(headers)
                channel.basic_publish(
                    exchange="",
                    routing_key=self.queue,
                    body=json.dumps(event["payload"]).encode(),
                    properties=pika.BasicProperties(
                        delivery_mode=2, content_type="application/json", message_id=event["event_id"], headers=headers
                    ),
                    mandatory=True,
                )
            logger.info("OrderCreated published event_id=%s", event["event_id"])
        finally:
            connection.close()


class OutboxDispatcher:
    def __init__(self, repo, publisher):
        self.repo = repo
        self.publisher = publisher
        self.stop_event = threading.Event()
        self.thread = None

    def drain_once(self):
        count = 0
        for event in self.repo.pending():
            self.publisher.publish(event)
            self.repo.mark_published(event["event_id"])
            count += 1
        return count

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.drain_once()
            except Exception as exc:
                logger.warning("Outbox сохраняет событие для повтора: %s", type(exc).__name__)
            self.stop_event.wait(1)

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="outbox", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
