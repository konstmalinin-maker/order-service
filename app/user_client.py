"""HTTP-клиент на основе сгенерированного OpenAPI SDK, retry и breaker."""

import logging
import os
import threading
import time
from dataclasses import dataclass

import httpx

from app.models import UserSnapshot
from user_contract_client import Client
from user_contract_client.api.users import get_user

logger = logging.getLogger(__name__)


class UserNotFound(Exception):
    pass


class UserUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ClientConfig:
    timeout: float = 2.0
    attempts: int = 3
    backoff: float = 0.5
    threshold: int = 5
    reset: float = 30.0


class CircuitBreaker:
    def __init__(self, threshold=5, reset=30, clock=time.monotonic, gauge=None):
        self.threshold = threshold
        self.reset = reset
        self.clock = clock
        self.gauge = gauge
        self.failures = 0
        self.generation = 0
        self.opened_at = None
        self.current = "closed"
        self.probing = False
        self.lock = threading.Lock()

    def refresh(self):
        if self.current == "open" and self.clock() - self.opened_at >= self.reset:
            self.current = "half_open"
            self.probing = False
        if self.gauge is not None:
            self.gauge.set({"closed": 0, "half_open": 0.5, "open": 1}[self.current])

    @property
    def state(self):
        with self.lock:
            self.refresh()
            return self.current

    def permit(self):
        with self.lock:
            self.refresh()
            if self.current == "open" or (self.current == "half_open" and self.probing):
                raise UserUnavailable("user-service недоступен: circuit breaker открыт")
            if self.current == "half_open":
                self.probing = True
            return self.generation

    def success(self, generation):
        with self.lock:
            if generation != self.generation:
                return
            self.failures = 0
            if self.current != "closed":
                self.generation += 1
            self.current = "closed"
            self.probing = False
            self.opened_at = None
            self.refresh()

    def failure(self, generation):
        with self.lock:
            if generation != self.generation:
                return
            self.failures += 1
            if self.current == "half_open" or self.failures >= self.threshold:
                self.current = "open"
                self.opened_at = self.clock()
                self.generation += 1
                logger.warning("Circuit breaker открыт")
            self.probing = False
            self.refresh()


class UserClient:
    def __init__(self, base_url, config=None, transport=None, sleep=time.sleep, clock=time.monotonic, gauge=None):
        self.config = config or ClientConfig()
        self.sleep = sleep
        self.breaker = CircuitBreaker(self.config.threshold, self.config.reset, clock, gauge)
        self.sdk = Client(
            base_url=base_url.rstrip("/"), timeout=httpx.Timeout(self.config.timeout), raise_on_unexpected_status=False
        )
        if transport is not None:
            self.sdk.set_httpx_client(httpx.Client(base_url=base_url, transport=transport, timeout=self.config.timeout))
        if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor.instrument_client(self.sdk.get_httpx_client())

    def get_user(self, user_id):
        generation = self.breaker.permit()
        for attempt in range(self.config.attempts):
            try:
                response = get_user.sync_detailed(user_id, client=self.sdk)
                if response.status_code == 404:
                    self.breaker.success(generation)
                    raise UserNotFound("Пользователь не найден")
                if response.status_code == 200:
                    user = UserSnapshot.model_validate(response.parsed.to_dict())
                    self.breaker.success(generation)
                    return user.model_dump(mode="json")
                logger.warning("user-service: HTTP %s, попытка %s", response.status_code, attempt + 1)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                logger.warning("user-service: %s, попытка %s", type(exc).__name__, attempt + 1)
            if attempt + 1 < self.config.attempts:
                self.sleep(self.config.backoff * 2**attempt)
        self.breaker.failure(generation)
        raise UserUnavailable(f"user-service недоступен после {self.config.attempts} попыток")

    def close(self):
        self.sdk.get_httpx_client().close()
