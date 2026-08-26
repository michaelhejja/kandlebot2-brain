"""Check full candle data."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from brain_app import create_app

app = create_app()
db = app.db

candle = db.get_latest_candle('ETH', '30m')
print("Full candle row:")
for key, value in dict(candle).items():
    print(f"  {key}: {value}")
