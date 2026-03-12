"""Oura OAuth2 flow: authorize URL, token exchange, state validation, and SQLite token store."""

import hmac
import hashlib
import os
import secrets
import time
from typing import Any

import httpx
from fastapi import HTTPException

from app.db import DEFAULT_USER_ID, get_oura_tokens, set_oura_tokens

OURA_AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
OURA_TOKEN_URL = "https://api.ouraring.com/oauth/token"
OURA_SCOPES = "email personal daily heartrate"


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


STATE_TTL_SECONDS = 600  # 10 minutes


def generate_state() -> str:
    """
    Generate OAuth state: random string, or signed state (nonce.expiry.sig) if SECRET_KEY is set.
    Signed state allows verifying expiry and prevents forgery.
    """
    secret = (os.getenv("SECRET_KEY") or "").strip()
    if secret:
        nonce = secrets.token_urlsafe(24)
        expiry = int(time.time()) + STATE_TTL_SECONDS
        payload = f"{nonce}.{expiry}"
        sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"
    return secrets.token_urlsafe(32)


def verify_state(received: str | None, stored: str | None) -> bool:
    """
    Verify OAuth callback state: received (from query) must match stored (from cookie), constant-time.
    If SECRET_KEY is set, stored must be a valid signed state (signature + not expired).
    Returns True only if both checks pass.
    """
    if not received or not stored:
        return False
    if not secrets.compare_digest(received, stored):
        return False
    secret = (os.getenv("SECRET_KEY") or "").strip()
    if not secret:
        return True
    parts = stored.rsplit(".", 1)
    if len(parts) != 2:
        return False
    payload, sig = parts[0], parts[1]
    expected_sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(sig, expected_sig):
        return False
    try:
        expiry = int(payload.split(".")[-1])
    except (ValueError, IndexError):
        return False
    if time.time() > expiry:
        return False
    return True


async def exchange_code_for_tokens(
    code: str,
    redirect_uri: str | None = None,
    store_key: str = "default",
    store: bool = True,
) -> dict[str, Any]:
    """
    Exchange authorization code for access and refresh tokens.
    If store=True (default), stores tokens for the user identified by store_key (legacy: "default" -> DEFAULT_USER_ID).
    If store=False, returns token dict without persisting (caller stores after resolving user_id).
    Raises HTTPException on failure.
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
    if store:
        uid = DEFAULT_USER_ID if store_key == "default" else DEFAULT_USER_ID
        set_oura_tokens(uid, access_token, refresh_token, expires_at)
    return {"access_token": access_token, "refresh_token": refresh_token, "expires_in": expires_in, "expires_at": expires_at}


async def refresh_oura_tokens(user_id: int) -> dict[str, Any]:
    """Refresh access token using stored refresh_token for user_id. Updates DB. Raises if missing or failed."""
    stored = get_oura_tokens(user_id)
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
    set_oura_tokens(user_id, access_token, new_refresh, expires_at)
    return {"access_token": access_token, "refresh_token": new_refresh, "expires_in": expires_in, "expires_at": expires_at}


def get_stored_access_token(user_id: int = DEFAULT_USER_ID) -> str | None:
    """Return current access token if present. Does not refresh."""
    stored = get_oura_tokens(user_id)
    if not stored:
        return None
    return stored.get("access_token")


def get_stored_tokens(user_id: int = DEFAULT_USER_ID) -> dict[str, Any] | None:
    """Return full stored token dict for the user, or None."""
    return get_oura_tokens(user_id)


async def get_valid_access_token(user_id: int) -> str:
    """
    Return a valid access token for the user, refreshing if expired.
    Raises HTTPException if no token or refresh fails.
    """
    stored = get_oura_tokens(user_id)
    if not stored:
        raise HTTPException(
            status_code=401,
            detail="Oura not connected. Visit /auth/oura/authorize to link your account.",
        )
    # Refresh if expired (with 60s buffer)
    if stored.get("expires_at", 0) < time.time() + 60 and stored.get("refresh_token"):
        await refresh_oura_tokens(user_id)
        stored = get_oura_tokens(user_id)
    token = stored.get("access_token") if stored else None
    if not token:
        raise HTTPException(status_code=401, detail="Oura token missing. Re-authorize via /auth/oura/authorize.")
    return token
