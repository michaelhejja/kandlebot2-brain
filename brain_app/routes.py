"""HTTP routes exposed to the Node.js signal-detection server."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from .features import (
    PayloadValidationError,
    build_feature_vector,
    check_timeframe_alignment,
    validate_payload,
    analyze_reversal_signals,
    calculate_reversal_weighted_decision,
)

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)


def _check_api_key() -> bool:
    expected = current_app.config.get("API_KEY")
    if not expected:
        return True  # auth disabled (local dev only)
    return request.headers.get("X-API-Key") == expected


@api_bp.get("/health")
def health():
    classifier = current_app.classifier
    return jsonify(
        status="ok",
        model_loaded=classifier.is_trained_model,
    )


@api_bp.post("/sync")
def sync_candle():
    """Receive candle from Node server and store it in the database.
    
    Expected payload:
    {
        "symbol": "ETH",
        "timeframe": "1m",  # NEW: Optional, defaults to "1m"
        "timestamp": 1787767200,  # Unix epoch (seconds), or ISO format string
        "open": 2500.0,
        "high": 2502.0,
        "low": 2499.0,
        "close": 2501.0,
        "volume": 1000.0,
        "indicators": {
            "ema_20": 2500.5,
            "ema_50": 2499.0,
            "ema_200": 2498.0,
            "crsi": 35.0,
            "adx": 25.0,
            "atr": 10.0,
            "mfi": 40.0
        }
    }
    """
    if not _check_api_key():
        return jsonify(error="Unauthorized"), 401

    payload = request.get_json(silent=True)

    try:
        # Validate required fields
        for field in ("symbol", "timestamp", "open", "high", "low", "close", "volume"):
            if field not in payload:
                return jsonify(error=f"Missing required field: '{field}'"), 400

        # Parse timestamp (handle Unix epoch or ISO format)
        ts_input = payload["timestamp"]
        if isinstance(ts_input, (int, float)):
            # Unix timestamp (seconds since epoch)
            timestamp = datetime.fromtimestamp(ts_input, tz=timezone.utc)
        else:
            # ISO format string (with or without 'Z')
            if str(ts_input).endswith("Z"):
                ts_input = str(ts_input)[:-1] + "+00:00"
            timestamp = datetime.fromisoformat(ts_input)

        # Extract OHLCV
        ohlcv = {
            "open": float(payload["open"]),
            "high": float(payload["high"]),
            "low": float(payload["low"]),
            "close": float(payload["close"]),
            "volume": float(payload["volume"]),
        }

        # Extract indicators (optional, may be empty)
        # Support both nested {"indicators": {...}} and flat format
        if "indicators" in payload:
            indicators = payload.get("indicators", {})
        else:
            # Flat format: extract from top-level keys
            indicators = {
                "ema_9": payload.get("ema_9"),
                "ema_20": payload.get("ema_20"),
                "ema_50": payload.get("ema_50"),
                "ema_200": payload.get("ema_200"),
                "crsi": payload.get("crsi"),
                "rsi": payload.get("rsi"),
                "adx": payload.get("adx"),
                "atr": payload.get("atr"),
                "mfi": payload.get("mfi"),
            }
            # Remove None values
            indicators = {k: v for k, v in indicators.items() if v is not None}
        
        # Handle ADX: if it's a dict {adx, pdi, mdi}, extract just the adx value
        if "adx" in indicators and isinstance(indicators["adx"], dict):
            indicators["adx"] = indicators["adx"].get("adx", 0)
        
        # Extract timeframe (optional, defaults to "1m")
        timeframe = payload.get("timeframe", "1m")

        # Store candle and auto-aggregate to higher timeframes (if 1m)
        current_app.candle_store.store_candle(
            symbol=payload["symbol"],
            timestamp=timestamp,
            ohlcv=ohlcv,
            indicators=indicators if indicators else None,
            source="node_server",
            timeframe=timeframe,  # NEW: Pass timeframe to store_candle
        )

        logger.info(
            f"Stored {timeframe} candle for {payload['symbol']} at {timestamp}, "
            f"close={payload['close']}"
        )

        return jsonify(
            status="ok",
            symbol=payload["symbol"],
            timeframe=timeframe,
            timestamp=timestamp.isoformat(),
            candle_stored=True,
        ), 201

    except ValueError as e:
        logger.error(f"Invalid value in sync payload: {e}")
        return jsonify(error=f"Invalid value: {str(e)}"), 400
    except Exception as e:
        logger.error(f"Error storing candle: {e}", exc_info=True)
        return jsonify(error=f"Error storing candle: {str(e)}"), 500


@api_bp.post("/reversal")
def record_reversal():
    """Record a detected reversal signal for confluence analysis.
    
    Payload:
    {
        "symbol": "ETH",
        "timeframe": "1m",
        "timestamp": "1787869260",  # Unix epoch or ISO 8601
        "signal": "BUY",  # or "SELL"
        "confidence": 65,  # 0-100
        "reversal_type": "CONVERGENCE_BOUNCE",
        "price_trend": 79.72,
        "mfi_trend": 11.25
    }
    
    Returns: {"status": "ok", "reversal_recorded": true}
    """
    if not _check_api_key():
        return jsonify(error="Unauthorized"), 401

    payload = request.get_json(silent=True)
    
    try:
        # Validate required fields
        required = ["symbol", "timeframe", "timestamp", "signal", "confidence"]
        for field in required:
            if field not in payload:
                raise ValueError(f"Missing required field: '{field}'")
        
        # Parse timestamp
        try:
            ts_val = payload["timestamp"]
            if isinstance(ts_val, str) and ts_val.isdigit():
                # Unix epoch in seconds
                timestamp = datetime.fromtimestamp(int(ts_val), tz=timezone.utc)
            elif isinstance(ts_val, (int, float)):
                # Unix epoch (numeric)
                timestamp = datetime.fromtimestamp(ts_val, tz=timezone.utc)
            else:
                # ISO 8601
                timestamp = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid timestamp format: {ts_val}") from e
        
        # Record the reversal
        current_app.db.insert_reversal(
            symbol=payload["symbol"],
            timeframe=payload["timeframe"],
            timestamp=timestamp,
            signal=payload["signal"],
            confidence=float(payload["confidence"]),
            reversal_type=payload.get("reversal_type"),
            price_trend=float(payload.get("price_trend", 0)) if payload.get("price_trend") else None,
            mfi_trend=float(payload.get("mfi_trend", 0)) if payload.get("mfi_trend") else None,
        )
        
        logger.info(
            f"Recorded reversal for {payload['symbol']} {payload['timeframe']} "
            f"at {timestamp}: {payload['signal']} ({payload['confidence']}%)"
        )
        
        return jsonify(
            status="ok",
            symbol=payload["symbol"],
            timeframe=payload["timeframe"],
            timestamp=timestamp.isoformat(),
            reversal_recorded=True,
        ), 201
    
    except ValueError as e:
        logger.error(f"Invalid value in reversal payload: {e}")
        return jsonify(error=f"Invalid value: {str(e)}"), 400
    except Exception as e:
        logger.error(f"Error recording reversal: {e}", exc_info=True)
        return jsonify(error=f"Error recording reversal: {str(e)}"), 500


@api_bp.post("/bulk-sync")
def bulk_sync_candles():
    """Receive multiple historical candles and store them all at once.
    
    Expected payload:
    {
        "candles": [
            {
                "symbol": "ETH",
                "timeframe": "1m",
                "timestamp": "2026-08-27T14:00:00Z",
                "open": 2500.0,
                "high": 2502.0,
                "low": 2499.0,
                "close": 2501.0,
                "volume": 1000.0,
                "ema_9": 2500.5,
                "ema_20": 2500.5,
                "ema_50": 2499.0,
                "ema_200": 2498.0,
                "crsi": 35.0,
                "adx": 25.0,
                "atr": 10.0,
                "mfi": 40.0
            },
            ...more candles...
        ]
    }
    """
    if not _check_api_key():
        return jsonify(error="Unauthorized"), 401

    payload = request.get_json(silent=True)
    
    if not isinstance(payload, dict) or "candles" not in payload:
        return jsonify(error="Invalid payload - expected {candles: [...]}"), 400
    
    if not isinstance(payload["candles"], list):
        return jsonify(error="'candles' must be an array"), 400
    
    candles = payload["candles"]
    if not candles:
        return jsonify(error="Empty candles array"), 400

    stored_count = 0
    errors = []

    for idx, candle in enumerate(candles):
        try:
            # Validate required fields
            for field in ("symbol", "timestamp", "open", "high", "low", "close", "volume"):
                if field not in candle:
                    errors.append(f"Candle {idx}: Missing required field '{field}'")
                    continue

            # Parse timestamp
            ts_input = candle["timestamp"]
            if isinstance(ts_input, (int, float)):
                timestamp = datetime.fromtimestamp(ts_input, tz=timezone.utc)
            else:
                ts_str = str(ts_input)
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                timestamp = datetime.fromisoformat(ts_str)

            # Extract OHLCV
            ohlcv = {
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle["volume"]),
            }

            # Extract indicators (all flat at top level)
            indicators = {}
            indicator_fields = [
                "ema_9", "ema_20", "ema_50", "ema_200",
                "crsi", "rsi", "adx", "atr", "mfi",
                "price_trend", "mfi_trend"
            ]
            for field in indicator_fields:
                if field in candle:
                    indicators[field] = candle[field]
            
            # Handle ADX: if it's a dict {adx, pdi, mdi}, extract just the adx value
            if "adx" in indicators and isinstance(indicators["adx"], dict):
                indicators["adx"] = indicators["adx"].get("adx", 0)

            # Extract timeframe (optional, defaults to "1m")
            timeframe = candle.get("timeframe", "1m")

            # Store candle
            current_app.candle_store.store_candle(
                symbol=candle["symbol"],
                timestamp=timestamp,
                ohlcv=ohlcv,
                indicators=indicators if indicators else None,
                source="node_server_bulk",
                timeframe=timeframe,
            )
            stored_count += 1

        except Exception as e:
            logger.error(f"Error storing candle {idx}: {e}")
            errors.append(f"Candle {idx}: {str(e)}")

    logger.info(f"Bulk sync completed: {stored_count}/{len(candles)} candles stored")
    
    response_data = {
        "status": "ok" if errors else "success",
        "total_candles": len(candles),
        "stored_count": stored_count,
    }
    
    if errors:
        response_data["errors"] = errors[:10]  # Limit to first 10 errors in response

    return jsonify(response_data), 201 if stored_count > 0 else 400


@api_bp.post("/analyze")
def analyze():
    if not _check_api_key():
        return jsonify(error="Unauthorized"), 401

    payload = request.get_json(silent=True)
    try:
        validate_payload(payload)
    except PayloadValidationError as exc:
        return jsonify(error=str(exc)), 400

    # Step 1: Check multi-timeframe alignment
    tf_alignment = check_timeframe_alignment(
        current_app.db,
        payload["symbol"],
        payload["signal_type"],
    )
    
    logger.info(
        f"TF alignment for {payload['symbol']} ({payload['signal_type']}): "
        f"score={tf_alignment['tf_alignment_score']}, details={tf_alignment['details']}"
    )
    
    # Step 1b: Calculate reversal confluence (signal agreement from recent reversals)
    from brain_app.features import calculate_reversal_confluence
    confluence = calculate_reversal_confluence(
        current_app.db,
        payload["symbol"],
        payload["signal_type"],
        lookback_minutes=120  # Last 2 hours
    )
    logger.info(
        f"Reversal confluence for {payload['symbol']} ({payload['signal_type']}): "
        f"level={confluence['confluence_level']}, ratio={confluence['confluence_ratio']}, "
        f"boost={confluence['confidence_boost']}%"
    )
    
    # Step 2: Soft rejection with confluence override
    # If confluence is very_high or high, proceed even with low TF alignment
    confluence_level = confluence.get('confluence_level', 'none')
    tf_score = tf_alignment["tf_alignment_score"]
    
    if tf_score < 2 and confluence_level not in ['very_high', 'high']:
        # Hard reject: poor alignment AND poor confluence
        return jsonify(
            symbol=payload["symbol"],
            signal_type=payload["signal_type"],
            decision="reject",
            confidence=0.0,
            model_used="multi_tf_filter",
            reason="insufficient_timeframe_alignment_and_confluence",
            # Diagnostic info
            tf_alignment_score=tf_score,
            tf_alignment_details=tf_alignment["details"],
            per_timeframe_checks=tf_alignment.get("detailed_checks", {}),
            # New: Confluence details
            confluence_analysis=confluence,
            diagnostic={
                "message": f"TF Score {tf_alignment['tf_alignment_score']}/4 - Need ≥2 for analysis",
                "primary_tfs_status": {
                    "30m": "✓" if tf_alignment["tf_30m_aligned"] else "✗",
                    "1h": "✓" if tf_alignment["tf_1h_aligned"] else "✗",
                    "4h": "✓" if tf_alignment["tf_4h_aligned"] else "✗",
                    "12h": "✓" if tf_alignment["tf_12h_aligned"] else "✗",
                },
                "secondary_tfs_status": {
                    "5m": "✓" if tf_alignment["tf_5m_aligned"] else "✗",
                    "15m": "✓" if tf_alignment["tf_15m_aligned"] else "✗",
                }
            }
        ), 200
    
    # Step 3: Build feature vector with TF features
    features = build_feature_vector(payload, tf_features=tf_alignment)
    
    # Step 4: Get ML prediction (adjust threshold based on alignment strength)
    classifier = current_app.classifier
    
    if classifier.is_trained_model:
        import pandas as pd
        from brain_app.features import FEATURE_COLUMNS
        
        row = pd.DataFrame(
            [[features[col] for col in FEATURE_COLUMNS]],
            columns=FEATURE_COLUMNS
        )
        raw_confidence = float(classifier.pipeline.predict_proba(row)[0][1])
        
        # Adjust decision threshold based on alignment score AND confluence
        # Higher alignment = higher confidence requirement (stricter)
        # Score 4: require 0.75 (only take strongest signals when perfect alignment)
        # Score 3: require 0.60 (moderate threshold when 3 TFs confirm)
        # Score 2: require 0.45 (lenient threshold when only 2 TFs confirm)
        # Score 1: normally reject, but proceed with high confluence
        # Score 0: only proceed if confluence is very_high
        if tf_alignment["tf_alignment_score"] >= 4:
            threshold = 0.75
        elif tf_alignment["tf_alignment_score"] == 3:
            threshold = 0.60
        elif tf_alignment["tf_alignment_score"] == 2:
            threshold = 0.45
        elif tf_alignment["tf_alignment_score"] == 1 and confluence_level == 'very_high':
            threshold = 0.40  # Allow low TF score if confluence very strong
        else:
            threshold = 0.55  # Default stricter threshold
        
        decision = "accept" if raw_confidence >= threshold else "reject"
        
        # Confidence boost for high-conviction setups
        confidence = raw_confidence
        if tf_alignment["tf_alignment_score"] == 4 and raw_confidence >= 0.75:
            # Perfect alignment + strong model agreement = maximum confidence
            confidence = 0.98
        elif tf_alignment["tf_alignment_score"] == 4 and raw_confidence >= 0.60:
            # Perfect alignment + decent model agreement = boosted confidence
            confidence = min(0.90, raw_confidence + 0.15)
        elif tf_alignment["tf_alignment_score"] == 3 and raw_confidence >= 0.70:
            # Good alignment + strong model agreement = moderate boost
            confidence = min(0.85, raw_confidence + 0.10)
        
        # CONFLUENCE BOOST: Add confidence based on reversal agreement
        # Very high confluence (85%+): +40% confidence boost
        # High confluence (70-84%): +30% confidence boost
        # Medium confluence (55-69%): +15% confidence boost
        # Low confluence (40-54%): +5% confidence boost
        confluence_boost_pct = confluence.get('confidence_boost', 0) / 100.0  # Convert to decimal
        confidence += confluence_boost_pct
        confidence = min(0.99, confidence)  # Cap at 99%
        
        confidence = round(confidence, 4)
        
        logger.info(
            f"Confidence calculation: raw={raw_confidence:.3f} + tf_boost={tf_alignment['tf_alignment_score']} + "
            f"confluence_boost={confluence_boost_pct:.2f} = final={confidence:.3f}"
        )
    else:
        # Fallback to heuristic
        rsi = features.get("rsi", 50.0)
        confidence = 1.0 - (abs(rsi - 50.0) / 50.0)
        confidence = max(0.0, min(1.0, confidence))
        decision = "accept" if 30.0 <= rsi <= 70.0 else "reject"
    
    # Step 5: Analyze reversal signals from MFI detector
    reversal_analysis = analyze_reversal_signals(payload, payload["signal_type"])
    
    # Apply reversal-weighted decision adjustment
    reversal_decision = calculate_reversal_weighted_decision(
        confidence,
        reversal_analysis,
        tf_alignment["tf_alignment_score"]
    )
    
    # Update confidence with reversal adjustment
    confidence = reversal_decision["final_confidence"]
    
    logger.info(
        f"Reversal analysis for {payload['symbol']} ({payload['signal_type']}): "
        f"type={reversal_analysis['alignment_type']}, boost={reversal_decision['reversal_boost']:.1f}, "
        f"final_conf={confidence:.2%}"
    )
    
    # Step 6: Calculate optimal entry price and timing
    from brain_app.features import calculate_optimal_entry, classify_trade_type
    
    entry_analysis = calculate_optimal_entry(payload)
    trade_classification = classify_trade_type(payload, tf_alignment, confluence_level=confluence_level)
    
    return jsonify(
        symbol=payload["symbol"],
        signal_type=payload["signal_type"],
        decision=decision,
        confidence=confidence,
        model_used="trained" if classifier.is_trained_model else "heuristic_fallback",
        tf_alignment_score=tf_alignment["tf_alignment_score"],
        tf_alignment_details=tf_alignment["details"],
        per_timeframe_checks=tf_alignment.get("detailed_checks", {}),
        # Reversal detection analysis
        reversal_analysis={
            "has_reversal": reversal_analysis["has_reversal"],
            "reversal_signal": reversal_analysis["reversal_signal"],
            "reversal_confidence": reversal_analysis["reversal_confidence"],
            "alignment_type": reversal_analysis["alignment_type"],
            "aligns_with_signal": reversal_analysis["aligns_with_signal"],
            "price_trend": reversal_analysis["price_trend"],
            "mfi_trend": reversal_analysis["mfi_trend"],
            "reversal_boost": reversal_decision["reversal_boost"],
            "reasoning": reversal_decision["reasoning"],
        },
        # Entry guidance
        entry_guidance=entry_analysis,
        # Trade classification
        trade_classification=trade_classification,
        # Confluence analysis (new: tracks reversal signal agreement)
        confluence_analysis=confluence,
    ), 200
