"""Test analyze endpoint directly."""
import requests
import json

response = requests.post(
    "http://localhost:5000/analyze",
    json={
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
)

print(f"Status: {response.status_code}")
print(f"Response:")
print(json.dumps(response.json(), indent=2))
