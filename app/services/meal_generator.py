"""Meal plan generator via Grok API (xAI). Daily and weekly (batch) plans from biometrics."""

import json
import logging
import os
import re
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.biometrics import (
    BiometricData,
    DayPlan,
    GroceryItemWeekly,
    MealPlanResponse,
    WeeklyMealPlanResponse,
)

logger = logging.getLogger(__name__)

GROK_URL = "https://api.x.ai/v1/chat/completions"
GROK_MODEL = "grok-4-0709"

# Strict JSON schema we require from the model (for prompt)
MEAL_PLAN_JSON_SCHEMA = """
{
  "summary": "string (one sentence overview of today's plan rationale)",
  "meals": [
    { "type": "string (e.g. Breakfast, Lunch, Dinner, Snack)", "name": "string", "description": "string", "calories": number }
  ],
  "grocery_list": [
    { "item": "string", "quantity": "string" }
  ]
}
"""

SYSTEM_PROMPT = """You are a hyper-personalized meal planning assistant for PulsePlate. Given biometric data and user preferences, you generate a same-day meal plan and grocery list.

CRITICAL: You must respond with ONLY valid JSON and nothing else. No markdown, no code fences, no explanation before or after. The JSON must match this exact structure:
""" + MEAL_PLAN_JSON_SCHEMA.strip() + """

Rules:
- summary: One clear sentence explaining why this plan fits today's recovery and goals.
- meals: Include at least 3 main eating occasions (e.g. Breakfast, Lunch, Dinner) plus 1–2 snacks. Each meal must have type, name, description, and calories (integer).
- grocery_list: Every ingredient needed for the day, each with "item" and "quantity". No duplicates.
- Units: If the user's measurement_system is "us", use US customary units for all quantities (cups, fl oz, lb, oz, tbsp, tsp). If "metric", use metric (g, kg, ml, L). Apply to both meal descriptions and grocery_list quantities.
- Respect allergies strictly (e.g. if nuts: zero tree nuts or peanuts).
- Total meal calories should be close to the user's calorie_target.
- Diet style (mediterranean, keto, etc.) and goals (fat_loss, stable_glucose, etc.) must shape the plan."""

# Weekly batch plan: JSON schema for Grok
WEEKLY_MEAL_PLAN_JSON_SCHEMA = """
{
  "summary": "string (weekly overview: batch-friendly rationale, diet style, calorie target)",
  "days": [
    {
      "day": "string (e.g. Monday, Tuesday)",
      "meals": [
        { "type": "string", "name": "string", "description": "string", "calories": number }
      ]
    }
  ],
  "grocery_list": [
    { "item": "string", "quantity": "string", "prep_notes": "string or null (e.g. Batch cook Sunday)" }
  ]
}
"""

WEEKLY_SYSTEM_PROMPT = """You are a meal-prep and batch-cooking expert for PulsePlate. Given biometric data and user preferences, you generate a 5–7 day weekly meal plan optimized for cooking in batches, minimal waste, and shelf-stable/reheatable meals.

CRITICAL: Respond with ONLY valid JSON. No markdown, no code fences, no text before or after. Use this exact structure:
""" + WEEKLY_MEAL_PLAN_JSON_SCHEMA.strip() + """

Rules:
- summary: One paragraph explaining the weekly approach (batch-friendly, shared ingredients, how it fits recovery/goals).
- days: Array of 5–7 day objects. Each day has "day" (e.g. Monday) and "meals" (3–4 meals: breakfast, lunch, dinner, optional snack). Use quick-assembly breakfasts (e.g. overnight oats base + daily toppings, or batch-made frittata) since the plan is used after overnight data.
- grocery_list: ONE consolidated list for the whole week. Quantities scaled for the week. Include "prep_notes" where helpful (e.g. "Batch grill Sunday", "Cook in bulk", "Overnight oats base for week"). Minimize waste and reuse ingredients across days.
- Units: If the user's measurement_system is "us", use US customary units (cups, fl oz, lb, oz, tbsp, tsp) for all quantities in days and grocery_list. If "metric", use metric (g, kg, ml, L).
- Batch-friendly: recipes that scale, store well, reheat well; shared bases (e.g. large batch of grilled chicken, lentil soup, roasted veggies).
- Respect allergies strictly. Match diet_style and goals. Target calorie_target per day.
"""


def _build_user_prompt(data: BiometricData) -> str:
    """Build user message with biometrics and preferences for Grok."""
    return (
        "Generate today's meal plan and grocery list for this user. "
        "Return ONLY the JSON object, no other text.\n\n"
        "Biometrics and preferences:\n"
        f"{data.model_dump_json(indent=2)}"
    )


def _build_weekly_user_prompt(data: BiometricData, days: int) -> str:
    """Build user message for weekly batch plan."""
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    requested = day_names[:days]
    return (
        f"Generate a {days}-day weekly batch meal plan. Days to include: {', '.join(requested)}. "
        "Return ONLY the JSON object, no other text.\n\n"
        "Biometrics and preferences:\n"
        f"{data.model_dump_json(indent=2)}"
    )


def _extract_json_from_content(content: str) -> Any:
    """Strip optional markdown code block and parse JSON."""
    text = content.strip()
    match = re.search(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


def _get_grok_api_key() -> str:
    """Return GROK_API_KEY or raise HTTPException if missing."""
    api_key = (os.getenv("GROK_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="GROK_API_KEY is not configured. Set it in your environment or .env.",
        )
    return api_key


async def _call_grok_raw(system_content: str, user_content: str) -> Any:
    """
    POST to Grok with given system + user messages; return parsed JSON from response content.
    Raises HTTPException on missing key, API errors, or invalid JSON.
    """
    api_key = _get_grok_api_key()
    payload = {
        "model": GROK_MODEL,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(GROK_URL, headers=headers, json=payload)
    except httpx.TimeoutException as e:
        logger.warning("Grok API timeout: %s", e)
        raise HTTPException(status_code=504, detail=f"Grok API timeout: {e!s}") from e
    except httpx.RequestError as e:
        logger.warning("Grok API request failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Grok API request failed: {e!s}") from e

    if response.is_error:
        try:
            err_body = response.json()
            msg = err_body.get("error", {}).get("message", response.text) or response.text
        except Exception:
            msg = response.text or f"HTTP {response.status_code}"
        logger.warning("Grok API error response: %s", msg)
        raise HTTPException(
            status_code=response.status_code if 400 <= response.status_code < 600 else 502,
            detail=f"Grok API error: {msg}",
        )

    try:
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise HTTPException(status_code=502, detail="Grok API returned no choices in response.")
        content = (choices[0].get("message") or {}).get("content") or ""
        if not content.strip():
            raise HTTPException(status_code=502, detail="Grok API returned empty content.")
    except (KeyError, IndexError, TypeError) as e:
        raise HTTPException(status_code=502, detail=f"Unexpected Grok response shape: {e!s}") from e

    try:
        return _extract_json_from_content(content)
    except json.JSONDecodeError as e:
        logger.warning("Grok returned invalid JSON: %s", e)
        raise HTTPException(status_code=502, detail=f"Grok returned invalid JSON: {e!s}") from e


async def generate_meal_plan(data: BiometricData) -> MealPlanResponse:
    """
    Call Grok API to generate a same-day meal plan + grocery list from biometrics.
    Raises HTTPException on missing API key, API errors, or invalid JSON/schema.
    """
    parsed = await _call_grok_raw(SYSTEM_PROMPT, _build_user_prompt(data))
    try:
        return MealPlanResponse.model_validate(parsed)
    except ValidationError as e:
        logger.warning("Grok daily response schema validation failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail=f"Grok response did not match MealPlanResponse schema: {e!s}",
        ) from e


async def generate_weekly_meal_plan(data: BiometricData, days: int = 7) -> WeeklyMealPlanResponse:
    """
    Call Grok API to generate a 5–7 day batch/meal-prep plan: scalable, storable, shared ingredients.
    Prompt emphasizes batch-friendly recipes, shelf-life, minimal waste, prep_notes on grocery list.
    """
    if days < 5 or days > 7:
        raise HTTPException(status_code=400, detail="weekly_days must be between 5 and 7")
    parsed = await _call_grok_raw(WEEKLY_SYSTEM_PROMPT, _build_weekly_user_prompt(data, days))
    # Normalize: Grok may omit prep_notes; Pydantic accepts None.
    if "grocery_list" in parsed and isinstance(parsed["grocery_list"], list):
        for g in parsed["grocery_list"]:
            if isinstance(g, dict) and "prep_notes" not in g:
                g["prep_notes"] = None
    try:
        return WeeklyMealPlanResponse.model_validate(parsed)
    except ValidationError as e:
        logger.warning("Grok weekly response schema validation failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail=f"Grok response did not match WeeklyMealPlanResponse schema: {e!s}",
        ) from e
