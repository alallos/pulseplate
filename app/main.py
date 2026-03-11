"""PulsePlate - FastAPI backend

Hyper-personalized daily meal architect powered by your biometrics.
Turns Oura/Apple Watch data into zero-decision meal plans + grocery lists.
"""

from fastapi import FastAPI
from dotenv import load_dotenv

# Load environment variables early (even if .env is empty for now)
load_dotenv()

app = FastAPI(
    title="PulsePlate",
    description=(
        "Your biometrics-powered daily meal architect. "
        "Zero decisions — just optimized plates from today's recovery data."
    ),
    version="0.1.0-dev",
    docs_url="/docs",    # Swagger UI
    redoc_url="/redoc",
)


@app.get("/")
async def root():
    """Basic health check / welcome endpoint."""
    return {
        "message": "Welcome to PulsePlate! Your pulse → perfect plate.",
        "status": "alive",
        "docs": "Visit /docs for interactive API documentation (Swagger UI)",
    }


if __name__ == "__main__":
    # For quick local runs: python app/main.py
    # Recommended: use uvicorn in terminal for dev
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # auto-reload on code changes in dev
    )
