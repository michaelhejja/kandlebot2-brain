"""Train the signal-acceptance classifier from a labeled historical CSV.

Expected CSV columns:
  - one column per entry in brain_app.features.FEATURE_COLUMNS
  - a label column (default name: "label") with 1 = good signal, 0 = bad signal

Usage:
    python -m training.train_model --csv data/labeled_signals.csv --out models/model.joblib
"""
from __future__ import annotations

import argparse
import os

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from brain_app.features import FEATURE_COLUMNS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Path to labeled historical signals CSV")
    parser.add_argument("--label-column", default="label")
    parser.add_argument("--out", default="models/model.joblib")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()

    df = pd.read_csv(args.csv)

    missing = [c for c in FEATURE_COLUMNS + [args.label_column] if c not in df.columns]
    if missing:
        raise SystemExit(
            f"CSV is missing required columns: {missing}. "
            "Update brain_app/features.py FEATURE_COLUMNS or your CSV to match."
        )

    X = df[FEATURE_COLUMNS]
    y = df[args.label_column]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=args.test_size, random_state=args.random_state, stratify=y
    )

    pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                RandomForestClassifier(
                    n_estimators=300,
                    max_depth=None,
                    class_weight="balanced",
                    random_state=args.random_state,
                ),
            ),
        ]
    )
    pipeline.fit(X_train, y_train)

    y_pred = pipeline.predict(X_test)
    y_proba = pipeline.predict_proba(X_test)[:, 1]

    print(classification_report(y_test, y_pred))
    print(f"ROC AUC: {roc_auc_score(y_test, y_proba):.4f}")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    joblib.dump(pipeline, args.out)
    print(f"Saved model to {args.out}")


if __name__ == "__main__":
    main()
