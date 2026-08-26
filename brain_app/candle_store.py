"""Candle storage, aggregation, and gap recovery logic."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .database import TIMEFRAMES, Database

logger = logging.getLogger(__name__)


class CandleStore:
    """High-level interface for candle storage and aggregation."""

    def __init__(self, db: Database):
        """Initialize candle store with database connection.
        
        Args:
            db: Database instance.
        """
        self.db = db

    def store_candle(
        self,
        symbol: str,
        timestamp: datetime,
        ohlcv: dict,
        indicators: dict | None = None,
        source: str = "node_server",
        timeframe: str = "1m",  # NEW: Accept timeframe (default 1m for backward compat)
    ) -> None:
        """Store a candle at any timeframe.
        
        If timeframe is 1m, also auto-aggregate to higher timeframes.
        If timeframe is higher (30m, 1h, 4h, 12h), just store it directly.
        
        Args:
            symbol: Trading pair symbol (e.g., 'ETH').
            timestamp: Candle open time (UTC datetime).
            ohlcv: Dict with {open, high, low, close, volume}.
            indicators: Dict with indicator values (EMA20, EMA50, etc).
            source: Data source ('node_server', 'exchange_api', etc).
            timeframe: Candle timeframe ('1m', '30m', '1h', '4h', '12h'). Default: '1m'
        """
        # Store the candle at its timeframe
        self.db.insert_candle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
            ohlcv=ohlcv,
            indicators=indicators,
            source=source,
        )
        logger.debug(f"Stored {timeframe} candle for {symbol} at {timestamp}")

        # Only auto-aggregate if this is a 1min candle
        # (Higher TFs are sent directly from Node.js)
        if timeframe == "1m":
            self._aggregate_to_higher_timeframes(symbol, timestamp)

    def _aggregate_to_higher_timeframes(self, symbol: str, timestamp: datetime) -> None:
        """Aggregate 1min candles into 5m, 15m, 30m, 1h, 4h, 12h.
        
        For each higher timeframe, check if we have all constituent 1min candles.
        If yes, aggregate and store (or update if already exists).
        
        Args:
            symbol: Trading pair symbol.
            timestamp: Timestamp of the 1min candle just stored.
        """
        for tf, minutes in sorted(TIMEFRAMES.items()):
            if minutes == 1:  # Skip 1m
                continue

            # Find the "bucket" this timestamp belongs to
            bucket_start = self._get_bucket_start(timestamp, minutes)
            bucket_end = bucket_start + timedelta(minutes=minutes)

            # Try to aggregate constituent 1min candles
            aggregated = self._try_aggregate(symbol, bucket_start, bucket_end)
            if aggregated:
                try:
                    self.db.insert_candle(
                        symbol=symbol,
                        timeframe=tf,
                        timestamp=bucket_start,
                        ohlcv=aggregated["ohlcv"],
                        indicators=aggregated.get("indicators"),
                        source="aggregated",
                    )
                    logger.debug(f"Aggregated {tf} candle for {symbol} at {bucket_start}")
                except Exception as e:
                    # Candle already exists; could update if needed
                    logger.debug(f"Candle already exists for {symbol} {tf} at {bucket_start}: {e}")

    def _get_bucket_start(self, timestamp: datetime, minutes: int) -> datetime:
        """Find the start of the candle bucket for a given timeframe.
        
        E.g., for 5m bucket, 14:23 → 14:20, for 1h bucket, 14:23 → 14:00.
        
        Args:
            timestamp: Any timestamp within the bucket.
            minutes: Bucket size in minutes.
            
        Returns:
            Datetime representing the bucket start.
        """
        # Remove seconds/microseconds
        ts = timestamp.replace(second=0, microsecond=0)
        # Round down to nearest bucket
        minutes_since_hour = ts.minute
        bucket_minute = (minutes_since_hour // minutes) * minutes
        return ts.replace(minute=bucket_minute)

    def _try_aggregate(
        self, symbol: str, bucket_start: datetime, bucket_end: datetime
    ) -> dict | None:
        """Attempt to aggregate 1min candles into a single higher-timeframe candle.
        
        Returns None if constituent candles are missing (gap in data).
        
        Args:
            symbol: Trading pair symbol.
            bucket_start: Start of the aggregation window.
            bucket_end: End of the aggregation window (exclusive).
            
        Returns:
            Dict with {ohlcv, indicators} or None if data is incomplete.
        """
        constituent_candles = self.db.get_candles(
            symbol=symbol,
            timeframe="1m",
            since=bucket_start,
            limit=(bucket_end - bucket_start).seconds // 60,  # Expected count
        )

        expected_count = (bucket_end - bucket_start).seconds // 60
        if len(constituent_candles) != expected_count:
            logger.debug(
                f"Incomplete data for {symbol} {bucket_start}: "
                f"got {len(constituent_candles)}, expected {expected_count}"
            )
            return None

        # Aggregate OHLCV
        ohlcv = {
            "open": float(constituent_candles[0]["open"]),
            "high": max(float(c["high"]) for c in constituent_candles),
            "low": min(float(c["low"]) for c in constituent_candles),
            "close": float(constituent_candles[-1]["close"]),
            "volume": sum(float(c["volume"]) for c in constituent_candles),
        }

        # Aggregate indicators (average the last candle's indicators, or median)
        # For now, just use the last candle's indicators
        last_candle = constituent_candles[-1]
        indicators = {
            "ema_20": last_candle["ema_20"],
            "ema_50": last_candle["ema_50"],
            "ema_200": last_candle["ema_200"],
            "crsi": last_candle["crsi"],
            "adx": last_candle["adx"],
            "atr": last_candle["atr"],
            "mfi": last_candle["mfi"],
        }

        return {"ohlcv": ohlcv, "indicators": indicators}

    def get_recent_candles(
        self, symbol: str, timeframe: str, count: int = 100
    ) -> list[dict]:
        """Fetch the N most recent candles for a timeframe.
        
        Args:
            symbol: Trading pair symbol.
            timeframe: Candle timeframe.
            count: Number of candles to fetch.
            
        Returns:
            List of candle dicts, ordered by timestamp ascending.
        """
        now = datetime.now(timezone.utc)
        # Rough estimate: go back enough time to get `count` candles
        minutes = TIMEFRAMES.get(timeframe, 1)
        since = now - timedelta(minutes=minutes * count * 10)  # 10x buffer to be safe

        return self.db.get_candles(symbol, timeframe, since, limit=count)

    def detect_and_report_gaps(
        self, symbol: str, timeframe: str = "1m", hours_back: int = 24
    ) -> list[tuple[datetime, datetime]]:
        """Detect missing candles in a timeframe.
        
        Args:
            symbol: Trading pair symbol.
            timeframe: Candle timeframe to check.
            hours_back: Number of hours to look back.
            
        Returns:
            List of (gap_start, gap_end) tuples.
        """
        gaps = self.db.find_gaps(symbol, timeframe, hours_back)
        if gaps:
            logger.warning(
                f"Found {len(gaps)} gaps for {symbol} {timeframe} in last {hours_back}h: {gaps}"
            )
        return gaps
