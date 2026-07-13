import lightgbm as lgb

from ml.features import feature_names
from ml.tests.fixtures import synthetic_training_set
from ml.train import save_models, train_classifier, train_regressor


def test_train_classifier_and_regressor_produce_usable_models(tmp_path):
    df = synthetic_training_set()

    classifier, auc = train_classifier(df)
    regressor, mae = train_regressor(df)
    assert 0.0 <= auc <= 1.0
    assert mae >= 0.0

    save_models(classifier, regressor, tmp_path)
    assert (tmp_path / "classifier.txt").exists()
    assert (tmp_path / "regressor.txt").exists()
    assert (tmp_path / "features.json").exists()

    loaded = lgb.Booster(model_file=str(tmp_path / "classifier.txt"))
    prediction = loaded.predict(df[feature_names()].iloc[:1])
    assert len(prediction) == 1


def test_load_training_set_raises_on_empty_parquet(tmp_path):
    import pandas as pd

    from ml.train import load_training_set

    empty_path = tmp_path / "empty.parquet"
    pd.DataFrame({"a": []}).to_parquet(empty_path)
    try:
        load_training_set(empty_path)
        assert False, "expected ValueError"
    except ValueError:
        pass
