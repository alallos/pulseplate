# PulsePlate Beta Launch Checklist

## 1) Product Readiness Gates

- [ ] Core funnel works end-to-end on iPhone Safari and Android Chrome (manual QA):
  - [ ] Connect Oura
  - [ ] Load biometrics
  - [ ] Generate daily plan
  - [ ] Generate weekly plan
  - [ ] Save and reopen from history
- [x] Mobile web shell hardening in-repo: `viewport-fit=cover`, safe-area padding, `100dvh`, 16px inputs (iOS zoom), 44px touch targets, tap highlight, service worker `v8`.
- [ ] Quick preference chips visibly affect generated plans.
- [ ] Meal feedback (thumbs up/down) persists across sessions/devices.
- [ ] Error and empty states provide a clear next action (retry/reconnect).

## 2) Reliability + Quality

- [ ] Add API tests for:
  - [x] Meal feedback endpoints (`GET/PUT /meal-feedback`)
  - [x] Plan generation overrides include `plan_preferences` (`/generate-meal-plan/from-oura`)
  - [x] Support endpoint (`POST /support/report-issue`)
- [ ] Ensure no P0/P1 regressions for auth, generation, and history flows.
- [x] Service worker cache bumped for this shell pass (`pulseplate-shell-v8`).

## 3) Observability + Operations

- [x] Sentry: `Starlette` + `FastAPI` + logging integrations; explicit capture on Grok/meal-gen failures and Oura token-exchange exceptions when `SENTRY_DSN` is set.
- [x] Add user-facing issue reporting from the app (for fast beta triage).
- [x] Structured server logging for support reports with request/user context.
- [ ] Create a lightweight triage routine:
  - [ ] Daily review of errors/support reports
  - [ ] Severity tagging (P0/P1/P2)
  - [ ] SLA target for beta replies (e.g., < 24h)

## 4) Legal + Privacy

- [ ] Verify Privacy/Terms reflect:
  - [ ] Oura token usage
  - [ ] Stored preferences/plans/feedback
  - [ ] Data deletion workflow
- [ ] Validate account delete path from UI in production.

## 5) Beta Program Setup

- [ ] Define beta segment (20-40 users to start).
- [ ] Prepare invite + onboarding copy.
- [x] Add in-app feedback prompt after first successful plan generation.
- [x] Create issue template for beta feedback collection (`.github/ISSUE_TEMPLATE/beta-feedback.yml`).

## 6) Mobile App Track (Parallel)

- [x] iOS first — Capacitor scaffold in repo: `package.json`, `capacitor.config.json` (`webDir`: `app/static`). **On macOS:** `npm install` → `npx cap add ios` → add `"server": { "url": "https://YOUR_PRODUCTION_HOST" }` to `capacitor.config.json` (required: the SPA calls same-origin `/` APIs and Oura redirect must match your live host) → `npx cap sync ios` → open `ios/App` in Xcode → Archive → TestFlight.
- [ ] Deep link/OAuth callback validation in mobile shell (run after `server.url` points at prod; Oura callback stays `https://YOUR_HOST/auth/oura/callback`).
- [ ] Native share handoff checks (grocery/full plan).
- [ ] Android wrapper follow-up after iOS baseline is stable.

## 7) Go/No-Go Metrics (7 day window)

- [ ] Plan generation success rate >= 95%.
- [ ] Oura connect success rate >= 90%.
- [ ] Median open-to-plan latency <= 10s on mobile.
- [ ] Zero unresolved P0 issues.
- [ ] Support report handling loop active and monitored.

## Implementation Start (current sprint)

- [x] Add beta launch checklist.
- [x] Add in-app report issue flow (UI + API + tests).
- [x] Add API tests for meal feedback endpoints.
- [x] Add basic analytics events for key funnel steps.
- [x] Sentry hardening + meal-gen/OAuth capture helpers (`app/observability.py`).
- [x] Mobile PWA layout pass + SW `v8`; Capacitor iOS npm scaffold.
