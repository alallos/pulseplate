# PulsePlate Beta Launch Checklist

## 1) Product Readiness Gates

- [ ] Core funnel works end-to-end on iPhone Safari and Android Chrome:
  - [ ] Connect Oura
  - [ ] Load biometrics
  - [ ] Generate daily plan
  - [ ] Generate weekly plan
  - [ ] Save and reopen from history
- [ ] Quick preference chips visibly affect generated plans.
- [ ] Meal feedback (thumbs up/down) persists across sessions/devices.
- [ ] Error and empty states provide a clear next action (retry/reconnect).

## 2) Reliability + Quality

- [ ] Add API tests for:
  - [ ] Meal feedback endpoints (`GET/PUT /meal-feedback`)
  - [ ] Plan generation overrides include `plan_preferences`
  - [ ] Support endpoint (`POST /support/report-issue`)
- [ ] Ensure no P0/P1 regressions for auth, generation, and history flows.
- [ ] Confirm service worker cache version bumped for each shell change.

## 3) Observability + Operations

- [ ] Sentry captures generation errors and auth failures in production.
- [ ] Add user-facing issue reporting from the app (for fast beta triage).
- [ ] Add structured server logging for support reports with request/user context.
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
- [ ] Add in-app feedback prompt after first successful plan generation.
- [ ] Create issue template for beta feedback collection.

## 6) Mobile App Track (Parallel)

- [ ] iOS first: Capacitor wrapper + TestFlight build.
- [ ] Deep link/OAuth callback validation in mobile shell.
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
