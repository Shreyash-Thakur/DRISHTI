import pandas as pd
import pytest

from ml.service.predictor import Predictor
from ml.tests.fixtures import synthetic_training_set
from ml.train import save_models, train_classifier, train_regressor


def _train_tiny_models(tmp_path):
    df = synthetic_training_set()
    classifier, _ = train_classifier(df)
    regressor, _ = train_regressor(df)
    save_models(classifier, regressor, tmp_path)
    return tmp_path


def test_predictor_raises_if_models_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        Predictor(tmp_path, threshold=0.5)


def test_predictor_returns_none_fields_for_insufficient_history(tmp_path):
    model_dir = _train_tiny_models(tmp_path)
    predictor = Predictor(model_dir, threshold=0.5)
    as_of = pd.Timestamp("2026-07-13T00:00:05Z")
    metric_history = pd.DataFrame({
        "ts": [as_of], "node_id": ["pe-east"], "interface": ["eth0"],
        "utilization_pct": [10.0], "latency_ms": [5.0], "jitter_ms": [1.0], "packet_loss_pct": [0.0],
    })
    event_history = pd.DataFrame(columns=["ts", "node_id", "severity"])
    result = predictor.predict("pe-east", "eth0", metric_history, event_history, as_of)
    assert result == {
        "node_id": "pe-east", "interface": "eth0",
        "precursor_probability": None, "estimated_seconds_to_impact": None,
    }


def test_predictor_returns_numeric_fields_with_enough_history(tmp_path):
    model_dir = _train_tiny_models(tmp_path)
    predictor = Predictor(model_dir, threshold=0.0)  # threshold 0 forces the regressor to always run
    as_of = pd.Timestamp("2026-07-13T00:02:00Z")
    ts = pd.date_range(end=as_of, periods=25, freq="5s")
    metric_history = pd.DataFrame({
        "ts": ts, "node_id": ["pe-east"] * 25, "interface": ["eth0"] * 25,
        "utilization_pct": [10.0 + i for i in range(25)],
        "latency_ms": [5.0] * 25, "jitter_ms": [1.0] * 25, "packet_loss_pct": [0.0] * 25,
    })
    event_history = pd.DataFrame(columns=["ts", "node_id", "severity"])
    result = predictor.predict("pe-east", "eth0", metric_history, event_history, as_of)
    assert isinstance(result["precursor_probability"], float)
    assert isinstance(result["estimated_seconds_to_impact"], float)
