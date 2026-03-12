"""Pydantic models for PulsePlate API inputs/outputs."""

from typing import List, Optional
from pydantic import BaseModel, Field


class BiometricData(BaseModel):
    """Mock/current biometric snapshot from wearables (e.g., Oura)."""

    sleep_score: float = Field(..., ge=0, le=100, description="Sleep score out of 100")
    recovery_status: str = Field(
        ..., pattern="^(optimal|good|fair|low)$", description="Recovery category"
    )
    hrv_ms: float = Field(..., gt=0, description="Heart Rate Variability in ms")
    resting_hr_bpm: float = Field(..., gt=0, description="Resting heart rate")
    steps_yesterday: int = Field(..., ge=0, description="Steps from previous day")
    goals: List[str] = Field(
        default_factory=list, description="e.g. ['fat_loss', 'muscle_gain', 'stable_glucose']"
    )
    diet_style: str = Field(
        default="balanced", description="e.g. 'keto', 'mediterranean', 'vegan'"
    )
    calorie_target: int = Field(..., gt=1000, description="Daily calorie goal")
    allergies: Optional[List[str]] = Field(default=None, description="e.g. ['nuts', 'dairy']")


class MealPlanFromOuraOverrides(BaseModel):
    """Optional overrides when generating a meal plan from Oura (biometrics from ring, rest from body)."""

    goals: List[str] = Field(default_factory=list, description="e.g. ['fat_loss', 'stable_glucose']")
    diet_style: str = Field(default="balanced", description="e.g. 'mediterranean', 'keto'")
    calorie_target: int = Field(default=2000, gt=1000, description="Daily calorie goal")
    allergies: Optional[List[str]] = Field(default=None, description="e.g. ['nuts', 'dairy']")


class MealPlanResponse(BaseModel):
    """Structured output: single-day meal plan + grocery list."""

    summary: str = Field(..., description="One-sentence overview of today's plan rationale")
    meals: List[dict] = Field(..., description="List of 3 meals + snacks")
    grocery_list: List[dict] = Field(..., description="Items with quantities")


class DayPlan(BaseModel):
    """One day in a weekly meal plan."""

    day: str = Field(..., description="Day label, e.g. Monday")
    meals: List[dict] = Field(..., description="Meals for this day (type, name, description, calories)")


class GroceryItemWeekly(BaseModel):
    """Grocery item for weekly plan: scaled quantity + optional batch/prep notes."""

    item: str = Field(..., description="Ingredient name")
    quantity: str = Field(..., description="Quantity for the week, e.g. 2kg, 1.5L")
    prep_notes: Optional[str] = Field(default=None, description="e.g. Batch cook Sunday")


class WeeklyMealPlanResponse(BaseModel):
    """Structured output: 5–7 day batch meal plan + consolidated grocery list."""

    summary: str = Field(..., description="Weekly overview and batch-prep rationale")
    days: List[DayPlan] = Field(..., description="One entry per day (5–7 days)")
    grocery_list: List[GroceryItemWeekly] = Field(
        ..., description="Consolidated weekly list with scaled quantities and prep notes"
    )
