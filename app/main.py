"""PulsePlate - FastAPI backend

Hyper-personalized daily meal architect powered by your biometrics.
Turns Oura/Apple Watch data into zero-decision meal plans + grocery lists.
"""

from fastapi import FastAPI, Body
from dotenv import load_dotenv
from app.models.biometrics import BiometricData, MealPlanResponse
from app.services.meal_generator import generate_meal_plan_mock

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


@app.get("/")
async def root():
    """Basic health check / welcome endpoint."""
    return {
        "message": "Welcome to PulsePlate! Your pulse → perfect plate.",
        "status": "alive",
        "docs": "Visit /docs for interactive API documentation (Swagger UI)",
    }

@app.post("/generate-meal-plan", response_model=MealPlanResponse)
async def generate_meal_plan(
    biometrics: BiometricData = Body(
        ...,
        description="Current biometric data + user preferences",
        example={
            "sleep_score": 78,
            "recovery_status": "fair",
            "hrv_ms": 45,
            "resting_hr_bpm": 62,
            "steps_yesterday": 8500,
            "goals": ["fat_loss", "stable_glucose"],
            "diet_style": "mediterranean",
            "calorie_target": 2200,
            "allergies": ["nuts"],
        },
    )
):
    """
    Generate a same-day meal plan + grocery list from mock/current biometrics.
    (Mock LLM for now — real Grok integration next.)
    """
    plan = generate_meal_plan_mock(biometrics)
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
