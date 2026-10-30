"""Decision forensics — Phase 1.

Append-only JSONL log of every "interesting" brain decision, written
synchronously before the HTTP response returns. Survives Brain restarts
(it's a file) and is the source of truth for later Mongo sync.

File layout:  data/decisions/YYYY-MM-DD.jsonl   (one file per UTC day)
Schema:       v1   (bump SCHEMA_VERSION when adding/renaming fields)

An "interesting" decision is:
  - any ACCEPT, OR
  - a REJECT where raw_confidence >= INTERESTING_REJECT_THRESHOLD
    (i.e. "close calls" worth reviewing; silent noise-floor rejects are skipped)

This module is pure observability — it must never raise into the request
path. All errors are swallowed with a logger.error().
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
INTERESTING_REJECT_THRESHOLD = 0.55

# Resolve data dir relative to this file so it works regardless of cwd.
_BASE_DIR = Path(__file__).resolve().parent.parent
_LOG_DIR = _BASE_DIR / "data" / "decisions"


def _ensure_log_dir() -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def _todays_path() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _ensure_log_dir() / f"{today}.jsonl"


def _is_interesting(decision: str, raw_confidence: float) -> bool:
    if decision == "accept":
        return True
    return raw_confidence >= INTERESTING_REJECT_THRESHOLD


def _derive_rejection_reason(
    decision: str,
    raw_confidence: float,
    threshold: float,
    smoothing_result: dict[str, Any] | None,
) -> str | None:
    """Best-effort root-cause tag for rejects. Returned as a short string
    the UI/queries can group on."""
    if decision != "reject":
        return None
    if smoothing_result and smoothing_result.get("applied_opposing_cooldown"):
        return "opposing_direction_cooldown"
    if smoothing_result and smoothing_result.get("applied_hysteresis"):
        return "hysteresis_blocked_flip"
    if raw_confidence < threshold:
        return "below_threshold"
    return "unknown"


def log_decision(
    *,
    payload: dict[str, Any],
    decision: str,
    final_confidence: float,
    raw_confidence: float,
    threshold: float,
    tf_alignment: dict[str, Any],
    confluence: dict[str, Any] | None,
    confluence_level: str | None,
    smoothing_result: dict[str, Any] | None,
    trade_classification: dict[str, Any] | None,
    model_used: str,
    extras: dict[str, Any] | None = None,
) -> None:
    """Append one decision record to today's JSONL file.

    Never raises. Safe to call unconditionally — the "interesting" filter
    is applied here so call sites stay simple.
    """
    try:
        if not _is_interesting(decision, raw_confidence):
            return

        record = {
            "schema_version": SCHEMA_VERSION,
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": payload.get("symbol"),
            "signal_type": payload.get("signal_type"),
            "decision": decision,
            "rejection_reason": _derive_rejection_reason(
                decision, raw_confidence, threshold, smoothing_result
            ),
            # Confidence stack — raw model prob vs the boosted number the UI sees.
            # Distinguishing these is critical for troubleshooting; see roadmap P2.
            "raw_confidence": round(float(raw_confidence), 4),
            "final_confidence": round(float(final_confidence), 4),
            "threshold": round(float(threshold), 4),
            # Market state
            "close": payload.get("close"),
            "rsi": payload.get("rsi"),
            "mfi": payload.get("mfi"),
            "atr": payload.get("atr"),
            # Multi-timeframe context
            "tf_alignment_score": tf_alignment.get("tf_alignment_score"),
            "tf_alignment_details": tf_alignment.get("details"),
            # Confluence
            "confluence_level": confluence_level,
            "confluence_boost": (confluence or {}).get("confidence_boost"),
            # Smoothing + cooldown trace
            "smoothing": {
                "applied_hysteresis": (smoothing_result or {}).get("applied_hysteresis"),
                "applied_opposing_cooldown": (smoothing_result or {}).get(
                    "applied_opposing_cooldown"
                ),
                "opposing_cooldown_details": (smoothing_result or {}).get(
                    "opposing_cooldown_details"
                ),
                "previous_decision": (smoothing_result or {}).get("previous_decision"),
                "confidence_gap": (smoothing_result or {}).get("confidence_gap"),
            },
            # Classification
            "trade_type": (trade_classification or {}).get("trade_type"),
            "grade": (trade_classification or {}).get("grade"),
            "model_used": model_used,
        }

        if extras:
            record["extras"] = extras

        path = _todays_path()
        line = json.dumps(record, default=str, separators=(",", ":"))
        # Append + fsync-ish: open in append-text mode, write, close.
        # Python buffers to OS; for our volume (≤ a few writes/min) this is fine.
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:  # pragma: no cover — must never crash the request
        logger.error(f"forensics.log_decision failed: {e}", exc_info=True)


def read_recent(
    *, symbol: str | None = None, limit: int = 50, days_back: int = 2
) -> list[dict[str, Any]]:
    """Read the most recent N decisions from today + prior N-1 days.
    Useful for `/debug/recent-decisions` endpoints and ad-hoc inspection."""
    out: list[dict[str, Any]] = []
    try:
        _ensure_log_dir()
        files = sorted(_LOG_DIR.glob("*.jsonl"), reverse=True)[:days_back]
        for path in files:
            try:
                with open(path, encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if symbol and rec.get("symbol") != symbol:
                    continue
                out.append(rec)
                if len(out) >= limit:
                    return out
    except Exception as e:  # pragma: no cover
        logger.error(f"forensics.read_recent failed: {e}", exc_info=True)
    return out
