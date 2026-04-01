import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.auth import create_session_token
from app.models.biometrics import (
    BiometricData,
    DayPlan,
    GroceryItemWeekly,
    MealPlanResponse,
    WeeklyMealPlanResponse,
)


@pytest.fixture(autouse=True)
def _disable_slowapi_rate_limit():
    """
    slowapi uses an in-memory limiter keyed by client address.
    TestClient often reports the same "remote" identity, so we override the limiter
    key func to be unique per request and reset storage before each test.
    """
    limiter = getattr(main_module.app.state, "limiter", None)
    if limiter is None:
        return
    limiter.reset()
    limiter._key_func = lambda request: str(uuid.uuid4())

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


@pytest.fixture
def auth_headers(monkeypatch):
    """Generate a valid JWT for auth-required endpoints."""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    user_id = 123
    token = create_session_token(user_id)
    return {"Authorization": f"Bearer {token}"}


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
        response = c.post(
            "/generate-meal-plan",
            json=BIOMETRIC_PAYLOAD,
            headers={"X-Forwarded-For": "127.0.0.1"},
        )
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
            headers={"X-Forwarded-For": "127.0.0.2"},
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


def test_generate_meal_plan_daily_returns_503_when_grok_api_key_missing(client, monkeypatch):
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    response = client.post(
        "/generate-meal-plan",
        json=BIOMETRIC_PAYLOAD,
        headers={"X-Forwarded-For": "127.0.0.3"},
    )
    assert response.status_code == 503
    data = response.json()
    assert "GROK_API_KEY is not configured" in data.get("detail", "")


def test_generate_meal_plan_weekly_returns_503_when_grok_api_key_missing(client, monkeypatch):
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    response = client.post(
        "/generate-meal-plan",
        json=BIOMETRIC_PAYLOAD,
        params={"weekly_prep": True, "weekly_days": 7},
        headers={"X-Forwarded-For": "127.0.0.4"},
    )
    assert response.status_code == 503
    data = response.json()
    assert "GROK_API_KEY is not configured" in data.get("detail", "")


def test_generate_meal_plan_weekly_days_out_of_bounds_returns_422(client):
    # Endpoint query params enforce ge=5, le=7 at the FastAPI layer.
    response = client.post(
        "/generate-meal-plan",
        json=BIOMETRIC_PAYLOAD,
        params={"weekly_prep": True, "weekly_days": 4},
        headers={"X-Forwarded-For": "127.0.0.5"},
    )
    assert response.status_code == 422


@patch("app.services.meal_generator._call_grok_raw", new_callable=AsyncMock)
def test_generate_meal_plan_daily_invalid_grok_response_returns_502(mock_call, client):
    mock_call.return_value = {
        "summary": "Bad payload",
        "meals": "not-a-list",
        "grocery_list": [],
    }
    response = client.post(
        "/generate-meal-plan",
        json=BIOMETRIC_PAYLOAD,
        headers={"X-Forwarded-For": "127.0.0.6"},
    )
    assert response.status_code == 502
    data = response.json()
    assert data.get("detail", "").startswith("Grok response did not match MealPlanResponse schema:")


@patch("app.services.meal_generator._call_grok_raw", new_callable=AsyncMock)
def test_generate_meal_plan_weekly_invalid_grok_response_returns_502(mock_call, client):
    mock_call.return_value = {
        "summary": "Bad payload",
        "days": "not-a-list",
        "grocery_list": [],
    }
    response = client.post(
        "/generate-meal-plan",
        json=BIOMETRIC_PAYLOAD,
        params={"weekly_prep": True, "weekly_days": 7},
        headers={"X-Forwarded-For": "127.0.0.7"},
    )
    assert response.status_code == 502
    data = response.json()
    assert data.get("detail", "").startswith("Grok response did not match WeeklyMealPlanResponse schema:")


def _sample_biometric_data(**kwargs):
    base = dict(
        sleep_score=80,
        recovery_status="good",
        hrv_ms=50,
        resting_hr_bpm=60,
        steps_yesterday=10000,
        calorie_target=2000,
    )
    base.update(kwargs)
    return BiometricData(**base)


@patch("app.main.save_plan")
@patch("app.main.generate_meal_plan_service", new_callable=AsyncMock)
@patch("app.main.fetch_oura_biometrics", new_callable=AsyncMock)
@patch("app.main.get_valid_access_token", new_callable=AsyncMock)
def test_generate_meal_plan_from_oura_merges_plan_preferences_from_overrides(
    mock_token,
    mock_fetch,
    mock_gen,
    mock_save,
    auth_headers,
    client,
):
    """Overrides.plan_preferences is passed through to the generator merged payload."""
    mock_token.return_value = "oura-access"
    mock_fetch.return_value = _sample_biometric_data(plan_preferences=None)
    mock_gen.return_value = MealPlanResponse(
        summary="ok",
        meals=[
            {"type": "Breakfast", "name": "X", "description": "d", "calories": 1},
            {"type": "Lunch", "name": "Y", "description": "d", "calories": 1},
            {"type": "Dinner", "name": "Z", "description": "d", "calories": 1},
        ],
        grocery_list=[{"item": "a", "quantity": "1"}],
    )
    response = client.post(
        "/generate-meal-plan/from-oura",
        headers={**auth_headers, "X-Forwarded-For": "127.0.0.5"},
        json={"plan_preferences": ["high protein"]},
    )
    assert response.status_code == 200
    mock_gen.assert_called_once()
    passed = mock_gen.call_args[0][0]
    assert passed.plan_preferences == ["high protein"]
    mock_save.assert_called_once()


@patch("app.main.save_plan")
@patch("app.main.generate_meal_plan_service", new_callable=AsyncMock)
@patch("app.main.fetch_oura_biometrics", new_callable=AsyncMock)
@patch("app.main.get_valid_access_token", new_callable=AsyncMock)
def test_generate_meal_plan_from_oura_keeps_ring_plan_preferences_when_override_omits_them(
    mock_token,
    mock_fetch,
    mock_gen,
    mock_save,
    auth_headers,
    client,
):
    """When request body does not set plan_preferences, merged data keeps Oura snapshot values."""
    mock_token.return_value = "oura-access"
    mock_fetch.return_value = _sample_biometric_data(plan_preferences=["batch cook sunday"])
    mock_gen.return_value = MealPlanResponse(
        summary="ok",
        meals=[
            {"type": "Breakfast", "name": "X", "description": "d", "calories": 1},
            {"type": "Lunch", "name": "Y", "description": "d", "calories": 1},
            {"type": "Dinner", "name": "Z", "description": "d", "calories": 1},
        ],
        grocery_list=[{"item": "a", "quantity": "1"}],
    )
    response = client.post(
        "/generate-meal-plan/from-oura",
        headers={**auth_headers, "X-Forwarded-For": "127.0.0.6"},
        json={"diet_style": "vegan"},
    )
    assert response.status_code == 200
    mock_gen.assert_called_once()
    assert mock_gen.call_args[0][0].plan_preferences == ["batch cook sunday"]


@patch("app.main.save_plan")
@patch("app.main.generate_weekly_meal_plan", new_callable=AsyncMock)
@patch("app.main.fetch_oura_biometrics", new_callable=AsyncMock)
@patch("app.main.get_valid_access_token", new_callable=AsyncMock)
def test_generate_weekly_meal_plan_from_oura_merges_plan_preferences(
    mock_token,
    mock_fetch,
    mock_weekly,
    mock_save,
    auth_headers,
    client,
):
    mock_token.return_value = "oura-access"
    mock_fetch.return_value = _sample_biometric_data()
    mock_weekly.return_value = WeeklyMealPlanResponse(
        summary="Weekly ok",
        days=[
            DayPlan(
                day="Monday",
                meals=[
                    {"type": "Breakfast", "name": "Oats", "description": "d", "calories": 300},
                    {"type": "Lunch", "name": "Salad", "description": "d", "calories": 400},
                    {"type": "Dinner", "name": "Tofu", "description": "d", "calories": 500},
                ],
            ),
        ],
        grocery_list=[GroceryItemWeekly(item="Oats", quantity="2 cups", prep_notes=None)],
    )
    response = client.post(
        "/generate-meal-plan/from-oura",
        headers={**auth_headers, "X-Forwarded-For": "127.0.0.8"},
        params={"weekly_prep": True, "weekly_days": 5},
        json={"plan_preferences": ["minimal prep"]},
    )
    assert response.status_code == 200
    mock_weekly.assert_called_once()
    merged = mock_weekly.call_args.args[0]
    assert merged.plan_preferences == ["minimal prep"]
    assert mock_weekly.call_args.kwargs["days"] == 5
    mock_save.assert_called_once()


def test_preferences_requires_auth(client):
    response = client.get("/preferences")
    assert response.status_code == 401


@patch("app.main.get_user_preferences")
def test_get_preferences_returns_saved_prefs(mock_get_prefs, auth_headers, client):
    mock_get_prefs.return_value = {
        "goals": ["fat_loss"],
        "diet_style": "keto",
        "calorie_target": 1800,
        "allergies": ["nuts"],
        "measurement_system": "metric",
    }
    response = client.get("/preferences", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["measurement_system"] == "metric"
    mock_get_prefs.assert_called_once()


@patch("app.main.get_user_preferences")
@patch("app.main.set_user_preferences")
def test_update_preferences_saves_and_returns_prefs(
    mock_set_prefs,
    mock_get_prefs,
    auth_headers,
    client,
):
    mock_get_prefs.return_value = {
        "goals": ["muscle_gain"],
        "diet_style": "balanced",
        "calorie_target": 2100,
        "allergies": None,
        "measurement_system": "us",
    }
    payload = {
        "goals": ["muscle_gain"],
        "diet_style": "balanced",
        "calorie_target": 2100,
        "allergies": None,
        "measurement_system": "us",
    }
    response = client.put("/preferences", headers=auth_headers, json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["diet_style"] == "balanced"
    mock_set_prefs.assert_called_once()


def test_meal_feedback_requires_auth(client):
    response = client.get("/meal-feedback")
    assert response.status_code == 401


@patch("app.main.get_user_meal_feedback")
def test_get_meal_feedback_returns_saved_map(mock_get_feedback, auth_headers, client):
    mock_get_feedback.return_value = {"overnight oats": "up", "fried rice": "down"}
    response = client.get("/meal-feedback", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "feedback" in data
    assert data["feedback"]["overnight oats"] == "up"
    mock_get_feedback.assert_called_once()


@patch("app.main.get_user_meal_feedback")
@patch("app.main.set_user_meal_feedback")
def test_update_meal_feedback_saves_map(mock_set_feedback, mock_get_feedback, auth_headers, client):
    mock_get_feedback.return_value = {"oatmeal": "up"}
    payload = {"feedback": {"oatmeal": "up"}}
    response = client.put("/meal-feedback", headers=auth_headers, json=payload)
    assert response.status_code == 200
    assert response.json()["feedback"]["oatmeal"] == "up"
    mock_set_feedback.assert_called_once()


def test_update_meal_feedback_rejects_non_object(auth_headers, client):
    response = client.put("/meal-feedback", headers=auth_headers, json={"feedback": "bad"})
    assert response.status_code == 400
    assert response.json()["detail"] == "feedback must be an object"


def test_report_issue_requires_auth(client):
    response = client.post("/support/report-issue", json={"message": "something broke"})
    assert response.status_code == 401


def test_report_issue_requires_message(auth_headers, client):
    response = client.post("/support/report-issue", headers=auth_headers, json={"page": "/"})
    assert response.status_code == 400
    assert response.json()["detail"] == "message is required"


def test_report_issue_accepts_valid_payload(auth_headers, client):
    response = client.post(
        "/support/report-issue",
        headers=auth_headers,
        json={"message": "Generate button stuck on loading", "page": "/", "app_version": "web"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "received"
    assert isinstance(data.get("issue_id"), str)
    assert len(data["issue_id"]) == 8


def test_analytics_event_accepts_valid_payload(client):
    response = client.post("/analytics/events", json={"event": "plan_generate_started", "props": {"mode": "daily"}})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_analytics_event_requires_event_field(client):
    response = client.post("/analytics/events", json={"props": {}})
    assert response.status_code == 400
    assert response.json()["detail"] == "event is required"


def test_analytics_event_rejects_non_object_props(client):
    response = client.post("/analytics/events", json={"event": "x", "props": "bad"})
    assert response.status_code == 400
    assert response.json()["detail"] == "props must be an object"


def test_admin_beta_metrics_requires_auth(client):
    response = client.get("/admin/beta-metrics")
    assert response.status_code == 401


def test_admin_db_health_requires_auth(client):
    response = client.get("/admin/db-health")
    assert response.status_code == 401


def test_admin_db_health_repair_requires_auth(client):
    response = client.post("/admin/db-health/repair")
    assert response.status_code == 401


@patch("app.main.get_db_schema_health")
def test_admin_db_health_returns_schema_readiness(mock_health, auth_headers, client):
    mock_health.return_value = {
        "db_backend": "postgres",
        "ready": True,
        "required_tables": {"beta_analytics_events": True, "support_issue_reports": True},
        "missing_tables": [],
        "autofix_attempted": False,
        "autofix_changed": False,
        "checked_at": "2026-04-01T00:00:00Z",
    }
    response = client.get("/admin/db-health", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert data["required_tables"]["beta_analytics_events"] is True
    mock_health.assert_called_once_with(autofix=False)


@patch("app.main.init_db")
@patch("app.main.get_db_schema_health")
def test_admin_db_health_repair_runs_init_and_returns_readiness(mock_health, mock_init_db, auth_headers, client):
    mock_health.return_value = {
        "db_backend": "postgres",
        "ready": True,
        "required_tables": {"beta_analytics_events": True, "support_issue_reports": True},
        "missing_tables": [],
        "autofix_attempted": False,
        "autofix_changed": False,
        "checked_at": "2026-04-01T00:00:00Z",
    }
    response = client.post("/admin/db-health/repair", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["ready"] is True
    mock_init_db.assert_called_once()
    mock_health.assert_called_once_with(autofix=False)


def test_admin_beta_issues_export_requires_auth(client):
    response = client.get("/admin/beta-issues-export")
    assert response.status_code == 401


@patch("app.main.list_support_issue_reports")
def test_admin_beta_issues_export_returns_csv(mock_list, auth_headers, client):
    mock_list.return_value = [
        {
            "issue_id": "abc",
            "user_id": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "message": "hello, world",
            "page": "/",
            "app_version": "1",
            "triage_severity": "P1",
            "triage_status": "new",
            "triage_owner": "",
            "triaged_at": None,
            "resolved_at": None,
            "last_response_at": None,
            "response_note": "note",
        }
    ]
    response = client.get("/admin/beta-issues-export", headers=auth_headers)
    assert response.status_code == 200
    assert "text/csv" in (response.headers.get("content-type") or "").lower()
    disposition = response.headers.get("content-disposition") or ""
    assert "attachment" in disposition
    assert "pulseplate-beta-issues-" in disposition
    body = response.text
    assert "issue_id" in body
    assert "hello, world" in body
    mock_list.assert_called_once()


def test_admin_beta_page_requires_auth(client):
    response = client.get("/admin/beta")
    assert response.status_code == 401


def test_admin_beta_page_returns_html_when_authed(auth_headers, client):
    response = client.get("/admin/beta", headers=auth_headers)
    assert response.status_code == 200
    assert "text/html" in (response.headers.get("content-type") or "")
    assert "Beta Ops" in response.text


def test_admin_update_issue_triage_requires_auth(client):
    response = client.put("/admin/beta-issues/abc12345/triage", json={"severity": "P1", "status": "new"})
    assert response.status_code == 401


@patch("app.main.update_support_issue_triage")
def test_admin_update_issue_triage_accepts_valid_payload(mock_update, auth_headers, client):
    mock_update.return_value = True
    response = client.put(
        "/admin/beta-issues/abc12345/triage",
        headers=auth_headers,
        json={"severity": "P1", "status": "in_progress", "owner": "alex", "mark_responded": True, "response_note": "sent follow-up"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    mock_update.assert_called_once()


@patch("app.main.update_support_issue_triage")
def test_admin_update_issue_triage_rejects_invalid_field_types(mock_update, auth_headers, client):
    response = client.put(
        "/admin/beta-issues/abc12345/triage",
        headers=auth_headers,
        json={"severity": 1},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "severity must be a string"
    mock_update.assert_not_called()


@patch("app.main.update_support_issue_triage")
def test_admin_update_issue_triage_rejects_invalid_mark_responded_type(mock_update, auth_headers, client):
    response = client.put(
        "/admin/beta-issues/abc12345/triage",
        headers=auth_headers,
        json={"mark_responded": "yes"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "mark_responded must be a boolean"
    mock_update.assert_not_called()


@patch("app.main.update_support_issue_triage")
def test_admin_update_issue_triage_returns_404_when_missing(mock_update, auth_headers, client):
    mock_update.return_value = False
    response = client.put(
        "/admin/beta-issues/missing/triage",
        headers=auth_headers,
        json={"status": "resolved"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Issue not found"


@patch("app.main.get_beta_metrics_summary")
def test_admin_beta_metrics_returns_summary(mock_summary, auth_headers, client):
    mock_summary.return_value = {
        "events_24h": {"plan_generate_started": 3},
        "events_7d": {"plan_generate_started": 10},
        "recent_issue_reports": [],
        "triage_summary": {"unresolved_total": 1, "unresolved_p0": 0, "needs_reply_24h": 1},
    }
    response = client.get("/admin/beta-metrics", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "events_24h" in data
    assert data["events_7d"]["plan_generate_started"] == 10
    assert data["triage_summary"]["unresolved_total"] == 1
    assert data["triage_summary"]["needs_reply_24h"] == 1


@patch("app.main.init_db")
@patch("app.main.get_beta_metrics_summary")
def test_admin_beta_metrics_retries_after_init_db(mock_summary, mock_init_db, auth_headers, client):
    expected = {
        "events_24h": {},
        "events_7d": {},
        "recent_issue_reports": [],
        "triage_summary": {"unresolved_total": 0, "unresolved_p0": 0, "needs_reply_24h": 0},
    }
    mock_summary.side_effect = [Exception('relation "beta_analytics_events" does not exist'), expected]
    response = client.get("/admin/beta-metrics", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == expected
    assert mock_summary.call_count == 2
    mock_init_db.assert_called_once()


@patch("app.main.init_db")
@patch("app.main.get_beta_metrics_summary")
def test_admin_beta_metrics_returns_empty_payload_if_retry_still_fails(mock_summary, mock_init_db, auth_headers, client):
    mock_summary.side_effect = [Exception("first"), Exception("second")]
    response = client.get("/admin/beta-metrics", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {
        "events_24h": {},
        "events_7d": {},
        "recent_issue_reports": [],
        "triage_summary": {"unresolved_total": 0, "unresolved_p0": 0, "needs_reply_24h": 0},
    }
    assert mock_summary.call_count == 2
    mock_init_db.assert_called_once()


@patch("app.main.init_db")
@patch("app.main.list_support_issue_reports")
def test_admin_beta_issues_export_retries_after_init_db(mock_list, mock_init_db, auth_headers, client):
    mock_list.side_effect = [Exception('relation "support_issue_reports" does not exist'), []]
    response = client.get("/admin/beta-issues-export", headers=auth_headers)
    assert response.status_code == 200
    assert "text/csv" in (response.headers.get("content-type") or "").lower()
    assert mock_list.call_count == 2
    mock_init_db.assert_called_once()


@patch("app.main.init_db")
@patch("app.main.list_support_issue_reports")
def test_admin_beta_issues_export_returns_empty_csv_if_retry_still_fails(mock_list, mock_init_db, auth_headers, client):
    mock_list.side_effect = [Exception("first"), Exception("second")]
    response = client.get("/admin/beta-issues-export", headers=auth_headers)
    assert response.status_code == 200
    assert "text/csv" in (response.headers.get("content-type") or "").lower()
    assert "issue_id,user_id,created_at" in response.text
    assert mock_list.call_count == 2
    mock_init_db.assert_called_once()


@patch("app.main.get_plans")
def test_list_plans_returns_history(mock_get_plans, auth_headers, client):
    mock_get_plans.return_value = [
        {"id": 1, "generated_at": "2026-01-01T00:00:00Z"},
        {"id": 2, "generated_at": "2026-01-02T00:00:00Z"},
    ]
    response = client.get("/plans?limit=2&offset=0", headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json()) == 2
    mock_get_plans.assert_called_once()


@patch("app.main.get_plan_by_id")
def test_get_plan_returns_404_when_missing(mock_get_plan, auth_headers, client):
    mock_get_plan.return_value = None
    response = client.get("/plans/999", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "Plan not found"
    mock_get_plan.assert_called_once()


@patch("app.main.get_plan_by_id")
def test_get_plan_returns_plan_when_found(mock_get_plan, auth_headers, client):
    mock_get_plan.return_value = {"id": 1, "weekly_days": None, "is_weekly": False}
    response = client.get("/plans/1", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["id"] == 1
    mock_get_plan.assert_called_once()


@patch("app.main.clear_oura_tokens")
def test_disconnect_oura_redirects_and_clears_tokens(mock_clear_tokens, auth_headers, client):
    response = client.post("/auth/disconnect", headers=auth_headers, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("location") == "/?oura=disconnected"
    mock_clear_tokens.assert_called_once()
    assert "Set-Cookie" in response.headers or "set-cookie" in response.headers


@patch("app.main.delete_user_data")
def test_delete_account_redirects_and_deletes_data(mock_delete_user_data, auth_headers, client):
    response = client.post("/account/delete", headers=auth_headers, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("location") == "/?account=deleted"
    mock_delete_user_data.assert_called_once()
    assert "Set-Cookie" in response.headers or "set-cookie" in response.headers


@pytest.mark.parametrize(
    "path",
    [
        "/about",
        "/privacy",
        "/terms",
        "/manifest.webmanifest",
        "/service-worker.js",
        "/favicon.svg",
        "/icons/icon-192.png",
        "/icons/icon-512.png",
    ],
)
def test_public_pages_return_200(client, path):
    response = client.get(path)
    assert response.status_code == 200


def test_favicon_ico_redirects_to_svg(client):
    response = client.get("/favicon.ico", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("location") == "/favicon.svg"


def test_webhook_status_requires_auth(client):
    response = client.get("/webhooks/oura/status")
    assert response.status_code == 401


def test_webhook_events_requires_auth(client):
    response = client.get("/webhooks/oura/events?limit=5")
    assert response.status_code == 401


def test_webhook_subscriptions_requires_auth(client):
    response = client.get("/webhooks/oura/subscriptions")
    assert response.status_code == 401


@patch("app.main.list_oura_webhook_subscriptions", new_callable=AsyncMock)
def test_webhook_subscriptions_returns(mock_subs, auth_headers, client):
    mock_subs.return_value = [
        {
            "id": "sub-1",
            "callback_url": "https://example.com/webhooks/oura",
            "event_type": "update",
            "data_type": "daily_sleep",
            "expiration_time": "2026-06-18T19:11:11Z",
        }
    ]
    response = client.get("/webhooks/oura/subscriptions", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "subscriptions" in data
    assert len(data["subscriptions"]) == 1
    assert data["subscriptions"][0]["data_type"] == "daily_sleep"


@patch("app.main.get_oura_tokens")
@patch("app.main.get_latest_oura_webhook_event_for_user")
def test_webhook_status_returns_latest_event(mock_latest, mock_tokens, auth_headers, client):
    mock_tokens.return_value = {"access_token": "oura-access-token"}
    mock_latest.return_value = {"received_at": "2026-01-01T00:00:00Z", "event_type": "sleep"}
    response = client.get("/webhooks/oura/status", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["connected"] is True
    assert data["last_event_at"] == "2026-01-01T00:00:00Z"
    assert data["last_event_type"] == "sleep"


@patch("app.main.get_recent_oura_webhook_events_for_user")
def test_webhook_events_returns_recent(mock_recent, auth_headers, client):
    mock_recent.return_value = [
        {"event_type": "sleep", "received_at": "2026-01-01T00:00:00Z"},
        {"event_type": "readiness", "received_at": "2026-01-01T01:00:00Z"},
    ]
    response = client.get("/webhooks/oura/events?limit=5", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "events" in data
    assert len(data["events"]) == 2
    assert data["events"][0]["event_type"] == "sleep"


def test_webhook_debug_requires_auth(client):
    response = client.get("/webhooks/oura/debug")
    assert response.status_code == 401


@patch("app.main.get_user_oura_user_id")
@patch("app.main.get_recent_oura_webhook_events")
def test_webhook_debug_returns_stored_user_id_and_recent_events(
    mock_recent_all, mock_user_oura_id, auth_headers, client
):
    mock_user_oura_id.return_value = "oura-user-123"
    mock_recent_all.return_value = [
        {"oura_user_id": "oura-user-123", "event_type": "sleep", "received_at": "2026-01-01T00:00:00Z"},
        {"oura_user_id": None, "event_type": "readiness", "received_at": "2026-01-01T01:00:00Z"},
    ]
    response = client.get("/webhooks/oura/debug", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["connected_user_oura_user_id"] == "oura-user-123"
    assert len(data["recent_events_all_users"]) == 2
    assert "events_count_all_users" in data


@patch("app.main.save_oura_webhook_event")
def test_oura_webhook_accepts_json_and_persists(mock_save, client):
    payload = {"owner_id": "oura-user-123", "type": "sleep", "data": {"score": 80}}
    response = client.post("/webhooks/oura", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    mock_save.assert_called_once()


def test_oura_webhook_rejects_invalid_json(client):
    response = client.post(
        "/webhooks/oura",
        data="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_oura_webhook_verification_get_returns_challenge(client):
    response = client.get(
        "/webhooks/oura?verification_token=any-token&challenge=test-challenge"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["challenge"] == "test-challenge"

