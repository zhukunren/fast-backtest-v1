"""SQLite persistence and transactions for shared Web task state."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=lambda item: str(item).casefold())
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    return str(value)


def _encode_payload(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(payload),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=_json_default,
    )


def _decode_payload(raw: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(raw or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Web task-state database contains invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Web task-state payload must be an object")
    return payload


def _payload_updated_at(payload: Mapping[str, Any], fallback: float) -> float:
    try:
        return float(payload.get("updated_at", fallback) or fallback)
    except (TypeError, ValueError):
        return float(fallback)


def _expire_payload(
    payload: dict[str, Any],
    *,
    current_key: str,
    now: float,
) -> dict[str, Any]:
    payload["running"] = False
    payload[current_key] = ""
    payload["cancelled"] = True
    if "error" in payload:
        payload["error"] = "task owner lease expired"
    started = float(payload.get("start_time", 0.0) or 0.0)
    payload["elapsed_sec"] = round(max(now - started, 0.0), 1) if started > 0 else 0.0
    if "updated_at" in payload:
        payload["updated_at"] = now
    if "finished_at" in payload:
        payload["finished_at"] = now
    if "task_age_sec" in payload:
        payload["task_age_sec"] = payload["elapsed_sec"]
    return payload


class SQLiteTaskStore:
    """Transactional task records shared by all local Web worker processes."""

    def __init__(self, path: Path, *, lease_timeout_sec: float) -> None:
        self.path = Path(path).resolve()
        self.lease_timeout_sec = float(lease_timeout_sec)
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.path),
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema(connection)
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS web_task_state (
                    state_key TEXT PRIMARY KEY,
                    exclusive_group TEXT NOT NULL,
                    current_key TEXT NOT NULL,
                    owner_token TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS web_task_state_group_idx
                ON web_task_state (exclusive_group)
                """
            )
            self._schema_ready = True

    @staticmethod
    def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
        return _decode_payload(row["payload_json"])

    def _write_row(
        self,
        connection: sqlite3.Connection,
        *,
        state_key: str,
        exclusive_group: str,
        current_key: str,
        owner_token: str,
        task_id: str,
        payload: Mapping[str, Any],
        updated_at: float,
    ) -> None:
        connection.execute(
            """
            INSERT INTO web_task_state (
                state_key, exclusive_group, current_key, owner_token,
                task_id, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(state_key) DO UPDATE SET
                exclusive_group = excluded.exclusive_group,
                current_key = excluded.current_key,
                owner_token = excluded.owner_token,
                task_id = excluded.task_id,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                state_key,
                exclusive_group,
                current_key,
                owner_token,
                task_id,
                _encode_payload(payload),
                float(updated_at),
            ),
        )

    def _is_stale(self, row: sqlite3.Row, payload: Mapping[str, Any], *, now: float) -> bool:
        updated_at = _payload_updated_at(payload, float(row["updated_at"]))
        return bool(payload.get("running")) and now - updated_at > self.lease_timeout_sec

    def read(self, state_key: str) -> dict[str, Any] | None:
        now = time.time()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM web_task_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        payload = self._row_payload(row)
        if not self._is_stale(row, payload, now=now):
            return payload
        return self._expire_if_stale(state_key, now=now)

    def _expire_if_stale(self, state_key: str, *, now: float) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM web_task_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            payload = self._row_payload(row)
            if self._is_stale(row, payload, now=now):
                payload = _expire_payload(
                    payload,
                    current_key=str(row["current_key"]),
                    now=now,
                )
                self._write_row(
                    connection,
                    state_key=str(row["state_key"]),
                    exclusive_group=str(row["exclusive_group"]),
                    current_key=str(row["current_key"]),
                    owner_token=str(row["owner_token"]),
                    task_id=str(row["task_id"]),
                    payload=payload,
                    updated_at=now,
                )
            connection.commit()
            return payload
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def try_start(
        self,
        *,
        state_key: str,
        exclusive_group: str,
        current_key: str,
        owner_token: str,
        task_id: str,
        payload: Mapping[str, Any],
    ) -> bool:
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM web_task_state WHERE exclusive_group = ?",
                (exclusive_group,),
            ).fetchall()
            active = False
            for row in rows:
                current = self._row_payload(row)
                if not bool(current.get("running")):
                    continue
                if self._is_stale(row, current, now=now):
                    expired = _expire_payload(
                        current,
                        current_key=str(row["current_key"]),
                        now=now,
                    )
                    self._write_row(
                        connection,
                        state_key=str(row["state_key"]),
                        exclusive_group=str(row["exclusive_group"]),
                        current_key=str(row["current_key"]),
                        owner_token=str(row["owner_token"]),
                        task_id=str(row["task_id"]),
                        payload=expired,
                        updated_at=now,
                    )
                else:
                    active = True
            if active:
                connection.commit()
                return False
            started = dict(payload)
            if "updated_at" in started:
                started["updated_at"] = now
            self._write_row(
                connection,
                state_key=state_key,
                exclusive_group=exclusive_group,
                current_key=current_key,
                owner_token=owner_token,
                task_id=task_id,
                payload=started,
                updated_at=now,
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mutate(
        self,
        *,
        state_key: str,
        owner_token: str,
        task_id: str,
        transform: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM web_task_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
            if (
                row is None
                or str(row["owner_token"]) != owner_token
                or str(row["task_id"]) != task_id
            ):
                connection.commit()
                return None
            payload = self._row_payload(row)
            if not bool(payload.get("running")):
                connection.commit()
                return None
            transform(payload)
            updated_at = time.time()
            if "updated_at" in payload:
                payload["updated_at"] = updated_at
            if not bool(payload.get("running")) and "finished_at" in payload:
                payload["finished_at"] = updated_at
            self._write_row(
                connection,
                state_key=state_key,
                exclusive_group=str(row["exclusive_group"]),
                current_key=str(row["current_key"]),
                owner_token=owner_token,
                task_id=task_id,
                payload=payload,
                updated_at=updated_at,
            )
            connection.commit()
            return payload
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def replace(
        self,
        *,
        state_key: str,
        exclusive_group: str,
        current_key: str,
        owner_token: str,
        task_id: str,
        payload: Mapping[str, Any],
        expected_owner_token: str = "",
        expected_task_id: str = "",
    ) -> bool:
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM web_task_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
            if row is not None:
                current = self._row_payload(row)
                if bool(current.get("running")) and self._is_stale(row, current, now=now):
                    current = _expire_payload(
                        current,
                        current_key=str(row["current_key"]),
                        now=now,
                    )
                if bool(current.get("running")) and (
                    str(row["owner_token"]) != expected_owner_token
                    or str(row["task_id"]) != expected_task_id
                ):
                    connection.commit()
                    return False
            self._write_row(
                connection,
                state_key=state_key,
                exclusive_group=exclusive_group,
                current_key=current_key,
                owner_token=owner_token,
                task_id=task_id,
                payload=payload,
                updated_at=now,
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


__all__ = ["SQLiteTaskStore"]
