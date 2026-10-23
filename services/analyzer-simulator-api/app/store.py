import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class SimulatorStore:
    def __init__(self, database_path: str | None = None) -> None:
        default_path = Path(__file__).resolve().parents[1] / "data" / "simulator.db"
        self.database_path = Path(database_path or os.environ.get("SIMULATOR_DB_PATH", default_path))
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS study_catalog_revisions (
                id TEXT PRIMARY KEY, catalog_id TEXT NOT NULL, version INTEGER NOT NULL,
                payload TEXT NOT NULL, UNIQUE(catalog_id, version))""")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS inbound_receipts (
                    analyzer_id TEXT NOT NULL, protocol TEXT NOT NULL,
                    identity TEXT NOT NULL, digest TEXT NOT NULL, ack TEXT NOT NULL,
                    PRIMARY KEY(analyzer_id, protocol, identity)
                )"""
            )
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analyzer_protocol_profiles (
                    id TEXT PRIMARY KEY,
                    analyzer_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(analyzer_id, name, version)
                )
                """
            )

    def list_catalogs(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM study_catalog_revisions ORDER BY catalog_id, version DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_catalog(self, revision: dict) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO study_catalog_revisions VALUES (?, ?, ?, ?)",
                               (revision["id"], revision["catalogId"], revision["version"], self._json(revision)))

    def next_generator_sequence(self, key: str) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            value = int(row[0]) + 1 if row else 0
            connection.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (key, str(value)))
        return value

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

    def list_protocol_profiles(self, analyzer_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload, status FROM analyzer_protocol_profiles WHERE analyzer_id = ? ORDER BY name, version DESC",
                (analyzer_id,),
            ).fetchall()
        profiles = []
        for row in rows:
            profile = json.loads(row[0])
            # The table status is authoritative: prior versions are archived
            # when another version becomes active.
            profile["status"] = row[1]
            profiles.append(profile)
        return profiles

    def save_protocol_profile(self, profile: dict) -> None:
        with self._connect() as connection:
            if profile["status"] == "ACTIVE":
                connection.execute(
                    "UPDATE analyzer_protocol_profiles SET status = 'ARCHIVED' WHERE analyzer_id = ? AND status = 'ACTIVE'",
                    (profile["analyzerId"],),
                )
            connection.execute(
                """
                INSERT INTO analyzer_protocol_profiles
                    (id, analyzer_id, name, version, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    version = excluded.version,
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    profile["id"], profile["analyzerId"], profile["name"], profile["version"],
                    profile["status"], self._json(profile), profile["createdAt"], profile["updatedAt"],
                ),
            )

    def delete_protocol_profile(self, analyzer_id: str, profile_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE analyzer_protocol_profiles SET status = 'RETIRED' WHERE analyzer_id = ? AND id = ?",
                (analyzer_id, profile_id),
            )

    def get_active_protocol_profile(self, analyzer_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM analyzer_protocol_profiles
                WHERE analyzer_id = ? AND status = 'ACTIVE'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (analyzer_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

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

    def delete_orders_by_analyzer(self, analyzer_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM orders WHERE analyzer_id = ?", (analyzer_id,))

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

    def delete_messages_by_analyzer(self, analyzer_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM messages WHERE analyzer_id = ?", (analyzer_id,))

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

    def get_receipt(self, analyzer_id: str, protocol: str, identity: str) -> tuple | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT digest, ack FROM inbound_receipts WHERE analyzer_id=? AND protocol=? AND identity=?",
                (analyzer_id, protocol, identity),
            ).fetchone()

    def save_receipt(self, analyzer_id: str, protocol: str, identity: str, digest: str, ack: str) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO inbound_receipts VALUES (?, ?, ?, ?, ?)",
                               (analyzer_id, protocol, identity, digest, ack))

    def clear_receipts(self, analyzer_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM inbound_receipts WHERE analyzer_id=?", (analyzer_id,))

    @contextmanager
    def transaction(self):
        if getattr(self._local, "connection", None) is not None:
            yield self._local.connection
            return
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            self._local.connection = connection
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            self._local.connection = None
            connection.close()

    @contextmanager
    def _connect(self):
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            yield existing
            return
        with self.transaction() as connection:
            yield connection

    def _json(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
