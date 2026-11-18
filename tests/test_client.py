"""HTTP timeout/retry/generated SDK, circuit breaker и HALF_OPEN-проверка."""

from unittest.mock import Mock

import httpx
import pytest

from app.user_client import CircuitBreaker, UserClient, UserNotFound, UserUnavailable

USER = {"id": 1, "email": "a@example.com", "name": "Иван", "created_at": "2026-11-18T10:00:00Z"}


def test_generated_client_and_timeout():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=USER)

    client = UserClient("http://users", transport=httpx.MockTransport(handler))
    assert client.get_user(1) == USER
    assert str(requests[0].url) == "http://users/users/1"
    assert requests[0].extensions["timeout"]["connect"] == 2.0
    client.close()


def test_retries_exponential_then_success():
    calls = []
    sleep = Mock()

    def handler(request):
        calls.append(request)
        return httpx.Response(500, json={}) if len(calls) < 3 else httpx.Response(200, json=USER)

    c = UserClient("http://users", transport=httpx.MockTransport(handler), sleep=sleep)
    assert c.get_user(1) == USER and len(calls) == 3
    assert [call.args[0] for call in sleep.call_args_list] == [0.5, 1.0]
    assert c.breaker.state == "closed"
    c.close()


@pytest.mark.parametrize("failure", ["timeout", "connection", "500", "503", "invalid_json", "invalid_schema"])
def test_failure_and_open_circuit(failure):
    calls = []
    clock = [0.0]

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if failure == "connection":
            raise httpx.ConnectError("offline", request=request)
        if failure == "invalid_json":
            return httpx.Response(200, content=b"{")
        if failure == "invalid_schema":
            return httpx.Response(200, json={"id": 1})
        return httpx.Response(int(failure), json={})

    c = UserClient("http://users", transport=httpx.MockTransport(handler), sleep=lambda s: None, clock=lambda: clock[0])
    for _ in range(5):
        with pytest.raises(UserUnavailable):
            c.get_user(1)
    assert len(calls) == 15 and c.breaker.state == "open"
    with pytest.raises(UserUnavailable, match="breaker"):
        c.get_user(1)
    assert len(calls) == 15
    c.close()


def test_404_is_not_retried_and_resets_failure():
    sleep = Mock()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404, json={"error": "Нет"})

    c = UserClient("http://users", transport=httpx.MockTransport(handler), sleep=sleep)
    c.breaker.failures = 4
    with pytest.raises(UserNotFound):
        c.get_user(1)
    assert len(calls) == 1 and c.breaker.failures == 0
    sleep.assert_not_called()
    c.close()


def test_half_open_recovery_and_stale_results():
    clock = [0.0]
    gauge = Mock()
    b = CircuitBreaker(1, 30, clock=lambda: clock[0], gauge=gauge)
    old = b.permit()
    b.failure(old)
    assert b.state == "open"
    b.success(old)
    b.failure(old)
    assert b.state == "open"
    clock[0] = 30
    assert b.state == "half_open"
    token = b.permit()
    with pytest.raises(UserUnavailable):
        b.permit()
    b.success(token)
    assert b.state == "closed" and b.failures == 0
    gauge.set.assert_called_with(0)


def test_failed_half_open_probe_reopens():
    clock = [0.0]
    b = CircuitBreaker(1, 2, clock=lambda: clock[0])
    b.failure(b.permit())
    clock[0] = 3
    token = b.permit()
    b.failure(token)
    assert b.state == "open" and b.opened_at == 3
