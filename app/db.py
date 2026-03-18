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
            try:
                cur.execute("ALTER TABLE users ADD COLUMN measurement_system TEXT DEFAULT 'us'")
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            for col in ("email", "oura_user_id", "measurement_system"):
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
) -> None:
    """Store or update Oura tokens for the user."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _get_conn() as conn:
        cur = conn.cursor()
        if _use_pg:
            cur.execute(
                _q("""
                    UPDATE users SET oura_access_token = ?, oura_refresh_token = ?, oura_expires_at = ?, updated_at = ?
                    WHERE id = ?
                """),
                (access_token, refresh_token or "", expires_at, now, user_id),
            )
            if cur.rowcount == 0:
                cur.execute(
                    _q("""
                        INSERT INTO users (id, oura_access_token, oura_refresh_token, oura_expires_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """),
                    (user_id, access_token, refresh_token or "", expires_at, now, now),
                )
        else:
            cur.execute(
                _q("""
                    INSERT INTO users (id, oura_access_token, oura_refresh_token, oura_expires_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        oura_access_token = excluded.oura_access_token,
                        oura_refresh_token = excluded.oura_refresh_token,
                        oura_expires_at = excluded.oura_expires_at,
                        updated_at = excluded.updated_at
                """),
                (user_id, access_token, refresh_token or "", expires_at, now, now),
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


def delete_user_data(user_id: int) -> None:
    """Delete user's plans and user row (data deletion)."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("DELETE FROM plans WHERE user_id = ?"), (user_id,))
        cur.execute(_q("DELETE FROM users WHERE id = ?"), (user_id,))

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
