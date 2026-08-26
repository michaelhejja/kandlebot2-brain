# Quick Start: NODE → Brain Integration

## 1. Update Your NODE Server to Send Candles

Every minute, after calculating indicators, send a POST to the brain:

```javascript
// Node.js example (every 1 minute)
const axios = require('axios');

async function syncCandleWithBrain(symbol, ohlcv, indicators) {
  try {
    const payload = {
      symbol: symbol,  // 'ETH'
      timestamp: new Date().toISOString(),
      open: ohlcv.open,
      high: ohlcv.high,
      low: ohlcv.low,
      close: ohlcv.close,
      volume: ohlcv.volume,
      indicators: {
        ema_20: indicators.ema20,
        ema_50: indicators.ema50,
        ema_200: indicators.ema200,
        crsi: indicators.crsi,
        adx: indicators.adx,
        atr: indicators.atr,
        mfi: indicators.mfi,
      }
    };
    
    // Continuous sync endpoint (creates this next)
    await axios.post('http://localhost:5000/sync', payload, {
      headers: { 'X-API-Key': process.env.BRAIN_API_KEY }
    });
  } catch (err) {
    console.error('Brain sync failed:', err.message);
  }
}
```

## 2. Add a /sync Endpoint to Brain

Edit `brain_app/routes.py` to add continuous sync:

```python
@api_bp.post("/sync")
def sync_candle():
    """Receive 1-minute candle from Node server and store it."""
    if not _check_api_key():
        return jsonify(error="Unauthorized"), 401
    
    payload = request.get_json(silent=True)
    
    try:
        # Validate required fields
        for field in ("symbol", "timestamp", "open", "high", "low", "close", "volume"):
            if field not in payload:
                return jsonify(error=f"Missing field: {field}"), 400
        
        # Parse timestamp
        timestamp = datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
        
        # Extract OHLCV
        ohlcv = {
            "open": float(payload["open"]),
            "high": float(payload["high"]),
            "low": float(payload["low"]),
            "close": float(payload["close"]),
            "volume": float(payload["volume"]),
        }
        
        # Extract indicators (optional)
        indicators = payload.get("indicators", {})
        
        # Store candle and auto-aggregate
        current_app.candle_store.store_candle(
            symbol=payload["symbol"],
            timestamp=timestamp,
            ohlcv=ohlcv,
            indicators=indicators,
            source="node_server",
        )
        
        return jsonify(
            status="ok",
            symbol=payload["symbol"],
            candle_stored=True,
        ), 201
        
    except Exception as e:
        logger.error(f"Error storing candle: {e}")
        return jsonify(error=str(e)), 400
```

## 3. Update /analyze to Use Stored Data

When NODE detects a signal, the brain now has full historical context:

```python
@api_bp.post("/analyze")
def analyze():
    """Receive signal from Node server, validate with full context."""
    if not _check_api_key():
        return jsonify(error="Unauthorized"), 401

    payload = request.get_json(silent=True)
    try:
        validate_payload(payload)
    except PayloadValidationError as exc:
        return jsonify(error=str(exc)), 400
    
    symbol = payload["symbol"]
    
    # Get context: recent candles across timeframes
    context = {
        "current_1m": current_app.candle_store.get_recent_candles(symbol, "1m", count=20),
        "recent_5m": current_app.candle_store.get_recent_candles(symbol, "5m", count=20),
        "recent_1h": current_app.candle_store.get_recent_candles(symbol, "1h", count=10),
        "recent_4h": current_app.candle_store.get_recent_candles(symbol, "4h", count=5),
    }
    
    # Check for gaps
    gaps = current_app.candle_store.detect_and_report_gaps(symbol, "1m", hours_back=1)
    if gaps:
        logger.warning(f"Gaps detected for {symbol}: {gaps}")
        # Attempt recovery
        for gap_start, gap_end in gaps:
            current_app.gap_recovery.recover_gap(
                symbol, gap_start, gap_end, 
                current_app.candle_store.store_candle
            )
    
    # Build feature vector for ML model
    features = build_feature_vector(payload)
    
    # Get ML prediction
    result = current_app.classifier.predict(features)
    
    # Add multi-timeframe context to result
    result["context"] = context
    result["gaps_detected"] = len(gaps)

    return jsonify(
        symbol=symbol,
        signal_type=payload["signal_type"],
        **result,
    )
```

## 4. Run Locally

```bash
# Terminal 1: Start the brain
source .venv/bin/activate
export DATABASE_PATH="data/candles.db"
export NODE_SERVER_URL="http://localhost:3000"  # Your Node server
export EXCHANGE_NAME="binance"
export BRAIN_API_KEY="your-secret-key"
python wsgi.py  # or: gunicorn wsgi:app

# Terminal 2: Test sync
curl -X POST http://localhost:5000/sync \
  -H "X-API-Key: your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{
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
      "mfi": 40.0
    }
  }'
```

## 5. Deploy to Heroku

```bash
git add -A
git commit -m "Add database and gap recovery"

heroku config:set \
  DATABASE_PATH="data/candles.db" \
  NODE_SERVER_URL="https://your-node-server.com" \
  EXCHANGE_NAME="binance" \
  BRAIN_API_KEY="your-production-secret"

git push heroku main
```

## 6. Database Schema

```bash
# Inspect the database
sqlite3 data/candles.db

# List tables
.tables

# Check candles table schema
.schema candles

# Query recent candles
SELECT symbol, timeframe, COUNT(*) as count 
FROM candles 
GROUP BY symbol, timeframe 
ORDER BY timeframe, symbol;

# Find gaps
SELECT symbol, timeframe, timestamp 
FROM candles 
WHERE symbol = 'ETH' AND timeframe = '1m' 
ORDER BY timestamp DESC 
LIMIT 20;
```

## Key Points

1. **NODE sends every minute** (not just on signals)
2. **Brain stores and aggregates** automatically
3. **On signal**, brain has full multi-timeframe context
4. **Gap recovery** is automatic (Node server → Exchange API fallback)
5. **Features.py** must match your indicator names exactly
6. **ML model** improves over time with labeled signal outcomes

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Database is locked" | SQLite contention. Upgrade to PostgreSQL for production. |
| Gaps detected | Check NODE server status. Brain will auto-recover from exchange API. |
| Missing indicators | Check `features.py` FEATURE_COLUMNS matches payload. |
| Slow aggregation | Normal. Each candle aggregates instantly. First sync will auto-recover 1h of data. |
| Model not loaded | Run `python -m training.train_model` to train ML classifier. |
