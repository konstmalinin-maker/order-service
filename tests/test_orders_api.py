"""API заказов, изолированная БД и реальный сгенерированный HTTP SDK с transport."""

import sqlite3
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories import OrderRepository
from app.user_client import ClientConfig, UserClient
from tests.test_client import USER


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    c = UserClient(
        "http://users", transport=httpx.MockTransport(lambda req: httpx.Response(200, json=USER)), sleep=lambda s: None
    )
    with TestClient(create_app(tmp_path / "orders.sqlite3", user_client=c)) as value:
        yield value


def test_create_list_get_and_outbox(client):
    assert client.get("/orders").json() == []
    r = client.post("/orders", json={"user_id": 1, "total": 1500.50})
    assert r.status_code == 201
    order = r.json()
    assert order["total"] == 1500.5 and order["status"] == "created"
    assert client.get("/orders").json() == [order]
    assert client.get("/orders/" + str(order["id"])).json() == order
    pending = client.app.state.repo.pending()
    assert len(pending) == 1 and pending[0]["payload"]["type"] == "OrderCreated"
    assert pending[0]["payload"]["order"]["id"] == order["id"]
    assert client.get("/orders/9999").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user_id": 0, "total": 10},
        {"user_id": True, "total": 10},
        {"user_id": "1", "total": 10},
        {"user_id": 1, "total": 0},
        {"user_id": 1, "total": -1},
        {"user_id": 1, "total": True},
        {"user_id": 1, "total": "10"},
        {"user_id": 1, "total": 1.001},
        {"user_id": 1, "total": 1000000001},
        {"user_id": 1, "total": 10, "status": "paid"},
    ],
)
def test_invalid_input_no_http_or_write(client, payload):
    with patch.object(client.app.state.user_client, "get_user") as get:
        assert client.post("/orders", json=payload).status_code == 400
    get.assert_not_called()
    assert client.get("/orders").json() == []


@pytest.mark.parametrize("total", [0.01, 1000000000, 10, 10.10])
def test_money_boundaries(client, total):
    result = client.post("/orders", json={"user_id": 1, "total": total})
    assert result.status_code == 201 and result.json()["total"] == total


def test_health_schema_docs_metrics(client):
    assert client.get("/health").json()["circuit_breaker"] == "closed"
    assert "swagger-ui" in client.get("/docs").text
    schema = client.get("/openapi.json").json()
    assert "503" in schema["paths"]["/orders"]["post"]["responses"]
    assert "422" not in schema["paths"]["/orders"]["post"]["responses"]
    assert "api_requests_total" in client.get("/metrics/").text


@pytest.mark.parametrize("status,expected", [(404, 404), (500, 503)])
def test_dependency_failures_preserve_database(tmp_path, monkeypatch, status, expected):
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    c = UserClient(
        "http://users",
        config=ClientConfig(threshold=1),
        transport=httpx.MockTransport(lambda req: httpx.Response(status, json={"error": "Нет"})),
        sleep=lambda s: None,
    )
    with TestClient(create_app(tmp_path / "orders.sqlite3", user_client=c)) as api:
        r = api.post("/orders", json={"user_id": 1, "total": 10})
        assert r.status_code == expected and api.get("/orders").json() == []
        if expected == 503:
            assert r.headers["Retry-After"] == "30"
            assert api.get("/health").json()["circuit_breaker"] == "open"


def test_repository_persistence_and_atomic_outbox(tmp_path):
    path = tmp_path / "db.sqlite3"
    repo = OrderRepository(path)
    order = repo.create(1, Decimal("10.10"), {"traceparent": "test"})
    second = OrderRepository(path)
    assert second.get(order["id"]) == order and second.pending()[0]["headers"] == {"traceparent": "test"}
    with second.connect() as db:
        db.execute("CREATE TRIGGER outbox_fail BEFORE INSERT ON outbox BEGIN SELECT RAISE(FAIL, 'disk'); END")
    with pytest.raises(sqlite3.IntegrityError):
        second.create(1, Decimal("20"), {})
    assert second.list_all() == [order]


def test_local_database_error(client):
    with patch.object(client.app.state.repo, "list_all", side_effect=sqlite3.OperationalError("disk")):
        assert client.get("/orders").status_code == 503


def test_invalid_path_json(client):
    assert client.get("/orders/abc").status_code == 400
    assert client.post("/orders", content="{", headers={"Content-Type": "application/json"}).status_code == 400
