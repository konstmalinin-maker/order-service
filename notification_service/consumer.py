"""Независимый потребитель OrderCreated: inbox-idempotency и manual ACK."""

import json
import logging
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

import pika
from opentelemetry import propagate, trace
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class OrderData(BaseModel):
    id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    total: float = Field(gt=0)


class OrderCreated(BaseModel):
    event_id: UUID
    type: Literal["OrderCreated"]
    version: Literal[1]
    order: OrderData


class NotificationRepository:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS notifications (event_id TEXT PRIMARY KEY, order_id INTEGER NOT NULL, user_id INTEGER NOT NULL, received_at TEXT NOT NULL)"
            )

    def record(self, event):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            result = db.execute(
                "INSERT OR IGNORE INTO notifications VALUES (?,?,?,?)",
                (str(event.event_id), event.order.id, event.order.user_id, datetime.now(timezone.utc).isoformat()),
            )
            return result.rowcount == 1

    def list_all(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM notifications ORDER BY rowid")]


class NotificationConsumer:
    def __init__(self, repo, url, queue="order_created"):
        self.repo = repo
        self.url = url
        self.queue = queue
        self.stop_event = threading.Event()
        self.connected = False
        self.thread = None

    def handle(self, channel, method, properties, body):
        try:
            event = OrderCreated.model_validate(json.loads(body))
        except (ValidationError, ValueError, TypeError):
            logger.error("notification_invalid_event")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        try:
            context = propagate.extract(properties.headers or {})
            with trace.get_tracer(__name__).start_as_current_span("OrderCreated consume", context=context):
                fresh = self.repo.record(event)
                logger.info(
                    "%s event_id=%s order_id=%s user_id=%s",
                    "notification_sent" if fresh else "notification_duplicate",
                    event.event_id,
                    event.order.id,
                    event.order.user_id,
                )
            channel.basic_ack(delivery_tag=method.delivery_tag)
        except sqlite3.Error:
            logger.exception("notification_storage_unavailable")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    def loop(self):
        while not self.stop_event.is_set():
            connection = None
            try:
                parameters = pika.URLParameters(self.url)
                parameters.socket_timeout = 3
                parameters.blocked_connection_timeout = 3
                connection = pika.BlockingConnection(parameters)
                channel = connection.channel()
                channel.queue_declare(queue=self.queue, durable=True)
                channel.basic_qos(prefetch_count=1)
                channel.basic_consume(queue=self.queue, on_message_callback=self.handle, auto_ack=False)
                self.connected = True
                while not self.stop_event.is_set() and connection.is_open:
                    connection.process_data_events(time_limit=1)
            except (pika.exceptions.AMQPError, OSError) as exc:
                logger.warning("notification_reconnect: %s", type(exc).__name__)
            finally:
                self.connected = False
                if connection is not None and connection.is_open:
                    connection.close()
            self.stop_event.wait(1)

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True, name="notifications")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
