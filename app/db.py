"""SQLite persistence for Oura tokens, user preferences, and plan history."""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

# Default: project root (parent of app/)
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "pulseplate.db"
DB_PATH = Path(os.getenv("PULSEPLATE_DB", str(_DEFAULT_DB_PATH)))

DEFAULT_USER_ID = 1


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create users and plans tables if they do not exist. Migrate users with new columns. Safe to call on every startup."""
    with _get_conn() as conn:
        conn.execute("""
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
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Migration: add email/oura_user_id if table already existed without them
        for col in ("email", "oura_user_id"):
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.execute("""
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_user_id ON plans(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_generated_at ON plans(generated_at)")
        conn.commit()


def get_oura_tokens(user_id: int = DEFAULT_USER_ID) -> dict[str, Any] | None:
    """Return stored Oura tokens for the user, or None if not connected."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT oura_access_token, oura_refresh_token, oura_expires_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row or not row[0]:
        return None
    return {
        "access_token": row[0],
        "refresh_token": row[1],
        "expires_at": row[2],
    }


def get_or_create_user_by_email(email: str) -> int:
    """
    Return user id for the given email. If a user with this email exists, return their id.
    If the legacy single user (id=1) has no email set, assign this email to them and return 1.
    Otherwise create a new user and return the new id.
    """
    if not (email or "").strip():
        raise ValueError("email is required")
    email = email.strip()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            return row[0]
        legacy = conn.execute(
            "SELECT id FROM users WHERE id = 1 AND (email IS NULL OR email = '')"
        ).fetchone()
        if legacy:
            conn.execute(
                "UPDATE users SET email = ?, updated_at = ? WHERE id = 1",
                (email, now),
            )
            conn.commit()
            return 1
        max_row = conn.execute("SELECT MAX(id) FROM users").fetchone()
        next_id = (max_row[0] or 0) + 1
        conn.execute(
            "INSERT INTO users (id, email, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (next_id, email, now, now),
        )
        conn.commit()
        return next_id


def set_oura_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str | None,
    expires_at: int,
) -> None:
    """Store or update Oura tokens for the user. Upserts the user row."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (id, oura_access_token, oura_refresh_token, oura_expires_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                oura_access_token = excluded.oura_access_token,
                oura_refresh_token = excluded.oura_refresh_token,
                oura_expires_at = excluded.oura_expires_at,
                updated_at = excluded.updated_at
            """,
            (user_id, access_token, refresh_token or "", expires_at, now, now),
        )
        conn.commit()


def get_user_preferences(user_id: int = DEFAULT_USER_ID) -> dict[str, Any]:
    """Return saved preferences (goals, diet_style, calorie_target, allergies). Defaults if not set."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT goals, diet_style, calorie_target, allergies FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return {"goals": [], "diet_style": "balanced", "calorie_target": 2000, "allergies": None}
    goals = json.loads(row[0]) if row[0] else []
    diet_style = row[1] or "balanced"
    calorie_target = row[2] if row[2] is not None else 2000
    allergies = json.loads(row[3]) if row[3] else None
    return {
        "goals": goals,
        "diet_style": diet_style,
        "calorie_target": calorie_target,
        "allergies": allergies,
    }


def set_user_preferences(
    user_id: int,
    goals: list[str] | None = None,
    diet_style: str | None = None,
    calorie_target: int | None = None,
    allergies: list[str] | None = None,
) -> None:
    """Update saved preferences. Creates user row if missing (with NULL tokens)."""
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
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (id, goals, diet_style, calorie_target, allergies, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                goals = excluded.goals,
                diet_style = excluded.diet_style,
                calorie_target = excluded.calorie_target,
                allergies = excluded.allergies,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                json.dumps(prefs["goals"]),
                prefs["diet_style"],
                prefs["calorie_target"],
                json.dumps(prefs["allergies"]) if prefs["allergies"] is not None else None,
                now,
                now,
            ),
        )
        conn.commit()


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
        cur = conn.execute(
            """
            INSERT INTO plans (user_id, generated_at, biometric_snapshot, plan_json, weekly_days, is_weekly)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, generated_at, biometric_snapshot, plan_json, weekly_days, 1 if is_weekly else 0),
        )
        conn.commit()
        return cur.lastrowid


def get_plans(
    user_id: int,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return paginated plan history for the user (newest first)."""
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, generated_at, plan_json, weekly_days, is_weekly
            FROM plans WHERE user_id = ? ORDER BY generated_at DESC LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()
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
        row = conn.execute(
            "SELECT id, user_id, generated_at, biometric_snapshot, plan_json, weekly_days, is_weekly FROM plans WHERE id = ? AND user_id = ?",
            (plan_id, user_id),
        ).fetchone()
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
