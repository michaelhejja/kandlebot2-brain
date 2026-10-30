"""Feature extraction shared by both the live API and the training pipeline.

Keeping this logic in one place guarantees the feature vector built at
inference time (brain_app/routes.py) exactly matches what the model was
trained on (training/train_model.py).

IMPORTANT: `FEATURE_COLUMNS` is a placeholder. Update it to match whatever
indicator keys your Node.js server actually sends in the `indicators` object,
and make sure your training CSV has the same column names.
"""
from __future__ import annotations

from typing import Any
import time

# Canonical, ordered list of indicator features the model expects.
# MUST match exactly what the trained model was trained on (see train_model.py)
# Do NOT reorder or modify without retraining the model.
FEATURE_COLUMNS = [
    "confidence",        # 0-100: reversal detection confidence
    "ema_9",            # 1m EMA 9
    "ema_20",           # 1m EMA 20
    "ema_50",           # 1m EMA 50
    "ema_200",          # 1m EMA 200
    "mfi",              # Money Flow Index (0-100)
    "rsi",              # RSI (0-100)
    "adx",              # Average Directional Index (0-100)
    "price_trend",      # -100 to +100: price momentum
    "mfi_trend",        # -100 to +100: money flow momentum
    "signal",           # 1.0 for BUY, 0.0 for SELL
]


class RisingFloorDetector:
    """Detects rising floor (LONG) or falling ceiling (SHORT) progression.
    
    Buffers rapid successive analyze events into "clusters" (ignores noise within 60s),
    then tracks if subsequent clusters show progression in the right direction.
    Provides confidence boost for each successive cluster in the trend direction.
    
    In-memory only; resets on Brain restart.
    """
    
    def __init__(self, cluster_window_seconds: int = 60, max_clusters_per_direction: int = 10):
        self.cluster_window = cluster_window_seconds
        self.max_clusters = max_clusters_per_direction
        # Structure: {symbol: {'LONG': {...}, 'SHORT': {...}}}
        self.data: dict[str, dict[str, dict]] = {}
    
    def get_boost(self, symbol: str, close: float, direction: str) -> dict[str, Any]:
        """
        Process a new analyze event and return rising floor boost.
        
        Args:
            symbol: Trading pair (e.g., 'ETH')
            close: Current close price
            direction: 'LONG' or 'SHORT'
            
        Returns:
            {
                'boost': 0-4 (confidence boost percentage),
                'reason': str explaining the boost,
                'cluster_count': int (how many finalized clusters),
                'progression': bool (is this cluster showing progression?)
            }
        """
        current_time = time.time()
        
        # Initialize symbol tracking
        if symbol not in self.data:
            self.data[symbol] = {
                'LONG': {'finalized_clusters': [], 'current_cluster': None},
                'SHORT': {'finalized_clusters': [], 'current_cluster': None}
            }
        
        dir_data = self.data[symbol][direction]
        
        # Initialize or check if we need to finalize current cluster
        if dir_data['current_cluster'] is None:
            dir_data['current_cluster'] = {
                'start_time': current_time,
                'closes': [close]
            }
            return {'boost': 0, 'reason': 'First cluster (baseline)', 'cluster_count': 0, 'progression': False}
        
        time_since_start = current_time - dir_data['current_cluster']['start_time']
        
        if time_since_start > self.cluster_window:
            # Finalize current cluster
            avg_close = sum(dir_data['current_cluster']['closes']) / len(dir_data['current_cluster']['closes'])
            dir_data['finalized_clusters'].append(avg_close)
            
            # Trim history to prevent unbounded growth
            if len(dir_data['finalized_clusters']) > self.max_clusters:
                dir_data['finalized_clusters'] = dir_data['finalized_clusters'][-self.max_clusters:]
            
            # Start new cluster with current price
            dir_data['current_cluster'] = {
                'start_time': current_time,
                'closes': [close]
            }
            
            # Calculate boost based on progression
            boost_data = self._calculate_progression_boost(dir_data['finalized_clusters'], direction)
            boost_data['cluster_count'] = len(dir_data['finalized_clusters'])
            return boost_data
        else:
            # Still within cluster window - add to current cluster buffer
            dir_data['current_cluster']['closes'].append(close)
            return {'boost': 0, 'reason': 'In cluster (buffering noise)', 'cluster_count': len(dir_data['finalized_clusters']), 'progression': False}
    
    def _calculate_progression_boost(self, finalized_clusters: list[float], direction: str) -> dict[str, Any]:
        """
        Detect rising floor (LONG: prices climbing higher between clusters)
        or falling ceiling (SHORT: prices dropping lower between clusters).
        
        Returns +1% for each successive cluster that shows progression.
        Stops counting when progression breaks.
        """
        if not finalized_clusters or len(finalized_clusters) < 2:
            return {'boost': 0, 'reason': 'Need 2+ clusters to detect progression', 'progression': False}
        
        try:
            boost = 0
            progression_count = 0
            
            # Walk backward from most recent cluster, counting consecutive progression
            for i in range(len(finalized_clusters) - 1, 0, -1):
                current = finalized_clusters[i]
                previous = finalized_clusters[i - 1]
                
                # Defensive: ensure values are numbers
                if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
                    break
                
                if direction == 'LONG':
                    # Rising floor: current price > previous price
                    if current > previous:
                        boost += 1
                        progression_count += 1
                    else:
                        break  # Progression stopped
                elif direction == 'SHORT':
                    # Falling ceiling: current price < previous price
                    if current < previous:
                        boost += 1
                        progression_count += 1
                    else:
                        break  # Progression stopped
            
            boost = min(boost, 4)  # Cap at 4%
            
            if progression_count == 0:
                reason = f"No {direction.lower()} progression (price moved wrong direction)"
            elif progression_count == 1:
                reason = f"{progression_count} successive {direction.lower()} cluster (+1% boost)"
            else:
                reason = f"{progression_count} consecutive {direction.lower()} clusters (+{progression_count}% boost, momentum building!)"
            
            return {
                'boost': boost,
                'reason': reason,
                'progression': progression_count > 0
            }
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Error in progression boost calculation: {e}")
            return {'boost': 0, 'reason': f'Calculation error (safe fallback)', 'progression': False}


# Global detector instance - persists during Brain session
_rising_floor_detector = RisingFloorDetector(cluster_window_seconds=300, max_clusters_per_direction=10)


def get_rising_floor_boost(symbol: str, close: float, direction: str) -> dict[str, Any]:
    """
    Get confidence boost from rising floor/falling ceiling detection.
    Call this in Step 5d of the analyze endpoint.
    Includes error handling to ensure function never crashes.
    """
    try:
        if not symbol or close is None or not direction:
            return {'boost': 0, 'reason': 'Invalid parameters', 'progression': False}
        
        result = _rising_floor_detector.get_boost(symbol, close, direction)
        
        # Validate result structure
        if not isinstance(result, dict) or 'boost' not in result:
            return {'boost': 0, 'reason': 'Invalid result structure', 'progression': False}
        
        return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error in get_rising_floor_boost: {e}", exc_info=True)
        return {'boost': 0, 'reason': 'Error in rising floor detection (safe fallback)', 'progression': False}


class PayloadValidationError(ValueError):
    """Raised when an incoming /analyze request is missing required fields."""


def _get_multi_tf_ema200(symbol: str, candle_store) -> dict[str, float]:
    """Get EMA200 values from all available timeframes for a symbol.
    
    Returns dict like: {"1m": 2500.5, "5m": 2500.2, "15m": 2499.8, ...}
    """
    ema200_by_tf = {}
    timeframes = ["1m", "5m", "15m", "30m", "1h", "4h", "12h"]
    
    try:
        for tf in timeframes:
            latest_candle = candle_store.get_latest_candle(symbol, tf)
            if latest_candle and latest_candle.get("indicators"):
                ema200 = latest_candle["indicators"].get("ema_200")
                if ema200:
                    ema200_by_tf[tf] = ema200
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Could not fetch multi-TF EMA200: {e}")
    
    return ema200_by_tf


# Weekly pivots only change once a new ISO week begins - cache per (symbol, week_start)
_WEEKLY_PIVOT_CACHE: dict[tuple[str, str], dict] = {}


def calculate_weekly_pivots(symbol: str, candle_store) -> dict[str, Any]:
    """Calculate standard weekly pivot points from the prior completed calendar week.

    Uses ISO calendar weeks (Monday 00:00 UTC start). PP/R1-R3/S1-S3 are fixed
    for the entire current week regardless of timeframe, so results are cached
    per (symbol, week_start) and only recomputed once a new week begins.

    Returns dict with pp, r1, r2, r3, s1, s2, s3, week_start, and the prior
    week's high/low/close. Returns {} if insufficient candle history exists yet.
    """
    from datetime import datetime, timedelta, timezone

    if not candle_store:
        return {}

    try:
        now = datetime.now(timezone.utc)
        current_week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        prev_week_start = current_week_start - timedelta(days=7)

        cache_key = (symbol, current_week_start.date().isoformat())
        if cache_key in _WEEKLY_PIVOT_CACHE:
            return _WEEKLY_PIVOT_CACHE[cache_key]

        # Reconstruct the prior week's H/L/C from stored candles (prefer finer TF, fall back to coarser)
        candles: list[dict] = []
        for tf in ["1h", "4h", "12h"]:
            fetched = candle_store.db.get_candles(symbol, tf, since=prev_week_start, limit=200)
            in_prior_week = []
            for c in fetched:
                ts = c["timestamp"]
                ts_dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(ts)
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                if ts_dt < current_week_start:
                    in_prior_week.append(c)
            if len(in_prior_week) >= 4:
                candles = in_prior_week
                break

        if not candles:
            return {}

        high = max(float(c["high"]) for c in candles)
        low = min(float(c["low"]) for c in candles)
        close = float(candles[-1]["close"])

        pp = (high + low + close) / 3
        r1 = (2 * pp) - low
        s1 = (2 * pp) - high
        r2 = pp + (high - low)
        s2 = pp - (high - low)
        r3 = high + 2 * (pp - low)
        s3 = low - 2 * (high - pp)

        pivots = {
            "pp": round(pp, 2),
            "r1": round(r1, 2),
            "r2": round(r2, 2),
            "r3": round(r3, 2),
            "s1": round(s1, 2),
            "s2": round(s2, 2),
            "s3": round(s3, 2),
            "week_start": current_week_start.date().isoformat(),
            "prior_week_high": round(high, 2),
            "prior_week_low": round(low, 2),
            "prior_week_close": round(close, 2),
        }
        _WEEKLY_PIVOT_CACHE[cache_key] = pivots
        return pivots
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error calculating weekly pivots: {e}", exc_info=True)
        return {}


def _pivot_limit_entry(close: float, signal_type: str, pivots: dict) -> dict | None:
    """Check if price is approaching a weekly pivot level in the trade's favor.

    If so, suggests a LIMIT entry placed at that level (a likely reaction zone)
    instead of chasing the current market price. Returns None if no pivots are
    available or price isn't within the "approaching" band of any level.
    """
    if not pivots or "pp" not in pivots:
        return None

    NEAR_MIN_PCT = 0.15  # already essentially at the level - just enter now
    NEAR_MAX_PCT = 2.0   # too far away for the level to be actionable yet

    pp = pivots["pp"]

    if signal_type == "short":
        # Resistance levels above price are good LIMIT SELL zones
        candidates = [("R1", pivots.get("r1")), ("R2", pivots.get("r2")), ("R3", pivots.get("r3"))]
        if close < pp:
            candidates.insert(0, ("PP", pp))
        above = [(name, lvl) for name, lvl in candidates if lvl is not None and lvl > close]
        if not above:
            return None
        name, level = min(above, key=lambda pair: pair[1])
        distance_pct = (level - close) / close * 100
        if NEAR_MIN_PCT <= distance_pct <= NEAR_MAX_PCT:
            return {
                "entry_recommendation": "LIMIT_AT_PIVOT",
                "entry_price": level,
                "entry_reason": f"Approaching weekly {name} (${level:.2f}) - place LIMIT SHORT there instead of chasing market",
            }
    else:
        # Support levels below price are good LIMIT BUY zones
        candidates = [("S1", pivots.get("s1")), ("S2", pivots.get("s2")), ("S3", pivots.get("s3"))]
        if close > pp:
            candidates.insert(0, ("PP", pp))
        below = [(name, lvl) for name, lvl in candidates if lvl is not None and lvl < close]
        if not below:
            return None
        name, level = max(below, key=lambda pair: pair[1])
        distance_pct = (close - level) / close * 100
        if NEAR_MIN_PCT <= distance_pct <= NEAR_MAX_PCT:
            return {
                "entry_recommendation": "LIMIT_AT_PIVOT",
                "entry_price": level,
                "entry_reason": f"Approaching weekly {name} (${level:.2f}) - place LIMIT LONG there instead of chasing market",
            }

    return None


def calculate_12h_momentum_boost(symbol: str, signal_type: str, candle_store) -> dict[str, Any]:
    """Calculate confidence boost based on strong 12H trend momentum.
    
    "Buy the dip" and "go with the flow" opportunities: when 12H shows strong 
    directional trend with strong momentum indicators, small setup anomalies 
    on smaller timeframes become low-risk high-probability entries.
    
    Logic:
    - ADX > 25: Trend exists
    - ADX > 50: Strong trend (larger boost potential)
    - PDI > MDI * 2: Strong upward momentum (for LONG signals)
    - PDI > MDI * 3: Very strong upward momentum (bigger boost)
    - MDI > PDI * 2: Strong downward momentum (for SHORT signals)
    - MDI > PDI * 3: Very strong downward momentum (bigger boost)
    
    Returns dict with:
        - has_12h_trend: bool (ADX > 25)
        - momentum_direction: str (long/short/none)
        - momentum_ratio: float (stronger ratio = more conviction)
        - adx_strength: float (ADX value)
        - confidence_boost: float (0.0-0.15, amount to add to confidence)
        - boost_reason: str (explanation)
    """
    boost_data = {
        "has_12h_trend": False,
        "momentum_direction": "none",
        "momentum_ratio": 1.0,
        "adx_strength": 0.0,
        "confidence_boost": 0.0,
        "boost_reason": "No 12H trend"
    }
    
    try:
        if not candle_store:
            return boost_data
        
        latest_12h = candle_store.get_latest_candle(symbol, "12h")
        if not latest_12h or not latest_12h.get("indicators"):
            return boost_data
        
        indicators = latest_12h["indicators"]
        
        # Extract ADX and directional indicators
        adx = indicators.get("adx", 0.0)
        pdi = indicators.get("pdi", 0.0)  # Positive Directional Index
        mdi = indicators.get("mdi", 0.0)  # Negative Directional Index
        
        # ADX must be > 25 to have a trend
        if adx <= 25.0:
            return boost_data
        
        boost_data["has_12h_trend"] = True
        boost_data["adx_strength"] = adx
        
        # Determine momentum direction and ratio
        if pdi > mdi:
            # Uptrend on 12H
            momentum_ratio = pdi / mdi if mdi > 0 else pdi / 0.1  # Avoid div by zero
            boost_data["momentum_direction"] = "long"
            boost_data["momentum_ratio"] = momentum_ratio
            
            # Only apply boost if signal_type matches (LONG signal in uptrend)
            if signal_type == "long":
                # Calculate boost based on ratio and ADX strength
                if momentum_ratio >= 3.0:
                    # Very strong momentum (PDI > MDI * 3)
                    if adx > 50:
                        boost = 0.15  # Max boost: 15%
                        reason = f"Very strong 12H uptrend (ADX={adx:.0f}, PDI/MDI={momentum_ratio:.1f}x)"
                    else:
                        boost = 0.10  # Medium boost: 10%
                        reason = f"Very strong 12H uptrend (ADX={adx:.0f}, PDI/MDI={momentum_ratio:.1f}x)"
                elif momentum_ratio >= 2.0:
                    # Strong momentum (PDI > MDI * 2)
                    if adx > 50:
                        boost = 0.10  # Medium boost: 10%
                        reason = f"Strong 12H uptrend (ADX={adx:.0f}, PDI/MDI={momentum_ratio:.1f}x)"
                    else:
                        boost = 0.06  # Small boost: 6%
                        reason = f"Strong 12H uptrend (ADX={adx:.0f}, PDI/MDI={momentum_ratio:.1f}x)"
                else:
                    # Mild uptrend
                    boost = 0.03  # Tiny boost: 3%
                    reason = f"12H uptrend trend present (ADX={adx:.0f})"
                
                boost_data["confidence_boost"] = boost
                boost_data["boost_reason"] = reason
        
        elif mdi > pdi:
            # Downtrend on 12H
            momentum_ratio = mdi / pdi if pdi > 0 else mdi / 0.1  # Avoid div by zero
            boost_data["momentum_direction"] = "short"
            boost_data["momentum_ratio"] = momentum_ratio
            
            # Only apply boost if signal_type matches (SHORT signal in downtrend)
            if signal_type == "short":
                # Calculate boost based on ratio and ADX strength
                if momentum_ratio >= 3.0:
                    # Very strong momentum (MDI > PDI * 3)
                    if adx > 50:
                        boost = 0.15  # Max boost: 15%
                        reason = f"Very strong 12H downtrend (ADX={adx:.0f}, MDI/PDI={momentum_ratio:.1f}x)"
                    else:
                        boost = 0.10  # Medium boost: 10%
                        reason = f"Very strong 12H downtrend (ADX={adx:.0f}, MDI/PDI={momentum_ratio:.1f}x)"
                elif momentum_ratio >= 2.0:
                    # Strong momentum (MDI > PDI * 2)
                    if adx > 50:
                        boost = 0.10  # Medium boost: 10%
                        reason = f"Strong 12H downtrend (ADX={adx:.0f}, MDI/PDI={momentum_ratio:.1f}x)"
                    else:
                        boost = 0.06  # Small boost: 6%
                        reason = f"Strong 12H downtrend (ADX={adx:.0f}, MDI/PDI={momentum_ratio:.1f}x)"
                else:
                    # Mild downtrend
                    boost = 0.03  # Tiny boost: 3%
                    reason = f"12H downtrend trend present (ADX={adx:.0f})"
                
                boost_data["confidence_boost"] = boost
                boost_data["boost_reason"] = reason
        
        return boost_data
    
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Error calculating 12H momentum boost: {e}")
        return boost_data


def calculate_optimal_entry(
    payload: dict,
    trade_type: str = "SCALP",
    candle_store = None,
    weekly_pivots: dict | None = None,
) -> dict[str, Any]:
    """Calculate optimal entry price and EMA200-based take profit targets.
    
    Uses EMA200 levels across multiple timeframes as natural return points.
    Strategy varies by trade type:
    - SCALP: Conservative = EMA200 on 1m/5m/15m, Aggressive = break below
    - SWING: Conservative = EMA200 on 30m/1h, Aggressive = EMA200 on 4h/12h
    - TREND_START: Conservative = EMA200 on 4h/12h, Aggressive = higher TF
    
    If weekly_pivots is provided and price is approaching a pivot level in the
    trade's favor, recommends a LIMIT_AT_PIVOT entry at that level instead of
    chasing the market, and uses pivot levels to tighten the conservative TP.
    
    Args:
        payload: Full payload dict with symbol, signal_type, indicators
        trade_type: Type of trade (SCALP, SWING, TREND_START) for TP calculation
        candle_store: Database access to fetch multi-timeframe EMA200 data
        weekly_pivots: Optional weekly pivot levels from calculate_weekly_pivots()
        
    Returns:
        Dict with entry_recommendation, entry_price, stop_loss, take_profit targets
    """
    try:
        # Extract indicators from either nested or flat structure
        if "indicators" in payload and isinstance(payload["indicators"], dict):
            indicators = payload["indicators"]
        else:
            # Flat format - extract from top level
            indicators = payload
        
        symbol = payload.get("symbol", "ETH")
        signal_type = payload.get("signal_type", "long")
        
        rsi = indicators.get("rsi", 50.0)
        ema_9 = indicators.get("ema_9", 0.0)
        ema_20 = indicators.get("ema_20", 0.0)
        ema_200 = indicators.get("ema_200", 0.0)
        atr = indicators.get("atr", 0.0)
        close = indicators.get("close", ema_9)  # Use EMA9 as fallback
        volume = indicators.get("volume", 0.0)
        
        # Get multi-timeframe EMA200 levels for TP calculation
        ema200_by_tf = {}
        if candle_store:
            ema200_by_tf = _get_multi_tf_ema200(symbol, candle_store)
        
        # Check if price is approaching a weekly pivot level in the trade's favor
        pivot_override = _pivot_limit_entry(close, signal_type, weekly_pivots) if weekly_pivots else None
        
        # Calculate take profit targets based on trade type and EMA200 levels
        if signal_type == "long":
            # LONG signal entry logic
            distance_from_ema9 = ((ema_9 - close) / ema_9 * 100) if ema_9 else 0
            
            # Entry recommendation
            if pivot_override:
                recommendation = pivot_override["entry_recommendation"]
                entry_price = pivot_override["entry_price"]
                entry_reason = pivot_override["entry_reason"]
            elif distance_from_ema9 > 0.5:  # Below EMA9 = good entry
                # For SWING trades, prefer waiting for pullback; SCALP/TREND_START can enter immediately
                if trade_type == "SWING":
                    recommendation = "WAIT_FOR_DIP"
                    entry_price = ema_9 * 0.998  # Target near EMA9
                    entry_reason = "Wait for pullback to EMA9 - better risk/reward for swing hold"
                else:
                    recommendation = "ENTER_NOW"
                    entry_price = close
                    entry_reason = "Price below EMA9 - strong entry zone"
            elif 30 <= rsi <= 50:  # RSI in good zone for long
                # For SWING trades, be more conservative
                if trade_type == "SWING":
                    recommendation = "WAIT_FOR_DIP" if rsi > 60 else "ENTER_NOW"
                    entry_price = ema_9 * 0.998 if rsi > 60 else close
                    entry_reason = "Optimal RSI zone, wait for slight pullback" if rsi > 60 else "RSI optimal for long entry"
                else:
                    recommendation = "ENTER_NOW"
                    entry_price = close
                    entry_reason = "RSI optimal for long entry"
            elif rsi > 65:  # Overbought
                recommendation = "WAIT_FOR_DIP"
                entry_price = ema_9 * 0.995  # Pullback target
                entry_reason = "Price overbought, wait for pullback to EMA9"
            elif rsi < 30:  # Oversold
                # SWING: Wait for bounce confirmation; SCALP: Enter immediately
                if trade_type == "SWING":
                    recommendation = "WAIT_FOR_DIP"
                    entry_price = ema_9 * 0.998
                    entry_reason = "Oversold bounce expected - wait for confirmation on pullback"
                else:
                    recommendation = "ENTER_NOW"
                    entry_price = close
                    entry_reason = "Oversold conditions - strong bounce potential"
            else:
                recommendation = "ENTER_NOW"
                entry_price = close
                entry_reason = "Normal entry zone"
            
            stop_loss = entry_price - (atr * 1.5)  # 1.5x ATR below entry for LONG
            
            # Calculate take profits based on trade type using EMA200 levels
            if trade_type == "SCALP":
                # SCALP: Conservative = return to EMA200 on 1m/5m/15m
                conservative_emas = [ema200_by_tf.get(tf) for tf in ["1m", "5m", "15m"] if tf in ema200_by_tf]
                if conservative_emas:
                    take_profit_conservative = min(conservative_emas)  # Closest EMA200 level
                else:
                    take_profit_conservative = entry_price + (atr * 1.5)  # Fallback to ATR
                
                # Aggressive = break below EMA200 on higher timeframe
                if "30m" in ema200_by_tf:
                    take_profit_aggressive = ema200_by_tf["30m"] * 0.995  # Just below 30m EMA200
                else:
                    take_profit_aggressive = entry_price + (atr * 3.0)  # Fallback
                    
            elif trade_type == "SWING":
                # SWING: Conservative = return to EMA200 on 30m/1h
                conservative_emas = [ema200_by_tf.get(tf) for tf in ["30m", "1h"] if tf in ema200_by_tf]
                if conservative_emas:
                    take_profit_conservative = min(conservative_emas)
                else:
                    take_profit_conservative = entry_price + (atr * 2.0)  # Fallback
                
                # Aggressive = return to EMA200 on 4h/12h
                aggressive_emas = [ema200_by_tf.get(tf) for tf in ["4h", "12h"] if tf in ema200_by_tf]
                if aggressive_emas:
                    take_profit_aggressive = min(aggressive_emas)
                else:
                    take_profit_aggressive = entry_price + (atr * 4.0)  # Fallback
                    
            else:  # TREND_START
                # TREND_START: Conservative = EMA200 on 4h/12h
                conservative_emas = [ema200_by_tf.get(tf) for tf in ["4h", "12h"] if tf in ema200_by_tf]
                if conservative_emas:
                    take_profit_conservative = min(conservative_emas)
                else:
                    take_profit_conservative = entry_price + (atr * 3.0)  # Fallback
                
                # Aggressive = return to higher TF or extended move
                if "12h" in ema200_by_tf:
                    take_profit_aggressive = ema200_by_tf["12h"] * 1.005  # Above 12h EMA200
                else:
                    take_profit_aggressive = entry_price + (atr * 6.0)  # Fallback
                    
        else:  # SHORT signal
            # SHORT signal entry logic
            distance_from_ema9 = ((close - ema_9) / ema_9 * 100) if ema_9 else 0
            
            if pivot_override:
                recommendation = pivot_override["entry_recommendation"]
                entry_price = pivot_override["entry_price"]
                entry_reason = pivot_override["entry_reason"]
            elif distance_from_ema9 > 0.5:  # Above EMA9 = good SHORT entry
                # For SWING trades, prefer waiting for pullback; SCALP/TREND_START can enter immediately
                if trade_type == "SWING":
                    recommendation = "WAIT_FOR_BOUNCE"
                    entry_price = ema_9 * 1.002  # Target near EMA9
                    entry_reason = "Wait for bounce to EMA9 - better risk/reward for swing hold"
                else:
                    recommendation = "ENTER_NOW"
                    entry_price = close
                    entry_reason = "Price above EMA9 - strong SHORT entry"
            elif 50 <= rsi <= 70:  # RSI good for short
                # For SWING trades, be more conservative
                if trade_type == "SWING":
                    recommendation = "WAIT_FOR_BOUNCE" if rsi < 40 else "ENTER_NOW"
                    entry_price = ema_9 * 1.002 if rsi < 40 else close
                    entry_reason = "Optimal RSI zone, wait for slight bounce" if rsi < 40 else "RSI optimal for SHORT entry"
                else:
                    recommendation = "ENTER_NOW"
                    entry_price = close
                    entry_reason = "RSI optimal for SHORT entry"
            elif rsi < 35:  # Oversold
                recommendation = "WAIT_FOR_BOUNCE"
                entry_price = ema_9 * 1.005  # Bounce target
                entry_reason = "Oversold, wait for bounce to EMA9"
            elif rsi > 70:  # Overbought
                # SWING: Wait for pullback confirmation; SCALP: Enter immediately
                if trade_type == "SWING":
                    recommendation = "WAIT_FOR_BOUNCE"
                    entry_price = ema_9 * 1.002
                    entry_reason = "Overbought pullback expected - wait for confirmation on bounce"
                else:
                    recommendation = "ENTER_NOW"
                    entry_price = close
                    entry_reason = "Overbought conditions - strong reversal"
            else:
                recommendation = "ENTER_NOW"
                entry_price = close
                entry_reason = "Normal entry zone"
            
            stop_loss = entry_price + (atr * 1.5)  # 1.5x ATR above entry for SHORT
            
            # SHORT: Same EMA200 logic but inverted (prices go down)
            if trade_type == "SCALP":
                # SCALP: Conservative = return to EMA200 on 1m/5m/15m
                conservative_emas = [ema200_by_tf.get(tf) for tf in ["1m", "5m", "15m"] if tf in ema200_by_tf]
                if conservative_emas:
                    take_profit_conservative = max(conservative_emas)  # Highest EMA200 level (for SHORT)
                else:
                    take_profit_conservative = entry_price - (atr * 1.5)
                
                # Aggressive = break above EMA200
                if "30m" in ema200_by_tf:
                    take_profit_aggressive = ema200_by_tf["30m"] * 1.005  # Just above 30m EMA200
                else:
                    take_profit_aggressive = entry_price - (atr * 3.0)
                    
            elif trade_type == "SWING":
                conservative_emas = [ema200_by_tf.get(tf) for tf in ["30m", "1h"] if tf in ema200_by_tf]
                if conservative_emas:
                    take_profit_conservative = max(conservative_emas)
                else:
                    take_profit_conservative = entry_price - (atr * 2.0)
                
                aggressive_emas = [ema200_by_tf.get(tf) for tf in ["4h", "12h"] if tf in ema200_by_tf]
                if aggressive_emas:
                    take_profit_aggressive = max(aggressive_emas)
                else:
                    take_profit_aggressive = entry_price - (atr * 4.0)
                    
            else:  # TREND_START
                conservative_emas = [ema200_by_tf.get(tf) for tf in ["4h", "12h"] if tf in ema200_by_tf]
                if conservative_emas:
                    take_profit_conservative = max(conservative_emas)
                else:
                    take_profit_conservative = entry_price - (atr * 3.0)
                
                if "12h" in ema200_by_tf:
                    take_profit_aggressive = ema200_by_tf["12h"] * 0.995  # Below 12h EMA200
                else:
                    take_profit_aggressive = entry_price - (atr * 6.0)
        
        # Tighten conservative TP using weekly pivots if a closer reaction level exists
        if weekly_pivots:
            if signal_type == "long":
                resistances = [weekly_pivots.get(k) for k in ("r1", "r2", "r3") if weekly_pivots.get(k)]
                if weekly_pivots.get("pp", 0) > entry_price:
                    resistances.append(weekly_pivots["pp"])
                above = [lvl for lvl in resistances if lvl > entry_price]
                if above:
                    take_profit_conservative = min(take_profit_conservative, min(above))
            else:
                supports = [weekly_pivots.get(k) for k in ("s1", "s2", "s3") if weekly_pivots.get(k)]
                if weekly_pivots.get("pp", 0) < entry_price:
                    supports.append(weekly_pivots["pp"])
                below = [lvl for lvl in supports if lvl < entry_price]
                if below:
                    take_profit_conservative = max(take_profit_conservative, max(below))
        
        return {
            "entry_recommendation": recommendation,
            "entry_price": round(entry_price, 2),
            "entry_reason": entry_reason,
            "stop_loss": round(stop_loss, 2),
            "take_profit_conservative": round(take_profit_conservative, 2),
            "take_profit_aggressive": round(take_profit_aggressive, 2),
            "risk_reward_ratio": round(
                abs(entry_price - take_profit_aggressive) / abs(entry_price - stop_loss), 2
            ) if abs(entry_price - stop_loss) > 0 else 0,
            "tp_method": "EMA200-based" if ema200_by_tf else "ATR-based",
            "ema200_levels_used": ema200_by_tf if ema200_by_tf else None,
            "weekly_pivots": weekly_pivots if weekly_pivots else None,
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error calculating entry: {e}", exc_info=True)
        return {
            "entry_recommendation": "ERROR",
            "entry_price": 0.0,
            "entry_reason": str(e),
            "stop_loss": 0.0,
            "take_profit_conservative": 0.0,
            "take_profit_aggressive": 0.0,
            "risk_reward_ratio": 0.0,
            "tp_method": "ERROR",
            "ema200_levels_used": None,
        }


def classify_trade_type(
    payload: dict,
    tf_alignment: dict,
    confluence_level: str = "none",  # NEW: confluence level from calculate_reversal_confluence
) -> dict[str, Any]:
    """Classify trade as scalp, swing, or trend-start with grade A/B/C.
    
    SCALP: High momentum, small range, quick exit (5-30 min)
    SWING: Medium momentum, medium range, medium hold (2-5 days)
    TREND_START: Lower momentum initially, expanding range, long hold
    
    Args:
        payload: Full payload dict with symbol, signal_type, indicators
        tf_alignment: TF alignment dict with score and individual TF flags
        confluence_level: Confluence strength ('very_high', 'high', 'medium', 'low', 'none')
        
    Returns:
        Dict with trade_type, grade, characteristics, hold_time_estimate
    """
    try:
        # Extract indicators from either nested or flat structure
        if "indicators" in payload and isinstance(payload["indicators"], dict):
            indicators = payload["indicators"]
        else:
            # Flat format - extract from top level
            indicators = payload
        
        rsi = indicators.get("rsi", 50.0)
        atr = indicators.get("atr", 0.0)
        macd = indicators.get("macd", 0.0)
        macd_signal = indicators.get("macd_signal", 0.0)
        volume = indicators.get("volume", 0.0)
        ema_9 = indicators.get("ema_9", 0.0)
        ema_20 = indicators.get("ema_20", 0.0)
        ema_200 = indicators.get("ema_200", 0.0)
        
        # Get TF alignment strength
        tf_score = tf_alignment.get("tf_alignment_score", 0)
        
        # Graded, EMA-based quality caps the loose boolean score so e.g. "price below
        # the 12h/4h EMA200" can no longer count as aligned.
        tfq = tf_alignment.get("tf_quality")
        tf_quality = (
            sum(w * tfq.get(tf, 0.0) for tf, w in TF_WEIGHTS["SWING"].items())
            if tfq else None
        )
        if tf_quality is not None:
            tf_score = min(tf_score, round(tf_quality * 4))
        
        # Momentum indicators
        macd_momentum = macd - macd_signal  # Positive = momentum
        rsi_extremity = abs(rsi - 50.0)  # How extreme is RSI
        
        # Trend structure: how aligned are EMAs
        if ema_200:
            macro_trend_strength = (ema_20 - ema_200) / ema_200 * 100
        else:
            macro_trend_strength = 0
        
        # Decision logic
        is_high_momentum = rsi_extremity > 22  # Strong RSI signal (22+ from neutral)
        # MACD is not sent by the Node server, so also accept ADX as strong momentum.
        adx_val = indicators.get("adx")
        adx_val = adx_val.get("adx") if isinstance(adx_val, dict) else adx_val
        is_strong_macd = abs(macd_momentum) > 2.0 or (adx_val or 0) >= 25

        # Trend direction relative to the signal (positive = trend supports the trade)
        direction = -1 if payload.get("signal_type") == "short" else 1
        directional_trend = macro_trend_strength * direction
        is_extreme_rsi = rsi_extremity > 35  # Very oversold/overbought (35+ from neutral, stricter for SCALP)
        
        # Classify based on characteristics
        # Priority 1: EXTREME reversals (scalps) - high probability, quick
        # NOTE: tf_score == 3 only (not >=) so perfect 4/4 alignment always falls through
        # to Priority 2, which has its own SWING/TREND_START subdivision for that case.
        if is_extreme_rsi and is_strong_macd:
            # SCALP: Extreme conditions = quick reversal bounce (HTF gates applied later)
            trade_type = "SCALP"
            if rsi_extremity > 40:
                grade = "A"  # Very extreme (40+ from neutral)
            elif rsi_extremity > 35:
                grade = "B"
            else:
                grade = "C"
            characteristics = [
                "Extreme momentum reversal",
                "Quick move expected (5-30 min)",
                "Take profits at 1.5-2x ATR",
                "High probability short-term",
            ]
            hold_time = "5-30 minutes"
            risk_level = "Medium"
            
        # Priority 2: PERFECT alignment (4/4 TFs)
        elif tf_score == 4:
            # Perfect alignment - differentiate by momentum
            if is_extreme_rsi and is_strong_macd:
                # Perfect alignment + extreme momentum = swing
                trade_type = "SWING"
                grade = "A"
                characteristics = [
                    "Perfect timeframe alignment",
                    "Extreme momentum entry",
                    "2-5 day hold",
                    "Excellent risk/reward",
                ]
                hold_time = "2-5 days"
                risk_level = "Medium"
            elif directional_trend > 0.5 and (is_strong_macd or is_high_momentum):
                # Perfect alignment + good MACD or momentum + trend = trend start
                trade_type = "TREND_START"
                grade = "A"
                characteristics = [
                    "Perfect multi-TF alignment",
                    "Strong trend confirmation",
                    "Beginning of major move",
                    "High probability long-term setup",
                ]
                hold_time = "1-4 weeks"
                risk_level = "Low"
            else:
                # Perfect alignment but weak momentum = conservative swing
                trade_type = "SWING"
                grade = "B"
                characteristics = [
                    "Perfect multi-TF confirmation",
                    "Conservative entry opportunity",
                    "Medium-term hold",
                ]
                hold_time = "1-5 days"
                risk_level = "Medium"
            
        # Priority 3: GOOD momentum (high RSI extremity) + high alignment - SWING preferred
        # P1 TIGHTENING (2026-10): require 3+ TF alignment (was 2+) and raise
        # RSI extremity floor for Grade C. The old comment "lowered tf_score to 2+
        # to prioritize SWING trades (more signals)" was actively generating the
        # loose/conflicting SWING accepts we want to eliminate.
        elif is_high_momentum and tf_score >= 3:  # was tf_score >= 2
            trade_type = "SWING"
            if rsi_extremity > 25:
                grade = "A"
            elif rsi_extremity > 20:  # was > 18
                grade = "B"
            else:
                grade = "C"
            characteristics = [
                "Strong momentum move",
                "Multi-TF confirmation",
                "2-5 day hold expected",
                "Wait for optimal entry point",
            ]
            hold_time = "2-5 days"
            risk_level = "Medium"
            
        # Priority 4: DEVELOPING trend (3+ TFs aligned)
        elif tf_score >= 3 and directional_trend >= 1.0:
            trade_type = "TREND_START"
            if tf_score == 3:
                grade = "B"
            else:
                grade = "C"
            characteristics = [
                "Multiple timeframe alignment",
                "Trend structure building",
                "Long-term momentum forming",
                "Scale into position",
            ]
            hold_time = "1-4 weeks"
            risk_level = "Low-Medium"
            
        else:
            # Weak setup or fallback
            trade_type = "SCALP"
            grade = "C"
            characteristics = ["Weak setup", "Low probability"]
            hold_time = "5-15 minutes"
            risk_level = "High"
        
        # NEW: Apply confluence-based grade upgrades
        # When multiple recent signals agree, upgrade the grade to reflect conviction
        if tf_quality is not None and tf_quality < 0.5:
            pass  # HTFs against the trade: confluence must not lift the grade
        elif confluence_level == "very_high":
            # 85%+ consensus = strong validation, upgrade any grade
            if grade == "C":
                grade = "B"
            elif grade == "B":
                grade = "A"
            # A stays A
        elif confluence_level == "high":
            # 70-84% consensus = good validation, but don't upgrade B→A
            if grade == "C":
                grade = "B"
            # B and A stay the same
        # medium/low/none confluence: no grade upgrade
        
        return {
            "trade_type": trade_type,
            "grade": grade,
            "hold_time_estimate": hold_time,
            "risk_level": risk_level,
            "characteristics": characteristics,
            "momentum_score": round(rsi_extremity, 1),
            "trend_strength": round(macro_trend_strength, 2),
            "confidence": f"{grade}-tier {trade_type}",
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error classifying trade: {e}", exc_info=True)
        return {
            "trade_type": "ERROR",
            "grade": "N/A",
            "hold_time_estimate": "N/A",
            "risk_level": "ERROR",
            "characteristics": [str(e)],
            "momentum_score": 0.0,
            "trend_strength": 0.0,
            "confidence": "ERROR",
        }



def check_timeframe_alignment(
    db,
    symbol: str,
    signal_type: str,
) -> dict[str, Any]:
    """Check multi-timeframe alignment for a signal.
    
    Primary TFs (for score 0-4): 30m, 1h, 4h, 12h
    Secondary TFs (bonus): 5m, 15m
    
    Args:
        db: Database instance.
        symbol: Trading pair symbol (e.g., 'ETH').
        signal_type: 'long' or 'short'.
        
    Returns:
        Dict with:
            - tf_alignment_score: 0-4 alignment score (primary TFs only)
            - tf_5m_aligned: bool (secondary confirmation)
            - tf_15m_aligned: bool (secondary confirmation)
            - tf_30m_aligned: bool
            - tf_1h_aligned: bool
            - tf_4h_aligned: bool
            - tf_12h_aligned: bool
            - details: human-readable explanation
            - detailed_checks: dict with per-timeframe failure reasons
    """
    try:
        # Fetch latest candles from each timeframe
        candles = {}
        for tf in ["5m", "15m", "30m", "1h", "4h", "12h"]:
            latest = db.get_latest_candle(symbol, tf)
            candles[tf] = latest
        
        # Check if we have primary TF data (30m, 1h, 4h, 12h)
        primary_tfs = ["30m", "1h", "4h", "12h"]
        if not all(candles.get(tf) for tf in primary_tfs):
            missing = [tf for tf in primary_tfs if not candles.get(tf)]
            return {
                "tf_alignment_score": 0,
                "tf_5m_aligned": 0,
                "tf_15m_aligned": 0,
                "tf_30m_aligned": 0,
                "tf_1h_aligned": 0,
                "tf_4h_aligned": 0,
                "tf_12h_aligned": 0,
                "details": f"Missing candle data for: {', '.join(missing)}",
                "detailed_checks": {
                    tf: f"No data" for tf in missing
                }
            }
        
        # Calculate alignment for each timeframe with detailed feedback
        detailed_checks = {}
        
        aligned_5m, reason_5m = _check_5m_alignment_with_reason(candles["5m"], signal_type)
        aligned_15m, reason_15m = _check_15m_alignment_with_reason(candles["15m"], signal_type)
        aligned_30m, reason_30m = _check_30m_alignment_with_reason(candles["30m"], signal_type)
        aligned_1h, reason_1h = _check_1h_alignment_with_reason(candles["1h"], signal_type)
        aligned_4h, reason_4h = _check_4h_alignment_with_reason(candles["4h"], signal_type)
        aligned_12h, reason_12h = _check_12h_alignment_with_reason(candles["12h"], signal_type)
        
        detailed_checks["5m"] = reason_5m
        detailed_checks["15m"] = reason_15m
        detailed_checks["30m"] = reason_30m
        detailed_checks["1h"] = reason_1h
        detailed_checks["4h"] = reason_4h
        detailed_checks["12h"] = reason_12h
        
        # Primary score (0-4): 30m, 1h, 4h, 12h
        score = sum([aligned_30m, aligned_1h, aligned_4h, aligned_12h])
        
        # Build explanation
        details = f"""
5m: {'✓' if aligned_5m else '✗'} | 
15m: {'✓' if aligned_15m else '✗'} | 
30m: {'✓' if aligned_30m else '✗'} | 
1h: {'✓' if aligned_1h else '✗'} | 
4h: {'✓' if aligned_4h else '✗'} | 
12h: {'✓' if aligned_12h else '✗'}
        """.strip()
        
        return {
            "tf_alignment_score": score,
            "tf_5m_aligned": 1 if aligned_5m else 0,
            "tf_15m_aligned": 1 if aligned_15m else 0,
            "tf_30m_aligned": 1 if aligned_30m else 0,
            "tf_1h_aligned": 1 if aligned_1h else 0,
            "tf_4h_aligned": 1 if aligned_4h else 0,
            "tf_12h_aligned": 1 if aligned_12h else 0,
            "details": details,
            "detailed_checks": detailed_checks,
            "tf_quality": _tf_quality(candles, signal_type),
            "scalp_turn": _scalp_turn(candles, signal_type),
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error checking TF alignment: {e}", exc_info=True)
        return {
            "tf_alignment_score": 0,
            "tf_5m_aligned": 0,
            "tf_15m_aligned": 0,
            "tf_30m_aligned": 0,
            "tf_1h_aligned": 0,
            "tf_4h_aligned": 0,
            "tf_12h_aligned": 0,
            "details": f"Error checking alignment: {str(e)}",
            "detailed_checks": {"error": str(e)},
        }


# ---------------------------------------------------------------------------
# Strict weighted TF quality (SWING / TREND_START / SCALP decision tightening)
# ---------------------------------------------------------------------------

# Per-timeframe weights (each set sums to 1.0).
# SWING / TREND_START lean on the higher timeframes; SCALP on the lower ones.
TF_WEIGHTS = {
    "SWING":  {"5m": 0.00, "15m": 0.05, "30m": 0.10, "1h": 0.20, "4h": 0.30, "12h": 0.35},
    "SCALP":  {"5m": 0.25, "15m": 0.25, "30m": 0.25, "1h": 0.15, "4h": 0.07, "12h": 0.03},
}
TF_WEIGHTS["TREND_START"] = TF_WEIGHTS["SWING"]

# Threshold added on top of the base model threshold: BASE + SPAN * (1 - quality).
# quality=1.0 -> +BASE, quality=0.5 -> +BASE + SPAN/2, ...
SWING_THRESHOLD_BASE = 0.05
SWING_THRESHOLD_SPAN = 0.25
SCALP_THRESHOLD_SPAN = 0.05


def _tf_quality(candles: dict, signal_type: str) -> dict[str, float]:
    """Graded 0..1 trend quality per timeframe, using EMAs and price vs average.

    Unlike the boolean `_check_*_with_reason` helpers, EMA stack / price-vs-EMA200
    positions actually count here. Missing indicators are skipped (not penalised).
    """
    long = signal_type == "long"

    def ok(a, b):
        """1.0 if a is on the signal side of b, else 0.0; None if unknown."""
        if a is None or b is None:
            return None
        return float(a > b) if long else float(a < b)

    def mean(parts):
        parts = [p for p in parts if p is not None]
        return sum(parts) / len(parts) if parts else 0.0

    def g(tf, key):
        return (candles.get(tf) or {}).get(key)

    def adx(tf):
        a = g(tf, "adx")
        a = a.get("adx") if isinstance(a, dict) else a
        return None if a is None else float(a > 20)

    def rsi_ok(tf):
        r = g(tf, "rsi")
        if r is None:
            return None
        return float(r > 20) if long else float(r < 80)  # not blown-out against us

    q = {}
    q["5m"] = mean([ok(g("5m", "ema_9"), g("5m", "ema_20")), ok(g("5m", "close"), g("5m", "ema_9"))])
    for tf in ("15m", "30m"):
        q[tf] = mean([ok(g(tf, "ema_20"), g(tf, "ema_50")), ok(g(tf, "close"), g(tf, "ema_9"))])
    q["1h"] = mean([
        ok(g("1h", "ema_20"), g("1h", "ema_50")),
        ok(g("1h", "ema_50"), g("1h", "ema_200")),
        ok(g("1h", "close"), g("1h", "ema_200")),
        adx("1h"),
    ])
    q["4h"] = mean([
        ok(g("4h", "ema_50"), g("4h", "ema_200")),
        ok(g("4h", "close"), g("4h", "ema_200")),
        ok(g("4h", "close"), g("4h", "ema_50")),
        rsi_ok("4h"),
    ])
    q["12h"] = mean([
        ok(g("12h", "close"), g("12h", "ema_200")),
        ok(g("12h", "ema_50"), g("12h", "ema_200")),
        ok(g("12h", "close"), g("12h", "ema_50")),
    ])
    return {tf: round(v, 3) for tf, v in q.items()}


def _scalp_turn(candles: dict, signal_type: str) -> dict[str, Any]:
    """Evidence that a reversal scalp is turning (exhaustion), not still falling.

    Per short timeframe (5m, 15m) counts: RSI/MFI at an extreme, price reclaimed
    EMA9, and a rejection candle (closed up, in the top half of its range).
    Also reports whether the 1h is still trending hard against the trade.
    """
    long = signal_type == "long"

    def evidence(c):
        if not c:
            return 0
        n = 0
        rsi, mfi = c.get("rsi"), c.get("mfi")
        if (rsi is not None and (rsi <= 30 if long else rsi >= 70)) or \
           (mfi is not None and (mfi <= 20 if long else mfi >= 80)):
            n += 1
        close, ema9 = c.get("close"), c.get("ema_9")
        if close is not None and ema9 is not None and (close > ema9 if long else close < ema9):
            n += 1
        o, h, l = c.get("open"), c.get("high"), c.get("low")
        if None not in (close, o, h, l) and h > l:
            pos = (close - l) / (h - l)
            if (close > o and pos >= 0.5) if long else (close < o and pos <= 0.5):
                n += 1
        return n

    score = max(evidence(candles.get("5m")), evidence(candles.get("15m")))

    adx, pdi, mdi = _adx_parts(candles.get("1h") or {})
    htf_freefall = bool(
        adx is not None and pdi is not None and mdi is not None and adx >= 25
        and ((mdi > pdi) if long else (pdi > mdi))
    )
    return {"turn_score": score, "htf_freefall": htf_freefall}


SCALP_MIN_TURN_SCORE = 2  # of 3 exhaustion signs on 5m or 15m


def _adx_parts(c: dict) -> tuple:
    """(adx, pdi, mdi) from a candle: payload shape {adx: {adx,pdi,mdi}} or DB-row shape adx/pdi/mdi."""
    a = c.get("adx")
    if isinstance(a, dict):
        return a.get("adx"), a.get("pdi"), a.get("mdi")
    return a, c.get("pdi"), c.get("mdi")


# ---------------------------------------------------------------------------
# SWING = reversal at an area with evidence (never a falling knife)
# ---------------------------------------------------------------------------
SWING_WEIGHTS = {
    "level_held": 0.25,         # support/resistance held repeatedly
    "rising_lows": 0.25,        # latest low higher than the previous (RSI too)
    "confluence": 0.15,         # several agreeing reversal events
    "location": 0.15,           # at a weekly pivot or 4h/12h EMA200
    "not_falling_knife": 0.20,  # price is not still accelerating through the level
}
SWING_MIN_EVIDENCE = 0.60       # below this a SWING falls back to SCALP
SWING_KNIFE_CAP = 0.40          # evidence is capped here if it looks like a falling knife
_SWING_LOOKBACKS = {"1h": 72, "4h": 240}  # hours of history per structure timeframe


def _swing_structure(candles: list[dict], long: bool, fallback_atr: float) -> dict | None:
    """Pivot structure on one timeframe. Mirrored for shorts (pivot highs).

    Values are sign-flipped so "higher is better for the reversal" in both directions.
    """
    if len(candles) < 10:
        return None
    s = 1.0 if long else -1.0
    vals = [(c.get("low") if long else -c.get("high")) for c in candles]
    if any(v is None for v in vals):
        return None
    osc = [(None if c.get("rsi") is None else s * c["rsi"]) for c in candles]
    atr = candles[-1].get("atr") or fallback_atr
    k = 2

    pivots = []
    for i in range(k, len(vals) - k):
        if vals[i] == min(vals[i - k:i + k + 1]) and (not pivots or i - pivots[-1]["i"] > k):
            pivots.append({"i": i, "val": vals[i], "osc": osc[i]})
    # Provisional pivot: signals fire near the extreme, before it is confirmed by k later candles
    if not pivots or len(vals) - 1 - pivots[-1]["i"] > k + 1:
        j = min(range(len(vals) - 3, len(vals)), key=lambda x: vals[x])
        if not pivots or j - pivots[-1]["i"] > k:
            pivots.append({"i": j, "val": vals[j], "osc": osc[j]})

    # Level held: biggest cluster of pivots within 0.5 ATR of each other
    tol = 0.5 * atr
    cluster = max((sum(1 for q in pivots if abs(q["val"] - p["val"]) <= tol) for p in pivots), default=0)
    level = 0.0 if cluster < 2 else (0.5 if cluster == 2 else 1.0)

    # Rising lows: latest pivot above the previous one, ideally with RSI also higher
    rising = 0.0
    last = prev = None
    if len(pivots) >= 2:
        last, prev = pivots[-1], pivots[-2]
        if last["val"] > prev["val"] + 0.1 * atr:
            rising += 0.5
        if last["osc"] is not None and prev["osc"] is not None and last["osc"] > prev["osc"]:
            rising += 0.5

    # Falling-knife checks (3): no big lower low, not breaking through the latest low, not in free-fall
    close_val = s * candles[-1]["close"]
    checks = []
    if last is not None and prev is not None:
        checks.append(last["val"] >= prev["val"] - atr)
    if last is not None:
        checks.append(close_val >= last["val"] - 0.5 * atr)
    adx, pdi, mdi = _adx_parts(candles[-1])
    if None not in (adx, pdi, mdi, candles[-1].get("ema_9")):
        against = (mdi > pdi) if long else (pdi > mdi)
        reclaimed = candles[-1]["close"] > candles[-1]["ema_9"] if long else candles[-1]["close"] < candles[-1]["ema_9"]
        checks.append(not (adx >= 25 and against and not reclaimed))
    passed = sum(checks)
    knife = 1.0 if checks and passed == len(checks) else (0.5 if len(checks) >= 2 and passed == len(checks) - 1 else 0.0)

    return {"level_held": level, "rising_lows": rising, "not_falling_knife": knife,
            "pivots": len(pivots), "cluster": cluster}


def swing_reversal_evidence(db, payload: dict, confluence: dict, weekly_pivots: dict | None) -> dict[str, Any]:
    """Score (0..1) how much evidence there is for a high-quality reversal at this price.

    Components (see SWING_WEIGHTS): a level that held repeatedly, rising lows,
    confluence of reversal events, location at a weekly pivot / HTF EMA200, and
    not being a falling knife. Structure is evaluated on 1h and 4h; level/rising take
    the better timeframe, falling-knife takes the worse.
    """
    from datetime import datetime, timedelta, timezone

    symbol, signal = payload["symbol"], payload["signal_type"]
    long = signal == "long"
    price = payload.get("close")
    now = datetime.now(timezone.utc)

    structs, latest = {}, {}
    for tf, hours in _SWING_LOOKBACKS.items():
        try:
            cs = db.get_candles(symbol, tf, since=now - timedelta(hours=hours), limit=300)
            latest[tf] = cs[-1] if cs else db.get_latest_candle(symbol, tf)
            fallback_atr = (price or 0) * 0.005
            structs[tf] = _swing_structure(cs, long, fallback_atr)
        except Exception:
            structs[tf] = None
    valid = [v for v in structs.values() if v]

    if not valid or price is None:
        return {"score": 0.0, "sufficient": False, "components": {}, "reason": "insufficient_history",
                "min_required": SWING_MIN_EVIDENCE}

    comp = {
        "level_held": max(v["level_held"] for v in valid),
        "rising_lows": max(v["rising_lows"] for v in valid),
        "not_falling_knife": min(v["not_falling_knife"] for v in valid),
    }

    # Confluence with a floor: one event is not confluence
    total = (confluence or {}).get("total_reversals", 0) or 0
    ratio = (confluence or {}).get("confluence_ratio", 0) or 0
    comp["confluence"] = 1.0 if (total >= 3 and ratio >= 0.6) else (0.5 if (total == 2 and ratio >= 0.6) else 0.0)

    # Location: near a weekly pivot (S1-S3 for longs / R1-R3 for shorts) or a 4h/12h EMA200
    h1 = latest.get("1h") or {}
    atr1h = h1.get("atr") or price * 0.005
    near = []
    for key in (("s1", "s2", "s3") if long else ("r1", "r2", "r3")):
        lvl = (weekly_pivots or {}).get(key)
        if lvl and abs(price - lvl) <= atr1h:
            near.append(f"weekly_{key}")
    for tf in ("4h", "12h"):
        c = latest.get(tf) if tf in latest else db.get_latest_candle(symbol, tf)
        if c and c.get("ema_200") and abs(price - c["ema_200"]) <= max(atr1h, c.get("atr") or 0):
            near.append(f"{tf}_ema200")
    comp["location"] = 1.0 if near else 0.0

    score = sum(SWING_WEIGHTS[k] * comp[k] for k in SWING_WEIGHTS)
    if comp["not_falling_knife"] == 0.0:
        score = min(score, SWING_KNIFE_CAP)  # never catch falling knives

    return {
        "score": round(score, 3),
        "sufficient": score >= SWING_MIN_EVIDENCE,
        "min_required": SWING_MIN_EVIDENCE,
        "components": {k: round(v, 2) for k, v in comp.items()},
        "near": near,
        "structure": structs,
    }


def strict_trade_adjustment(tf_alignment: dict, trade_type: str | None, swing_evidence: dict | None = None) -> dict[str, Any]:
    """Trade-type-aware tightening of the accept decision.

    Returns:
        weighted_quality: 0..1 weighted TF quality for this trade type
        threshold_delta:  amount to ADD to the model accept threshold
        hard_reject:      reason string if a SCALP hard-gate trips, else None
    """
    tfq = tf_alignment.get("tf_quality")
    if not tfq or trade_type not in TF_WEIGHTS:
        return {"weighted_quality": None, "threshold_delta": 0.0, "hard_reject": None}

    weights = TF_WEIGHTS[trade_type]
    quality = sum(weights[tf] * tfq.get(tf, 0.0) for tf in weights)
    hard_reject = None
    reversal = trade_type == "SWING" and swing_evidence is not None
    if reversal:
        quality = swing_evidence["score"]  # reversal evidence replaces trend alignment

    if trade_type == "SCALP":
        delta = SCALP_THRESHOLD_SPAN * (1 - quality)
        # Hard gates: situations where a scalp is simply a bad idea.
        # Short TFs against the trade are fine for a reversal scalp if there is
        # evidence of exhaustion/turning; otherwise it is a falling knife.
        turn = tf_alignment.get("scalp_turn") or {}
        turn_score = turn.get("turn_score", 0)
        if tfq.get("5m", 0) == 0 and tfq.get("15m", 0) == 0 and turn_score < SCALP_MIN_TURN_SCORE:
            hard_reject = "scalp_no_reversal_evidence"
        elif tfq.get("4h", 0) == 0 and tfq.get("12h", 0) == 0 and turn.get("htf_freefall") \
                and turn_score < SCALP_MIN_TURN_SCORE:
            hard_reject = "scalp_fighting_higher_tf_freefall"
    else:  # SWING / TREND_START: strict weighted score, no hard gates
        delta = SWING_THRESHOLD_BASE + SWING_THRESHOLD_SPAN * (1 - quality)

    return {
        "weighted_quality": round(quality, 3),
        "threshold_delta": round(delta, 4),
        "hard_reject": hard_reject,
        "reversal_mode": reversal,
    }


HEURISTIC_THRESHOLD_BASE = 0.30


def heuristic_decision(rsi: float, tf_alignment: dict, strict_adj: dict) -> tuple[str, float, float]:
    """Fallback accept/reject when no trained model is loaded.

    confidence = RSI-centredness (1.0 at RSI 50) x weighted TF quality, so a
    setup that fights the higher timeframes can't pass on RSI alone.
    threshold = HEURISTIC_THRESHOLD_BASE + trade-type threshold_delta
    (SWING/TREND_START stricter than SCALP).

    Returns (decision, confidence, threshold).
    """
    if strict_adj.get("reversal_mode"):
        # SWING reversals are expected at RSI extremes: confidence is the evidence score itself
        confidence = strict_adj["weighted_quality"]
        threshold = HEURISTIC_THRESHOLD_BASE + strict_adj.get("threshold_delta", 0.0)
        return ("accept" if confidence >= threshold else "reject"), round(confidence, 4), round(threshold, 4)
    rsi_conf = max(0.0, min(1.0, 1.0 - abs(rsi - 50.0) / 50.0))
    quality = strict_adj.get("weighted_quality")
    if quality is None:  # trade type unknown: fall back to simple TF score
        quality = tf_alignment.get("tf_alignment_score", 0) / 4.0
    confidence = rsi_conf * quality
    threshold = HEURISTIC_THRESHOLD_BASE + strict_adj.get("threshold_delta", 0.0)
    ok = 30.0 <= rsi <= 70.0 and confidence >= threshold and not strict_adj.get("hard_reject")
    return ("accept" if ok else "reject"), round(confidence, 4), round(threshold, 4)


def _check_30m_alignment_with_reason(candle: dict, signal_type: str) -> tuple[bool, str]:
    """30m alignment check with detailed failure reason."""
    if not candle:
        return False, "No 30m candle data"
    
    ema_20 = candle.get("ema_20")
    ema_50 = candle.get("ema_50")
    close = candle.get("close")
    ema_9 = candle.get("ema_9")
    
    if None in [ema_20, ema_50, close, ema_9]:
        missing = [k for k, v in {"ema_20": ema_20, "ema_50": ema_50, "close": close, "ema_9": ema_9}.items() if v is None]
        return False, f"Missing indicators: {missing}"
    
    if signal_type == "long":
        if ema_20 <= ema_50:
            return False, f"EMA20 ({ema_20:.2f}) ≤ EMA50 ({ema_50:.2f}) - need uptrend"
        if close <= ema_9:
            return False, f"Close ({close:.2f}) ≤ EMA9 ({ema_9:.2f}) - need price above MA"
        return True, f"EMA20 > EMA50 and Close > EMA9 ✓"
    else:  # short
        if ema_20 >= ema_50:
            return False, f"EMA20 ({ema_20:.2f}) ≥ EMA50 ({ema_50:.2f}) - need downtrend"
        if close >= ema_9:
            return False, f"Close ({close:.2f}) ≥ EMA9 ({ema_9:.2f}) - need price below MA"
        return True, f"EMA20 < EMA50 and Close < EMA9 ✓"


def _check_1h_alignment_with_reason(candle: dict, signal_type: str) -> tuple[bool, str]:
    """1h alignment check with detailed failure reason."""
    if not candle:
        return False, "No 1h candle data"
    
    ema_20 = candle.get("ema_20")
    ema_50 = candle.get("ema_50")
    ema_200 = candle.get("ema_200")
    # ADX can be either a dict {adx, pdi, mdi} or a scalar value
    adx_data = candle.get("adx", 0)
    adx = adx_data.get("adx", 0) if isinstance(adx_data, dict) else adx_data
    rsi = candle.get("rsi")
    
    if None in [ema_20, ema_50, ema_200, rsi] or not adx:
        missing = [k for k, v in {"ema_20": ema_20, "ema_50": ema_50, "ema_200": ema_200, "rsi": rsi}.items() if v is None]
        if not adx:
            missing.append("adx")
        return False, f"1h: Missing: {missing}" if missing else "1h: No ADX data"
    
    # Check trend strength (ADX) - hard requirement for alignment
    # EMA positions are informational only (confluence can override)
    adx_check = adx > 20
    
    info = []
    if signal_type == "long":
        ema_quality = "✓" if ema_20 > ema_50 > ema_200 else "✗"
        info.append(f"EMA hierarchy: {ema_quality} (20:{ema_20:.0f} 50:{ema_50:.0f} 200:{ema_200:.0f})")
        info.append(f"Trend strength: ADX {adx:.0f} {'✓strong' if adx_check else '✗weak'}")
        info.append(f"RSI: {rsi:.0f} {'✓' if rsi > 40 else '✗'}")
        return adx_check, f"1h: {' | '.join(info)}"
    else:  # short
        ema_quality = "✓" if ema_20 < ema_50 < ema_200 else "✗"
        info.append(f"EMA hierarchy: {ema_quality} (20:{ema_20:.0f} 50:{ema_50:.0f} 200:{ema_200:.0f})")
        info.append(f"Trend strength: ADX {adx:.0f} {'✓strong' if adx_check else '✗weak'}")
        info.append(f"RSI: {rsi:.0f} {'✓' if rsi < 60 else '✗'}")
        return adx_check, f"1h: {' | '.join(info)}"


def _check_4h_alignment_with_reason(candle: dict, signal_type: str) -> tuple[bool, str]:
    """4h alignment check with detailed failure reason."""
    if not candle:
        return False, "No 4h candle data"
    
    ema_50 = candle.get("ema_50")
    ema_200 = candle.get("ema_200")
    close = candle.get("close")
    rsi = candle.get("rsi")
    
    if None in [ema_50, ema_200, close, rsi]:
        missing = [k for k, v in {"ema_50": ema_50, "ema_200": ema_200, "close": close, "rsi": rsi}.items() if v is None]
        return False, f"Missing: {missing}"
    
    # EMA positions are informational (confluence can override)
    # Check for extreme RSI only
    if signal_type == "long":
        ema_ok = ema_50 > ema_200
        price_ok = close > ema_200
        rsi_extreme = rsi < 20  # Oversold = hard reject
        info = f"4h macro: EMA50 vs 200M {'✓' if ema_ok else '✗'} | Price {'✓ above' if price_ok else '✗ below'} 200MA | RSI {rsi:.0f}"
        return not rsi_extreme, info
    else:  # short
        ema_ok = ema_50 < ema_200
        price_ok = close < ema_200
        rsi_extreme = rsi > 80  # Overbought = hard reject
        info = f"4h macro: EMA50 vs 200M {'✓' if ema_ok else '✗'} | Price {'✓ below' if price_ok else '✗ above'} 200MA | RSI {rsi:.0f}"
        return not rsi_extreme, info


def _check_12h_alignment_with_reason(candle: dict, signal_type: str) -> tuple[bool, str]:
    """12h alignment check with detailed failure reason."""
    if not candle:
        return False, "No 12h candle data"
    
    ema_200 = candle.get("ema_200")
    close = candle.get("close")
    
    if None in [ema_200, close]:
        missing = [k for k, v in {"ema_200": ema_200, "close": close}.items() if v is None]
        return False, f"Missing: {missing}"
    
    # Macro trend (12h) - informational only, confluence can override
    # Always return True for data availability, report position diagnostically
    if signal_type == "long":
        price_pos = "✓ above" if close > ema_200 else "✗ below"
        return True, f"12h macro: Price {price_pos} 200MA (EMA200: {ema_200:.0f})"
    else:  # short
        price_pos = "✓ below" if close < ema_200 else "✗ above"
        return True, f"12h macro: Price {price_pos} 200MA (EMA200: {ema_200:.0f})"


def _check_5m_alignment_with_reason(candle: dict, signal_type: str) -> tuple[bool, str]:
    """5m alignment check with detailed failure reason."""
    if not candle:
        return False, "No 5m candle data"
    
    ema_9 = candle.get("ema_9")
    ema_20 = candle.get("ema_20")
    
    if None in [ema_9, ema_20]:
        missing = [k for k, v in {"ema_9": ema_9, "ema_20": ema_20}.items() if v is None]
        return False, f"Missing: {missing}"
    
    if signal_type == "long":
        status = "✓ bullish" if ema_9 > ema_20 else "✗ bearish"
        return True, f"5m trend: {status} (EMA9: {ema_9:.0f}, EMA20: {ema_20:.0f})"
    else:  # short
        status = "✓ bearish" if ema_9 < ema_20 else "✗ bullish"
        return True, f"5m trend: {status} (EMA9: {ema_9:.0f}, EMA20: {ema_20:.0f})"


def _check_15m_alignment_with_reason(candle: dict, signal_type: str) -> tuple[bool, str]:
    """15m alignment check with detailed failure reason."""
    if not candle:
        return False, "No 15m candle data"
    
    ema_20 = candle.get("ema_20")
    ema_50 = candle.get("ema_50")
    close = candle.get("close")
    ema_9 = candle.get("ema_9")
    
    if None in [ema_20, ema_50, close, ema_9]:
        missing = [k for k, v in {"ema_20": ema_20, "ema_50": ema_50, "close": close, "ema_9": ema_9}.items() if v is None]
        return False, f"Missing: {missing}"
    
    if signal_type == "long":
        ema_qual = "✓" if ema_20 > ema_50 else "✗"
        price_qual = "✓" if close > ema_9 else "✗"
        return True, f"15m swing: EMA20>50 {ema_qual} | Price>EMA9 {price_qual}"
    else:  # short
        ema_qual = "✓" if ema_20 < ema_50 else "✗"
        price_qual = "✓" if close < ema_9 else "✗"
        return True, f"15m swing: EMA20<50 {ema_qual} | Price<EMA9 {price_qual}"


def _check_30m_alignment(candle: dict, signal_type: str) -> bool:
    """30m confirmation: trend direction + volume.
    
    LONG: EMA20 > EMA50, Close > EMA9, Volume >= 20-candle average
    SHORT: EMA20 < EMA50, Close < EMA9, Volume >= 20-candle average
    """
    if not candle:
        return False
    
    ema_20 = candle.get("ema_20")
    ema_50 = candle.get("ema_50")
    close = candle.get("close")
    ema_9 = candle.get("ema_9")
    
    if None in [ema_20, ema_50, close, ema_9]:
        return False
    
    if signal_type == "long":
        return ema_20 > ema_50 and close > ema_9
    else:  # short
        return ema_20 < ema_50 and close < ema_9


def _check_1h_alignment(candle: dict, signal_type: str) -> bool:
    """1h confirmation: full trend structure + ADX strength + RSI zone.
    
    LONG: EMA20 > EMA50 > EMA200, ADX > 20, RSI > 40
    SHORT: EMA20 < EMA50 < EMA200, ADX > 20, RSI < 60
    """
    if not candle:
        return False
    
    ema_20 = candle.get("ema_20")
    ema_50 = candle.get("ema_50")
    ema_200 = candle.get("ema_200")
    # ADX can be either a dict {adx, pdi, mdi} or a scalar value
    adx_data = candle.get("adx", 0)
    adx = adx_data.get("adx", 0) if isinstance(adx_data, dict) else adx_data
    rsi = candle.get("rsi")
    
    if None in [ema_20, ema_50, ema_200, rsi] or not adx:
        return False
    
    adx_check = adx > 20
    
    if signal_type == "long":
        return (
            ema_20 > ema_50 > ema_200
            and adx_check
            and rsi > 40
        )
    else:  # short
        return (
            ema_20 < ema_50 < ema_200
            and adx_check
            and rsi < 60
        )


def _check_4h_alignment(candle: dict, signal_type: str) -> bool:
    """4h confirmation: macro trend + price position + momentum.
    
    LONG: EMA50 > EMA200, Close > EMA200, RSI > 50
    SHORT: EMA50 < EMA200, Close < EMA200, RSI < 50
    """
    if not candle:
        return False
    
    ema_50 = candle.get("ema_50")
    ema_200 = candle.get("ema_200")
    close = candle.get("close")
    rsi = candle.get("rsi")
    
    if None in [ema_50, ema_200, close, rsi]:
        return False
    
    if signal_type == "long":
        return (
            ema_50 > ema_200
            and close > ema_200
            and rsi > 50
        )
    else:  # short
        return (
            ema_50 < ema_200
            and close < ema_200
            and rsi < 50
        )


def _check_12h_alignment(candle: dict, signal_type: str) -> bool:
    """12h confirmation: very long-term structure + trend strength.
    
    LONG: EMA50 > EMA200, ADX > 20
    SHORT: EMA50 < EMA200, ADX > 20
    """
    if not candle:
        return False
    
    ema_50 = candle.get("ema_50")
    ema_200 = candle.get("ema_200")
    # ADX can be either a dict {adx, pdi, mdi} or a scalar value
    adx_data = candle.get("adx", 0)
    adx = adx_data.get("adx", 0) if isinstance(adx_data, dict) else adx_data
    
    if None in [ema_50, ema_200] or not adx:
        return False
    
    adx_check = adx > 20
    
    if signal_type == "long":
        return ema_50 > ema_200 and adx_check
    else:  # short
        return ema_50 < ema_200 and adx_check


def _check_5m_alignment(candle: dict, signal_type: str) -> bool:
    """5m confirmation: quick momentum check (intermediate TF).
    
    LONG: EMA9 > EMA20, momentum aligned
    SHORT: EMA9 < EMA20, momentum aligned
    
    5m is used for scalp confirmation - lighter requirements than 30m+
    """
    if not candle:
        return False
    
    ema_9 = candle.get("ema_9")
    ema_20 = candle.get("ema_20")
    close = candle.get("close")
    
    if None in [ema_9, ema_20, close]:
        return False
    
    if signal_type == "long":
        return ema_9 > ema_20
    else:  # short
        return ema_9 < ema_20


def _check_15m_alignment(candle: dict, signal_type: str) -> bool:
    """15m confirmation: bridge between 5m scalps and 30m swings.
    
    LONG: EMA20 > EMA50, Close > EMA9, RSI > 35
    SHORT: EMA20 < EMA50, Close < EMA9, RSI < 65
    
    15m provides good swing confirmation
    """
    if not candle:
        return False
    
    ema_20 = candle.get("ema_20")
    ema_50 = candle.get("ema_50")
    close = candle.get("close")
    ema_9 = candle.get("ema_9")
    rsi = candle.get("rsi")
    
    if None in [ema_20, ema_50, close, ema_9, rsi]:
        return False
    
    if signal_type == "long":
        return ema_20 > ema_50 and close > ema_9 and rsi > 35
    else:  # short
        return ema_20 < ema_50 and close < ema_9 and rsi < 65



def validate_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise PayloadValidationError("Request body must be a JSON object.")

    for field in ("symbol", "signal_type"):
        if field not in payload:
            raise PayloadValidationError(f"Missing required field: '{field}'")

    if payload["signal_type"] not in ("long", "short", "NORMAL", "CLASSIC_DIVERGENCE", "EXTREME_DIVERGENCE", "MFI_LEADING_FLIP", "CONVERGENCE_BOUNCE"):
        raise PayloadValidationError("'signal_type' must be a valid signal type")

    # Indicators can be nested (old format) or flat (new format)
    # If flat, we'll extract them below; if nested, they must exist
    if "indicators" in payload:
        if not isinstance(payload["indicators"], dict):
            raise PayloadValidationError("'indicators' must be an object")


def build_feature_vector(payload: dict[str, Any], tf_features: dict | None = None) -> dict[str, float]:
    """Extract a fixed-order, fixed-shape feature dict from a signal payload.

    Extracts exactly the 11 features the trained model was trained on:
    1. confidence - reversal detection confidence (0-100)
    2. ema_9, ema_20, ema_50, ema_200 - exponential moving averages
    3. mfi - Money Flow Index
    4. rsi - Relative Strength Index
    5. adx - Average Directional Index
    6. price_trend - price momentum (-100 to +100)
    7. mfi_trend - money flow momentum (-100 to +100)
    8. signal - 1.0 for BUY, 0.0 for SELL
    
    Handles both flat and nested indicator formats:
    - Flat: indicators are top-level fields (ema_20, rsi, etc.)
    - Nested: indicators are under an "indicators" object
    
    Missing indicator keys default to 0.0 so the model always receives a
    consistent shape.
    
    Args:
        payload: Signal payload with indicators (flat or nested).
        tf_features: Optional dict with multi-TF alignment features (currently unused).
    """
    # Handle both flat and nested formats
    if "indicators" in payload and isinstance(payload["indicators"], dict):
        # Nested format
        indicators = payload["indicators"]
    else:
        # Flat format - extract all indicator-like fields
        indicators = {
            k: payload[k] for k in payload 
            if k in [
                "ema_9", "ema_20", "ema_50", "ema_200",
                "rsi", "mfi", "adx", "price_trend", "mfi_trend", "confidence"
            ]
        }
    
    # Build feature vector in exact order of FEATURE_COLUMNS
    # Map 'confidence' from payload (may be 'reversal_confidence')
    confidence = indicators.get('confidence', payload.get('reversal_confidence', payload.get('confidence', 50.0)))
    
    # Map signal_type to numeric: BUY=1.0, SELL=0.0
    signal_type = payload.get("signal_type", "BUY")
    signal_numeric = 1.0 if signal_type.upper() == "BUY" else 0.0
    
    # Handle ADX: can be scalar or nested object {adx, pdi, mdi}
    adx_value = indicators.get("adx", 0.0)
    if isinstance(adx_value, dict):
        adx_value = adx_value.get("adx", 0.0)
    
    # Build feature dict in FEATURE_COLUMNS order
    features = {
        "confidence": float(confidence),
        "ema_9": float(indicators.get("ema_9", 0.0)),
        "ema_20": float(indicators.get("ema_20", 0.0)),
        "ema_50": float(indicators.get("ema_50", 0.0)),
        "ema_200": float(indicators.get("ema_200", 0.0)),
        "mfi": float(indicators.get("mfi", 0.0)),
        "rsi": float(indicators.get("rsi", 0.0)),
        "adx": float(adx_value),
        "price_trend": float(indicators.get("price_trend", 0.0)),
        "mfi_trend": float(indicators.get("mfi_trend", 0.0)),
        "signal": signal_numeric,
    }
    
    return features


def calculate_reversal_confluence(
    db,
    symbol: str,
    signal_type: str,
    lookback_minutes: int = 120,
) -> dict[str, Any]:
    """Calculate confluence score based on recent reversal signals.
    
    Confluence = how many recent reversals agree with the current signal.
    More confluence = higher confidence for entry.
    
    Example:
    - Signal: SHORT (expecting price down)
    - Recent reversals in last 2 hours: 8 SELL signals, 2 BUY signals
    - Confluence: 8 matching + 2 conflicting = 80% alignment
    - Confidence boost: +30% (strong confluence)
    
    Args:
        db: Database instance.
        symbol: Trading pair symbol.
        signal_type: 'long' or 'short'.
        lookback_minutes: Time window to check (default 2 hours).
        
    Returns:
        Dict with:
        {
            'confluent_count': int,  # Number of reversals matching signal
            'conflicting_count': int,  # Number opposing signal
            'total_reversals': int,  # Total reversals in window
            'confluence_ratio': float,  # confluent / total (0-1)
            'confidence_boost': float,  # Percentage points to add (0-40)
            'confluence_level': str,  # 'very_high', 'high', 'medium', 'low', 'none'
        }
    """
    try:
        # Get recent reversals for this symbol
        reversals = db.get_recent_reversals(
            symbol=symbol,
            signal_type=None,  # Get all reversals
            lookback_minutes=lookback_minutes,
        )
        
        if not reversals:
            return {
                'confluent_count': 0,
                'conflicting_count': 0,
                'total_reversals': 0,
                'confluence_ratio': 0.0,
                'confidence_boost': 0.0,
                'confluence_level': 'none',
            }
        
        # Map signal_type to expected reversal signal
        expected_signal = "BUY" if signal_type == "long" else "SELL"
        
        # Count confluent vs conflicting reversals
        confluent_count = sum(1 for r in reversals if r['signal'] == expected_signal)
        conflicting_count = len(reversals) - confluent_count
        total_reversals = len(reversals)
        
        # Calculate confluence ratio (0-1)
        confluence_ratio = confluent_count / total_reversals if total_reversals > 0 else 0
        
        # Determine confidence boost based on confluence
        if confluence_ratio >= 0.85:  # 85%+ agreement
            confluence_level = 'very_high'
            confidence_boost = 40.0  # Maximum boost
        elif confluence_ratio >= 0.70:  # 70-84% agreement
            confluence_level = 'high'
            confidence_boost = 30.0
        elif confluence_ratio >= 0.55:  # 55-69% agreement
            confluence_level = 'medium'
            confidence_boost = 15.0
        elif confluence_ratio >= 0.40:  # 40-54% agreement
            confluence_level = 'low'
            confidence_boost = 5.0
        else:  # Less than 40% agreement
            confluence_level = 'none'
            confidence_boost = 0.0
        
        return {
            'confluent_count': confluent_count,
            'conflicting_count': conflicting_count,
            'total_reversals': total_reversals,
            'confluence_ratio': round(confluence_ratio, 3),
            'confidence_boost': confidence_boost,
            'confluence_level': confluence_level,
        }
    
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error calculating confluence: {e}", exc_info=True)
        return {
            'confluent_count': 0,
            'conflicting_count': 0,
            'total_reversals': 0,
            'confluence_ratio': 0.0,
            'confidence_boost': 0.0,
            'confluence_level': 'error',
        }


def analyze_reversal_signals(payload: dict[str, Any], signal_type: str) -> dict[str, Any]:
    """Analyze reversal signals and determine how they align with the trade signal.
    
    When MFI reversal detection finds a pattern, check if it agrees or disagrees 
    with the incoming signal. Alignment = confidence boost.
    
    Args:
        payload: Signal payload with indicators (may include reversal_signal, reversal_confidence)
        signal_type: 'long' or 'short'
    
    Returns:
        Dict with reversal analysis: {
            'has_reversal': bool,
            'reversal_signal': 'BUY'|'SELL'|None,
            'reversal_confidence': 0-100,
            'aligns_with_signal': bool,
            'alignment_type': 'confirming'|'conflicting'|'neutral'|'none',
            'confidence_boost': 0-20 (percentage points to add to model confidence)
        }
    """
    # Extract indicators from either nested or flat structure
    if "indicators" in payload and isinstance(payload["indicators"], dict):
        indicators = payload["indicators"]
    else:
        # Flat format - extract from top level
        indicators = payload
    
    reversal_signal = indicators.get("reversal_signal")
    reversal_confidence = float(indicators.get("reversal_confidence", 0))
    price_trend = float(indicators.get("price_trend", 0))
    mfi_trend = float(indicators.get("mfi_trend", 0))
    
    # Map signal_type to expected reversal signal
    expected_reversal = "BUY" if signal_type == "long" else "SELL"
    
    # Determine alignment
    aligns = reversal_signal == expected_reversal
    
    # Calculate alignment type
    if reversal_confidence == 0 or reversal_signal is None:
        alignment_type = "none"
        confidence_boost = 0
    elif aligns and reversal_confidence >= 70:
        alignment_type = "confirming"
        # Strong reversal confirmation = big confidence boost
        confidence_boost = min(20, reversal_confidence * 0.2)  # Up to 20 points
    elif aligns and reversal_confidence >= 50:
        alignment_type = "confirming"
        confidence_boost = min(12, reversal_confidence * 0.15)  # Up to 12 points
    elif not aligns and reversal_confidence >= 75:
        alignment_type = "conflicting"
        confidence_boost = -15  # Penalty for strong disagreement
    else:
        alignment_type = "neutral"
        confidence_boost = 0
    
    return {
        "has_reversal": reversal_signal is not None,
        "reversal_signal": reversal_signal,
        "reversal_confidence": reversal_confidence,
        "price_trend": price_trend,
        "mfi_trend": mfi_trend,
        "aligns_with_signal": aligns,
        "alignment_type": alignment_type,
        "confidence_boost": confidence_boost,
    }


def calculate_reversal_weighted_decision(
    model_confidence: float,
    reversal_analysis: dict[str, Any],
    tf_alignment_score: int
) -> dict[str, Any]:
    """Incorporate reversal signals into final decision with intelligent weighting.
    
    Strategy:
    - Perfect TF alignment (4/4) + confirming reversal = Big confidence boost (20 points)
    - Good TF alignment (3+) + confirming reversal = Medium confidence boost (12 points)
    - Conflicting reversal with high confidence = Warn or reduce confidence
    - Weak reversal signals = Minimal impact
    
    Args:
        model_confidence: Base ML model confidence (0-1)
        reversal_analysis: Dict from analyze_reversal_signals()
        tf_alignment_score: TF alignment score (0-4)
    
    Returns:
        {
            'final_confidence': 0-1 (adjusted confidence),
            'reversal_boost': points added/subtracted,
            'reasoning': explanation of reversal impact,
            'reversal_weight': 'strong'|'medium'|'weak'|'none'
        }
    """
    base_conf_percent = model_confidence * 100
    confidence_boost = reversal_analysis.get("confidence_boost", 0)
    alignment_type = reversal_analysis.get("alignment_type", "none")
    
    # Determine reversal weight based on alignment score and confidence
    if alignment_type == "confirming":
        if tf_alignment_score == 4:
            reversal_weight = "strong"  # Perfect alignment + confirming reversal
        elif tf_alignment_score >= 3:
            reversal_weight = "medium"  # Good alignment + confirming reversal
        else:
            reversal_weight = "weak"
    elif alignment_type == "conflicting":
        reversal_weight = "strong"  # Conflict needs attention
    else:
        reversal_weight = "none"
    
    # Apply reversal boost with diminishing returns (can't boost past 0.98)
    final_conf_percent = base_conf_percent + confidence_boost
    final_conf_percent = min(98, max(0, final_conf_percent))
    final_confidence = final_conf_percent / 100
    
    # Build reasoning message
    if alignment_type == "none":
        reasoning = "No reversal signal detected - using model confidence only"
    elif alignment_type == "confirming":
        reasoning = f"✓ Reversal signal CONFIRMS trade ({reversal_analysis['reversal_confidence']}% confidence) - confidence boosted {confidence_boost:.0f} points"
    elif alignment_type == "conflicting":
        reasoning = f"⚠ Reversal signal CONFLICTS with trade ({reversal_analysis['reversal_confidence']}% confidence) - confidence reduced {abs(confidence_boost):.0f} points"
    else:
        reasoning = "Weak reversal signal - minimal impact"
    
    return {
        "final_confidence": final_confidence,
        "reversal_boost": confidence_boost,
        "reasoning": reasoning,
        "reversal_weight": reversal_weight,
        "base_confidence": model_confidence,
    }

