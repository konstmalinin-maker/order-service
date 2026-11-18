"""Outbox, идемпотентный consumer, обработка ошибок и жизненный цикл."""

import json
import os
import sqlite3
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.messaging import EventPublisher, OutboxDispatcher
from app.repositories import OrderRepository
from notification_service.consumer import NotificationConsumer, NotificationRepository
from notification_service.main import create_app


@pytest.fixture
def event():
    return {
        "event_id": str(uuid4()),
        "type": "OrderCreated",
        "version": 1,
        "order": {"id": 1, "user_id": 1, "total": 10.50},
    }


def test_outbox_retries_without_losing_order(tmp_path):
    repo = OrderRepository(tmp_path / "orders.sqlite3")
    order = repo.create(1, Decimal("10.50"), {})
    publisher = Mock()
    publisher.publish.side_effect = RuntimeError("offline")
    dispatcher = OutboxDispatcher(repo, publisher)
    with pytest.raises(RuntimeError):
        dispatcher.drain_once()
    assert repo.get(order["id"]) == order and len(repo.pending()) == 1
    publisher.publish.side_effect = None
    assert dispatcher.drain_once() == 1 and repo.pending() == []
    assert dispatcher.drain_once() == 0


def test_publisher_durable_confirm_and_close(event):
    connection = Mock()
    channel = connection.channel.return_value
    with patch("app.messaging.pika.BlockingConnection", return_value=connection):
        EventPublisher("amqp://guest:guest@localhost/", queue="test").publish(
            {"event_id": event["event_id"], "payload": event, "headers": {}}
        )
    channel.queue_declare.assert_called_once_with(queue="test", durable=True)
    channel.confirm_delivery.assert_called_once()
    call = channel.basic_publish.call_args.kwargs
    assert call["properties"].delivery_mode == 2 and call["mandatory"] is True
    assert json.loads(call["body"]) == event
    connection.close.assert_called_once()


def test_publisher_closes_after_failure(event):
    connection = Mock()
    connection.channel.return_value.basic_publish.side_effect = RuntimeError("broker")
    with patch("app.messaging.pika.BlockingConnection", return_value=connection):
        with pytest.raises(RuntimeError):
            EventPublisher("amqp://guest:guest@localhost/").publish(
                {"event_id": event["event_id"], "payload": event, "headers": {}}
            )
    connection.close.assert_called_once()


def test_notification_deduplicates_and_acks(tmp_path, event, caplog):
    repo = NotificationRepository(tmp_path / "notifications.sqlite3")
    consumer = NotificationConsumer(repo, "amqp://guest:guest@localhost/")
    channel = Mock()
    method = SimpleNamespace(delivery_tag=7)
    properties = SimpleNamespace(headers={})
    for _ in range(2):
        consumer.handle(channel, method, properties, json.dumps(event).encode())
    assert len(repo.list_all()) == 1
    assert channel.basic_ack.call_count == 2
    channel.basic_nack.assert_not_called()


@pytest.mark.parametrize("body", [b"{", b"{}", b"null", b'{"type":"Unknown"}'])
def test_poison_message_is_not_requeued(tmp_path, body):
    repo = NotificationRepository(tmp_path / "n.db")
    consumer = NotificationConsumer(repo, "amqp://guest:guest@localhost/")
    channel = Mock()
    consumer.handle(channel, SimpleNamespace(delivery_tag=1), SimpleNamespace(headers=None), body)
    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    assert repo.list_all() == []


def test_storage_failure_requeues(tmp_path, event):
    repo = NotificationRepository(tmp_path / "n.db")
    consumer = NotificationConsumer(repo, "amqp://guest:guest@localhost/")
    channel = Mock()
    with patch.object(repo, "record", side_effect=sqlite3.OperationalError("disk")):
        consumer.handle(
            channel, SimpleNamespace(delivery_tag=1), SimpleNamespace(headers=None), json.dumps(event).encode()
        )
    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
    channel.basic_ack.assert_not_called()


def test_notification_http_health_and_lifecycle(tmp_path):
    created = []

    class Consumer:
        def __init__(self, repo, url):
            self.connected = False
            created.append(self)

        def start(self):
            self.connected = True

        def stop(self):
            self.connected = False

    api = create_app(tmp_path / "n.db", consumer_factory=Consumer)
    with TestClient(api) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/live").status_code == 200
        assert client.get("/notifications").json() == []
        created[0].connected = False
        assert client.get("/health").status_code == 503
    assert not created[0].connected


@pytest.mark.integration
def test_real_rabbitmq_event_delivery_and_deduplication(tmp_path, event):
    url = os.environ.get("TEST_RABBITMQ_URL")
    if not url:
        pytest.skip("TEST_RABBITMQ_URL is required for real broker integration")
    queue = "pr8_test_" + uuid4().hex
    repo = NotificationRepository(tmp_path / "n.db")
    consumer = NotificationConsumer(repo, url, queue=queue)
    consumer.start()
    try:
        publisher = EventPublisher(url, queue=queue)
        envelope = {"event_id": event["event_id"], "payload": event, "headers": {}}
        publisher.publish(envelope)
        publisher.publish(envelope)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not repo.list_all():
            time.sleep(0.1)
        assert len(repo.list_all()) == 1 and repo.list_all()[0]["order_id"] == 1
    finally:
        consumer.stop()
        import pika

        connection = pika.BlockingConnection(pika.URLParameters(url))
        connection.channel().queue_delete(queue=queue)
        connection.close()


def test_dispatcher_thread_can_stop_after_failure(tmp_path):
    repo = OrderRepository(tmp_path / "o.db")
    publisher = Mock()
    dispatcher = OutboxDispatcher(repo, publisher)
    with patch.object(dispatcher, "drain_once", side_effect=RuntimeError("offline")):
        dispatcher.start()
        time.sleep(0.03)
        dispatcher.stop()
    assert dispatcher.thread is not None and not dispatcher.thread.is_alive()
