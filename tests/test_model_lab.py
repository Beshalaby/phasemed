from backend.model_lab import cross_validate, dataset_rows, extract_features, predict, train_binary, train_forest, train_regression
from backend.models import BoundingBox, Geometry, PatientModel, PatientObject, Relationship, TemporalLink


def model(model_id: str, finding: bool, volume: float) -> PatientModel:
    objects = [
        PatientObject(
            id=f"{model_id}:volume",
            type="volume",
            label="Source volume",
            geometry=Geometry(centroid=(0, 0, 0), bounding_box=BoundingBox(min=(0, 0, 0), max=(1, 1, 1)), volume_mm3=volume, mesh_id=f"mesh:{model_id}"),
        )
    ]
    if finding:
        objects.append(PatientObject(id=f"{model_id}:finding", type="finding", label="Finding", geometry=Geometry(centroid=(1, 1, 1), bounding_box=BoundingBox(min=(0, 0, 0), max=(2, 2, 2)), volume_mm3=volume / 2)))
    return PatientModel(
        id=model_id,
        patient_id=f"patient:{model_id}",
        study_id=f"study:{model_id}",
        objects=objects,
        relationships=[Relationship(id=f"rel:{model_id}", source_object_id=objects[0].id, target_object_id=objects[-1].id, type="associated_with", method="test") ] if finding else [],
        temporal_links=[TemporalLink(id=f"time:{model_id}", source_object_id=objects[-1].id, target_object_id=objects[-1].id, type="new", confidence=1.0)] if finding else [],
    )


def test_feature_extraction_is_stable_and_typed():
    features = extract_features(model("m1", True, 100))
    assert features["object_count"] == 2.0
    assert features["finding_count"] == 1.0
    assert features["mesh_count"] == 1.0
    assert features["total_volume_mm3"] == 150.0


def test_feature_extraction_includes_source_pixel_and_finding_signal():
    patient = model("imaging", True, 100)
    patient.metadata.update({"source_series_count": 2, "source_instance_count": 3})
    patient.objects[0].metadata["pixel_statistics"] = [{"mean": 10, "min": -100, "max": 300}, {"mean": 30, "min": -200, "max": 500}]
    features = extract_features(patient)
    assert features["source_series_count"] == 2.0
    assert features["source_mean_intensity"] == 20.0
    assert features["source_min_intensity"] == -200.0
    assert features["finding_volume_mm3"] == 50.0


def test_binary_model_trains_and_explains_prediction():
    models = [model("m1", False, 10), model("m2", False, 12), model("m3", True, 100), model("m4", True, 120)]
    rows = dataset_rows(models, {item.id: int(item.objects[-1].type == "finding") for item in models})
    artifact = train_binary(rows, name="Finding presence", iterations=120)
    result = predict(artifact, models[-1])
    assert artifact["type"] == "binary-logistic-regression"
    assert artifact["training"]["row_count"] == 4
    assert result["prediction"] == 1
    assert set(result["contributions"]) == {item["name"] for item in artifact["feature_schema"]}


def test_regression_model_supports_continuous_outcomes():
    models = [model(f"m{i}", False, volume) for i, volume in enumerate((10, 20, 30, 40), start=1)]
    rows = dataset_rows(models, {item.id: float(index) * 2.5 for index, item in enumerate(models, start=1)}, task="regression")
    artifact = train_regression(rows, name="Volume outcome", iterations=120)
    result = predict(artifact, models[-1])
    assert artifact["type"] == "linear-regression"
    assert set(("mse", "rmse", "mae", "r2")) <= set(artifact["training"]["metrics"])
    assert isinstance(result["prediction"], float)


def test_random_forest_classifier_is_deterministic_and_explainable():
    models = [model("m1", False, 10), model("m2", False, 12), model("m3", True, 100), model("m4", True, 120)]
    rows = dataset_rows(models, {item.id: int(item.objects[-1].type == "finding") for item in models})
    artifact = train_forest(rows, name="Finding forest", task="binary", n_estimators=12, max_depth=4, seed=17)
    result = predict(artifact, models[-1])
    assert artifact["type"] == "random-forest-classifier"
    assert len(artifact["trees"]) == 12
    assert set(artifact["feature_importance"]) == set(FEATURE["name"] for FEATURE in artifact["feature_schema"])
    assert 0.0 <= result["probability_positive"] <= 1.0
    assert len(result["tree_predictions"]) == 12


def test_random_forest_regressor_supports_continuous_outcomes():
    models = [model(f"m{i}", False, volume) for i, volume in enumerate((10, 20, 30, 40, 50), start=1)]
    rows = dataset_rows(models, {item.id: float(index) * 2.5 for index, item in enumerate(models, start=1)}, task="regression")
    artifact = train_forest(rows, name="Volume forest", task="regression", n_estimators=10, max_depth=4, seed=17)
    result = predict(artifact, models[-1])
    assert artifact["type"] == "random-forest-regressor"
    assert artifact["training"]["validation"]["row_count"] == 1
    assert isinstance(result["prediction"], float)


def test_large_cohort_persists_deterministic_validation_metrics():
    models = [model(f"m{i}", i >= 4, float(i * 10)) for i in range(1, 7)]
    rows = dataset_rows(models, {item.id: int(item.objects[-1].type == "finding") for item in models})
    artifact = train_binary(rows, name="Validated finding model", iterations=80)
    validation = artifact["training"]["validation"]
    assert validation["row_count"] == 1
    assert validation["model_ids"] == ["m6"]
    assert "accuracy" in validation["metrics"]


def test_cross_validation_is_deterministic_and_stratified():
    models = [model(f"cv{i}", i % 2 == 1, float(i * 10)) for i in range(1, 9)]
    rows = dataset_rows(models, {item.id: int(item.objects[-1].type == "finding") for item in models})
    first = cross_validate(rows, task="binary", algorithm="random-forest", name="CV forest", folds=4, n_estimators=8, max_depth=3, seed=17)
    second = cross_validate(rows, task="binary", algorithm="random-forest", name="CV forest", folds=4, n_estimators=8, max_depth=3, seed=17)
    assert first["fold_count"] == 4
    assert first["metrics"] == second["metrics"]
    assert all(len(fold["model_ids"]) == 2 for fold in first["folds"])
