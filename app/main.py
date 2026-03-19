"""PulsePlate - FastAPI backend

Hyper-personalized daily meal architect powered by your biometrics.
Turns Oura/Apple Watch data into zero-decision meal plans + grocery lists.
"""

import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from fastapi import FastAPI, Body, Query, HTTPException, Request
from fastapi.responses import RedirectResponse, FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

from app.auth import create_session_token, get_current_user_id, CurrentUserId
from app.models.biometrics import (
    BiometricData,
    MealPlanFromOuraOverrides,
    MealPlanResponse,
    WeeklyMealPlanResponse,
)
from app.services.meal_generator import (
    generate_meal_plan as generate_meal_plan_service,
    generate_weekly_meal_plan,
)
from app.services.oura_oauth import (
    exchange_code_for_tokens,
    generate_state,
    get_authorize_url,
    get_valid_access_token,
    verify_state,
)
from app.services.oura_client import fetch_oura_biometrics, fetch_oura_personal_info
from app.db import (
    init_db,
    get_user_preferences,
    set_user_preferences,
    get_or_create_user_by_email,
    set_oura_tokens,
    clear_oura_tokens,
    delete_user_data,
    save_plan,
    save_oura_webhook_event,
    get_plans,
    get_plan_by_id,
    get_oura_tokens,
    get_latest_oura_webhook_event_for_user,
    get_recent_oura_webhook_events_for_user,
)

# Load environment variables early (even if .env is empty for now)
load_dotenv()

# Structured logging: timestamp (UTC), level, logger name, message
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Optional Sentry monitoring (enabled only if SENTRY_DSN is set)
_sentry_dsn = (os.getenv("SENTRY_DSN") or "").strip()
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[FastApiIntegration()],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.0")),
        environment=os.getenv("SENTRY_ENVIRONMENT") or None,
    )
    log.info("Sentry monitoring enabled")
else:
    log.info("Sentry monitoring not configured (SENTRY_DSN not set)")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Assign request_id and log request start/end."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        log.info("request_started method=%s path=%s request_id=%s", request.method, request.scope.get("path"), request_id)
        response = await call_next(request)
        log.info("request_finished method=%s path=%s request_id=%s status=%s", request.method, request.scope.get("path"), request_id, response.status_code)
        return response


limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB. Shutdown: optional cleanup."""
    init_db()
    log.info("PulsePlate startup complete")
    yield
    # Shutdown: nothing to close for SQLite/Postgres per-request pattern


app = FastAPI(
    title="PulsePlate",
    description=(
        "Your biometrics-powered daily meal architect. "
        "Zero decisions — just optimized plates from today's recovery data."
    ),
    version="0.1.0-dev",
    docs_url="/docs",    # Swagger UI
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(RequestLoggingMiddleware)


_STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/")
async def root():
    """Serve the PulsePlate app UI."""
    index = _STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {
        "message": "Welcome to PulsePlate! Your pulse → perfect plate.",
        "status": "alive",
        "docs": "Visit /docs for interactive API documentation (Swagger UI)",
    }


@app.get("/about")
async def about():
    """Serve a shareable 'How it works' page."""
    page = _STATIC_DIR / "about.html"
    if page.exists():
        return FileResponse(page)
    raise HTTPException(status_code=404, detail="Not found")


@app.get("/privacy")
async def privacy():
    """Serve privacy & deletion policy page."""
    page = _STATIC_DIR / "privacy.html"
    if page.exists():
        return FileResponse(page)
    raise HTTPException(status_code=404, detail="Not found")


@app.get("/terms")
async def terms():
    """Serve terms of use page."""
    page = _STATIC_DIR / "terms.html"
    if page.exists():
        return FileResponse(page)
    raise HTTPException(status_code=404, detail="Not found")


@app.get("/health")
async def health():
    """Health check for monitoring / load balancers. Always returns 200 if the app is up."""
    return {"status": "alive", "message": "PulsePlate"}


@app.get("/health/ready")
async def health_ready():
    """
    Readiness check: returns 200 if the app can generate meal plans (GROK_API_KEY set), 503 otherwise.
    Use for deployment / load balancers to avoid routing to instances that cannot serve plan requests.
    """
    api_key = (os.getenv("GROK_API_KEY") or "").strip()
    if not api_key:
        log.warning("health_ready failed: GROK_API_KEY not set")
        raise HTTPException(status_code=503, detail="GROK_API_KEY not configured")
    return {"status": "ready", "message": "PulsePlate ready to generate meal plans"}


def _extract_oura_user_id(payload: object) -> str | None:
    """Best-effort extraction of an Oura user identifier from webhook payloads."""
    if not isinstance(payload, dict):
        return None

    def _maybe_str(v: object) -> str | None:
        return str(v) if v is not None and v != "" else None

    # Common top-level keys seen in webhook payloads from various integrations.
    for key in ("owner_id", "user_id", "oura_user_id", "userId"):
        v = payload.get(key)
        out = _maybe_str(v)
        if out:
            return out

    # Nested shapes (best-effort)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("owner_id", "user_id", "oura_user_id", "userId"):
            v = data.get(key)
            out = _maybe_str(v)
            if out:
                return out

    user = payload.get("user")
    if isinstance(user, dict):
        for key in ("id", "owner_id", "user_id"):
            out = _maybe_str(user.get(key))
            if out:
                return out

    return None


def _extract_oura_event_type(payload: object) -> str | None:
    """Best-effort extraction of event type/name for storage."""
    if not isinstance(payload, dict):
        return None
    for key in ("type", "event_type", "event", "kind"):
        v = payload.get(key)
        if v:
            return str(v)
    return None


@app.post("/webhooks/oura", include_in_schema=False)
async def oura_webhook(request: Request):
    """
    Receiver endpoint for Oura webhooks.

    Note: This implementation stores the raw webhook payload for debugging and future processing.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    oura_user_id = _extract_oura_user_id(payload)
    event_type = _extract_oura_event_type(payload)

    try:
        save_oura_webhook_event(
            oura_user_id=oura_user_id,
            event_type=event_type,
            payload=payload if isinstance(payload, dict) else {"payload": payload},
        )
    except Exception:
        # Never fail the webhook delivery due to our storage layer.
        log.exception("Failed to persist Oura webhook event")

    return {"status": "ok"}


@app.get("/webhooks/oura/status", summary="Latest Oura webhook status (for this user)")
async def oura_webhook_status(user_id: CurrentUserId):
    """
    Returns whether the user is connected to Oura and when the most recent webhook
    event was received for this user.
    """
    tokens = get_oura_tokens(user_id)
    connected = bool(tokens and tokens.get("access_token"))
    event = get_latest_oura_webhook_event_for_user(user_id)
    return {
        "connected": connected,
        "last_event_at": (event or {}).get("received_at"),
        "last_event_type": (event or {}).get("event_type"),
    }


@app.get("/webhooks/oura/events", summary="Recent Oura webhook events (for this user)")
async def oura_webhook_events(
    user_id: CurrentUserId,
    limit: int = Query(5, ge=1, le=20, description="Number of recent events to return"),
):
    """Return the most recent stored webhook events for this user (debug/verification)."""
    events = get_recent_oura_webhook_events_for_user(user_id, limit=limit)
    return {"events": events}


# --- Oura OAuth ---

@app.get("/auth/oura/authorize")
async def oura_authorize():
    """
    Redirect to Oura to authorize the app. State is stored in a cookie and validated on callback (CSRF protection).
    If SECRET_KEY is set, state is signed and expiry-checked.
    """
    state = generate_state()
    url = get_authorize_url(state=state)
    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(
        key="oura_state",
        value=state,
        httponly=True,
        max_age=600,
        samesite="lax",
    )
    return response


@app.get("/auth/oura/callback")
async def oura_callback(
    request: Request,
    code: str | None = Query(None, description="Authorization code from Oura"),
    state: str | None = Query(None, description="State echoed back by Oura; must match cookie"),
    error: str | None = Query(None, description="e.g. access_denied if user declined"),
):
    """
    Oura redirects here after user authorizes. Validates state (CSRF), exchanges code for tokens,
    fetches Oura email to resolve/create user, stores tokens for that user, issues JWT session cookie.
    """
    if error:
        return RedirectResponse(url="/?oura_error=" + error, status_code=302)
    if not code:
        return RedirectResponse(url="/?oura_error=missing_code", status_code=302)
    stored_state = request.cookies.get("oura_state")
    if not verify_state(state, stored_state):
        return RedirectResponse(url="/?oura_error=invalid_state", status_code=302)
    try:
        tokens = await exchange_code_for_tokens(code=code, store=False)
    except HTTPException:
        raise
    except Exception:
        return RedirectResponse(url="/?oura_error=token_exchange_failed", status_code=302)
    access_token = tokens["access_token"]
    try:
        info = await fetch_oura_personal_info(access_token)
    except HTTPException:
        return RedirectResponse(url="/?oura_error=personal_info_failed", status_code=302)
    email = (info.get("email") or info.get("id") or "unknown")
    if not isinstance(email, str):
        email = str(email)
    oura_user_id = info.get("id")
    if oura_user_id is not None:
        oura_user_id = str(oura_user_id)
    try:
        user_id = get_or_create_user_by_email(email)
    except ValueError:
        return RedirectResponse(url="/?oura_error=invalid_email", status_code=302)
    set_oura_tokens(
        user_id,
        access_token,
        tokens.get("refresh_token"),
        tokens.get("expires_at", 0),
        oura_user_id=oura_user_id,
    )
    jwt_token = create_session_token(user_id)
    response = RedirectResponse(url="/?oura=connected", status_code=302)
    response.delete_cookie(key="oura_state")
    response.set_cookie(
        key="session",
        value=jwt_token,
        httponly=True,
        max_age=60 * 60 * 24 * 7,  # 7 days
        samesite="lax",
    )
    return response


@app.post("/auth/logout", summary="Log out (clear session cookie)")
async def logout():
    """Clear the session cookie on the client."""
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(key="session")
    return response


@app.post("/auth/disconnect", summary="Disconnect Oura (clear tokens) and log out")
async def disconnect_oura(user_id: CurrentUserId):
    """Clear Oura tokens for this user and end the session."""
    clear_oura_tokens(user_id)
    response = RedirectResponse(url="/?oura=disconnected", status_code=302)
    response.delete_cookie(key="session")
    return response


@app.post("/account/delete", summary="Delete my data (plans + user) and log out")
async def delete_account(user_id: CurrentUserId):
    """Delete plan history and user record for this user, then end the session."""
    delete_user_data(user_id)
    response = RedirectResponse(url="/?account=deleted", status_code=302)
    response.delete_cookie(key="session")
    return response


@app.get("/preferences")
async def get_preferences(user_id: CurrentUserId):
    """Return saved user preferences (goals, diet_style, calorie_target, allergies). Requires auth."""
    return get_user_preferences(user_id)


@app.put("/preferences")
async def update_preferences(
    user_id: CurrentUserId,
    overrides: MealPlanFromOuraOverrides = Body(..., description="Goals, diet_style, calorie_target, allergies to save"),
):
    """Save user preferences. Used as defaults when generating meal plan from Oura. Requires auth."""
    set_user_preferences(
        user_id,
        goals=overrides.goals,
        diet_style=overrides.diet_style,
        calorie_target=overrides.calorie_target,
        allergies=overrides.allergies,
        measurement_system=overrides.measurement_system,
    )
    return get_user_preferences(user_id)


@app.get("/biometrics/oura", response_model=BiometricData)
async def get_oura_biometrics(user_id: CurrentUserId):
    """
    Fetch current biometrics from Oura (sleep, readiness, activity, HR, HRV).
    Requires auth (session after Oura connect).
    """
    access_token = await get_valid_access_token(user_id)
    return await fetch_oura_biometrics(access_token)


@app.post(
    "/generate-meal-plan",
    response_model=Union[MealPlanResponse, WeeklyMealPlanResponse],
    summary="Generate meal plan (daily or weekly)",
)
@limiter.limit("5/minute")
async def generate_meal_plan(
    request: Request,
    biometrics: BiometricData = Body(
        ...,
        description="Current biometric data + user preferences",
        examples=[
            {
                "sleep_score": 78,
                "recovery_status": "fair",
                "hrv_ms": 45,
                "resting_hr_bpm": 62,
                "steps_yesterday": 8500,
                "goals": ["fat_loss", "stable_glucose"],
                "diet_style": "mediterranean",
                "calorie_target": 2200,
                "allergies": ["nuts"],
            }
        ],
    ),
    weekly_prep: bool = Query(
        False,
        description="If true, generate a 5–7 day batch meal plan instead of a single day.",
    ),
    weekly_days: int = Query(
        7,
        ge=5,
        le=7,
        description="Number of days for weekly plan (5–7). Used only when weekly_prep=true.",
    ),
):
    """
    Generate a meal plan + grocery list from biometrics.

    - **Daily mode** (`weekly_prep=false`, default): Same-day plan via Grok API.
    - **Weekly mode** (`weekly_prep=true`): 5–7 day batch/meal-prep plan via Grok API; batch-friendly recipes, consolidated grocery list with prep notes.
    """
    if weekly_prep:
        return await generate_weekly_meal_plan(biometrics, days=weekly_days)
    return await generate_meal_plan_service(biometrics)


@app.post(
    "/generate-meal-plan/from-oura",
    response_model=Union[MealPlanResponse, WeeklyMealPlanResponse],
    summary="Generate meal plan from Oura (daily or weekly)",
)
@limiter.limit("5/minute")
async def generate_meal_plan_from_oura(
    request: Request,
    user_id: CurrentUserId,
    overrides: MealPlanFromOuraOverrides | None = Body(
        None,
        description="Optional goals, diet, calories, allergies; biometrics come from Oura. Send {} or omit for defaults.",
    ),
    weekly_prep: bool = Query(
        False,
        description="If true, generate a 5–7 day batch meal plan instead of a single day.",
    ),
    weekly_days: int = Query(
        7,
        ge=5,
        le=7,
        description="Number of days for weekly plan (5–7). Used only when weekly_prep=true.",
    ),
):
    """
    Fetch current biometrics from Oura, merge with your preferences, and generate a meal plan + grocery list.
    Requires auth. Saves the generated plan to history.
    """
    saved = get_user_preferences(user_id)
    use_saved = overrides is None or (
        (overrides.goals == [] and overrides.diet_style == "balanced"
         and overrides.calorie_target == 2000 and overrides.allergies is None)
    )
    if use_saved:
        o = MealPlanFromOuraOverrides(
            goals=saved["goals"],
            diet_style=saved["diet_style"],
            calorie_target=saved["calorie_target"],
            allergies=saved["allergies"],
            measurement_system=saved.get("measurement_system", "us"),
        )
    else:
        o = overrides or MealPlanFromOuraOverrides()
    access_token = await get_valid_access_token(user_id)
    biometrics = await fetch_oura_biometrics(access_token)
    merged = biometrics.model_copy(
        update={
            "goals": o.goals or biometrics.goals,
            "diet_style": o.diet_style or biometrics.diet_style,
            "calorie_target": o.calorie_target,
            "allergies": o.allergies if o.allergies is not None else biometrics.allergies,
            "measurement_system": o.measurement_system or biometrics.measurement_system,
        }
    )
    if weekly_prep:
        result = await generate_weekly_meal_plan(merged, days=weekly_days)
    else:
        result = await generate_meal_plan_service(merged)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_plan(
        user_id=user_id,
        generated_at=generated_at,
        biometric_snapshot=merged.model_dump_json(),
        plan_json=result.model_dump_json(),
        weekly_days=weekly_days if weekly_prep else None,
        is_weekly=weekly_prep,
    )
    return result


@app.get("/plans")
async def list_plans(
    user_id: CurrentUserId,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Return paginated plan history for the current user (newest first). Requires auth."""
    return get_plans(user_id, limit=limit, offset=offset)


@app.get("/plans/{plan_id}")
async def get_plan(
    plan_id: int,
    user_id: CurrentUserId,
):
    """Return a single saved plan by id if it belongs to the current user. Requires auth."""
    plan = get_plan_by_id(plan_id, user_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan

if __name__ == "__main__":
    # For quick local runs: python app/main.py
    # Recommended: use uvicorn in terminal for dev
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # auto-reload on code changes in dev
    )
