# PulsePlate

**Your pulse → perfect plate.** Hyper-personalized meal plans from your biometrics.

- Connects to **Oura Ring** (OAuth2) for sleep, recovery, HRV, RHR, steps
- Uses **Grok (xAI)** to generate daily or weekly batch meal plans + grocery lists
- Saves preferences and plan history; installable as a PWA on mobile

## Tech stack

- **Backend:** Python 3.11+ / FastAPI  
- **LLM:** Grok API (xAI)  
- **Database:** PostgreSQL when `DATABASE_URL` is set (e.g. Railway); SQLite otherwise for local dev  
- **Auth:** JWT session after Oura connect; optional Sentry, rate limiting

## Run locally

1. **Clone and enter the repo**
   ```bash
   git clone https://github.com/YOUR_USERNAME/pulseplate.git
   cd pulseplate
   ```

2. **Create a virtualenv and install deps**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   # source .venv/bin/activate   # macOS/Linux
   pip install -r requirements.txt
   ```

3. **Configure environment**
   - Copy `.env.example` to `.env` and fill in your values (see below).

4. **Start the app**
   ```bash
   python -m uvicorn app.main:app --reload --port 8000
   ```
   Open http://localhost:8000

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROK_API_KEY` | Yes | xAI API key for meal plan generation |
| `OURA_CLIENT_ID` | Yes (for Oura) | Oura app client ID |
| `OURA_CLIENT_SECRET` | Yes (for Oura) | Oura app client secret |
| `OURA_REDIRECT_URI` | Yes (for Oura) | Callback URL, e.g. `http://localhost:8000/auth/oura/callback` (local) or your Railway URL + `/auth/oura/callback` |
| `SECRET_KEY` | Yes (for auth) | Secret for JWT and OAuth state signing; use a long random string |
| `DATABASE_URL` | No | PostgreSQL URL (e.g. from Railway); if unset, SQLite is used |
| `SENTRY_DSN` | No | Sentry DSN for error monitoring |

See `.env.example` for a full list.

## Getting API keys

- **Grok:** [xAI API](https://console.x.ai/) — create an API key.  
- **Oura:** [Oura Developer Portal](https://cloud.ouraring.com/) — create an app, set redirect URI(s), and request scopes (e.g. `email`, `personal`, `daily`, `heartrate`).

## Deploy to Railway

1. Create a project and connect your GitHub repo.  
2. Add a **Postgres** plugin in Railway; it sets `DATABASE_URL` automatically.  
3. Set env vars in the service (same as above; use your Railway URL for `OURA_REDIRECT_URI`).  
4. Add a **Procfile** (already in repo): `web: python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT`  
5. In the Oura Developer Portal, add your Railway callback URL to redirect URIs.  
6. Deploy; tokens and plan history persist in Postgres.

## Tests

```bash
python -m pytest
```

## License

Use and extend as you like.
