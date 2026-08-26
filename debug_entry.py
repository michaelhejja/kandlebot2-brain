"""Debug entry and trade classification functions."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from brain_app.features import calculate_optimal_entry, classify_trade_type, check_timeframe_alignment
from brain_app import create_app

app = create_app()
db = app.db

# Test payload
test_payload = {
    "symbol": "ETH",
    "signal_type": "long",
    "indicators": {
        "close": 2501.0,
        "rsi": 65.0,
        "ema_9": 2500.5,
        "ema_21": 2499.0,
        "macd": 5.0,
        "macd_signal": 3.0,
        "atr": 12.0,
        "volume": 1500.0,
        "ema_200": 2490.0,
    }
}

print("Testing calculate_optimal_entry()...")
try:
    result = calculate_optimal_entry(test_payload)
    print("Result:")
    for key, value in result.items():
        print(f"  {key}: {value}")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n\nTesting classify_trade_type()...")
try:
    tf_alignment = check_timeframe_alignment(db, "ETH", "long")
    print(f"TF Alignment: {tf_alignment}")
    
    result = classify_trade_type(test_payload, tf_alignment)
    print("Result:")
    for key, value in result.items():
        print(f"  {key}: {value}")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
