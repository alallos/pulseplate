# Beta Go/No-Go Runbook

Use this before sending invites each day.

## Automated checks (in app)

Open `/admin/beta` and confirm:

- `DB health: ready`
- `Go / No-Go (7d): Ready`
- `Unresolved P0: 0`
- `Needs reply (24h): 0`

If DB health is not ready, click **Repair DB schema** and refresh.

## Manual checks (required)

These still require human validation before broad invites:

1. Mobile E2E smoke on real devices
   - iPhone Safari: connect Oura, load biometrics, generate daily, generate weekly, reopen from history
   - Android Chrome: same flow
2. Confirm quick preference chips visibly change generated output.
3. Confirm meal feedback persists across sessions/devices for one tester account pair.
4. Validate account delete flow in production from UI.
5. Verify privacy/terms wording still matches current behavior after latest deploys.

## Invite decision

- **GO**: all automated checks pass and manual checks pass.
- **HOLD**: any P0 unresolved, DB health not ready, or manual check fails.

## Invite execution

- Use `docs/beta-ready-to-send-messages.md`.
- Start with 20-40 testers.
- Keep daily triage loop active (`docs/beta-triage-routine.md`).
