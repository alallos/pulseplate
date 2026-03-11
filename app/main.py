"""PulsePlate - FastAPI backend

Hyper-personalized daily meal architect powered by your biometrics.
Turns Oura/Apple Watch data into zero-decision meal plans + grocery lists.
"""

from pathlib import Path

from fastapi import FastAPI, Body, Query, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from dotenv import load_dotenv

from app.models.biometrics import (
    BiometricData,
    MealPlanFromOuraOverrides,
    MealPlanResponse,
)
from app.services.meal_generator import generate_meal_plan as generate_meal_plan_service
from app.services.oura_oauth import (
    exchange_code_for_tokens,
    generate_state,
    get_authorize_url,
    get_valid_access_token,
)
from app.services.oura_client import fetch_oura_biometrics
from app.db import DEFAULT_USER_ID, init_db, get_user_preferences, set_user_preferences

# Load environment variables early (even if .env is empty for now)
load_dotenv()

app = FastAPI(
    title="PulsePlate",
    description=(
        "Your biometrics-powered daily meal architect. "
        "Zero decisions — just optimized plates from today's recovery data."
    ),
    version="0.1.0-dev",
    docs_url="/docs",    # Swagger UI
    redoc_url="/redoc",
)


@app.on_event("startup")
async def startup() -> None:
    """Ensure SQLite DB and tables exist."""
    init_db()


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


@app.get("/health")
async def health():
    """Health check for monitoring / load balancers."""
    return {"status": "alive", "message": "PulsePlate"}


# --- Oura OAuth ---

@app.get("/auth/oura/authorize")
async def oura_authorize():
    """
    Redirect to Oura to authorize the app. After approval, user is sent to /auth/oura/callback.
    """
    state = generate_state()
    url = get_authorize_url(state=state)
    response = RedirectResponse(url=url, status_code=302)
    # Store state in cookie for callback verification (optional; for production use signed state)
    response.set_cookie(key="oura_state", value=state, httponly=True, max_age=600)
    return response


@app.get("/auth/oura/callback")
async def oura_callback(
    code: str | None = Query(None, description="Authorization code from Oura"),
    state: str | None = Query(None),
    error: str | None = Query(None, description="e.g. access_denied if user declined"),
):
    """
    Oura redirects here after user authorizes. Exchanges code for tokens and stores them.
    Redirects to / with success or error.
    """
    if error:
        return RedirectResponse(url="/?oura_error=" + error, status_code=302)
    if not code:
        return RedirectResponse(url="/?oura_error=missing_code", status_code=302)
    try:
        await exchange_code_for_tokens(code=code)
    except HTTPException:
        raise
    except Exception:
        return RedirectResponse(url="/?oura_error=token_exchange_failed", status_code=302)
    return RedirectResponse(url="/?oura=connected", status_code=302)


@app.get("/preferences")
async def get_preferences():
    """Return saved user preferences (goals, diet_style, calorie_target, allergies)."""
    return get_user_preferences()


@app.put("/preferences")
async def update_preferences(
    overrides: MealPlanFromOuraOverrides = Body(..., description="Goals, diet_style, calorie_target, allergies to save"),
):
    """Save user preferences. Used as defaults when generating meal plan from Oura."""
    set_user_preferences(
        DEFAULT_USER_ID,
        goals=overrides.goals,
        diet_style=overrides.diet_style,
        calorie_target=overrides.calorie_target,
        allergies=overrides.allergies,
    )
    return get_user_preferences()


@app.get("/biometrics/oura", response_model=BiometricData)
async def get_oura_biometrics():
    """
    Fetch current biometrics from Oura (sleep, readiness, activity, HR, HRV).
    Requires having connected Oura via /auth/oura/authorize first.
    Returns BiometricData with Oura-derived fields; goals/diet/calories/allergies use defaults (override in UI or merge when calling /generate-meal-plan).
    """
    access_token = await get_valid_access_token()
    return await fetch_oura_biometrics(access_token)


@app.post("/generate-meal-plan", response_model=MealPlanResponse)
async def generate_meal_plan(
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
    )
):
    """
    Generate a same-day meal plan + grocery list from biometrics via Grok API.
    """
    return await generate_meal_plan_service(biometrics)


@app.post("/generate-meal-plan/from-oura", response_model=MealPlanResponse)
async def generate_meal_plan_from_oura(
    overrides: MealPlanFromOuraOverrides | None = Body(
        None,
        description="Optional goals, diet, calories, allergies; biometrics come from Oura. Send {} or omit for defaults.",
    ),
):
    """
    Fetch current biometrics from Oura, merge with your preferences, and generate today's meal plan + grocery list.
    Requires Oura connected via /auth/oura/authorize. Uses saved preferences from DB when overrides are omitted.
    """
    saved = get_user_preferences()
    # Use saved prefs when no body or empty body (defaults)
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
        )
    else:
        o = overrides or MealPlanFromOuraOverrides()
    access_token = await get_valid_access_token()
    biometrics = await fetch_oura_biometrics(access_token)
    merged = biometrics.model_copy(
        update={
            "goals": o.goals or biometrics.goals,
            "diet_style": o.diet_style or biometrics.diet_style,
            "calorie_target": o.calorie_target,
            "allergies": o.allergies if o.allergies is not None else biometrics.allergies,
        }
    )
    return await generate_meal_plan_service(merged)

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
