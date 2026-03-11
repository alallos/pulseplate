"""Meal plan generator via Grok API (xAI). Produces same-day plan + grocery list from biometrics."""

import json
import os
import re
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.biometrics import BiometricData, MealPlanResponse

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
- grocery_list: Every ingredient needed for the day, each with "item" and "quantity" (e.g. "200g", "2 medium", "as needed"). No duplicates.
- Respect allergies strictly (e.g. if nuts: zero tree nuts or peanuts).
- Total meal calories should be close to the user's calorie_target.
- Diet style (mediterranean, keto, etc.) and goals (fat_loss, stable_glucose, etc.) must shape the plan."""


def _build_user_prompt(data: BiometricData) -> str:
    """Build user message with biometrics and preferences for Grok."""
    return (
        "Generate today's meal plan and grocery list for this user. "
        "Return ONLY the JSON object, no other text.\n\n"
        "Biometrics and preferences:\n"
        f"{data.model_dump_json(indent=2)}"
    )


def _extract_json_from_content(content: str) -> Any:
    """Strip optional markdown code block and parse JSON."""
    text = content.strip()
    # Remove ```json ... ``` or ``` ... ``` if present
    match = re.search(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


async def generate_meal_plan(data: BiometricData) -> MealPlanResponse:
    """
    Call Grok API to generate a same-day meal plan + grocery list from biometrics.
    Raises HTTPException on missing API key, API errors, or invalid JSON/schema.
    """
    api_key = os.getenv("GROK_API_KEY")
    if not api_key or not api_key.strip():
        raise HTTPException(
            status_code=503,
            detail="GROK_API_KEY is not configured. Set it in your environment or .env.",
        )

    payload = {
        "model": GROK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(data)},
        ],
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(GROK_URL, headers=headers, json=payload)
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail=f"Grok API timeout: {e!s}") from e
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Grok API request failed: {e!s}",
        ) from e

    if response.is_error:
        try:
            err_body = response.json()
            msg = err_body.get("error", {}).get("message", response.text) or response.text
        except Exception:
            msg = response.text or f"HTTP {response.status_code}"
        raise HTTPException(
            status_code=response.status_code if 400 <= response.status_code < 600 else 502,
            detail=f"Grok API error: {msg}",
        )

    try:
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise HTTPException(
                status_code=502,
                detail="Grok API returned no choices in response.",
            )
        content = (choices[0].get("message") or {}).get("content") or ""
        if not content.strip():
            raise HTTPException(
                status_code=502,
                detail="Grok API returned empty content.",
            )
    except (KeyError, IndexError, TypeError) as e:
        raise HTTPException(
            status_code=502,
            detail=f"Unexpected Grok response shape: {e!s}",
        ) from e

    try:
        parsed = _extract_json_from_content(content)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Grok returned invalid JSON: {e!s}",
        ) from e

    try:
        return MealPlanResponse.model_validate(parsed)
    except ValidationError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Grok response did not match MealPlanResponse schema: {e!s}",
        ) from e
