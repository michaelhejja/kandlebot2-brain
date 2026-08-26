"""Debug script to inspect database contents and TF alignment checks."""
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))

from brain_app import create_app
from brain_app.features import check_timeframe_alignment

app = create_app()
db = app.db

print("=" * 70)
print("DATABASE DEBUG - Checking candles and alignment")
print("=" * 70)

# Check what candles exist
for symbol in ['ETH', 'BTC']:
    print(f"\n📍 {symbol}:")
    for tf in ['1m', '30m', '1h', '4h', '12h']:
        candle = db.get_latest_candle(symbol, tf)
        if candle:
            print(f"  {tf:4s} | ✓ Found | Close: {candle['close']:.2f} | EMA20: {candle.get('ema_20', 'N/A')} | RSI: {candle.get('rsi', 'N/A')} | ADX: {candle.get('adx', 'N/A')}")
        else:
            print(f"  {tf:4s} | ✗ MISSING")

# Test alignment checks
print("\n" + "=" * 70)
print("ALIGNMENT CHECKS")
print("=" * 70)

for symbol in ['ETH', 'BTC']:
    for signal_type in ['long', 'short']:
        alignment = check_timeframe_alignment(db, symbol, signal_type)
        print(f"\n{symbol} ({signal_type.upper()}):")
        print(f"  Score: {alignment['tf_alignment_score']}/4")
        print(f"  Details: {alignment['details']}")
        print(f"  30m aligned: {alignment['tf_30m_aligned']}")
        print(f"  1h aligned: {alignment['tf_1h_aligned']}")
        print(f"  4h aligned: {alignment['tf_4h_aligned']}")
        print(f"  12h aligned: {alignment['tf_12h_aligned']}")
