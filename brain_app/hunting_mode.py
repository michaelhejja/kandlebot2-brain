"""Hunting Mode: Detect and amplify high-confidence entries during TREND_START inflection zones.

When a TREND_START signal is detected, the market is at an inflection point where a major
move is likely to begin. This module tracks recent TREND_START signals and boosts confidence
for subsequent reversals that align with that direction, creating a "hunting mode" where
the system looks for optimal entries before the big move.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

# In-memory cache of active hunting mode signals
# Structure: {(symbol, direction): {"timestamp": dt, "confidence": float, "trade_type": str}}
_HUNTING_CACHE: Dict[tuple, Dict[str, Any]] = {}

# Hunting window: 24 hours after TREND_START
HUNTING_WINDOW_HOURS = 24
HUNTING_WINDOW = timedelta(hours=HUNTING_WINDOW_HOURS)

# Confidence boost for aligned follow-up signals
HUNTING_MODE_BOOST = 0.15  # +15% confidence boost


def record_trend_start(symbol: str, direction: str, confidence: float, grade: str) -> None:
    """Record a TREND_START signal to activate hunting mode.
    
    Args:
        symbol: Trading pair (e.g., "ETH", "BTC")
        direction: Signal direction ("LONG" or "SHORT")
        confidence: Confidence level (0-1)
        grade: Trade grade ("A", "B", or "C")
    """
    try:
        key = (symbol, direction)
        _HUNTING_CACHE[key] = {
            "timestamp": datetime.now(timezone.utc),
            "confidence": confidence,
            "grade": grade,
            "initial_direction": direction,
        }
        
        import logging
        logging.getLogger(__name__).info(
            f"🎯 HUNTING MODE ACTIVATED: {symbol} {direction} (Grade {grade}, {confidence:.1%})"
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error recording TREND_START for hunting: {e}")


def get_hunting_mode_status(symbol: str, reversal_direction: str) -> Dict[str, Any]:
    """Check if symbol is in hunting mode and if reversal aligns.
    
    Args:
        symbol: Trading pair (e.g., "ETH")
        reversal_direction: Direction of current reversal ("LONG" or "SHORT")
        
    Returns:
        Dict with:
        - is_hunting: True if active hunting mode exists
        - direction_aligned: True if reversal matches TREND_START direction
        - confidence_boost: Confidence increment to apply (0.0 if not hunting)
        - hunting_info: Dict with TREND_START details if hunting
    """
    try:
        now = datetime.now(timezone.utc)
        
        # Check both directions for active hunting signals
        for direction in ["LONG", "SHORT"]:
            key = (symbol, direction)
            
            if key not in _HUNTING_CACHE:
                continue
            
            hunting_signal = _HUNTING_CACHE[key]
            signal_time = hunting_signal["timestamp"]
            age = now - signal_time
            
            # Check if signal is still within hunting window
            if age > HUNTING_WINDOW:
                # Expired, remove from cache
                del _HUNTING_CACHE[key]
                continue
            
            # Active hunting signal found
            is_aligned = direction == reversal_direction
            confidence_boost = HUNTING_MODE_BOOST if is_aligned else 0.0
            
            return {
                "is_hunting": True,
                "direction_aligned": is_aligned,
                "confidence_boost": confidence_boost,
                "hunting_info": {
                    "trend_start_direction": direction,
                    "trend_start_grade": hunting_signal["grade"],
                    "trend_start_confidence": hunting_signal["confidence"],
                    "trend_start_timestamp": signal_time.isoformat(),
                    "hunting_age_minutes": round(age.total_seconds() / 60),
                    "hunting_window_hours": HUNTING_WINDOW_HOURS,
                },
            }
        
        # No active hunting signal
        return {
            "is_hunting": False,
            "direction_aligned": False,
            "confidence_boost": 0.0,
            "hunting_info": None,
        }
        
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error checking hunting mode: {e}")
        return {
            "is_hunting": False,
            "direction_aligned": False,
            "confidence_boost": 0.0,
            "hunting_info": None,
        }


def clear_hunting_mode(symbol: Optional[str] = None) -> None:
    """Clear hunting mode cache (debug utility).
    
    Args:
        symbol: If provided, clear only that symbol. Otherwise, clear all.
    """
    try:
        if symbol:
            keys_to_remove = [k for k in _HUNTING_CACHE.keys() if k[0] == symbol]
            for key in keys_to_remove:
                del _HUNTING_CACHE[key]
            import logging
            logging.getLogger(__name__).info(f"Cleared hunting mode for {symbol}")
        else:
            _HUNTING_CACHE.clear()
            import logging
            logging.getLogger(__name__).info("Cleared all hunting mode signals")
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error clearing hunting mode: {e}")


def get_active_hunting_signals() -> Dict[str, Dict[str, Any]]:
    """Get all active hunting signals (debug utility).
    
    Returns:
        Dict of active (symbol, direction) pairs with their hunt data
    """
    now = datetime.now(timezone.utc)
    active = {}
    
    for key, data in list(_HUNTING_CACHE.items()):
        age = now - data["timestamp"]
        if age <= HUNTING_WINDOW:
            symbol, direction = key
            active[f"{symbol}_{direction}"] = {
                **data,
                "timestamp": data["timestamp"].isoformat(),
                "age_minutes": round(age.total_seconds() / 60),
            }
        else:
            # Cleanup expired entries
            del _HUNTING_CACHE[key]
    
    return active
