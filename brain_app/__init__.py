"""Application factory for the kandlebot2 signal-analysis brain service."""
from flask import Flask

from .config import Config
from .database import Database
from .candle_store import CandleStore
from .gap_recovery import GapRecovery
from .model import SignalClassifier
from .routes import api_bp
from .debug_routes import debug_bp


def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    # One model instance per process, loaded once at startup.
    app.classifier = SignalClassifier(
        model_path=app.config["MODEL_PATH"],
        decision_threshold=app.config["DECISION_THRESHOLD"],
    )

    # Initialize database and candle store
    app.db = Database(app.config["DATABASE_PATH"])
    app.candle_store = CandleStore(app.db)
    app.gap_recovery = GapRecovery(
        node_server_url=app.config.get("NODE_SERVER_URL"),
        exchange_name=app.config.get("EXCHANGE_NAME", "binance"),
    )

    app.register_blueprint(api_bp)
    app.register_blueprint(debug_bp)
    return app
