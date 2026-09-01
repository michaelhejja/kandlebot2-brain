"""Decision smoothing and hysteresis to prevent confidence whiplash.

Solves the problem of 98% confidence → 15% confidence on consecutive minutes.
Uses three strategies:

1. Temporal Averaging: Average confidence across last 2-3 candles
2. Decision Hysteresis: Require 20% confidence margin to flip decisions
3. Alignment Stability: Cache TF alignment scores for 5min to avoid re-evaluation noise
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# In-memory cache for decision history
# Structure: {(symbol, signal_type): {"decisions": [...], "tf_alignments": {...}}}
DECISION_CACHE = {}

# Configuration for smoothing behavior
HYSTERESIS_MARGIN = 0.20  # 20% margin required to flip decisions (0.20 confidence)
TEMPORAL_WINDOW = 3  # Average across last 3 candles
ALIGNMENT_CACHE_DURATION = 300  # Cache TF alignment for 5 minutes


def smooth_decision(
    symbol: str,
    signal_type: str,
    raw_confidence: float,
    raw_decision: str,
    current_alignment_score: int,
) -> dict[str, Any]:
    """Apply decision smoothing with hysteresis and temporal averaging.
    
    Prevents 98% → 15% confidence swings by:
    1. Averaging confidence across last 2-3 candles
    2. Requiring 20% margin to flip from ACCEPT to REJECT or vice versa
    3. Caching timeframe alignment to reduce noise from single TF shifts
    
    Args:
        symbol: Trading symbol (e.g., 'ETH-USDT')
        signal_type: 'long' or 'short'
        raw_confidence: Raw model confidence (0-1)
        raw_decision: Raw decision ('accept' or 'reject')
        current_alignment_score: Current TF alignment score (0-4)
        
    Returns:
        Dict with:
        - smoothed_decision: ACCEPT/REJECT (potentially changed by hysteresis)
        - smoothed_confidence: Averaged confidence (0-1)
        - previous_decision: Last decision for this symbol/direction
        - applied_hysteresis: Bool indicating if hysteresis changed the decision
        - confidence_history: Recent confidence values
    """
    cache_key = (symbol, signal_type)
    now = datetime.now()
    
    # Initialize cache entry if needed
    if cache_key not in DECISION_CACHE:
        DECISION_CACHE[cache_key] = {
            "decisions": [],  # List of (timestamp, decision, confidence, alignment_score)
            "tf_alignments": {},  # symbol+tf → (timestamp, alignment_score)
        }
    
    history = DECISION_CACHE[cache_key]["decisions"]
    
    # Add current decision to history (keep last 3)
    history.append((now, raw_decision, raw_confidence, current_alignment_score))
    if len(history) > TEMPORAL_WINDOW:
        history.pop(0)
    
    # Get last decision for hysteresis comparison
    last_decision = history[-2] if len(history) >= 2 else None
    previous_decision = last_decision[1] if last_decision else None
    previous_confidence = last_decision[2] if last_decision else None
    
    # Strategy 1: Temporal averaging of confidence
    # Average confidence across recent candles to smooth spikes
    confidence_values = [c for _, _, c, _ in history]
    averaged_confidence = sum(confidence_values) / len(confidence_values)
    
    # Strategy 2: Decision hysteresis
    # Don't flip decisions without sufficient confidence margin
    smoothed_decision = raw_decision
    applied_hysteresis = False
    
    if previous_decision and len(history) >= 2:
        # Check if we're trying to flip decisions
        is_flip = previous_decision != raw_decision
        
        if is_flip:
            confidence_gap = abs(raw_confidence - previous_confidence)
            
            if confidence_gap < HYSTERESIS_MARGIN:
                # Insufficient margin - keep previous decision
                smoothed_decision = previous_decision
                applied_hysteresis = True
                
                logger.info(
                    f"HYSTERESIS APPLIED: {symbol} {signal_type} | "
                    f"Trying to flip {previous_decision}→{raw_decision} "
                    f"but gap only {confidence_gap:.2f} (need {HYSTERESIS_MARGIN:.2f}) | "
                    f"Keeping {smoothed_decision}"
                )
            else:
                logger.info(
                    f"DECISION FLIP APPROVED: {symbol} {signal_type} | "
                    f"{previous_decision}→{raw_decision} "
                    f"gap {confidence_gap:.2f} ≥ {HYSTERESIS_MARGIN:.2f}"
                )
    
    # Use averaged confidence for final output
    # If hysteresis blocked a flip, still report smoothed (averaged) confidence
    smoothed_confidence = averaged_confidence
    
    logger.info(
        f"DECISION SMOOTHED: {symbol} {signal_type} | "
        f"raw={raw_decision}/{raw_confidence:.3f} → "
        f"smoothed={smoothed_decision}/{smoothed_confidence:.3f} | "
        f"history={len(history)} candles | hysteresis={applied_hysteresis}"
    )
    
    return {
        "smoothed_decision": smoothed_decision,
        "smoothed_confidence": smoothed_confidence,
        "previous_decision": previous_decision,
        "previous_confidence": previous_confidence,
        "applied_hysteresis": applied_hysteresis,
        "confidence_history": confidence_values,
        "confidence_gap": abs(raw_confidence - previous_confidence) if previous_decision else 0.0,
    }


def cache_tf_alignment(
    symbol: str,
    signal_type: str,
    timeframe: str,
    alignment_score: int,
) -> None:
    """Cache timeframe alignment to reduce re-evaluation noise.
    
    Args:
        symbol: Trading symbol
        signal_type: 'long' or 'short'
        timeframe: e.g., '30m', '1h', '4h', '12h'
        alignment_score: 0-4 alignment score
    """
    cache_key = (symbol, signal_type)
    
    if cache_key not in DECISION_CACHE:
        DECISION_CACHE[cache_key] = {
            "decisions": [],
            "tf_alignments": {},
        }
    
    tf_key = f"{symbol}_{timeframe}_{signal_type}"
    DECISION_CACHE[cache_key]["tf_alignments"][tf_key] = (
        datetime.now(),
        alignment_score,
    )


def get_cached_tf_alignment(
    symbol: str,
    signal_type: str,
    timeframe: str,
) -> tuple[bool, int | None]:
    """Get cached TF alignment if still valid (< 5min old).
    
    Returns:
        (is_cached, alignment_score) - is_cached=True if valid cache hit
    """
    cache_key = (symbol, signal_type)
    
    if cache_key not in DECISION_CACHE:
        return (False, None)
    
    tf_key = f"{symbol}_{timeframe}_{signal_type}"
    alignments = DECISION_CACHE[cache_key]["tf_alignments"]
    
    if tf_key not in alignments:
        return (False, None)
    
    cached_time, alignment_score = alignments[tf_key]
    age = (datetime.now() - cached_time).total_seconds()
    
    if age < ALIGNMENT_CACHE_DURATION:
        logger.debug(
            f"TF alignment cache hit: {symbol}/{timeframe} "
            f"score={alignment_score} (age={age:.0f}s)"
        )
        return (True, alignment_score)
    
    # Cache expired
    return (False, None)


def clear_cache(symbol: str | None = None) -> None:
    """Clear decision cache (useful for debugging/restart).
    
    Args:
        symbol: If provided, only clear this symbol. Otherwise clear all.
    """
    global DECISION_CACHE
    
    if symbol:
        keys_to_delete = [k for k in DECISION_CACHE.keys() if k[0] == symbol]
        for key in keys_to_delete:
            del DECISION_CACHE[key]
        logger.info(f"Cleared cache for {symbol}")
    else:
        DECISION_CACHE = {}
        logger.info("Cleared all decision cache")
