"""Persistence for Oura tokens, user preferences, and plan history. Uses Postgres when DATABASE_URL is set, else SQLite."""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Postgres when deployed (e.g. Railway); SQLite for local dev
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip().replace("postgres://", "postgresql://")
_use_pg = bool(DATABASE_URL)

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "pulseplate.db"
DB_PATH = Path(os.getenv("PULSEPLATE_DB", str(_DEFAULT_DB_PATH)))

DEFAULT_USER_ID = 1


@contextmanager
def _get_conn():
    if _use_pg:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _q(sql: str) -> str:
    """Use %s placeholders for Postgres, ? for SQLite."""
    return sql.replace("?", "%s") if _use_pg else sql


def _ensure_oura_webhook_events_table() -> None:
    """Create Oura webhook events table if missing (safe to call repeatedly)."""
    with _get_conn() as conn:
        cur = conn.cursor()
        if _use_pg:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oura_webhook_events (
                    id SERIAL PRIMARY KEY,
                    oura_user_id TEXT,
                    event_type TEXT,
                    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    payload_json TEXT NOT NULL
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_oura_webhook_events_user_id ON oura_webhook_events(oura_user_id)"
            )
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oura_webhook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    oura_user_id TEXT,
                    event_type TEXT,
                    received_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_oura_webhook_events_user_id ON oura_webhook_events(oura_user_id)"
            )


def init_db() -> None:
    """Create users and plans tables if they do not exist. Safe to call on every startup."""
    with _get_conn() as conn:
        cur = conn.cursor()
        if _use_pg:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE,
                    oura_user_id TEXT,
                    oura_access_token TEXT,
                    oura_refresh_token TEXT,
                    oura_expires_at INTEGER,
                    goals TEXT,
                    diet_style TEXT,
                    calorie_target INTEGER,
                    allergies TEXT,
                    measurement_system TEXT DEFAULT 'us',
                    meal_feedback_json TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS plans (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    generated_at TEXT NOT NULL,
                    biometric_snapshot TEXT,
                    plan_json TEXT NOT NULL,
                    weekly_days INTEGER,
                    is_weekly SMALLINT NOT NULL DEFAULT 0
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_plans_user_id ON plans(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_plans_generated_at ON plans(generated_at)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oura_webhook_events (
                    id SERIAL PRIMARY KEY,
                    oura_user_id TEXT,
                    event_type TEXT,
                    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    payload_json TEXT NOT NULL
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_oura_webhook_events_user_id ON oura_webhook_events(oura_user_id)")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS beta_analytics_events (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER,
                    event_name TEXT NOT NULL,
                    props_json TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_beta_analytics_events_created_at ON beta_analytics_events(created_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_beta_analytics_events_name ON beta_analytics_events(event_name)")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS support_issue_reports (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER,
                    issue_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    page TEXT,
                    app_version TEXT,
                    triage_severity TEXT,
                    triage_status TEXT DEFAULT 'new',
                    triage_owner TEXT,
                    triaged_at TIMESTAMPTZ,
                    resolved_at TIMESTAMPTZ,
                    last_response_at TIMESTAMPTZ,
                    response_note TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_support_issue_reports_created_at ON support_issue_reports(created_at)")
            for col_sql in (
                "ALTER TABLE support_issue_reports ADD COLUMN triage_severity TEXT",
                "ALTER TABLE support_issue_reports ADD COLUMN triage_status TEXT DEFAULT 'new'",
                "ALTER TABLE support_issue_reports ADD COLUMN triage_owner TEXT",
                "ALTER TABLE support_issue_reports ADD COLUMN triaged_at TIMESTAMPTZ",
                "ALTER TABLE support_issue_reports ADD COLUMN resolved_at TIMESTAMPTZ",
                "ALTER TABLE support_issue_reports ADD COLUMN last_response_at TIMESTAMPTZ",
                "ALTER TABLE support_issue_reports ADD COLUMN response_note TEXT",
            ):
                try:
                    cur.execute(col_sql)
                except Exception:
                    pass
            try:
                cur.execute("ALTER TABLE users ADD COLUMN measurement_system TEXT DEFAULT 'us'")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE users ADD COLUMN meal_feedback_json TEXT")
            except Exception:
                pass
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    email TEXT UNIQUE,
                    oura_user_id TEXT,
                    oura_access_token TEXT,
                    oura_refresh_token TEXT,
                    oura_expires_at INTEGER,
                    goals TEXT,
                    diet_style TEXT,
                    calorie_target INTEGER,
                    allergies TEXT,
                    measurement_system TEXT,
                    meal_feedback_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            for col in ("email", "oura_user_id", "measurement_system", "meal_feedback_json"):
                try:
                    cur.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
            cur.execute("""
                CREATE TABLE IF NOT EXISTS plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    generated_at TEXT NOT NULL,
                    biometric_snapshot TEXT,
                    plan_json TEXT NOT NULL,
                    weekly_days INTEGER,
                    is_weekly INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_plans_user_id ON plans(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_plans_generated_at ON plans(generated_at)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oura_webhook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    oura_user_id TEXT,
                    event_type TEXT,
                    received_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_oura_webhook_events_user_id ON oura_webhook_events(oura_user_id)")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS beta_analytics_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    event_name TEXT NOT NULL,
                    props_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_beta_analytics_events_created_at ON beta_analytics_events(created_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_beta_analytics_events_name ON beta_analytics_events(event_name)")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS support_issue_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    issue_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    page TEXT,
                    app_version TEXT,
                    triage_severity TEXT,
                    triage_status TEXT,
                    triage_owner TEXT,
                    triaged_at TEXT,
                    resolved_at TEXT,
                    last_response_at TEXT,
                    response_note TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_support_issue_reports_created_at ON support_issue_reports(created_at)")
            for col, col_type in (
                ("triage_severity", "TEXT"),
                ("triage_status", "TEXT"),
                ("triage_owner", "TEXT"),
                ("triaged_at", "TEXT"),
                ("resolved_at", "TEXT"),
                ("last_response_at", "TEXT"),
                ("response_note", "TEXT"),
            ):
                try:
                    cur.execute(f"ALTER TABLE support_issue_reports ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass


def get_oura_tokens(user_id: int = DEFAULT_USER_ID) -> dict[str, Any] | None:
    """Return stored Oura tokens for the user, or None if not connected."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT oura_access_token, oura_refresh_token, oura_expires_at FROM users WHERE id = ?"),
            (user_id,),
        )
        row = cur.fetchone()
    if not row or not row[0]:
        return None
    return {
        "access_token": row[0],
        "refresh_token": row[1],
        "expires_at": row[2],
    }


def get_user_oura_user_id(user_id: int) -> str | None:
    """Return the stored Oura user id for this PulsePlate user."""
    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute(_q("SELECT oura_user_id FROM users WHERE id = ?"), (user_id,))
            row = cur.fetchone()
        if not row:
            return None
        # SQLite returns a Row; Postgres returns a tuple. Handle both.
        val = row[0]
        if val is None:
            return None
        s = str(val).strip()
        return s or None
    except Exception:
        # If DB schema migrations are behind (missing column), don't break the UI.
        return None


def get_latest_oura_webhook_event_for_user(user_id: int) -> dict[str, Any] | None:
    """
    Return the most recent stored Oura webhook event for this user.

    Note: webhook payloads are persisted for debugging and future processing.
    """
    oura_user_id = get_user_oura_user_id(user_id)
    if not oura_user_id:
        return None

    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                _q(
                    """
                    SELECT event_type, received_at
                    FROM oura_webhook_events
                    WHERE oura_user_id = ?
                    ORDER BY received_at DESC
                    LIMIT 1
                    """
                ),
                (oura_user_id,),
            )
            row = cur.fetchone()
        if not row:
            return None

        event_type = row[0]
        received_at = row[1] if len(row) > 1 else None
        received_at_str = None
        if received_at is not None:
            received_at_str = (
                received_at.isoformat().replace("+00:00", "Z")
                if hasattr(received_at, "isoformat")
                else str(received_at)
            )

        return {
            "event_type": event_type,
            "received_at": received_at_str,
        }
    except Exception:
        # If webhook-events table doesn't exist yet, create it and treat as no events.
        try:
            _ensure_oura_webhook_events_table()
        except Exception:
            pass
        return None


def get_recent_oura_webhook_events_for_user(user_id: int, limit: int = 5) -> list[dict[str, Any]]:
    """
    Return the most recent stored Oura webhook events for this PulsePlate user.

    This is primarily for debugging/verification so users can confirm webhooks are arriving.
    """
    oura_user_id = get_user_oura_user_id(user_id)
    if not oura_user_id:
        return []

    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                _q(
                    """
                    SELECT event_type, received_at
                    FROM oura_webhook_events
                    WHERE oura_user_id = ?
                    ORDER BY received_at DESC
                    LIMIT ?
                    """
                ),
                (oura_user_id, limit),
            )
            rows = cur.fetchall() or []
    except Exception:
        try:
            _ensure_oura_webhook_events_table()
        except Exception:
            pass
        return []

    events: list[dict[str, Any]] = []
    for row in rows:
        event_type = row[0] if len(row) > 0 else None
        received_at = row[1] if len(row) > 1 else None
        received_at_str = None
        if received_at is not None:
            received_at_str = (
                received_at.isoformat().replace("+00:00", "Z")
                if hasattr(received_at, "isoformat")
                else str(received_at)
            )
        events.append({"event_type": event_type, "received_at": received_at_str})

    return events


def get_recent_oura_webhook_events(limit: int = 20) -> list[dict[str, Any]]:
    """
    Return the most recent Oura webhook events across all stored users.
    Intended for troubleshooting webhook delivery/extraction.
    """
    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                _q(
                    """
                    SELECT oura_user_id, event_type, received_at
                    FROM oura_webhook_events
                    ORDER BY received_at DESC
                    LIMIT ?
                    """
                ),
                (limit,),
            )
            rows = cur.fetchall() or []
    except Exception:
        try:
            _ensure_oura_webhook_events_table()
        except Exception:
            pass
        return []

    events: list[dict[str, Any]] = []
    for row in rows:
        oura_user_id = row[0] if len(row) > 0 else None
        event_type = row[1] if len(row) > 1 else None
        received_at = row[2] if len(row) > 2 else None
        received_at_str = None
        if received_at is not None:
            received_at_str = (
                received_at.isoformat().replace("+00:00", "Z")
                if hasattr(received_at, "isoformat")
                else str(received_at)
            )
        events.append(
            {
                "oura_user_id": oura_user_id,
                "event_type": event_type,
                "received_at": received_at_str,
            }
        )

    return events


def get_oura_webhook_events_count_all_users() -> int:
    """Return total number of stored Oura webhook events (all users)."""
    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute(_q("SELECT COUNT(*) FROM oura_webhook_events"))
            row = cur.fetchone()
        if not row:
            return 0
        return int(row[0] or 0)
    except Exception:
        try:
            _ensure_oura_webhook_events_table()
            with _get_conn() as conn:
                cur = conn.cursor()
                cur.execute(_q("SELECT COUNT(*) FROM oura_webhook_events"))
                row = cur.fetchone()
            if not row:
                return 0
            return int(row[0] or 0)
        except Exception:
            return 0


def get_or_create_user_by_email(email: str) -> int:
    """Return user id for the given email. Creates user if not found."""
    if not (email or "").strip():
        raise ValueError("email is required")
    email = email.strip()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("SELECT id FROM users WHERE email = ?"), (email,))
        row = cur.fetchone()
        if row:
            return row[0]
        if _use_pg:
            cur.execute(
                _q("INSERT INTO users (email, created_at, updated_at) VALUES (?, ?, ?) RETURNING id"),
                (email, now, now),
            )
            return cur.fetchone()[0]
        legacy = cur.execute(
            _q("SELECT id FROM users WHERE id = 1 AND (email IS NULL OR email = '')")
        ).fetchone()
        if legacy:
            cur.execute(_q("UPDATE users SET email = ?, updated_at = ? WHERE id = 1"), (email, now))
            return 1
        cur.execute(_q("SELECT MAX(id) FROM users"))
        max_row = cur.fetchone()
        next_id = (max_row[0] or 0) + 1
        cur.execute(
            _q("INSERT INTO users (id, email, created_at, updated_at) VALUES (?, ?, ?, ?)"),
            (next_id, email, now, now),
        )
        return next_id


def set_oura_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str | None,
    expires_at: int,
    oura_user_id: str | None = None,
) -> None:
    """Store or update Oura tokens for the user."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _get_conn() as conn:
        cur = conn.cursor()
        if _use_pg:
            cur.execute(
                _q("""
                    UPDATE users
                    SET oura_access_token = ?,
                        oura_refresh_token = ?,
                        oura_expires_at = ?,
                        oura_user_id = COALESCE(?, oura_user_id),
                        updated_at = ?
                    WHERE id = ?
                """),
                (access_token, refresh_token or "", expires_at, oura_user_id, now, user_id),
            )
            if cur.rowcount == 0:
                cur.execute(
                    _q("""
                        INSERT INTO users (id, oura_access_token, oura_refresh_token, oura_expires_at, oura_user_id, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """),
                    (user_id, access_token, refresh_token or "", expires_at, oura_user_id, now, now),
                )
        else:
            cur.execute(
                _q("""
                    INSERT INTO users (id, oura_access_token, oura_refresh_token, oura_expires_at, oura_user_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        oura_access_token = excluded.oura_access_token,
                        oura_refresh_token = excluded.oura_refresh_token,
                        oura_expires_at = excluded.oura_expires_at,
                        oura_user_id = COALESCE(excluded.oura_user_id, users.oura_user_id),
                        updated_at = excluded.updated_at
                """),
                (user_id, access_token, refresh_token or "", expires_at, oura_user_id, now, now),
            )


def save_oura_webhook_event(
    oura_user_id: str | None,
    event_type: str | None,
    payload: dict[str, Any],
) -> None:
    """Persist a received Oura webhook payload for debugging + future processing."""
    received_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            if _use_pg:
                cur.execute(
                    _q("""
                        INSERT INTO oura_webhook_events (oura_user_id, event_type, payload_json)
                        VALUES (?, ?, ?)
                    """),
                    (oura_user_id, event_type, payload_json),
                )
            else:
                cur.execute(
                    _q("""
                        INSERT INTO oura_webhook_events (oura_user_id, event_type, received_at, payload_json)
                        VALUES (?, ?, ?, ?)
                    """),
                    (oura_user_id, event_type, received_at, payload_json),
                )
    except Exception:
        # If schema is behind (missing table), create and retry once.
        _ensure_oura_webhook_events_table()
        with _get_conn() as conn:
            cur = conn.cursor()
            if _use_pg:
                cur.execute(
                    _q("""
                        INSERT INTO oura_webhook_events (oura_user_id, event_type, payload_json)
                        VALUES (?, ?, ?)
                    """),
                    (oura_user_id, event_type, payload_json),
                )
            else:
                cur.execute(
                    _q("""
                        INSERT INTO oura_webhook_events (oura_user_id, event_type, received_at, payload_json)
                        VALUES (?, ?, ?, ?)
                    """),
                    (oura_user_id, event_type, received_at, payload_json),
                )


def clear_oura_tokens(user_id: int) -> None:
    """Remove Oura tokens for the user (disconnect)."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("""
                UPDATE users
                SET oura_access_token = NULL, oura_refresh_token = NULL, oura_expires_at = NULL, updated_at = ?
                WHERE id = ?
            """),
            (now, user_id),
        )


def get_user_meal_feedback(user_id: int = DEFAULT_USER_ID) -> dict[str, str]:
    """Return saved meal feedback map {meal_name: 'up'|'down'} for this user."""
    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute(_q("SELECT meal_feedback_json FROM users WHERE id = ?"), (user_id,))
            row = cur.fetchone()
    except Exception:
        return {}
    if not row or not row[0]:
        return {}
    try:
        parsed = json.loads(row[0])
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in parsed.items():
        if isinstance(k, str) and v in ("up", "down"):
            out[k] = v
    return out


def set_user_meal_feedback(user_id: int, feedback: dict[str, str]) -> None:
    """Persist meal feedback map for this user, creating row if needed."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cleaned: dict[str, str] = {}
    for k, v in (feedback or {}).items():
        if isinstance(k, str) and v in ("up", "down"):
            cleaned[k] = v
    payload = json.dumps(cleaned, separators=(",", ":"), ensure_ascii=False)
    with _get_conn() as conn:
        cur = conn.cursor()
        if _use_pg:
            cur.execute(
                _q("UPDATE users SET meal_feedback_json = ?, updated_at = ? WHERE id = ?"),
                (payload, now, user_id),
            )
            if cur.rowcount == 0:
                cur.execute(
                    _q("INSERT INTO users (id, meal_feedback_json, created_at, updated_at) VALUES (?, ?, ?, ?)"),
                    (user_id, payload, now, now),
                )
        else:
            cur.execute(
                _q(
                    """
                    INSERT INTO users (id, meal_feedback_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        meal_feedback_json = excluded.meal_feedback_json,
                        updated_at = excluded.updated_at
                    """
                ),
                (user_id, payload, now, now),
            )


def delete_user_data(user_id: int) -> None:
    """Delete user's plans and user row (data deletion)."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("DELETE FROM plans WHERE user_id = ?"), (user_id,))
        cur.execute(_q("DELETE FROM users WHERE id = ?"), (user_id,))


def save_beta_analytics_event(user_id: int | None, event_name: str, props: dict[str, Any] | None = None) -> None:
    """Persist a lightweight analytics event for beta metrics."""
    payload = json.dumps(props or {}, separators=(",", ":"), ensure_ascii=False)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _get_conn() as conn:
        cur = conn.cursor()
        if _use_pg:
            cur.execute(
                _q("INSERT INTO beta_analytics_events (user_id, event_name, props_json) VALUES (?, ?, ?)"),
                (user_id, event_name, payload),
            )
        else:
            cur.execute(
                _q("INSERT INTO beta_analytics_events (user_id, event_name, props_json, created_at) VALUES (?, ?, ?, ?)"),
                (user_id, event_name, payload, now),
            )


def save_support_issue_report(
    user_id: int | None, issue_id: str, message: str, page: str | None = None, app_version: str | None = None
) -> None:
    """Persist issue reports for beta triage dashboarding."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _get_conn() as conn:
        cur = conn.cursor()
        if _use_pg:
            cur.execute(
                _q(
                    "INSERT INTO support_issue_reports (user_id, issue_id, message, page, app_version) VALUES (?, ?, ?, ?, ?)"
                ),
                (user_id, issue_id, message, page, app_version),
            )
        else:
            cur.execute(
                _q(
                    "INSERT INTO support_issue_reports (user_id, issue_id, message, page, app_version, created_at) VALUES (?, ?, ?, ?, ?, ?)"
                ),
                (user_id, issue_id, message, page, app_version, now),
            )


def _issue_row_to_dict(r: Any) -> dict[str, Any]:
    """Map a support_issue_reports row tuple to API/export shape."""

    def _to_iso(v: Any) -> str | None:
        if v is None:
            return None
        return v.isoformat().replace("+00:00", "Z") if hasattr(v, "isoformat") else str(v)

    return {
        "issue_id": str(r[0]),
        "user_id": int(r[1]) if r[1] is not None else None,
        "message": str(r[2]),
        "page": str(r[3]) if r[3] is not None else None,
        "app_version": str(r[4]) if r[4] is not None else None,
        "triage_severity": str(r[5]) if r[5] is not None else None,
        "triage_status": str(r[6]) if r[6] is not None else "new",
        "triage_owner": str(r[7]) if r[7] is not None else None,
        "triaged_at": _to_iso(r[8]),
        "resolved_at": _to_iso(r[9]),
        "last_response_at": _to_iso(r[10]),
        "response_note": str(r[11]) if r[11] is not None else None,
        "created_at": _to_iso(r[12]) or "",
    }


def list_support_issue_reports(limit: int = 500) -> list[dict[str, Any]]:
    """Return recent issue reports newest first (for dashboard and CSV export)."""
    cap = max(1, min(int(limit), 5000))
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q(
                """
                SELECT issue_id, user_id, message, page, app_version, triage_severity, triage_status, triage_owner, triaged_at, resolved_at, last_response_at, response_note, created_at
                FROM support_issue_reports
                ORDER BY created_at DESC
                LIMIT ?
                """
            ),
            (cap,),
        )
        rows = cur.fetchall() or []
    return [_issue_row_to_dict(r) for r in rows]


def update_support_issue_triage(
    issue_id: str,
    severity: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    mark_responded: bool | None = None,
    response_note: str | None = None,
) -> bool:
    """Update triage metadata for a support issue by issue_id. Returns True if row updated."""
    severity_val = (severity or "").strip().upper() or None
    status_val = (status or "").strip().lower() or None
    owner_val = (owner or "").strip() or None

    if severity_val is not None and severity_val not in ("P0", "P1", "P2"):
        raise ValueError("severity must be one of: P0, P1, P2")
    if status_val is not None and status_val not in ("new", "in_progress", "resolved"):
        raise ValueError("status must be one of: new, in_progress, resolved")
    if response_note is not None and len(response_note) > 1000:
        raise ValueError("response_note must be <= 1000 characters")

    updates: list[str] = []
    params: list[Any] = []
    if severity is not None:
        updates.append("triage_severity = ?")
        params.append(severity_val)
    if status is not None:
        updates.append("triage_status = ?")
        params.append(status_val)
        if status_val == "resolved":
            updates.append("resolved_at = ?")
            params.append(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        else:
            updates.append("resolved_at = ?")
            params.append(None)
    if owner is not None:
        updates.append("triage_owner = ?")
        params.append(owner_val)
    if mark_responded is not None:
        updates.append("last_response_at = ?")
        params.append(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if mark_responded else None)
    if response_note is not None:
        updates.append("response_note = ?")
        params.append((response_note or "").strip() or None)
    if not updates:
        return False
    updates.append("triaged_at = ?")
    params.append(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    params.append(issue_id)

    with _get_conn() as conn:
        cur = conn.cursor()
        sql = "UPDATE support_issue_reports SET " + ", ".join(updates) + " WHERE issue_id = ?"
        cur.execute(_q(sql), tuple(params))
        return int(cur.rowcount or 0) > 0


def get_beta_metrics_summary() -> dict[str, Any]:
    """Return analytics event counts for last 24h/7d and recent issue reports."""
    with _get_conn() as conn:
        cur = conn.cursor()
        if _use_pg:
            cur.execute(
                """
                SELECT event_name, COUNT(*)::int
                FROM beta_analytics_events
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY event_name
                ORDER BY event_name
                """
            )
            rows24 = cur.fetchall() or []
            cur.execute(
                """
                SELECT event_name, COUNT(*)::int
                FROM beta_analytics_events
                WHERE created_at >= NOW() - INTERVAL '7 days'
                GROUP BY event_name
                ORDER BY event_name
                """
            )
            rows7 = cur.fetchall() or []
        else:
            cur.execute(
                """
                SELECT event_name, COUNT(*) as cnt
                FROM beta_analytics_events
                WHERE datetime(created_at) >= datetime('now', '-24 hours')
                GROUP BY event_name
                ORDER BY event_name
                """
            )
            rows24 = cur.fetchall() or []
            cur.execute(
                """
                SELECT event_name, COUNT(*) as cnt
                FROM beta_analytics_events
                WHERE datetime(created_at) >= datetime('now', '-7 days')
                GROUP BY event_name
                ORDER BY event_name
                """
            )
            rows7 = cur.fetchall() or []

    def _rows_to_map(rows: list[Any]) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            out[str(r[0])] = int(r[1] or 0)
        return out

    def _parse_ts(v: Any) -> float | None:
        if v is None:
            return None
        if hasattr(v, "timestamp"):
            try:
                return float(v.timestamp())
            except Exception:
                return None
        s = str(v).strip()
        if not s:
            return None
        # Accept timestamps like 2026-03-26T12:34:56Z or 2026-03-26 12:34:56
        s = s.replace("Z", "")
        s = s.replace("T", " ")
        try:
            return time.mktime(time.strptime(s[:19], "%Y-%m-%d %H:%M:%S"))
        except Exception:
            return None

    recent_issue_reports = list_support_issue_reports(limit=20)

    now_ts = time.time()
    needs_reply_24h = 0
    for item in recent_issue_reports:
        if item["triage_status"] == "resolved":
            continue
        lr_ts = _parse_ts(item.get("last_response_at"))
        if lr_ts is None or (now_ts - lr_ts) > (24 * 60 * 60):
            needs_reply_24h += 1

    return {
        "events_24h": _rows_to_map(rows24),
        "events_7d": _rows_to_map(rows7),
        "recent_issue_reports": recent_issue_reports,
        "triage_summary": {
            "unresolved_total": sum(1 for i in recent_issue_reports if i["triage_status"] != "resolved"),
            "unresolved_p0": sum(1 for i in recent_issue_reports if i["triage_status"] != "resolved" and (i["triage_severity"] or "") == "P0"),
            "needs_reply_24h": needs_reply_24h,
        },
    }

def get_user_preferences(user_id: int = DEFAULT_USER_ID) -> dict[str, Any]:
    """Return saved preferences. Defaults if not set."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT goals, diet_style, calorie_target, allergies, measurement_system FROM users WHERE id = ?"),
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"goals": [], "diet_style": "balanced", "calorie_target": 2000, "allergies": None, "measurement_system": "us"}
    goals = json.loads(row[0]) if row[0] else []
    diet_style = row[1] or "balanced"
    calorie_target = row[2] if row[2] is not None else 2000
    allergies = json.loads(row[3]) if row[3] else None
    measurement_system = (row[4] if len(row) > 4 else None) or "us"
    if measurement_system not in ("us", "metric"):
        measurement_system = "us"
    return {
        "goals": goals,
        "diet_style": diet_style,
        "calorie_target": calorie_target,
        "allergies": allergies,
        "measurement_system": measurement_system,
    }


def set_user_preferences(
    user_id: int,
    goals: list[str] | None = None,
    diet_style: str | None = None,
    calorie_target: int | None = None,
    allergies: list[str] | None = None,
    measurement_system: str | None = None,
) -> None:
    """Update saved preferences. Creates user row if missing."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    prefs = get_user_preferences(user_id)
    if goals is not None:
        prefs["goals"] = goals
    if diet_style is not None:
        prefs["diet_style"] = diet_style
    if calorie_target is not None:
        prefs["calorie_target"] = calorie_target
    if allergies is not None:
        prefs["allergies"] = allergies
    if measurement_system is not None and measurement_system in ("us", "metric"):
        prefs["measurement_system"] = measurement_system
    with _get_conn() as conn:
        cur = conn.cursor()
        if _use_pg:
            cur.execute(
                _q("""
                    UPDATE users SET goals = ?, diet_style = ?, calorie_target = ?, allergies = ?, measurement_system = ?, updated_at = ?
                    WHERE id = ?
                """),
                (
                    json.dumps(prefs["goals"]),
                    prefs["diet_style"],
                    prefs["calorie_target"],
                    json.dumps(prefs["allergies"]) if prefs["allergies"] is not None else None,
                    prefs.get("measurement_system", "us"),
                    now,
                    user_id,
                ),
            )
            if cur.rowcount == 0:
                cur.execute(
                    _q("""
                        INSERT INTO users (id, goals, diet_style, calorie_target, allergies, measurement_system, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """),
                    (
                        user_id,
                        json.dumps(prefs["goals"]),
                        prefs["diet_style"],
                        prefs["calorie_target"],
                        json.dumps(prefs["allergies"]) if prefs["allergies"] is not None else None,
                        prefs.get("measurement_system", "us"),
                        now,
                        now,
                    ),
                )
        else:
            cur.execute(
                _q("""
                    INSERT INTO users (id, goals, diet_style, calorie_target, allergies, measurement_system, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        goals = excluded.goals,
                        diet_style = excluded.diet_style,
                        calorie_target = excluded.calorie_target,
                        allergies = excluded.allergies,
                        measurement_system = excluded.measurement_system,
                        updated_at = excluded.updated_at
                """),
                (
                    user_id,
                    json.dumps(prefs["goals"]),
                    prefs["diet_style"],
                    prefs["calorie_target"],
                    json.dumps(prefs["allergies"]) if prefs["allergies"] is not None else None,
                    prefs.get("measurement_system", "us"),
                    now,
                    now,
                ),
            )


def save_plan(
    user_id: int,
    generated_at: str,
    biometric_snapshot: str | None,
    plan_json: str,
    weekly_days: int | None = None,
    is_weekly: bool = False,
) -> int:
    """Save a generated plan to history. Returns the new plan id."""
    with _get_conn() as conn:
        cur = conn.cursor()
        if _use_pg:
            cur.execute(
                _q("""
                    INSERT INTO plans (user_id, generated_at, biometric_snapshot, plan_json, weekly_days, is_weekly)
                    VALUES (?, ?, ?, ?, ?, ?) RETURNING id
                """),
                (user_id, generated_at, biometric_snapshot, plan_json, weekly_days, 1 if is_weekly else 0),
            )
            return cur.fetchone()[0]
        cur.execute(
            _q("""
                INSERT INTO plans (user_id, generated_at, biometric_snapshot, plan_json, weekly_days, is_weekly)
                VALUES (?, ?, ?, ?, ?, ?)
            """),
            (user_id, generated_at, biometric_snapshot, plan_json, weekly_days, 1 if is_weekly else 0),
        )
        return cur.lastrowid


def get_plans(
    user_id: int,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return paginated plan history for the user (newest first)."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("""
                SELECT id, user_id, generated_at, plan_json, weekly_days, is_weekly
                FROM plans WHERE user_id = ? ORDER BY generated_at DESC LIMIT ? OFFSET ?
            """),
            (user_id, limit, offset),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "user_id": r[1],
            "generated_at": r[2],
            "plan_json": r[3],
            "weekly_days": r[4],
            "is_weekly": bool(r[5]),
        }
        for r in rows
    ]


def get_plan_by_id(plan_id: int, user_id: int) -> dict[str, Any] | None:
    """Return a single plan by id if it belongs to the user."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT id, user_id, generated_at, biometric_snapshot, plan_json, weekly_days, is_weekly FROM plans WHERE id = ? AND user_id = ?"),
            (plan_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "user_id": row[1],
        "generated_at": row[2],
        "biometric_snapshot": row[3],
        "plan_json": row[4],
        "weekly_days": row[5],
        "is_weekly": bool(row[6]),
    }
