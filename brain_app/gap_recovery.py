"""Gap recovery: fill missing candles from external sources (Node server, exchange API)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class GapRecovery:
    """Recover missing candles from Node server or exchange API."""

    def __init__(self, node_server_url: Optional[str] = None, exchange_name: str = "binance"):
        """Initialize gap recovery with external data sources.
        
        Args:
            node_server_url: Base URL of Node server (e.g., http://localhost:3000).
            exchange_name: CCXT exchange name for fallback (e.g., 'binance').
        """
        self.node_server_url = node_server_url
        self.exchange_name = exchange_name
        self._exchange = None  # Lazy load ccxt

    def _get_exchange(self):
        """Lazy-load ccxt exchange instance."""
        if self._exchange is None:
            try:
                import ccxt
                exchange_class = getattr(ccxt, self.exchange_name)
                self._exchange = exchange_class()
            except ImportError:
                logger.error(
                    f"ccxt not installed. Install with: pip install ccxt"
                )
                self._exchange = False
            except AttributeError:
                logger.error(f"Exchange '{self.exchange_name}' not found in ccxt")
                self._exchange = False
        return self._exchange if self._exchange is not False else None

    def fetch_from_node_server(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[dict] | None:
        """Fetch missing candles from Node server historical endpoint.
        
        Expected Node server endpoint:
            GET /api/candles?symbol=ETH&start=2026-08-26T00:00:00Z&end=2026-08-26T01:00:00Z
        
        Returns:
            List of {timestamp, open, high, low, close, volume, indicators}
            or None if endpoint unavailable.
            
        Args:
            symbol: Trading pair symbol.
            start: Start time (inclusive).
            end: End time (exclusive).
        """
        if not self.node_server_url:
            logger.debug("Node server URL not configured; skipping")
            return None

        try:
            url = f"{self.node_server_url}/api/candles"
            params = {
                "symbol": symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "timeframe": "1m",
            }
            logger.info(f"Fetching candles from Node server: {url} with {params}")
            
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            
            data = resp.json()
            if not isinstance(data, list):
                logger.warning(f"Unexpected response format from Node server: {type(data)}")
                return None
            
            logger.info(f"Fetched {len(data)} candles from Node server")
            return data
            
        except requests.RequestException as e:
            logger.warning(f"Failed to fetch from Node server: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching from Node server: {e}")
            return None

    def fetch_from_exchange(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1m"
    ) -> list[dict] | None:
        """Fetch historical candles from exchange via CCXT.
        
        Args:
            symbol: Trading pair symbol (e.g., 'ETH').
            start: Start time (inclusive).
            end: End time (exclusive).
            timeframe: CCXT timeframe string ('1m', '5m', etc).
            
        Returns:
            List of {timestamp, open, high, low, close, volume} dicts
            or None if fetch fails.
        """
        exchange = self._get_exchange()
        if not exchange:
            logger.warning(f"Exchange '{self.exchange_name}' not available")
            return None

        try:
            # CCXT expects pair in format 'BTC/USDT'
            pair = f"{symbol}/USDT"
            
            # Convert datetime to milliseconds since epoch (CCXT uses ms)
            start_ms = int(start.timestamp() * 1000)
            end_ms = int(end.timestamp() * 1000)
            
            logger.info(
                f"Fetching from {self.exchange_name}: {pair} {timeframe} "
                f"from {start} to {end}"
            )
            
            all_candles = []
            current_ms = start_ms
            
            # CCXT fetch_ohlcv typically returns ~500 candles max per call
            while current_ms < end_ms:
                candles = exchange.fetch_ohlcv(
                    pair, timeframe=timeframe, since=current_ms, limit=1000
                )
                
                if not candles:
                    break
                
                all_candles.extend(candles)
                
                # Move to next batch
                last_candle_ms = candles[-1][0]
                if last_candle_ms >= end_ms or last_candle_ms <= current_ms:
                    break
                current_ms = last_candle_ms + 1
            
            # Convert CCXT format [timestamp_ms, o, h, l, c, v] to dict
            result = []
            for candle in all_candles:
                if len(candle) < 6:
                    continue
                ts_ms, o, h, l, c, v = candle[:6]
                ts = datetime.fromtimestamp(ts_ms / 1000)
                result.append({
                    "timestamp": ts.isoformat(),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                })
            
            logger.info(f"Fetched {len(result)} candles from {self.exchange_name}")
            return result
            
        except Exception as e:
            logger.error(f"Error fetching from exchange: {e}")
            return None

    def recover_gap(
        self,
        symbol: str,
        gap_start: datetime,
        gap_end: datetime,
        store_callback,
    ) -> int:
        """Attempt to fill a gap by fetching from Node server, then exchange.
        
        Args:
            symbol: Trading pair symbol.
            gap_start: Start of the gap (inclusive).
            gap_end: End of the gap (exclusive).
            store_callback: Callable(symbol, timestamp, ohlcv, source) to store each candle.
            
        Returns:
            Number of candles filled.
        """
        logger.info(f"Recovering gap for {symbol} from {gap_start} to {gap_end}")
        
        # Try Node server first
        candles = self.fetch_from_node_server(symbol, gap_start, gap_end)
        
        # Fallback to exchange
        if candles is None:
            logger.info("Node server fetch failed; trying exchange API")
            candles = self.fetch_from_exchange(symbol, gap_start, gap_end)
        
        if not candles:
            logger.warning(f"Could not recover gap for {symbol}: no data available")
            return 0
        
        # Store each candle
        count = 0
        for candle in candles:
            try:
                ts = datetime.fromisoformat(candle["timestamp"])
                ohlcv = {
                    "open": candle["open"],
                    "high": candle["high"],
                    "low": candle["low"],
                    "close": candle["close"],
                    "volume": candle["volume"],
                }
                # Indicators may not be available from exchange, that's ok
                indicators = {
                    k: candle.get(k) for k in ["ema_20", "ema_50", "ema_200", "crsi", "adx", "atr", "mfi"]
                }
                indicators = {k: v for k, v in indicators.items() if v is not None}
                
                store_callback(symbol, ts, ohlcv, indicators or None)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to store recovered candle: {e}")
        
        logger.info(f"Recovered {count} candles for {symbol}")
        return count
