"""Debug routes for checking database state on Heroku."""
import logging
from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)
debug_bp = Blueprint("debug", __name__, url_prefix="/debug")


@debug_bp.route("/db-status", methods=["GET"])
def db_status():
    """Check what's in the database."""
    from flask import current_app
    
    db = current_app.db
    
    try:
        with db.get_connection() as conn:
            # Count candles by timeframe
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT timeframe, COUNT(*) as count, MAX(timestamp) as latest
                FROM candles
                GROUP BY timeframe
                ORDER BY timeframe
                """
            )
            results = cursor.fetchall()
            
            timeframe_stats = {}
            for row in results:
                timeframe_stats[row["timeframe"]] = {
                    "count": row["count"],
                    "latest": row["latest"]
                }
            
            # Get total candles
            cursor.execute("SELECT COUNT(*) as total FROM candles")
            total = cursor.fetchone()["total"]
            
            return jsonify({
                "status": "ok",
                "total_candles": total,
                "by_timeframe": timeframe_stats
            }), 200
    except Exception as e:
        logger.error(f"Error checking DB status: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@debug_bp.route("/latest-candles", methods=["GET"])
def latest_candles():
    """Get latest candle from each timeframe."""
    from flask import current_app
    
    db = current_app.db
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            candles = {}
            
            for tf in ["1m", "5m", "15m", "30m", "1h", "4h", "12h"]:
                cursor.execute(
                    """
                    SELECT symbol, timeframe, timestamp, close, ema_20, ema_50, ema_200, 
                           crsi, rsi, adx, atr, mfi
                    FROM candles
                    WHERE timeframe = ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                    """,
                    (tf,)
                )
                row = cursor.fetchone()
                
                if row:
                    candles[tf] = {
                        "symbol": row["symbol"],
                        "timestamp": row["timestamp"],
                        "close": row["close"],
                        "ema_20": row["ema_20"],
                        "ema_50": row["ema_50"],
                        "ema_200": row["ema_200"],
                        "crsi": row["crsi"],
                        "rsi": row["rsi"],
                        "adx": row["adx"],
                        "atr": row["atr"],
                        "mfi": row["mfi"],
                    }
                else:
                    candles[tf] = None
            
            return jsonify({"latest_candles": candles}), 200
    except Exception as e:
        logger.error(f"Error fetching latest candles: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
