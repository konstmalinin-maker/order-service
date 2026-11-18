"""Notification-service: consumer + health/diagnostics; отдельный процесс и SQLite."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.observability import configure_observability
from notification_service.consumer import NotificationConsumer, NotificationRepository


def create_app(db_path=None, consumer_factory=NotificationConsumer):
    repo = NotificationRepository(db_path or os.getenv("DATA_PATH", "notifications.sqlite3"))
    consumer = consumer_factory(repo, os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"))

    @asynccontextmanager
    async def lifespan(app):
        consumer.start()
        yield
        consumer.stop()

    app = FastAPI(title="Notification Service", lifespan=lifespan)
    configure_observability(app, "notification-service")
    app.state.repo = repo
    app.state.consumer = consumer

    @app.get("/live")
    def live():
        return {"status": "ok"}

    @app.get("/health")
    def health():
        return JSONResponse(
            status_code=200 if consumer.connected else 503,
            content={
                "status": "ok" if consumer.connected else "waiting_for_broker",
                "service": "notification-service",
                "notifications": len(repo.list_all()),
            },
        )

    @app.get("/notifications")
    def notifications():
        return repo.list_all()

    return app


app = create_app()
