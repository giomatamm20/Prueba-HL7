import json
import os
import sqlite3
from pathlib import Path
from typing import Any


class SimulatorStore:
    def __init__(self, database_path: str | None = None) -> None:
        default_path = Path(__file__).resolve().parents[1] / "data" / "simulator.db"
        self.database_path = Path(database_path or os.environ.get("SIMULATOR_DB_PATH", default_path))
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analyzers (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    analyzer_id TEXT NOT NULL,
                    barcode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    analyzer_id TEXT,
                    direction TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def load_analyzers(self) -> dict[str, dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM analyzers ORDER BY id").fetchall()
        return {item["id"]: item for item in (json.loads(row[0]) for row in rows)}

    def save_analyzer(self, analyzer: dict) -> None:
        payload = self._json(analyzer)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analyzers (id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (analyzer["id"], payload, analyzer["updatedAt"]),
            )

    def delete_analyzer(self, analyzer_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM analyzers WHERE id = ?", (analyzer_id,))

    def load_orders(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM orders ORDER BY updated_at").fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_order(self, order: dict) -> None:
        payload = self._json(order)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO orders (id, analyzer_id, barcode, status, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    analyzer_id = excluded.analyzer_id,
                    barcode = excluded.barcode,
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    order["id"],
                    order["analyzerId"],
                    order["barcode"],
                    order["status"],
                    payload,
                    order["updatedAt"],
                ),
            )

    def load_messages(self, limit: int = 250) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM messages ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    def save_message(self, message: dict) -> None:
        payload = self._json(message)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (id, analyzer_id, direction, protocol, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message["id"],
                    message.get("analyzerId"),
                    message["direction"],
                    message["protocol"],
                    payload,
                    message["createdAt"],
                ),
            )

    def next_sequence(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key = 'sequence'").fetchone()
            value = int(row[0]) + 1 if row else 1
            connection.execute(
                """
                INSERT INTO meta (key, value) VALUES ('sequence', ?)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value
                """,
                (str(value),),
            )
        return value

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _json(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
