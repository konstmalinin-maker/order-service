"""Заказы и transactional outbox в отдельной SQLite БД."""

import json
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.common_models import utc_now


class OrderRepository:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, total_cents INTEGER NOT NULL CHECK(total_cents>0), status TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS outbox (event_id TEXT PRIMARY KEY, order_id INTEGER UNIQUE NOT NULL, payload TEXT NOT NULL, headers TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0)"
            )

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def order(row):
        if row is None:
            return None
        item = dict(row)
        item["total"] = item.pop("total_cents") / 100
        return item

    def create(self, user_id: int, total: Decimal, headers: dict):
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                "INSERT INTO orders(user_id,total_cents,status,created_at) VALUES (?,?,?,?)",
                (user_id, int(total * 100), "created", utc_now()),
            )
            order = self.order(db.execute("SELECT * FROM orders WHERE id=?", (cursor.lastrowid,)).fetchone())
            event = {"event_id": str(uuid4()), "type": "OrderCreated", "version": 1, "order": order}
            db.execute(
                "INSERT INTO outbox(event_id,order_id,payload,headers) VALUES (?,?,?,?)",
                (event["event_id"], order["id"], json.dumps(event), json.dumps(headers)),
            )
            return order

    def get(self, order_id):
        with closing(self.connect()) as db:
            return self.order(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())

    def list_all(self):
        with closing(self.connect()) as db:
            return [self.order(row) for row in db.execute("SELECT * FROM orders ORDER BY id")]

    def pending(self):
        with closing(self.connect()) as db:
            return [
                {
                    "event_id": row["event_id"],
                    "payload": json.loads(row["payload"]),
                    "headers": json.loads(row["headers"]),
                }
                for row in db.execute("SELECT * FROM outbox WHERE published=0 ORDER BY order_id LIMIT 50")
            ]

    def mark_published(self, event_id):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE outbox SET published=1 WHERE event_id=?", (event_id,))

    def ready(self):
        with closing(self.connect()) as db:
            return db.execute("SELECT 1").fetchone()[0] == 1
