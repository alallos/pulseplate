"""Oura OAuth2 flow: authorize URL, token exchange, and SQLite token store."""

import os
import secrets
import time
from typing import Any

import httpx
from fastapi import HTTPException

from app.db import DEFAULT_USER_ID, get_oura_tokens, init_db, set_oura_tokens

OURA_AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
OURA_TOKEN_URL = "https://api.ouraring.com/oauth/token"
OURA_SCOPES = "email personal daily heartrate"


def _user_id_from_store_key(store_key: str) -> int:
    """Map store_key to user id. For now only 'default' -> DEFAULT_USER_ID."""
    return DEFAULT_USER_ID if store_key == "default" else DEFAULT_USER_ID


def get_authorize_url(redirect_uri: str | None = None, state: str | None = None) -> str:
    """Build Oura authorization URL. State should be passed to callback for CSRF check."""
    client_id = os.getenv("OURA_CLIENT_ID")
    if not client_id or not client_id.strip():
        raise HTTPException(
            status_code=503,
            detail="OURA_CLIENT_ID is not configured. Set it in .env.",
        )
    uri = redirect_uri or os.getenv("OURA_REDIRECT_URI")
    if not uri or not uri.strip():
        raise HTTPException(
            status_code=503,
            detail="OURA_REDIRECT_URI is not configured. Set it in .env.",
        )
    params = {
        "response_type": "code",
        "client_id": client_id.strip(),
        "redirect_uri": uri.strip(),
        "scope": OURA_SCOPES,
    }
    if state is not None:
        params["state"] = state
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{OURA_AUTHORIZE_URL}?{query}"


def generate_state() -> str:
    """Generate a random state string for OAuth CSRF protection."""
    return secrets.token_urlsafe(32)


async def exchange_code_for_tokens(
    code: str,
    redirect_uri: str | None = None,
    store_key: str = "default",
) -> dict[str, Any]:
    """
    Exchange authorization code for access and refresh tokens.
    Stores tokens in memory under store_key. Raises HTTPException on failure.
    """
    client_id = os.getenv("OURA_CLIENT_ID")
    client_secret = os.getenv("OURA_CLIENT_SECRET")
    if not client_id or not client_id.strip():
        raise HTTPException(status_code=503, detail="OURA_CLIENT_ID is not configured.")
    if not client_secret or not client_secret.strip():
        raise HTTPException(status_code=503, detail="OURA_CLIENT_SECRET is not configured.")
    uri = redirect_uri or os.getenv("OURA_REDIRECT_URI")
    if not uri or not uri.strip():
        raise HTTPException(status_code=503, detail="OURA_REDIRECT_URI is not configured.")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "redirect_uri": uri.strip(),
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                OURA_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Oura token request failed: {e!s}") from e

    if response.is_error:
        try:
            err = response.json()
            msg = err.get("error_description") or err.get("error") or response.text
        except Exception:
            msg = response.text or f"HTTP {response.status_code}"
        raise HTTPException(
            status_code=response.status_code if 400 <= response.status_code < 600 else 502,
            detail=f"Oura token error: {msg}",
        )

    body = response.json()
    access_token = body.get("access_token")
    refresh_token = body.get("refresh_token")
    expires_in = body.get("expires_in")
    if not access_token:
        raise HTTPException(status_code=502, detail="Oura response missing access_token.")

    expires_at = int(time.time()) + int(expires_in or 0)
    uid = _user_id_from_store_key(store_key)
    set_oura_tokens(uid, access_token, refresh_token, expires_at)
    return {"access_token": access_token, "refresh_token": refresh_token, "expires_in": expires_in, "expires_at": expires_at}


async def refresh_oura_tokens(store_key: str = "default") -> dict[str, Any]:
    """Refresh access token using stored refresh_token. Updates DB. Raises if missing or failed."""
    uid = _user_id_from_store_key(store_key)
    stored = get_oura_tokens(uid)
    if not stored or not stored.get("refresh_token"):
        raise HTTPException(
            status_code=401,
            detail="No Oura refresh token. Re-authorize via /auth/oura/authorize.",
        )

    client_id = os.getenv("OURA_CLIENT_ID")
    client_secret = os.getenv("OURA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="Oura client credentials not configured.")

    data = {
        "grant_type": "refresh_token",
        "refresh_token": stored["refresh_token"],
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                OURA_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Oura refresh failed: {e!s}") from e

    if response.is_error:
        try:
            err = response.json()
            msg = err.get("error_description") or err.get("error") or response.text
        except Exception:
            msg = response.text
        raise HTTPException(
            status_code=response.status_code if 400 <= response.status_code < 600 else 502,
            detail=f"Oura refresh error: {msg}",
        )

    body = response.json()
    access_token = body.get("access_token")
    refresh_token = body.get("refresh_token")
    expires_in = body.get("expires_in")
    if not access_token:
        raise HTTPException(status_code=502, detail="Oura refresh response missing access_token.")

    expires_at = int(time.time()) + int(expires_in or 0)
    new_refresh = refresh_token or (stored.get("refresh_token") if stored else None)
    set_oura_tokens(uid, access_token, new_refresh, expires_at)
    return {"access_token": access_token, "refresh_token": new_refresh, "expires_in": expires_in, "expires_at": expires_at}


def get_stored_access_token(store_key: str = "default") -> str | None:
    """Return current access token if present. Does not refresh."""
    stored = get_oura_tokens(_user_id_from_store_key(store_key))
    if not stored:
        return None
    return stored.get("access_token")


def get_stored_tokens(store_key: str = "default") -> dict[str, Any] | None:
    """Return full stored token dict for the key, or None."""
    return get_oura_tokens(_user_id_from_store_key(store_key))


async def get_valid_access_token(store_key: str = "default") -> str:
    """
    Return a valid access token, refreshing if expired. Raises HTTPException if no token or refresh fails.
    """
    uid = _user_id_from_store_key(store_key)
    stored = get_oura_tokens(uid)
    if not stored:
        raise HTTPException(
            status_code=401,
            detail="Oura not connected. Visit /auth/oura/authorize to link your account.",
        )
    # Refresh if expired (with 60s buffer)
    if stored.get("expires_at", 0) < time.time() + 60 and stored.get("refresh_token"):
        await refresh_oura_tokens(store_key=store_key)
        stored = get_oura_tokens(uid)
    token = stored and stored.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Oura token missing. Re-authorize via /auth/oura/authorize.")
    return token
