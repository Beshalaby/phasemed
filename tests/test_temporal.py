from backend.models import BoundingBox, Geometry, PatientModel, PatientObject
from backend.temporal import compare_models


def make_model(model_id: str, volume: float) -> PatientModel:
    return PatientModel(id=model_id, patient_id="P1", study_id=model_id, objects=[PatientObject(id="nodule", type="finding", label="Pulmonary nodule", geometry=Geometry(centroid=(10, 10, 10), bounding_box=BoundingBox(min=(9, 9, 9), max=(11, 11, 11)), volume_mm3=volume))])


def test_temporal_match_calculates_volume_change():
    result = compare_models(make_model("current", 150), make_model("prior", 100))
    assert result["links"][0]["type"] == "changed_from"
    assert result["links"][0]["changes"]["volume_change_percent"] == 50.0


def test_temporal_match_records_resolved_prior_object():
    prior = make_model("prior", 100)
    current = PatientModel(id="current", patient_id="P1", study_id="current", objects=[])
    result = compare_models(current, prior)
    assert result["links"][0]["type"] == "resolved"
    assert result["links"][0]["changes"]["reason"] == "not matched in current study"
