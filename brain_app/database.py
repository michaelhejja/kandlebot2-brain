"""Database initialization, schema, and connection management."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

# SQL schema for candles table and indexes
CANDLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp DATETIME NOT NULL,
    
    -- OHLCV data
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    
    -- Technical indicators (populated by Node server for 1m candles)
    ema_9 REAL,
    ema_20 REAL,
    ema_50 REAL,
    ema_200 REAL,
    crsi REAL,
    rsi REAL,
    adx REAL,
    atr REAL,
    mfi REAL,
    
    -- Metadata
    source TEXT,
    is_confirmed BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(symbol, timeframe, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_candles_symbol_timeframe_time
ON candles(symbol, timeframe, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_candles_symbol_time
ON candles(symbol, timestamp DESC);
"""

# SQL schema for reversals table (tracks all detected reversals for confluence analysis)
REVERSALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS reversals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp DATETIME NOT NULL,
    
    -- Reversal signal metadata
    signal TEXT NOT NULL,  -- 'BUY' or 'SELL'
    reversal_type TEXT,     -- 'CONVERGENCE_BOUNCE', 'CLASSIC_DIVERGENCE', etc.
    confidence REAL NOT NULL,  -- 0-100
    
    -- Trend analysis at time of reversal
    price_trend REAL,  -- -100 to +100
    mfi_trend REAL,    -- -100 to +100
    
    -- Metadata
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(symbol, timeframe, timestamp, signal)
);

CREATE INDEX IF NOT EXISTS idx_reversals_symbol_signal_time
ON reversals(symbol, signal, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_reversals_symbol_time
ON reversals(symbol, timestamp DESC);
"""

# Timeframe definitions in minutes
TIMEFRAMES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "12h": 720,
}


class Database:
    """SQLite database connection and schema management."""

    def __init__(self, db_path: str):
        """Initialize database connection.
        
        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables and indexes if they don't exist."""
        with self.get_connection() as conn:
            conn.executescript(CANDLES_SCHEMA)
            conn.executescript(REVERSALS_SCHEMA)
            conn.commit()
        logger.info(f"Database initialized at {self.db_path}")

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database connections.
        
        Yields:
            sqlite3.Connection with row factory set to Column.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def insert_candle(
        self,
        symbol: str,
        timeframe: str,
        timestamp: datetime,
        ohlcv: dict,
        indicators: dict | None = None,
        source: str = "node_server",
    ) -> None:
        """Insert a single candle into the database.
        
        If a candle with the same symbol/timeframe/timestamp exists, it will be replaced.
        
        Args:
            symbol: Trading pair symbol (e.g., 'ETH').
            timeframe: Candle timeframe (e.g., '1m', '5m').
            timestamp: Candle open time (datetime, preferably UTC).
            ohlcv: Dict with keys {open, high, low, close, volume}.
            indicators: Optional dict with indicator values.
            source: Data source ('node_server', 'exchange_api', 'manual').
        """
        if indicators is None:
            indicators = {}

        with self.get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO candles (
                        symbol, timeframe, timestamp,
                        open, high, low, close, volume,
                        ema_9, ema_20, ema_50, ema_200, crsi, rsi, adx, atr, mfi,
                        source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        timeframe,
                        timestamp.isoformat(),
                        ohlcv.get("open", 0),
                        ohlcv.get("high", 0),
                        ohlcv.get("low", 0),
                        ohlcv.get("close", 0),
                        ohlcv.get("volume", 0),
                        indicators.get("ema_9"),
                        indicators.get("ema_20"),
                        indicators.get("ema_50"),
                        indicators.get("ema_200"),
                        indicators.get("crsi"),
                        indicators.get("rsi"),
                        indicators.get("adx"),
                        indicators.get("atr"),
                        indicators.get("mfi"),
                        source,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                # Candle already exists; update it instead
                logger.debug(
                    f"Candle already exists for {symbol} {timeframe} at {timestamp}; "
                    "updating with new data."
                )
                conn.execute(
                    """
                    UPDATE candles SET
                        open=?, high=?, low=?, close=?, volume=?,
                        ema_9=?, ema_20=?, ema_50=?, ema_200=?, crsi=?, rsi=?, adx=?, atr=?, mfi=?,
                        source=?
                    WHERE symbol=? AND timeframe=? AND timestamp=?
                    """,
                    (
                        ohlcv.get("open", 0),
                        ohlcv.get("high", 0),
                        ohlcv.get("low", 0),
                        ohlcv.get("close", 0),
                        ohlcv.get("volume", 0),
                        indicators.get("ema_9"),
                        indicators.get("ema_20"),
                        indicators.get("ema_50"),
                        indicators.get("ema_200"),
                        indicators.get("crsi"),
                        indicators.get("rsi"),
                        indicators.get("adx"),
                        indicators.get("atr"),
                        indicators.get("mfi"),
                        source,
                        symbol,
                        timeframe,
                        timestamp.isoformat(),
                    ),
                )
                conn.commit()

    def get_latest_candle(
        self, symbol: str, timeframe: str
    ) -> dict | None:
        """Fetch the most recent candle for a given symbol/timeframe.
        
        Args:
            symbol: Trading pair symbol.
            timeframe: Candle timeframe.
            
        Returns:
            Row dict or None if no candles exist.
        """
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM candles
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (symbol, timeframe),
            ).fetchone()
            return dict(row) if row else None

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 500,
    ) -> list[dict]:
        """Fetch candles within a time range.
        
        Args:
            symbol: Trading pair symbol.
            timeframe: Candle timeframe.
            since: Start time (inclusive).
            limit: Maximum number of candles to return.
            
        Returns:
            List of row dicts, ordered by timestamp ascending.
        """
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM candles
                WHERE symbol = ? AND timeframe = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (symbol, timeframe, since.isoformat(), limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_candle(self, symbol: str, timeframe: str, timestamp: datetime) -> None:
        """Delete a specific candle (useful for gap recovery overwrites).
        
        Args:
            symbol: Trading pair symbol.
            timeframe: Candle timeframe.
            timestamp: Candle open time.
        """
        with self.get_connection() as conn:
            conn.execute(
                "DELETE FROM candles WHERE symbol = ? AND timeframe = ? AND timestamp = ?",
                (symbol, timeframe, timestamp.isoformat()),
            )
            conn.commit()

    def find_gaps(
        self, symbol: str, timeframe: str, hours_back: int = 24
    ) -> list[tuple[datetime, datetime]]:
        """Detect missing candles within a time window.
        
        Returns list of (gap_start, gap_end) tuples where data is missing.
        
        Args:
            symbol: Trading pair symbol.
            timeframe: Candle timeframe (e.g., '1m', '5m').
            hours_back: Number of hours to check back from now.
            
        Returns:
            List of (gap_start, gap_end) tuples.
        """
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(hours=hours_back)
        
        # Fetch all candles in the window
        candles = self.get_candles(symbol, timeframe, start_time, limit=10000)
        
        if not candles:
            # No candles at all in this timeframe
            return [(start_time, now)]
        
        gaps = []
        tf_minutes = TIMEFRAMES.get(timeframe, 1)
        expected_interval = timedelta(minutes=tf_minutes)
        
        # Check for gaps between consecutive candles
        for i in range(len(candles) - 1):
            current_ts = datetime.fromisoformat(candles[i]["timestamp"])
            next_ts = datetime.fromisoformat(candles[i + 1]["timestamp"])
            expected_next = current_ts + expected_interval
            
            if next_ts > expected_next:
                gaps.append((expected_next, next_ts))
        
        # Check gap at the end (recent missing candles)
        if candles:
            last_ts = datetime.fromisoformat(candles[-1]["timestamp"])
            expected_next = last_ts + expected_interval
            if expected_next < now:
                gaps.append((expected_next, now))
        
        return gaps

    def insert_reversal(
        self,
        symbol: str,
        timeframe: str,
        timestamp: datetime,
        signal: str,
        confidence: float,
        reversal_type: str | None = None,
        price_trend: float | None = None,
        mfi_trend: float | None = None,
    ) -> None:
        """Record a detected reversal signal for confluence analysis.
        
        Args:
            symbol: Trading pair symbol (e.g., 'ETH').
            timeframe: Candle timeframe (e.g., '1m', '5m').
            timestamp: Candle timestamp when reversal detected.
            signal: 'BUY' or 'SELL'.
            confidence: Confidence level (0-100).
            reversal_type: Pattern type (e.g., 'CONVERGENCE_BOUNCE').
            price_trend: Price trend score (-100 to +100).
            mfi_trend: MFI trend score (-100 to +100).
        """
        with self.get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO reversals (
                        symbol, timeframe, timestamp, signal, confidence,
                        reversal_type, price_trend, mfi_trend
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        timeframe,
                        timestamp.isoformat(),
                        signal,
                        confidence,
                        reversal_type,
                        price_trend,
                        mfi_trend,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                # Reversal already exists; skip to avoid duplicates
                logger.debug(
                    f"Reversal already recorded for {symbol} {timeframe} "
                    f"at {timestamp} signal={signal}"
                )

    def get_recent_reversals(
        self,
        symbol: str,
        signal_type: str | None = None,
        lookback_minutes: int = 120,
    ) -> list[dict]:
        """Fetch recent reversals for confluence analysis.
        
        Args:
            symbol: Trading pair symbol.
            signal_type: Filter by 'long' (BUY) or 'short' (SELL), or None for all.
            lookback_minutes: How many minutes back to check.
            
        Returns:
            List of reversal dicts, ordered by timestamp descending (newest first).
        """
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(minutes=lookback_minutes)
        
        # Map signal_type to BUY/SELL
        signal_filter = None
        if signal_type == "long":
            signal_filter = "BUY"
        elif signal_type == "short":
            signal_filter = "SELL"
        
        with self.get_connection() as conn:
            if signal_filter:
                rows = conn.execute(
                    """
                    SELECT * FROM reversals
                    WHERE symbol = ? AND signal = ? AND timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT 100
                    """,
                    (symbol, signal_filter, start_time.isoformat()),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM reversals
                    WHERE symbol = ? AND timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT 100
                    """,
                    (symbol, start_time.isoformat()),
                ).fetchall()
            
            return [dict(row) for row in rows]
