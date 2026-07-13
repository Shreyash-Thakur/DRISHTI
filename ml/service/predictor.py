"""Loads trained models (Task 5's ml/train.py output) and runs
classifier -> (if above threshold) -> regressor inference for one node/interface."""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from ml.features import compute_features, feature_names


class Predictor:
    def __init__(self, model_dir: Path, threshold: float) -> None:
        classifier_path = Path(model_dir) / "classifier.txt"
        regressor_path = Path(model_dir) / "regressor.txt"
        features_path = Path(model_dir) / "features.json"
        for path in (classifier_path, regressor_path, features_path):
            if not path.exists():
                raise FileNotFoundError(
                    f"missing model artifact {path} — run `python -m ml.train` before starting the service"
                )
        saved_features = json.loads(features_path.read_text())
        expected = feature_names()
        if saved_features != expected:
            raise ValueError(
                "ml/models/features.json does not match ml.features.feature_names() — "
                "retrain with `python -m ml.train` after changing ml/features.py"
            )
        self.classifier = lgb.Booster(model_file=str(classifier_path))
        self.regressor = lgb.Booster(model_file=str(regressor_path))
        self.features = saved_features
        self.threshold = threshold

    def predict(
        self,
        node_id: str,
        interface: str,
        metric_history: pd.DataFrame,
        event_history: pd.DataFrame,
        as_of: pd.Timestamp,
    ) -> dict:
        feats = compute_features(metric_history, event_history, as_of)
        if feats is None:
            return {
                "node_id": node_id, "interface": interface,
                "precursor_probability": None, "estimated_seconds_to_impact": None,
            }
        row = pd.DataFrame([feats])[self.features]
        probability = float(self.classifier.predict(row)[0])
        estimated_seconds_to_impact = None
        if probability >= self.threshold:
            estimated_seconds_to_impact = float(self.regressor.predict(row)[0])
        return {
            "node_id": node_id, "interface": interface,
            "precursor_probability": probability,
            "estimated_seconds_to_impact": estimated_seconds_to_impact,
        }
