"""Oura API v2 client: fetch biometric data and map to BiometricData."""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import HTTPException

from app.models.biometrics import BiometricData

OURA_API_BASE = "https://api.ouraring.com"
log = logging.getLogger(__name__)


def _oura_error_message(resp: httpx.Response, label: str) -> str:
    """Build a short, user-safe error string (never dump upstream HTML pages)."""
    code = resp.status_code
    raw = (resp.text or "").strip()
    low = raw[:800].lower()
    if "<html" in low or "<!doctype" in low:
        return (
            f"Oura {label} returned HTTP {code} (service temporarily unavailable). "
            "Please try again in a few minutes."
        )
    if code in (502, 503, 504):
        return f"Oura {label} is temporarily unavailable (HTTP {code}). Please try again shortly."
    try:
        err = resp.json()
        msg = err.get("detail") or err.get("message")
        if isinstance(msg, str) and msg.strip():
            return f"Oura {label}: {msg.strip()[:400]}"
    except Exception:
        pass
    snippet = raw.replace("\n", " ").strip()
    if len(snippet) > 200:
        snippet = snippet[:200] + "…"
    return f"Oura {label}: {snippet or f'HTTP {code}'}"


def _status_for_oura_upstream(code: int) -> int:
    """Map Oura/nginx outages to a stable client status."""
    if code in (502, 503, 504):
        return 503
    if 400 <= code < 600:
        return code
    return 502


async def fetch_oura_personal_info(access_token: str) -> dict[str, Any]:
    """
    Fetch Oura personal info (email, id) for the authenticated user.
    Requires 'email' scope. Used after token exchange to resolve or create app user.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{OURA_API_BASE}/v2/usercollection/personal_info",
            headers=headers,
        )
    if response.is_error:
        log.warning(
            "Oura personal_info error status=%s body_prefix=%s",
            response.status_code,
            (response.text or "")[:120].replace("\n", " "),
        )
        raise HTTPException(
            status_code=_status_for_oura_upstream(response.status_code),
            detail=_oura_error_message(response, "personal_info"),
        )
    try:
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Oura personal_info invalid JSON: {e!s}") from e


# Map readiness score (0–100) to recovery_status
def _score_to_recovery(score: int | float | None) -> str:
    if score is None:
        return "fair"
    s = int(score)
    if s >= 85:
        return "optimal"
    if s >= 70:
        return "good"
    if s >= 55:
        return "fair"
    return "low"


async def fetch_oura_biometrics(access_token: str) -> BiometricData:
    """
    Fetch latest Oura data (daily_readiness, daily_sleep, daily_activity, sleep for HR/HRV)
    and map to BiometricData. Uses defaults for fields Oura does not provide (goals, diet, etc.).
    """
    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=7)).isoformat()
    end = today.isoformat()
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Fetch in parallel
        readiness_resp = client.get(
            f"{OURA_API_BASE}/v2/usercollection/daily_readiness",
            headers=headers,
            params={"start_date": start, "end_date": end},
        )
        sleep_resp = client.get(
            f"{OURA_API_BASE}/v2/usercollection/daily_sleep",
            headers=headers,
            params={"start_date": start, "end_date": end},
        )
        activity_resp = client.get(
            f"{OURA_API_BASE}/v2/usercollection/daily_activity",
            headers=headers,
            params={"start_date": start, "end_date": end},
        )
        sleep_detail_resp = client.get(
            f"{OURA_API_BASE}/v2/usercollection/sleep",
            headers=headers,
            params={"start_date": start, "end_date": end},
        )

        results = await asyncio.gather(readiness_resp, sleep_resp, activity_resp, sleep_detail_resp)

    readiness_r, sleep_r, activity_r, sleep_detail_r = results

    def check(resp: httpx.Response, name: str) -> dict[str, Any]:
        if resp.is_error:
            log.warning(
                "Oura %s error status=%s body_prefix=%s",
                name,
                resp.status_code,
                (resp.text or "")[:120].replace("\n", " "),
            )
            raise HTTPException(
                status_code=_status_for_oura_upstream(resp.status_code),
                detail=_oura_error_message(resp, name),
            )
        try:
            return resp.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Oura {name} invalid JSON: {e!s}") from e

    readiness = check(readiness_r, "daily_readiness")
    sleep_summary = check(sleep_r, "daily_sleep")
    activity = check(activity_r, "daily_activity")
    sleep_detail = check(sleep_detail_r, "sleep")

    # Most recent readiness -> recovery, plus 7-day trends
    readiness_list = readiness.get("data") or []
    readiness_list.sort(key=lambda x: x.get("day") or "", reverse=True)
    readiness_score: int | float | None = None
    resting_hr_from_readiness: float | None = None
    if readiness_list:
        latest = readiness_list[0]
        readiness_score = latest.get("score")
        contrib = latest.get("contributors") or {}
        rhr = contrib.get("resting_heart_rate")
        if rhr is not None:
            resting_hr_from_readiness = float(rhr)

    # Most recent sleep score
    sleep_list = sleep_summary.get("data") or []
    sleep_list.sort(key=lambda x: x.get("day") or "", reverse=True)
    sleep_score: float = 70.0
    if sleep_list:
        s = sleep_list[0].get("score")
        if s is not None:
            sleep_score = float(s)

    # Yesterday's steps (or latest activity day before today)
    activity_list = activity.get("data") or []
    activity_list.sort(key=lambda x: x.get("day") or "", reverse=True)
    steps_yesterday: int = 0
    for item in activity_list:
        day = item.get("day")
        if day and day != today.isoformat():
            steps_yesterday = int(item.get("steps") or 0)
            break

    # HRV and resting HR from detailed sleep (most recent night)
    sleep_detail_list = sleep_detail.get("data") or []
    sleep_detail_list.sort(key=lambda x: (x.get("day") or "", x.get("bedtime_start") or ""), reverse=True)
    hrv_ms: float = 40.0
    resting_hr_bpm: float = 60.0
    for item in sleep_detail_list:
        if item.get("type") == "deleted":
            continue
        hrv = item.get("average_hrv")
        if hrv is not None:
            hrv_ms = float(hrv)
        hr = item.get("average_heart_rate") or item.get("lowest_heart_rate")
        if hr is not None:
            resting_hr_bpm = float(hr)
        break
    if resting_hr_from_readiness is not None:
        resting_hr_bpm = resting_hr_from_readiness

    # Weekly aggregates for context (used especially for weekly plans)
    def _avg(items: list[dict[str, Any]], key: str) -> float | None:
        vals = [v for v in (it.get(key) for it in items) if v is not None]
        if not vals:
            return None
        return float(sum(vals) / len(vals))

    avg_readiness = _avg(readiness_list, "score")
    avg_sleep = _avg(sleep_list, "score")
    avg_steps = _avg(activity_list, "steps")

    readiness_trend = "flat"
    if len(readiness_list) >= 2:
        latest_r = readiness_list[0].get("score")
        oldest_r = readiness_list[-1].get("score")
        try:
            if latest_r is not None and oldest_r is not None:
                diff = float(latest_r) - float(oldest_r)
                if diff >= 5:
                    readiness_trend = "up"
                elif diff <= -5:
                    readiness_trend = "down"
        except (TypeError, ValueError):
            readiness_trend = "flat"

    weekly_parts: list[str] = []
    if avg_readiness is not None:
        weekly_parts.append(f"avg_readiness={avg_readiness:.1f}")
        weekly_parts.append(f"recovery_trend={readiness_trend}")
    if avg_sleep is not None:
        weekly_parts.append(f"avg_sleep_score={avg_sleep:.1f}")
    if avg_steps is not None:
        weekly_parts.append(f"avg_steps_per_day={int(avg_steps)}")
    weekly_summary = ""
    if weekly_parts:
        weekly_summary = "Last 7 days: " + ", ".join(weekly_parts)

    recovery_status = _score_to_recovery(readiness_score)

    return BiometricData(
        sleep_score=sleep_score,
        recovery_status=recovery_status,
        hrv_ms=hrv_ms,
        resting_hr_bpm=resting_hr_bpm,
        steps_yesterday=steps_yesterday,
        goals=[],
        diet_style="balanced",
        calorie_target=2000,
        allergies=None,
        weekly_summary=weekly_summary or None,
    )


async def list_oura_webhook_subscriptions() -> list[dict[str, Any]]:
    """
    List active Oura webhook subscriptions for the configured Oura app.

    Note: the Oura webhook subscription endpoints use header-based auth:
    `x-client-id` and `x-client-secret` (not the user's OAuth Bearer token).
    """
    client_id = (os.getenv("OURA_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("OURA_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="OURA_CLIENT_ID and OURA_CLIENT_SECRET must be configured to list webhook subscriptions.",
        )

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{OURA_API_BASE}/v2/webhook/subscription",
            headers={"x-client-id": client_id, "x-client-secret": client_secret},
        )

    if resp.is_error:
        log.warning(
            "Oura webhook subscription list error status=%s body_prefix=%s",
            resp.status_code,
            (resp.text or "")[:120].replace("\n", " "),
        )
        raise HTTPException(
            status_code=_status_for_oura_upstream(resp.status_code),
            detail=_oura_error_message(resp, "webhook subscription list"),
        )

    try:
        data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Oura webhook subscription list invalid JSON: {e!s}") from e

    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data["data"]

    raise HTTPException(status_code=502, detail="Oura webhook subscription list returned unexpected payload shape.")
