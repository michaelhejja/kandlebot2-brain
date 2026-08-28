"""Runtime configuration, sourced from environment variables."""
import os


class Config:
    # Path to the trained model artifact (joblib pipeline or pickle file). If missing, the
    # service falls back to a simple rule-based heuristic so the endpoint
    # still works before a model has been trained.
    # Supports: .joblib (joblib format) or .pkl (pickle format)
    MODEL_PATH = os.environ.get("MODEL_PATH", "models/trained_model.pkl")

    # Minimum predicted probability of "good signal" required to accept it.
    DECISION_THRESHOLD = float(os.environ.get("DECISION_THRESHOLD", "0.5"))

    # Optional shared-secret auth for the Node.js caller. Set this in
    # production (e.g. `heroku config:set BRAIN_API_KEY=...`) so the
    # endpoint isn't wide open on the public internet. If unset, auth is
    # skipped (convenient for local development only).
    API_KEY = os.environ.get("BRAIN_API_KEY")

    # Path to SQLite database for storing historical candles
    DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/candles.db")

    # Node server URL for gap recovery (e.g., http://localhost:3000)
    NODE_SERVER_URL = os.environ.get("NODE_SERVER_URL")

    # CCXT exchange name for fallback gap recovery (e.g., 'binance')
    EXCHANGE_NAME = os.environ.get("EXCHANGE_NAME", "binance")
