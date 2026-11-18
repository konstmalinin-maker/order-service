"""FastAPI order-service: отдельная БД, generated SDK, tracing и события."""

import os
import sqlite3
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Path
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry import propagate

from app.common_models import Error
from app.messaging import EventPublisher, OutboxDispatcher
from app.models import Order, OrderInput
from app.observability import configure_observability, local_swagger
from app.repositories import OrderRepository
from app.user_client import ClientConfig, UserClient, UserNotFound, UserUnavailable


def create_app(db_path=None, user_client=None, publisher=None):
    repo = OrderRepository(db_path or os.getenv("DATA_PATH", "orders.sqlite3"))
    rabbit = os.environ.get("RABBITMQ_URL")
    dispatcher = OutboxDispatcher(repo, publisher or EventPublisher(rabbit)) if (rabbit or publisher) else None

    @asynccontextmanager
    async def lifespan(app):
        if dispatcher is not None:
            dispatcher.start()
        yield
        if dispatcher is not None:
            dispatcher.stop()
        app.state.user_client.close()

    app = FastAPI(title="Order Service", version="1.0.0", docs_url=None, redoc_url=None, lifespan=lifespan)
    gauge = configure_observability(app, "order-service")
    client = user_client or UserClient(
        os.getenv("USER_SERVICE_URL", "http://localhost:5001"),
        ClientConfig(
            timeout=float(os.getenv("USER_TIMEOUT", "2")),
            attempts=int(os.getenv("USER_ATTEMPTS", "3")),
            backoff=float(os.getenv("USER_BACKOFF", "0.5")),
            threshold=int(os.getenv("CIRCUIT_THRESHOLD", "5")),
            reset=float(os.getenv("CIRCUIT_RESET", "30")),
        ),
        gauge=gauge,
    )
    app.state.user_client = client
    app.state.repo = repo
    app.state.dispatcher = dispatcher
    local_swagger(app)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse(
            status_code=400,
            content={
                "error": "Некорректные данные запроса",
                "details": [{"field": ".".join(str(v) for v in x["loc"]), "message": x["msg"]} for x in exc.errors()],
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})

    @app.exception_handler(UserNotFound)
    async def missing_user(request, exc):
        return JSONResponse(status_code=404, content={"error": str(exc)})

    @app.exception_handler(UserUnavailable)
    async def unavailable(request, exc):
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "30"},
            content={"error": str(exc), "circuit_breaker": client.breaker.state},
        )

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        return JSONResponse(status_code=503, content={"error": "Хранилище заказов недоступно"})

    @app.get("/health", operation_id="health")
    def health():
        repo.ready()
        return {
            "status": "ok",
            "service": "order-service",
            "storage": "sqlite",
            "circuit_breaker": client.breaker.state,
            "pending_events": len(repo.pending()),
        }

    @app.post(
        "/orders",
        response_model=Order,
        status_code=201,
        tags=["orders"],
        operation_id="create_order",
        responses={400: {"model": Error}, 404: {"model": Error}, 503: {"model": Error}},
    )
    def create_order(data: OrderInput):
        client.get_user(data.user_id)
        carrier = {}
        propagate.inject(carrier)
        return repo.create(data.user_id, data.total, carrier)

    @app.get("/orders", response_model=list[Order], tags=["orders"], operation_id="list_orders")
    def list_orders():
        return repo.list_all()

    @app.get(
        "/orders/{order_id}",
        response_model=Order,
        tags=["orders"],
        operation_id="get_order",
        responses={404: {"model": Error}, 400: {"model": Error}},
    )
    def get_order(order_id: Annotated[int, Path(gt=0)]):
        order = repo.get(order_id)
        if order is None:
            raise HTTPException(404, "Заказ не найден")
        return order

    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        for operations in schema["paths"].values():
            for operation in operations.values():
                if isinstance(operation, dict):
                    operation.get("responses", {}).pop("422", None)
        return schema

    app.openapi = openapi
    return app


app = create_app()
