# Beta Triage Routine

Use this daily routine during closed beta to keep blockers low and response times tight.

## Daily cadence (15-20 min)

1. Open `/admin/beta`.
2. Refresh metrics and confirm `DB health` is ready.
3. Review unresolved issues (P0-first sort enabled).
4. For each new issue:
   - set severity (`P0`, `P1`, `P2`)
   - set status (`new`, `in_progress`, `resolved`)
   - assign owner
5. Respond to user-facing reports and click **Mark responded**.
6. Export CSV for weekly share-out if needed.

## Severity policy

- `P0`: broken core flow (connect, generate, reopen history), data-loss risk, or repeated hard failures.
- `P1`: degraded core experience or confusing behavior with workaround.
- `P2`: polish, copy, or low-frequency issues.

## SLA target

- First response to beta issue reports: **within 24 hours**.
- Target in dashboard: `Needs reply (24h) = 0`.

## Escalation rules

- If `unresolved P0 > 0`, pause new invites.
- If Go/No-Go status is `Not ready`, prioritize fixes before expanding cohort.

## Weekly review

- Review exported issue CSV + Sentry.
- Summarize top themes, fixed items, and remaining blockers.
- Decide invite volume for next week (hold / same / expand).
