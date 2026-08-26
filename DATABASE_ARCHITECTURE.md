# Database & Gap Recovery Architecture

## Summary

Created a complete database layer with gap detection and recovery for the kandlebot2-brain trading signal analysis service.

## Components Built

### 1. **Database Layer** (`brain_app/database.py`)
- SQLite schema supporting multiple timeframes (1m, 5m, 15m, 30m, 1h, 4h, 12h)
- Stores OHLCV candles + technical indicators (EMA20, EMA50, EMA200, CRSI, ADX, ATR, MFI)
- Fast indexed queries by symbol, timeframe, and timestamp
- `Database.find_gaps()` - Detects missing candles within a time window
- `Database.insert_candle()` - Stores individual candles
- `Database.get_candles()` - Fetches time-range queries
- Connection management with context managers

### 2. **Candle Store** (`brain_app/candle_store.py`)
- High-level API for storing 1-minute candles
- **Auto-aggregation**: Automatically creates 5m, 15m, 30m, 1h, 4h, 12h candles from constituent 1min candles
- Aggregation only succeeds if ALL constituent candles are present (no gaps)
- Stores aggregated candles with source = "aggregated" for tracking
- `CandleStore.store_candle()` - Main entry point (1min only)
- `CandleStore._aggregate_to_higher_timeframes()` - Auto-aggregates after each 1min candle
- `CandleStore.get_recent_candles()` - Fetches N most recent candles with smart time buffering
- `CandleStore.detect_and_report_gaps()` - Public API for gap detection

### 3. **Gap Recovery** (`brain_app/gap_recovery.py`)
- **Hybrid recovery strategy**: Tries Node server first, falls back to exchange API
- `GapRecovery.fetch_from_node_server()` - Fetches from Node server HTTP endpoint
  - Expected endpoint: `GET /api/candles?symbol=ETH&start=...&end=...&timeframe=1m`
  - Returns list of `{timestamp, open, high, low, close, volume, indicators?}`
- `GapRecovery.fetch_from_exchange()` - Uses CCXT library to fetch from exchange (Binance by default)
  - Supports 1000+ cryptocurrency exchanges
  - Handles pagination (CCXT limits are ~500 candles per request)
- `GapRecovery.recover_gap()` - Main entry point
  - Takes gap_start and gap_end times
  - Calls store_callback for each recovered candle
  - Returns count of candles filled

## Configuration

Updated `brain_app/config.py` with new environment variables:

```python
DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/candles.db")
NODE_SERVER_URL = os.environ.get("NODE_SERVER_URL")  # e.g., http://localhost:3000
EXCHANGE_NAME = os.environ.get("EXCHANGE_NAME", "binance")
```

## Integration

Updated `brain_app/__init__.py` (app factory) to initialize all components:

```python
app.db = Database(app.config["DATABASE_PATH"])
app.candle_store = CandleStore(app.db)
app.gap_recovery = GapRecovery(
    node_server_url=app.config.get("NODE_SERVER_URL"),
    exchange_name=app.config.get("EXCHANGE_NAME", "binance"),
)
```

## Dependencies

Added to `requirements.txt`:
- `requests==2.32.3` - HTTP library for Node server communication
- `ccxt==4.5.67` - Cryptocurrency exchange API (1000+ exchanges supported)

## Testing

Created comprehensive test suite in `tests/test_database.py`:
- **17 tests** covering all three modules
- **Database tests** (4): insert, retrieve, gap detection, gap finding
- **CandleStore tests** (6): aggregation, gap handling, bucket calculation, recent candles
- **GapRecovery tests** (4): Node server fetch, exchange fetch, gap recovery
- **Integration tests** (1): full workflow

All tests use temporary databases and mocking for isolation. **All 17 tests pass**.

## Data Flow

### Continuous Sync (Every Minute)
```
NODE server → POST /analyze → Brain API
{symbol, timestamp, OHLCV, indicators}
  ↓
candle_store.store_candle()
  ↓
Database (1m table)
  ↓
Auto-aggregate to 5m, 15m, 30m, 1h, 4h, 12h
  ↓
Database (higher timeframe tables)
```

### Signal Detection
```
NODE server detects signal → POST /analyze?signal_type=long
{symbol, timestamp, OHLCV, indicators, signal_type, confidence_needed}
  ↓
Brain performs multi-timeframe analysis
  ↓
Returns: {decision, confidence, trade_type, entry_price, analysis}
```

### Gap Recovery
```
Brain detects gaps in 1m data
  ↓
gap_recovery.recover_gap(symbol, gap_start, gap_end)
  ↓
Try: fetch_from_node_server()
  ├─ Success → Store candles + re-aggregate
  ├─ Fail → Try: fetch_from_exchange()
     └─ Store candles + re-aggregate
```

## Next Steps

1. **Update Node server** to send 1min candles every minute (continuous sync)
2. **Update features.py** to match your actual indicators (EMA20, EMA50, EMA200, CRSI, ADX, ATR, MFI)
3. **Update /analyze endpoint** to:
   - Store incoming candle
   - Detect and recover gaps on startup
   - Perform multi-timeframe analysis
   - Return confidence + trade_type + entry_price
4. **Train ML model** on labeled historical data to improve signal validation
5. **Deploy** to Heroku with NODE_SERVER_URL config

## Key Advantages

✅ **Stateful**: Brain maintains full historical context (no per-request data transmission)
✅ **Low bandwidth**: Only 1-2 new candles per minute
✅ **Resilient**: Auto-recovers from gaps via Node server or exchange API
✅ **Scalable**: SQLite starts, upgrades to PostgreSQL later
✅ **Accurate**: Multi-timeframe context for signal validation
✅ **Learnable**: Persistent candle history for ML model training
