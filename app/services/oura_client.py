"""Oura API v2 client: fetch biometric data and map to BiometricData."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import HTTPException

from app.models.biometrics import BiometricData

OURA_API_BASE = "https://api.ouraring.com"

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
            try:
                err = resp.json()
                msg = err.get("detail") or err.get("message") or resp.text
            except Exception:
                msg = resp.text or f"HTTP {resp.status_code}"
            raise HTTPException(
                status_code=resp.status_code if 400 <= resp.status_code < 600 else 502,
                detail=f"Oura {name}: {msg}",
            )
        try:
            return resp.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Oura {name} invalid JSON: {e!s}") from e

    readiness = check(readiness_r, "daily_readiness")
    sleep_summary = check(sleep_r, "daily_sleep")
    activity = check(activity_r, "daily_activity")
    sleep_detail = check(sleep_detail_r, "sleep")

    # Most recent readiness -> recovery
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
    )
