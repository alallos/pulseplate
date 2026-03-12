import os

import pytest
from fastapi.testclient import TestClient

from app.main import app


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

