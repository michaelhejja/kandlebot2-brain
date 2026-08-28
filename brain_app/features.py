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


class PayloadValidationError(ValueError):
    """Raised when an incoming /analyze request is missing required fields."""


def calculate_optimal_entry(
    payload: dict,
    candles_1h: dict | None = None,
) -> dict[str, Any]:
    """Calculate optimal entry price and timing recommendation.
    
    Analyzes current price, ATR, and RSI to recommend:
    - ENTER_NOW: Price at support, good momentum
    - WAIT_FOR_DIP: Price elevated, wait for pullback
    - WAIT_FOR_BREAKOUT: Price consolidating, wait for confirmation
    
    Args:
        payload: Full payload dict with symbol, signal_type, indicators
        candles_1h: Optional 1h candle for support/resistance analysis
        
    Returns:
        Dict with entry_recommendation, entry_price, stop_loss, take_profit
    """
    try:
        # Extract indicators from either nested or flat structure
        if "indicators" in payload and isinstance(payload["indicators"], dict):
            indicators = payload["indicators"]
        else:
            # Flat format - extract from top level
            indicators = payload
        
        signal_type = payload.get("signal_type", "long")
        
        rsi = indicators.get("rsi", 50.0)
        ema_9 = indicators.get("ema_9", 0.0)
        ema_21 = indicators.get("ema_21", 0.0)
        atr = indicators.get("atr", 0.0)
        close = indicators.get("close", ema_9)  # Use EMA9 as fallback
        volume = indicators.get("volume", 0.0)
        
        if signal_type == "long":
            # LONG signal entry logic
            # Check if price is near support (EMA9)
            distance_from_ema9 = ((ema_9 - close) / ema_9 * 100) if ema_9 else 0
            
            # Recommendation based on position and RSI
            if distance_from_ema9 > 0.5:  # Below EMA9 = good entry
                recommendation = "ENTER_NOW"
                entry_price = close
                entry_reason = "Price below EMA9 - strong entry zone"
            elif 30 <= rsi <= 50:  # RSI in good zone for long
                recommendation = "ENTER_NOW"
                entry_price = close
                entry_reason = "RSI optimal for long entry"
            elif rsi > 65:  # Overbought
                recommendation = "WAIT_FOR_DIP"
                entry_price = ema_9 * 0.995  # Pullback target
                entry_reason = "Price overbought, wait for pullback to EMA9"
            elif rsi < 30:  # Oversold
                recommendation = "ENTER_NOW"
                entry_price = close
                entry_reason = "Oversold conditions - strong bounce potential"
            else:
                recommendation = "ENTER_NOW"
                entry_price = close
                entry_reason = "Normal entry zone"
            
            # Risk/reward targets
            stop_loss = ema_21 * 0.998  # Below EMA21
            take_profit_conservative = entry_price + (atr * 1.5)  # 1.5 ATR
            take_profit_aggressive = entry_price + (atr * 3.0)  # 3.0 ATR
            
        else:  # SHORT signal
            # SHORT signal entry logic
            distance_from_ema9 = ((close - ema_9) / ema_9 * 100) if ema_9 else 0
            
            if distance_from_ema9 > 0.5:  # Above EMA9 = good SHORT entry
                recommendation = "ENTER_NOW"
                entry_price = close
                entry_reason = "Price above EMA9 - strong SHORT entry"
            elif 50 <= rsi <= 70:  # RSI good for short
                recommendation = "ENTER_NOW"
                entry_price = close
                entry_reason = "RSI optimal for SHORT entry"
            elif rsi < 35:  # Oversold
                recommendation = "WAIT_FOR_BOUNCE"
                entry_price = ema_9 * 1.005  # Bounce target
                entry_reason = "Oversold, wait for bounce to EMA9"
            elif rsi > 70:  # Overbought
                recommendation = "ENTER_NOW"
                entry_price = close
                entry_reason = "Overbought conditions - strong reversal"
            else:
                recommendation = "ENTER_NOW"
                entry_price = close
                entry_reason = "Normal entry zone"
            
            stop_loss = ema_21 * 1.002  # Above EMA21
            take_profit_conservative = entry_price - (atr * 1.5)
            take_profit_aggressive = entry_price - (atr * 3.0)
        
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
        }


def classify_trade_type(
    payload: dict,
    tf_alignment: dict,
) -> dict[str, Any]:
    """Classify trade as scalp, swing, or trend-start with grade A/B/C.
    
    SCALP: High momentum, small range, quick exit (5-30 min)
    SWING: Medium momentum, medium range, medium hold (2-5 days)
    TREND_START: Lower momentum initially, expanding range, long hold
    
    Args:
        payload: Full payload dict with symbol, signal_type, indicators
        tf_alignment: TF alignment dict with score and individual TF flags
        
    Returns:
        Dict with trade_type, grade, characteristics, hold_time_estimate
    """
    try:
        # Extract indicators from nested structure
        indicators = payload.get("indicators", {})
        
        rsi = indicators.get("rsi", 50.0)
        atr = indicators.get("atr", 0.0)
        macd = indicators.get("macd", 0.0)
        macd_signal = indicators.get("macd_signal", 0.0)
        volume = indicators.get("volume", 0.0)
        ema_9 = indicators.get("ema_9", 0.0)
        ema_21 = indicators.get("ema_21", 0.0)
        ema_200 = indicators.get("ema_200", 0.0)
        
        # Get TF alignment strength
        tf_score = tf_alignment.get("tf_alignment_score", 0)
        
        # Momentum indicators
        macd_momentum = macd - macd_signal  # Positive = momentum
        rsi_extremity = abs(rsi - 50.0)  # How extreme is RSI
        
        # Trend structure: how aligned are EMAs
        if ema_200:
            macro_trend_strength = (ema_21 - ema_200) / ema_200 * 100
        else:
            macro_trend_strength = 0
        
        # Decision logic
        is_high_momentum = rsi_extremity > 20  # Strong RSI signal (20+ from neutral)
        is_strong_macd = abs(macd_momentum) > 1.5  # High threshold for strong
        is_extreme_rsi = rsi_extremity > 30  # Very oversold/overbought (30+ from neutral)
        
        # Classify based on characteristics
        # Priority 1: EXTREME reversals (scalps) - high probability, quick
        if is_extreme_rsi and is_strong_macd and tf_score >= 3:
            # SCALP: Extreme conditions + high TF confirmation = quick reversal
            trade_type = "SCALP"
            if rsi_extremity > 38:
                grade = "A"  # Very extreme
            elif rsi_extremity > 30:
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
            elif is_strong_macd or (is_high_momentum and abs(macro_trend_strength) > 0.5):
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
            
        # Priority 3: GOOD momentum (high RSI extremity) + high alignment
        elif is_high_momentum and tf_score >= 3:
            trade_type = "SWING"
            if rsi_extremity > 25:
                grade = "A"
            elif rsi_extremity > 15:
                grade = "B"
            else:
                grade = "C"
            characteristics = [
                "Strong momentum move",
                "Multi-TF confirmation",
                "2-5 day hold expected",
                "Good entry timing",
            ]
            hold_time = "2-5 days"
            risk_level = "Medium"
            
        # Priority 4: DEVELOPING trend (3+ TFs aligned)
        elif tf_score >= 3 and abs(macro_trend_strength) >= 1.0:
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
    adx = candle.get("adx")
    rsi = candle.get("rsi")
    
    if None in [ema_20, ema_50, ema_200, adx, rsi]:
        missing = [k for k, v in {"ema_20": ema_20, "ema_50": ema_50, "ema_200": ema_200, "adx": adx, "rsi": rsi}.items() if v is None]
        return False, f"Missing: {missing}"
    
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
    adx = candle.get("adx")
    rsi = candle.get("rsi")
    
    if None in [ema_20, ema_50, ema_200, adx, rsi]:
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
    adx = candle.get("adx")
    
    if None in [ema_50, ema_200, adx]:
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
    
    # Build feature dict in FEATURE_COLUMNS order
    features = {
        "confidence": float(confidence),
        "ema_9": float(indicators.get("ema_9", 0.0)),
        "ema_20": float(indicators.get("ema_20", 0.0)),
        "ema_50": float(indicators.get("ema_50", 0.0)),
        "ema_200": float(indicators.get("ema_200", 0.0)),
        "mfi": float(indicators.get("mfi", 0.0)),
        "rsi": float(indicators.get("rsi", 0.0)),
        "adx": float(indicators.get("adx", 0.0)),
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

