#!/usr/bin/env python3
"""
Database inspection script to diagnose candle storage and timeframe alignment.
Usage: python inspect_db.py [symbol] [optional: limit rows]
Example: python inspect_db.py ETH 20
"""

import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

def get_db():
    """Connect to the database."""
    db_path = Path(__file__).parent / "data" / "candles.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn

def inspect_database():
    """Main inspection routine."""
    symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "ETH"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    
    conn = get_db()
    cursor = conn.cursor()
    
    print(f"\n{'='*80}")
    print(f"Database Inspection: {symbol}")
    print(f"{'='*80}\n")
    
    # Count total candles by timeframe
    print(f"📊 Candle Count by Timeframe:\n")
    for tf in ["1m", "5m", "15m", "30m", "1h", "4h", "12h"]:
        cursor.execute(
            "SELECT COUNT(*) as cnt, MIN(timestamp) as earliest, MAX(timestamp) as latest "
            "FROM candles WHERE symbol = ? AND timeframe = ?",
            (symbol, tf)
        )
        row = cursor.fetchone()
        count = row["cnt"]
        earliest = row["earliest"]
        latest = row["latest"]
        
        if count > 0:
            print(f"  {tf:>4}  {count:>6} candles  |  Earliest: {earliest[:19]}  |  Latest: {latest[:19]}")
        else:
            print(f"  {tf:>4}  ⚠️  NO DATA")
    
    # Check primary timeframes for recent data
    print(f"\n\n📍 Primary Timeframe Status (for multi-TF alignment):\n")
    primary_tfs = ["30m", "1h", "4h", "12h"]
    all_primary_have_data = True
    
    for tf in primary_tfs:
        cursor.execute(
            "SELECT timestamp, close, ema_20, ema_50, ema_9, volume FROM candles "
            "WHERE symbol = ? AND timeframe = ? ORDER BY timestamp DESC LIMIT 3",
            (symbol, tf)
        )
        rows = cursor.fetchall()
        
        if rows:
            print(f"  {tf:>4}  ✓ Latest candle:")
            row = rows[0]
            print(f"        Time: {row['timestamp']}")
            print(f"        Close: {row['close']:.2f} | EMA9: {row['ema_9'] or 'N/A'} | EMA20: {row['ema_20'] or 'N/A'} | EMA50: {row['ema_50'] or 'N/A'}")
            print(f"        Volume: {row['volume']:.0f}\n")
        else:
            print(f"  {tf:>4}  ✗ NO DATA - Cannot check alignment for this timeframe\n")
            all_primary_have_data = False
    
    # Summary
    print(f"\n{'='*80}")
    if all_primary_have_data:
        print("✅ GOOD: All primary timeframes have data. Multi-TF alignment can be checked.")
    else:
        print("⚠️  WARNING: Missing data in primary timeframes (30m, 1h, 4h, 12h).")
        print("   The Brain needs all 4 primary TFs to perform alignment checks.")
        print("   Solution: Keep the Node server running to accumulate candles across timeframes.")
        print("   Expected: 30m needs ~30 min, 1h needs 1 hour, 4h needs 4 hours, 12h needs 12 hours")
    print(f"{'='*80}\n")
    
    # Show latest 1m candles (most recent data)
    print(f"📈 Latest {limit} 1-Minute Candles (Most Recent):\n")
    cursor.execute(
        "SELECT timestamp, close, ema_9, ema_20, crsi, rsi, adx, atr, mfi FROM candles "
        "WHERE symbol = ? AND timeframe = '1m' ORDER BY timestamp DESC LIMIT ?",
        (symbol, limit)
    )
    rows = cursor.fetchall()
    
    if rows:
        for i, row in enumerate(rows):
            print(f"  {i+1}. {row['timestamp']} | Close: {row['close']:.2f}")
            print(f"     EMA9: {row['ema_9'] or 'N/A':>6} | EMA20: {row['ema_20'] or 'N/A':>6} | CRSI: {row['crsi'] or 'N/A':>5} | RSI: {row['rsi'] or 'N/A':>5}")
            print(f"     ADX: {row['adx'] or 'N/A':>5} | ATR: {row['atr'] or 'N/A':>7} | MFI: {row['mfi'] or 'N/A':>5}\n")
    else:
        print(f"  ⚠️  No 1m candles found for {symbol}")
    
    conn.close()

if __name__ == "__main__":
    try:
        inspect_database()
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
