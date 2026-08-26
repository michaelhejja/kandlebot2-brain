"""Loads the trained classifier (if present) and produces accept/reject
decisions for incoming trade signals. Falls back to a simple RSI heuristic
when no trained model artifact exists yet, so the service is usable
end-to-end before the ML pipeline has been trained.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .features import FEATURE_COLUMNS

logger = logging.getLogger(__name__)


class SignalClassifier:
    def __init__(self, model_path: str, decision_threshold: float = 0.5):
        self.model_path = model_path
        self.decision_threshold = decision_threshold
        self.pipeline = self._load_model()

    def _load_model(self):
        if not os.path.exists(self.model_path):
            logger.warning(
                "No trained model found at '%s'; using heuristic fallback. "
                "Run training/train_model.py to train and save a real model.",
                self.model_path,
            )
            return None

        import joblib  # local import: only needed when a model actually loads

        return joblib.load(self.model_path)

    @property
    def is_trained_model(self) -> bool:
        return self.pipeline is not None

    def predict(self, features: dict[str, float]) -> dict[str, Any]:
        if self.pipeline is not None:
            return self._predict_with_model(features)
        return self._predict_with_heuristic(features)

    def _predict_with_model(self, features: dict[str, float]) -> dict[str, Any]:
        import pandas as pd

        row = pd.DataFrame([[features[col] for col in FEATURE_COLUMNS]], columns=FEATURE_COLUMNS)
        confidence = float(self.pipeline.predict_proba(row)[0][1])
        return {
            "decision": "accept" if confidence >= self.decision_threshold else "reject",
            "confidence": round(confidence, 4),
            "model_used": "trained",
        }

    @staticmethod
    def _predict_with_heuristic(features: dict[str, float]) -> dict[str, Any]:
        """Placeholder logic: reject overbought/oversold RSI extremes."""
        rsi = features.get("rsi", 50.0)
        confidence = 1.0 - (abs(rsi - 50.0) / 50.0)
        confidence = max(0.0, min(1.0, confidence))
        return {
            "decision": "accept" if 30.0 <= rsi <= 70.0 else "reject",
            "confidence": round(confidence, 4),
            "model_used": "heuristic_fallback",
        }
