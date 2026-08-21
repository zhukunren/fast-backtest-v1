from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


DEFAULT_CAMPAIGN_DB = Path("output/codex_campaigns/campaigns.sqlite3")


def resolve_campaign_db_path(
    path: str | Path | None,
    *,
    repo_root: str | Path,
) -> Path:
    """Resolve a strategy-research DB without allowing protocol-controlled writes elsewhere."""
    root = Path(repo_root).resolve()
    allowed = (root / "output" / "codex_campaigns").resolve()
    raw = Path(path) if path is not None and str(path).strip() else DEFAULT_CAMPAIGN_DB
    resolved = (raw if raw.is_absolute() else root / raw).resolve()
    try:
        relative = resolved.relative_to(allowed)
    except ValueError:
        raise ValueError(f"campaign DB must be inside {allowed}: {resolved}") from None
    if not relative.parts:
        raise ValueError("campaign DB must name a file below output/codex_campaigns/")
    return resolved


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def _connection(path: str | Path) -> Iterator[sqlite3.Connection]:
    db_path = Path(path)
    if not db_path.parent.is_dir():
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS campaigns (
            campaign_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            max_candidates INTEGER NOT NULL,
            familywise_alpha REAL NOT NULL,
            sealed_protocol_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            frozen_at TEXT
        );
        CREATE TABLE IF NOT EXISTS candidates (
            campaign_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            module TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            PRIMARY KEY (campaign_id, candidate_id),
            FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
        );
        CREATE TABLE IF NOT EXISTS shortlist (
            campaign_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            PRIMARY KEY (campaign_id, candidate_id),
            FOREIGN KEY (campaign_id, candidate_id) REFERENCES candidates(campaign_id, candidate_id)
        );
        CREATE TABLE IF NOT EXISTS attempts (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            phase TEXT NOT NULL,
            status TEXT NOT NULL,
            run_ref TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (campaign_id, candidate_id) REFERENCES candidates(campaign_id, candidate_id)
        );
        CREATE TABLE IF NOT EXISTS holdout_evaluations (
            campaign_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            protocol_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            validation_run_id TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            completed_at TEXT,
            decision TEXT,
            PRIMARY KEY (campaign_id, candidate_id),
            FOREIGN KEY (campaign_id, candidate_id) REFERENCES shortlist(campaign_id, candidate_id)
        );
        """
    )
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(campaigns)").fetchall()}
    if "sealed_protocol_sha256" not in columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN sealed_protocol_sha256 TEXT NOT NULL DEFAULT ''")


def initialize_campaign(
    path: str | Path,
    *,
    campaign_id: str,
    max_candidates: int,
    sealed_protocol_sha256: str,
    familywise_alpha: float = 0.05,
) -> dict[str, Any]:
    campaign_id = str(campaign_id or "").strip()
    if not campaign_id:
        raise ValueError("campaign_id is required")
    if int(max_candidates) < 1:
        raise ValueError("max_candidates must be >= 1")
    if not 0.0 < float(familywise_alpha) < 1.0:
        raise ValueError("familywise_alpha must be between 0 and 1")
    sealed_protocol_sha256 = str(sealed_protocol_sha256 or "").strip().casefold()
    if len(sealed_protocol_sha256) != 64 or any(char not in "0123456789abcdef" for char in sealed_protocol_sha256):
        raise ValueError("sealed_protocol_sha256 must be a 64-character hexadecimal SHA-256")
    with _connection(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO campaigns(campaign_id,status,max_candidates,familywise_alpha,sealed_protocol_sha256,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    campaign_id,
                    "open",
                    int(max_candidates),
                    float(familywise_alpha),
                    sealed_protocol_sha256,
                    _now(),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return get_campaign(path, campaign_id=campaign_id)


def get_campaign(path: str | Path, *, campaign_id: str) -> dict[str, Any]:
    with _connection(path) as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
        if row is None:
            raise KeyError(f"campaign not found: {campaign_id}")
        candidates = conn.execute(
            "SELECT candidate_id,source_sha256,module,first_seen_at FROM candidates WHERE campaign_id=? ORDER BY candidate_id",
            (campaign_id,),
        ).fetchall()
        shortlist = conn.execute(
            "SELECT candidate_id,source_sha256 FROM shortlist WHERE campaign_id=? ORDER BY candidate_id",
            (campaign_id,),
        ).fetchall()
        holdouts = conn.execute(
            "SELECT candidate_id,source_sha256,protocol_sha256,status,validation_run_id,claimed_at,completed_at,decision "
            "FROM holdout_evaluations WHERE campaign_id=? ORDER BY candidate_id",
            (campaign_id,),
        ).fetchall()
        attempts = conn.execute(
            "SELECT attempt_id,candidate_id,source_sha256,phase,status,run_ref,payload_json,created_at "
            "FROM attempts WHERE campaign_id=? ORDER BY attempt_id",
            (campaign_id,),
        ).fetchall()
    attempt_payloads: list[dict[str, Any]] = []
    for item in attempts:
        payload = dict(item)
        try:
            payload["payload"] = json.loads(str(payload.pop("payload_json") or "{}"))
        except json.JSONDecodeError:
            payload["payload"] = {}
        attempt_payloads.append(payload)
    return {
        **dict(row),
        "candidate_count": len(candidates),
        "attempt_count": len(attempt_payloads),
        "candidates": [dict(item) for item in candidates],
        "shortlist": [dict(item) for item in shortlist],
        "holdout_evaluations": [dict(item) for item in holdouts],
        "attempts": attempt_payloads,
    }


def register_candidate(
    path: str | Path,
    *,
    campaign_id: str,
    candidate_id: str,
    source_sha256: str,
    module: str,
) -> dict[str, Any]:
    candidate_id = str(candidate_id or "").strip()
    source_sha256 = str(source_sha256 or "").strip()
    if not candidate_id or not source_sha256:
        raise ValueError("candidate_id and source_sha256 are required")
    with _connection(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            campaign = conn.execute("SELECT * FROM campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
            if campaign is None:
                raise KeyError(f"campaign not found: {campaign_id}")
            existing = conn.execute(
                "SELECT source_sha256 FROM candidates WHERE campaign_id=? AND candidate_id=?",
                (campaign_id, candidate_id),
            ).fetchone()
            if existing is not None:
                if str(existing["source_sha256"]) != source_sha256:
                    raise ValueError("candidate_id is already bound to different source content")
                conn.commit()
                return get_campaign(path, campaign_id=campaign_id)
            source_owner = conn.execute(
                "SELECT candidate_id FROM candidates WHERE campaign_id=? AND source_sha256=?",
                (campaign_id, source_sha256),
            ).fetchone()
            if source_owner is not None:
                raise ValueError(f"candidate source content is already registered as {source_owner['candidate_id']}")
            if str(campaign["status"]) != "open":
                raise ValueError("campaign shortlist is frozen; new candidates are not allowed")
            count = int(
                conn.execute("SELECT COUNT(*) FROM candidates WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
            )
            if count >= int(campaign["max_candidates"]):
                raise ValueError("campaign candidate budget exhausted")
            conn.execute(
                "INSERT INTO candidates(campaign_id,candidate_id,source_sha256,module,first_seen_at) VALUES(?,?,?,?,?)",
                (campaign_id, candidate_id, source_sha256, str(module or ""), _now()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return get_campaign(path, campaign_id=campaign_id)


def freeze_shortlist(path: str | Path, *, campaign_id: str, candidate_ids: list[str]) -> dict[str, Any]:
    normalized = sorted({str(item).strip() for item in candidate_ids if str(item).strip()}, key=str.casefold)
    if not normalized:
        raise ValueError("shortlist requires at least one candidate_id")
    with _connection(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            campaign = conn.execute(
                "SELECT status,sealed_protocol_sha256 FROM campaigns WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
            if campaign is None:
                raise KeyError(f"campaign not found: {campaign_id}")
            if str(campaign["status"]) != "open":
                raise ValueError("campaign shortlist is already frozen")
            if not str(campaign["sealed_protocol_sha256"] or "").strip():
                raise ValueError("campaign has no pre-registered sealed holdout protocol hash")
            rows = conn.execute(
                f"SELECT candidate_id,source_sha256 FROM candidates WHERE campaign_id=? AND candidate_id IN ({','.join('?' for _ in normalized)})",
                (campaign_id, *normalized),
            ).fetchall()
            if len(rows) != len(normalized):
                found = {str(row["candidate_id"]) for row in rows}
                missing = [item for item in normalized if item not in found]
                raise ValueError(f"shortlist contains unregistered candidates: {missing}")
            conn.executemany(
                "INSERT INTO shortlist(campaign_id,candidate_id,source_sha256) VALUES(?,?,?)",
                [(campaign_id, str(row["candidate_id"]), str(row["source_sha256"])) for row in rows],
            )
            conn.execute(
                "UPDATE campaigns SET status='frozen',frozen_at=? WHERE campaign_id=?",
                (_now(), campaign_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return get_campaign(path, campaign_id=campaign_id)


def record_attempt(
    path: str | Path,
    *,
    campaign_id: str,
    candidate_id: str,
    source_sha256: str,
    phase: str,
    status: str,
    run_ref: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    with _connection(path) as conn:
        candidate = conn.execute(
            "SELECT source_sha256 FROM candidates WHERE campaign_id=? AND candidate_id=?",
            (campaign_id, candidate_id),
        ).fetchone()
        if candidate is None:
            raise ValueError("attempt candidate is not registered in the campaign")
        if str(candidate["source_sha256"]) != str(source_sha256 or "").strip():
            raise ValueError("attempt source does not match registered candidate content")
        conn.execute(
            "INSERT INTO attempts(campaign_id,candidate_id,source_sha256,phase,status,run_ref,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                campaign_id,
                candidate_id,
                source_sha256,
                str(phase or "discovery"),
                str(status or "unknown"),
                str(run_ref or ""),
                json.dumps(payload or {}, ensure_ascii=True, sort_keys=True, default=str),
                _now(),
            ),
        )


def claim_holdout(
    path: str | Path,
    *,
    campaign_id: str,
    candidate_id: str,
    source_sha256: str,
    protocol_sha256: str,
    validation_run_id: str,
) -> dict[str, Any]:
    with _connection(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            campaign = conn.execute("SELECT * FROM campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
            if campaign is None or str(campaign["status"]) != "frozen":
                raise ValueError("campaign shortlist must be frozen before sealed holdout evaluation")
            if str(campaign["sealed_protocol_sha256"]) != str(protocol_sha256 or "").strip().casefold():
                raise ValueError("sealed holdout protocol does not match the pre-registered campaign hash")
            item = conn.execute(
                "SELECT source_sha256 FROM shortlist WHERE campaign_id=? AND candidate_id=?",
                (campaign_id, candidate_id),
            ).fetchone()
            if item is None:
                raise ValueError("candidate is not in the frozen shortlist")
            if str(item["source_sha256"]) != source_sha256:
                raise ValueError("candidate source changed after shortlist freeze")
            conn.execute(
                "INSERT INTO holdout_evaluations(campaign_id,candidate_id,source_sha256,protocol_sha256,status,validation_run_id,claimed_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (campaign_id, candidate_id, source_sha256, protocol_sha256, "claimed", validation_run_id, _now()),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise ValueError("sealed holdout may be evaluated only once per frozen candidate") from exc
        except Exception:
            conn.rollback()
            raise
    return get_campaign(path, campaign_id=campaign_id)


def complete_holdout(
    path: str | Path,
    *,
    campaign_id: str,
    candidate_id: str,
    decision: str,
) -> None:
    with _connection(path) as conn:
        cursor = conn.execute(
            "UPDATE holdout_evaluations SET status='completed',completed_at=?,decision=? "
            "WHERE campaign_id=? AND candidate_id=? AND status IN ('claimed','running')",
            (_now(), str(decision or ""), campaign_id, candidate_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("sealed holdout claim is not active")


__all__ = [
    "DEFAULT_CAMPAIGN_DB",
    "claim_holdout",
    "complete_holdout",
    "freeze_shortlist",
    "get_campaign",
    "initialize_campaign",
    "record_attempt",
    "register_candidate",
    "resolve_campaign_db_path",
]
