import pytest
import tempfile
import shutil
from pathlib import Path

from brain_app import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Ensure the heuristic fallback path is exercised (no trained model on disk).
    monkeypatch.setenv("MODEL_PATH", "models/does_not_exist.joblib")
    
    # Use a fresh temporary database for each test
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is False


def test_sync_candle_success(client):
    """Should accept and store a valid candle."""
    payload = {
        "symbol": "ETH",
        "timestamp": "2026-08-26T14:30:00Z",
        "open": 2500.0,
        "high": 2502.0,
        "low": 2499.0,
        "close": 2501.0,
        "volume": 1000.0,
        "indicators": {
            "ema_20": 2500.5,
            "ema_50": 2499.0,
            "ema_200": 2498.0,
            "crsi": 35.0,
            "adx": 25.0,
            "atr": 10.0,
            "mfi": 40.0,
        },
    }
    resp = client.post("/sync", json=payload)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["symbol"] == "ETH"
    assert body["candle_stored"] is True


def test_sync_candle_minimal(client):
    """Should accept candle with minimal fields (no indicators)."""
    payload = {
        "symbol": "BTC",
        "timestamp": "2026-08-26T14:31:00Z",
        "open": 60000.0,
        "high": 60100.0,
        "low": 59900.0,
        "close": 60050.0,
        "volume": 500.0,
    }
    resp = client.post("/sync", json=payload)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["candle_stored"] is True


def test_sync_candle_missing_ohlcv_field(client):
    """Should reject candle missing OHLCV fields."""
    payload = {
        "symbol": "ETH",
        "timestamp": "2026-08-26T14:32:00Z",
        "open": 2500.0,
        "high": 2502.0,
        # Missing low, close, volume
    }
    resp = client.post("/sync", json=payload)
    assert resp.status_code == 400


def test_sync_candle_missing_symbol(client):
    """Should reject candle missing symbol."""
    payload = {
        "timestamp": "2026-08-26T14:33:00Z",
        "open": 2500.0,
        "high": 2502.0,
        "low": 2499.0,
        "close": 2501.0,
        "volume": 1000.0,
    }
    resp = client.post("/sync", json=payload)
    assert resp.status_code == 400


def test_sync_candle_invalid_timestamp(client):
    """Should reject candle with invalid timestamp."""
    payload = {
        "symbol": "ETH",
        "timestamp": "not-a-valid-timestamp",
        "open": 2500.0,
        "high": 2502.0,
        "low": 2499.0,
        "close": 2501.0,
        "volume": 1000.0,
    }
    resp = client.post("/sync", json=payload)
    assert resp.status_code == 400


def test_sync_candle_invalid_price(client):
    """Should reject candle with non-numeric price."""
    payload = {
        "symbol": "ETH",
        "timestamp": "2026-08-26T14:34:00Z",
        "open": "not-a-number",
        "high": 2502.0,
        "low": 2499.0,
        "close": 2501.0,
        "volume": 1000.0,
    }
    resp = client.post("/sync", json=payload)
    assert resp.status_code == 400


def test_sync_candle_iso_format_variations(client):
    """Should accept various ISO 8601 timestamp formats."""
    # Test with 'Z' suffix
    payload1 = {
        "symbol": "ETH",
        "timestamp": "2026-08-26T14:35:00Z",
        "open": 2500.0,
        "high": 2502.0,
        "low": 2499.0,
        "close": 2501.0,
        "volume": 1000.0,
    }
    resp1 = client.post("/sync", json=payload1)
    assert resp1.status_code == 201

    # Test with +00:00 suffix
    payload2 = {
        "symbol": "ETH",
        "timestamp": "2026-08-26T14:36:00+00:00",
        "open": 2500.0,
        "high": 2502.0,
        "low": 2499.0,
        "close": 2501.0,
        "volume": 1000.0,
    }
    resp2 = client.post("/sync", json=payload2)
    assert resp2.status_code == 201


def test_sync_candle_unix_timestamp(client):
    """Should accept Unix epoch timestamp (seconds since 1970-01-01)."""
    # 1787767200 = 2026-08-26 12:00:00 UTC
    payload = {
        "symbol": "ETH",
        "timestamp": 1787767200,
        "open": 2500.0,
        "high": 2502.0,
        "low": 2499.0,
        "close": 2501.0,
        "volume": 1000.0,
    }
    resp = client.post("/sync", json=payload)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["symbol"] == "ETH"
    assert body["candle_stored"] is True


def test_analyze_accepts_valid_payload(client):
    payload = {
        "symbol": "ETHUSD",
        "signal_type": "long",
        "indicators": {"rsi": 55.0, "ema_9": 3400.1, "ema_21": 3390.5},
    }
    resp = client.post("/analyze", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["decision"] in ("accept", "reject")
    assert 0.0 <= body["confidence"] <= 1.0
    assert body["model_used"] == "heuristic_fallback"


def test_analyze_rejects_missing_fields(client):
    resp = client.post("/analyze", json={"symbol": "ETHUSD"})
    assert resp.status_code == 400


def test_analyze_rejects_bad_signal_type(client):
    payload = {"symbol": "ETHUSD", "signal_type": "sideways", "indicators": {}}
    resp = client.post("/analyze", json=payload)
    assert resp.status_code == 400

