"""SQLite persistence for Oura tokens and user preferences."""

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
    """Create users table if it does not exist. Safe to call on every startup."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
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
