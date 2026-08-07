"""Trains the precursor classifier and time-to-impact regressor from
ml/dataset/training.parquet (see ml/dataset/generate.py), saves both plus the
feature list to ml/models/. Run manually after generate.py: `python -m ml.train`."""
from __future__ import annotations

import json
from pathlib import Path


import lightgbm as lgb
import pandas as pd
import time

from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    matthews_corrcoef,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import GroupShuffleSplit, StratifiedGroupKFold

from ml.config import get_settings
from ml.features import feature_names


def load_training_set(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if df.empty:
        raise ValueError(f"training set at {path} is empty — run ml/dataset/generate.py first")
    return df


def _fault_run_group(df: pd.DataFrame) -> pd.Series:
    """Leave-one-scenario-instance-out grouping: every contiguous run of the same
    node_id+interface+is_precursor value is one group, so a train/test split never
    puts samples from the same fault run (or the same baseline stretch) on both
    sides — that would leak and inflate held-out metrics."""
    key = df["node_id"] + "|" + df["interface"]
    key_changed = key != key.shift()
    precursor_changed = df["is_precursor"].diff().fillna(0) != 0
    return (key_changed | precursor_changed).cumsum()


def train_classifier(df: pd.DataFrame) -> tuple[lgb.LGBMClassifier, float]:
    """Uses StratifiedGroupKFold rather than GroupShuffleSplit: a plain group
    shuffle split can — and with small group counts, will — land a holdout fold
    containing only one class, which makes roc_auc_score undefined. Stratifying
    by is_precursor while still respecting fault-run groups avoids that failure
    mode for both the small synthetic test fixture and real (larger) datasets."""
    features = feature_names()
    groups = _fault_run_group(df)
    splitter = StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=0)
    train_idx, test_idx = next(
        (tr, te)
        for tr, te in splitter.split(df, df["is_precursor"], groups=groups)
        if df.iloc[te]["is_precursor"].nunique() > 1
    )
    model = lgb.LGBMClassifier(n_estimators=200, random_state=0)
    model.fit(
    df.iloc[train_idx][features],
    df.iloc[train_idx]["is_precursor"],
    )   
    X_test = df.iloc[test_idx][features]
    y_test = df.iloc[test_idx]["is_precursor"]

    probs = model.predict_proba(X_test)[:, 1]
    preds = model.predict(X_test)
    start = time.perf_counter()
    _ = model.predict(X_test)
    latency = (time.perf_counter() - start) * 1000 / len(X_test)
    auc = roc_auc_score(y_test, probs)
    accuracy = accuracy_score(y_test, preds)
    precision = precision_score(y_test, preds)
    recall = recall_score(y_test, preds)
    f1 = f1_score(y_test, preds)
    mcc = matthews_corrcoef(y_test, preds)

    print("\n=== Classifier Evaluation ===")
    print(f"Accuracy : {accuracy:.3f}")
    print(f"Precision: {precision:.3f}")
    print(f"Recall   : {recall:.3f}")
    print(f"F1 Score : {f1:.3f}")
    print(f"ROC-AUC  : {auc:.3f}")
    print(f"MCC             : {mcc:.3f}")
    print(f"Inference       : {latency:.3f} ms/sample")

    print("\nConfusion Matrix")
    print(confusion_matrix(y_test, preds))

    print("\nClassification Report")
    print(classification_report(y_test, preds))

    return model, {
    "auc": auc,
    "accuracy": accuracy,
    "precision": precision,
    "recall": recall,
    "f1": f1,
    "mcc": mcc,
    "latency": latency,
    }


def train_regressor(df: pd.DataFrame) -> tuple[lgb.LGBMRegressor, float]:
    features = feature_names()
    ramp_only = df[df["is_precursor"] == 1].reset_index(drop=True)
    groups = _fault_run_group(ramp_only)
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=0)
    train_idx, test_idx = next(splitter.split(ramp_only, groups=groups))
    model = lgb.LGBMRegressor(n_estimators=200, random_state=0)
    model.fit(ramp_only.iloc[train_idx][features], ramp_only.iloc[train_idx]["seconds_to_impact"])
    predictions = model.predict(ramp_only.iloc[test_idx][features])

    y_test = ramp_only.iloc[test_idx]["seconds_to_impact"]

    mae = mean_absolute_error(y_test, predictions)
    rmse = mean_squared_error(y_test, predictions) ** 0.5
    r2 = r2_score(y_test, predictions)

    print("\n========== REGRESSOR ==========")
    print(f"MAE   : {mae:.2f} s")
    print(f"RMSE  : {rmse:.2f} s")
    print(f"R²    : {r2:.3f}")

    return model, {
    "mae": mae,
    "rmse": rmse,
    "r2": r2,
    }


def save_models(classifier: lgb.LGBMClassifier, regressor: lgb.LGBMRegressor, model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    classifier.booster_.save_model(str(model_dir / "classifier.txt"))
    regressor.booster_.save_model(str(model_dir / "regressor.txt"))
    (model_dir / "features.json").write_text(json.dumps(feature_names()))


def main() -> None:
    settings = get_settings()
    df = load_training_set(settings.dataset_dir / "training.parquet")
    print("\n========== DATASET ==========")
    print(f"Samples            : {len(df)}")
    print(f"Features           : {len(feature_names())}")
    print(f"Positive samples   : {df['is_precursor'].sum()}")
    print(f"Negative samples   : {len(df)-df['is_precursor'].sum()}")
    classifier, cls = train_classifier(df)
    print("\n========== TOP 10 FEATURES ==========")

    importance = sorted(
        zip(feature_names(), classifier.feature_importances_),
        key=lambda x: x[1],
        reverse=True,
    )

    for name, score in importance[:10]:
        print(f"{name:35} {score}")
    regressor, reg = train_regressor(df)

    print("\n========== FINAL METRICS ==========")
    print(f"Accuracy   : {cls['accuracy']:.3f}")
    print(f"Precision  : {cls['precision']:.3f}")
    print(f"Recall     : {cls['recall']:.3f}")
    print(f"F1         : {cls['f1']:.3f}")
    print(f"ROC-AUC    : {cls['auc']:.3f}")
    print(f"MCC        : {cls['mcc']:.3f}")
    print(f"Latency    : {cls['latency']:.3f} ms/sample")

    print()

    print(f"MAE        : {reg['mae']:.2f}s")
    print(f"RMSE       : {reg['rmse']:.2f}s")
    print(f"R²         : {reg['r2']:.3f}")
    save_models(classifier, regressor, settings.model_dir)
    classifier_size = (
    settings.model_dir / "classifier.txt"
    ).stat().st_size / 1024

    regressor_size = (
        settings.model_dir / "regressor.txt"
    ).stat().st_size / 1024

    print("\n========== MODEL ==========")
    print(f"Classifier size : {classifier_size:.1f} KB")
    print(f"Regressor size  : {regressor_size:.1f} KB")
    print(f"saved models to {settings.model_dir}")


if __name__ == "__main__":
    main()
