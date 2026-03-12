"""JWT session auth: create token after Oura OAuth, dependency for protected routes."""

import os
import time
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 60 * 60 * 24 * 7  # 7 days
COOKIE_NAME = "session"


def _get_secret() -> str:
    secret = (os.getenv("SECRET_KEY") or os.getenv("JWT_SECRET") or "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="SECRET_KEY or JWT_SECRET must be set for authentication.",
        )
    return secret


def create_session_token(user_id: int) -> str:
    """Create a JWT for the given user_id. Used after Oura callback."""
    secret = _get_secret()
    payload = {
        "sub": str(user_id),
        "exp": int(time.time()) + JWT_EXPIRY_SECONDS,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> int | None:
    try:
        secret = _get_secret()
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            return None
        return int(sub)
    except (jwt.InvalidTokenError, ValueError):
        return None


async def get_current_user_id(request: Request) -> int:
    """
    Dependency: resolve user id from JWT in Cookie (session) or Authorization: Bearer.
    Raises 401 if missing or invalid.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token and request.headers.get("authorization", "").lower().startswith("bearer "):
        token = request.headers.get("authorization", "").split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Connect Oura via /auth/oura/authorize.",
        )
    user_id = _decode_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session. Connect Oura again.",
        )
    return user_id


# Type alias for route dependencies
CurrentUserId = Annotated[int, Depends(get_current_user_id)]
