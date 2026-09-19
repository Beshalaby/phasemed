from backend.models import BoundingBox, Geometry, PatientModel, PatientObject
from backend.temporal import compare_models


def make_model(model_id: str, volume: float, extent: float = 2, surface: float = 24) -> PatientModel:
    half = extent / 2
    return PatientModel(id=model_id, patient_id="P1", study_id=model_id, objects=[PatientObject(id="nodule", type="finding", label="Pulmonary nodule", geometry=Geometry(centroid=(10, 10, 10), bounding_box=BoundingBox(min=(10 - half, 10 - half, 10 - half), max=(10 + half, 10 + half, 10 + half)), volume_mm3=volume, surface_area_mm2=surface))])


def test_temporal_match_calculates_volume_change():
    result = compare_models(make_model("current", 150, extent=3, surface=36), make_model("prior", 100, extent=2, surface=24))
    assert result["links"][0]["type"] == "changed_from"
    assert result["links"][0]["changes"]["volume_change_percent"] == 50.0
    assert result["links"][0]["changes"]["diameter_delta_mm"] == 1.0
    assert result["links"][0]["changes"]["surface_area_delta_mm2"] == 12.0


def test_temporal_match_records_resolved_prior_object():
    prior = make_model("prior", 100)
    current = PatientModel(id="current", patient_id="P1", study_id="current", objects=[])
    result = compare_models(current, prior)
    assert result["links"][0]["type"] == "resolved"
    assert result["links"][0]["changes"]["reason"] == "not matched in current study"
