"""Trains the precursor classifier and time-to-impact regressor from
ml/dataset/training.parquet (see ml/dataset/generate.py), saves both plus the
feature list to ml/models/. Run manually after generate.py: `python -m ml.train`."""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd
from sklearn.metrics import mean_absolute_error, roc_auc_score
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
    model.fit(df.iloc[train_idx][features], df.iloc[train_idx]["is_precursor"])
    predictions = model.predict_proba(df.iloc[test_idx][features])[:, 1]
    auc = roc_auc_score(df.iloc[test_idx]["is_precursor"], predictions)
    return model, float(auc)


def train_regressor(df: pd.DataFrame) -> tuple[lgb.LGBMRegressor, float]:
    features = feature_names()
    ramp_only = df[df["is_precursor"] == 1].reset_index(drop=True)
    groups = _fault_run_group(ramp_only)
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=0)
    train_idx, test_idx = next(splitter.split(ramp_only, groups=groups))
    model = lgb.LGBMRegressor(n_estimators=200, random_state=0)
    model.fit(ramp_only.iloc[train_idx][features], ramp_only.iloc[train_idx]["seconds_to_impact"])
    predictions = model.predict(ramp_only.iloc[test_idx][features])
    mae = mean_absolute_error(ramp_only.iloc[test_idx]["seconds_to_impact"], predictions)
    return model, float(mae)


def save_models(classifier: lgb.LGBMClassifier, regressor: lgb.LGBMRegressor, model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    classifier.booster_.save_model(str(model_dir / "classifier.txt"))
    regressor.booster_.save_model(str(model_dir / "regressor.txt"))
    (model_dir / "features.json").write_text(json.dumps(feature_names()))


def main() -> None:
    settings = get_settings()
    df = load_training_set(settings.dataset_dir / "training.parquet")
    classifier, auc = train_classifier(df)
    regressor, mae = train_regressor(df)
    print(f"classifier held-out AUC: {auc:.3f}")
    print(f"regressor held-out MAE: {mae:.1f}s")
    save_models(classifier, regressor, settings.model_dir)
    print(f"saved models to {settings.model_dir}")


if __name__ == "__main__":
    main()
