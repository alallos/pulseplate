# PulsePlate

**Your pulse. Your plate. AI-optimized.**

Hyper-personalized daily meal architect powered by biometrics.

- Pulls sleep score, recovery, HRV, RHR, etc. from wearables (Oura first)
- Uses AI (Grok API) to generate same-day meal plans (3 meals + 1-2 snacks) tailored to today's recovery/metabolic state
- Outputs: detailed meals, grocery list with quantities, future one-click delivery drafts

**Core philosophy**: Zero decision fatigue — biometrics → actionable food plans automatically.

## Tech Stack (early phase)
- Backend: Python 3.11+ / FastAPI
- LLM: Grok API (xAI)
- Database: SQLite to start
- Future: Oura API v2, PostgreSQL, mobile frontend

## Quick Start (when on desktop)
```bash
git clone https://github.com/YOUR_USERNAME/pulseplate.git
cd pulseplate
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env       # then fill in GROK_API_KEY etc.
uvicorn app.main:app --reload --port 8000
