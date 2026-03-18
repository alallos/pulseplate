import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

# Minimal valid payload for POST /generate-meal-plan
BIOMETRIC_PAYLOAD = {
    "sleep_score": 78,
    "recovery_status": "fair",
    "hrv_ms": 45,
    "resting_hr_bpm": 62,
    "steps_yesterday": 8500,
    "calorie_target": 2000,
}


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint_always_alive(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "alive"


def test_health_ready_returns_503_when_grok_api_key_missing(client, monkeypatch):
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    response = client.get("/health/ready")
    assert response.status_code == 503
    data = response.json()
    assert data.get("detail") == "GROK_API_KEY not configured"


def test_health_ready_returns_200_when_grok_api_key_present(client, monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-key")
    response = client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ready"


def test_protected_biometrics_route_requires_auth(client, monkeypatch):
    # Ensure auth-related environment variables are not forcing unexpected behavior
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)

    response = client.get("/biometrics/oura")
    assert response.status_code == 401


@patch("app.main.generate_meal_plan_service", new_callable=AsyncMock)
def test_generate_meal_plan_daily_returns_plan(mock_daily):
    """POST /generate-meal-plan (daily) returns 200 and plan structure when Grok is mocked."""
    mock_daily.return_value = {
        "summary": "Test summary for today.",
        "meals": [
            {"type": "Breakfast", "name": "Oatmeal", "description": "Steel-cut oats.", "calories": 350},
            {"type": "Lunch", "name": "Salad", "description": "Green salad.", "calories": 450},
            {"type": "Dinner", "name": "Grilled chicken", "description": "With veggies.", "calories": 550},
        ],
        "grocery_list": [{"item": "Oats", "quantity": "1 cup"}, {"item": "Chicken", "quantity": "6 oz"}],
    }
    with TestClient(app) as c:
        response = c.post("/generate-meal-plan", json=BIOMETRIC_PAYLOAD)
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert data["summary"] == "Test summary for today."
    assert "meals" in data
    assert len(data["meals"]) == 3
    assert "grocery_list" in data
    assert len(data["grocery_list"]) == 2
    mock_daily.assert_called_once()


@patch("app.main.generate_weekly_meal_plan", new_callable=AsyncMock)
def test_generate_meal_plan_weekly_returns_plan(mock_weekly):
    """POST /generate-meal-plan?weekly_prep=true returns 200 and weekly structure when Grok is mocked."""
    mock_weekly.return_value = {
        "summary": "Weekly batch plan for recovery.",
        "days": [
            {
                "day": "Monday",
                "meals": [
                    {"type": "Breakfast", "name": "Overnight oats", "description": "Base batch.", "calories": 300},
                    {"type": "Lunch", "name": "Soup", "description": "Lentil.", "calories": 400},
                    {"type": "Dinner", "name": "Chicken", "description": "Grilled.", "calories": 500},
                ],
            },
        ],
        "grocery_list": [
            {"item": "Rolled oats", "quantity": "2 cups", "prep_notes": "Batch Sunday"},
            {"item": "Chicken breast", "quantity": "1.5 lb", "prep_notes": None},
        ],
    }
    with TestClient(app) as c:
        response = c.post(
            "/generate-meal-plan",
            json=BIOMETRIC_PAYLOAD,
            params={"weekly_prep": True, "weekly_days": 7},
        )
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "days" in data
    assert len(data["days"]) == 1
    assert data["days"][0]["day"] == "Monday"
    assert "grocery_list" in data
    assert len(data["grocery_list"]) >= 1
    mock_weekly.assert_called_once()

