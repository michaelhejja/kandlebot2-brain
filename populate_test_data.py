"""Populate the database with test candles for multi-TF alignment checking."""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from brain_app import create_app
from brain_app.database import Database

def populate_test_data():
    """Create test candles across all timeframes."""
    app = create_app()
    db = app.db
    
    # Use current time as reference
    now = datetime.now(timezone.utc)
    
    # Create candles for ETH - recent data
    candle_data = {
        '1m': {
            'open': 2500.0, 'high': 2502.0, 'low': 2499.0, 'close': 2501.0, 'volume': 1000.0,
            'ema_9': 2500.0, 'ema_20': 2500.5, 'ema_50': 2495.0, 'ema_200': 2490.0,
            'crsi': 35.0, 'rsi': 65.0, 'adx': 28.0, 'atr': 10.0, 'mfi': 40.0
        },
        '30m': {
            'open': 2498.0, 'high': 2504.0, 'low': 2497.0, 'close': 2502.5, 'volume': 15000.0,
            'ema_9': 2502.0, 'ema_20': 2501.0, 'ema_50': 2496.0, 'ema_200': 2491.0,
            'crsi': 55.0, 'rsi': 65.0, 'adx': 32.0, 'atr': 12.0, 'mfi': 55.0
        },
        '1h': {
            'open': 2495.0, 'high': 2510.0, 'low': 2495.0, 'close': 2505.0, 'volume': 45000.0,
            'ema_9': 2504.0, 'ema_20': 2502.0, 'ema_50': 2498.0, 'ema_200': 2493.0,
            'crsi': 58.0, 'rsi': 68.0, 'adx': 35.0, 'atr': 14.0, 'mfi': 60.0
        },
        '4h': {
            'open': 2480.0, 'high': 2515.0, 'low': 2480.0, 'close': 2508.0, 'volume': 180000.0,
            'ema_9': 2506.0, 'ema_20': 2503.0, 'ema_50': 2500.0, 'ema_200': 2495.0,
            'crsi': 62.0, 'rsi': 72.0, 'adx': 40.0, 'atr': 18.0, 'mfi': 65.0
        },
        '12h': {
            'open': 2450.0, 'high': 2520.0, 'low': 2450.0, 'close': 2510.0, 'volume': 540000.0,
            'ema_9': 2508.0, 'ema_20': 2505.0, 'ema_50': 2502.0, 'ema_200': 2497.0,
            'crsi': 65.0, 'rsi': 75.0, 'adx': 45.0, 'atr': 22.0, 'mfi': 70.0
        },
    }
    
    print("📊 Populating test candles for ETH (LONG setup - all TFs aligned)...\n")
    
    for timeframe, indicators in candle_data.items():
        # Calculate timestamp based on timeframe
        if timeframe == '1m':
            ts = now
        elif timeframe == '30m':
            ts = now - timedelta(minutes=now.minute % 30, seconds=now.second)
        elif timeframe == '1h':
            ts = now - timedelta(minutes=now.minute, seconds=now.second)
        elif timeframe == '4h':
            ts = now - timedelta(hours=now.hour % 4, minutes=now.minute, seconds=now.second)
        else:  # 12h
            ts = now - timedelta(hours=now.hour % 12, minutes=now.minute, seconds=now.second)
        
        db.insert_candle(
            symbol='ETH',
            timeframe=timeframe,
            timestamp=ts,
            ohlcv={
                'open': indicators['open'],
                'high': indicators['high'],
                'low': indicators['low'],
                'close': indicators['close'],
                'volume': indicators['volume'],
            },
            indicators={
                'ema_9': indicators['ema_9'],
                'ema_20': indicators['ema_20'],
                'ema_50': indicators['ema_50'],
                'ema_200': indicators['ema_200'],
                'crsi': indicators['crsi'],
                'rsi': indicators['rsi'],
                'adx': indicators['adx'],
                'atr': indicators['atr'],
                'mfi': indicators['mfi'],
            },
            source='test_data'
        )
        
        print(f"  ✓ {timeframe:4s} | EMA: {indicators['ema_20']:.0f} > {indicators['ema_50']:.0f} > {indicators['ema_200']:.0f} | "
              f"RSI: {indicators['crsi']:.0f} | ADX: {indicators['adx']:.0f}")
    
    # Also create BTC test data (MEDIUM setup)
    print("\n📊 Populating test candles for BTC (MEDIUM setup - some TFs aligned)...\n")
    
    btc_data = {
        '1m': {
            'open': 59500.0, 'high': 60000.0, 'low': 59400.0, 'close': 59800.0, 'volume': 5000.0,
            'ema_9': 59750.0, 'ema_20': 59700.0, 'ema_50': 59200.0, 'ema_200': 58500.0,
            'crsi': 45.0, 'rsi': 55.0, 'adx': 22.0, 'atr': 300.0, 'mfi': 48.0
        },
        '30m': {
            'open': 59400.0, 'high': 60100.0, 'low': 59300.0, 'close': 59850.0, 'volume': 75000.0,
            'ema_9': 59800.0, 'ema_20': 59750.0, 'ema_50': 59300.0, 'ema_200': 58600.0,
            'crsi': 50.0, 'rsi': 58.0, 'adx': 25.0, 'atr': 350.0, 'mfi': 52.0
        },
        '1h': {
            'open': 59200.0, 'high': 60200.0, 'low': 59100.0, 'close': 59900.0, 'volume': 225000.0,
            'ema_9': 59850.0, 'ema_20': 59800.0, 'ema_50': 59400.0, 'ema_200': 58700.0,
            'crsi': 55.0, 'rsi': 62.0, 'adx': 28.0, 'atr': 400.0, 'mfi': 58.0
        },
        '4h': {
            'open': 58800.0, 'high': 60500.0, 'low': 58700.0, 'close': 59950.0, 'volume': 900000.0,
            'ema_9': 59900.0, 'ema_20': 59850.0, 'ema_50': 59500.0, 'ema_200': 58800.0,
            'crsi': 60.0, 'rsi': 68.0, 'adx': 32.0, 'atr': 500.0, 'mfi': 65.0
        },
        '12h': {
            'open': 58000.0, 'high': 61000.0, 'low': 58000.0, 'close': 60000.0, 'volume': 2700000.0,
            'ema_9': 59950.0, 'ema_20': 59900.0, 'ema_50': 59100.0, 'ema_200': 58300.0,
            'crsi': 65.0, 'rsi': 72.0, 'adx': 38.0, 'atr': 600.0, 'mfi': 70.0
        },
    }
    
    for timeframe, indicators in btc_data.items():
        # Calculate timestamp
        if timeframe == '1m':
            ts = now
        elif timeframe == '30m':
            ts = now - timedelta(minutes=now.minute % 30, seconds=now.second)
        elif timeframe == '1h':
            ts = now - timedelta(minutes=now.minute, seconds=now.second)
        elif timeframe == '4h':
            ts = now - timedelta(hours=now.hour % 4, minutes=now.minute, seconds=now.second)
        else:  # 12h
            ts = now - timedelta(hours=now.hour % 12, minutes=now.minute, seconds=now.second)
        
        db.insert_candle(
            symbol='BTC',
            timeframe=timeframe,
            timestamp=ts,
            ohlcv={
                'open': indicators['open'],
                'high': indicators['high'],
                'low': indicators['low'],
                'close': indicators['close'],
                'volume': indicators['volume'],
            },
            indicators={
                'ema_9': indicators['ema_9'],
                'ema_20': indicators['ema_20'],
                'ema_50': indicators['ema_50'],
                'ema_200': indicators['ema_200'],
                'crsi': indicators['crsi'],
                'rsi': indicators['rsi'],
                'adx': indicators['adx'],
                'atr': indicators['atr'],
                'mfi': indicators['mfi'],
            },
            source='test_data'
        )
        
        print(f"  ✓ {timeframe:4s} | EMA: {indicators['ema_20']:.0f} > {indicators['ema_50']:.0f} > {indicators['ema_200']:.0f} | "
              f"RSI: {indicators['crsi']:.0f} | ADX: {indicators['adx']:.0f}")
    
    print("\n✅ Test data populated successfully!")
    print(f"\nDatabase: {app.config['DATABASE_PATH']}")

if __name__ == "__main__":
    populate_test_data()
