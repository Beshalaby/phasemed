import json

from backend.elasticsearch import ElasticsearchBridge, config, model_document, study_document
from backend.models import Geometry, PatientModel, PatientObject


def test_elasticsearch_config_is_server_side(monkeypatch):
    monkeypatch.setenv("PHASEMED_ELASTICSEARCH_URL", "https://search.example.test:443")
    monkeypatch.setenv("PHASEMED_ELASTICSEARCH_API_KEY", "encoded-key")
    settings = config()

    assert settings.configured
    assert settings.url == "https://search.example.test:443"
    assert settings.api_key == "encoded-key"
    assert ElasticsearchBridge(settings).status()["elasticsearch_endpoint"] == "search.example.test"


def test_index_documents_exclude_patient_identifiers_and_uids():
    study = study_document(
        {
            "id": "study-local-1",
            "patient_id": "patient-secret",
            "patient_name": "Jane Doe",
            "study_instance_uid": "1.2.826.0.1.3680043.8.498.123456",
            "description": "CT chest",
            "modality": "CT",
            "status": "ready",
        }
    )
    model = PatientModel(
        id="model:study-local-1:v1",
        patient_id="patient-secret",
        study_id="study-local-1",
        objects=[
            PatientObject(
                id="seg:1.2.826.0.1.3680043.8.498.123456:1",
                type="anatomy",
                label="Left lung",
                geometry=Geometry(centroid=(0, 0, 0), bounding_box={"min": (0, 0, 0), "max": (1, 1, 1)}),
            )
        ],
    )
    indexed = json.dumps({"study": study, "model": model_document(model)})

    assert "patient-secret" not in indexed
    assert "Jane Doe" not in indexed
    assert "1.2.826.0.1.3680043.8.498.123456" not in indexed
    assert "Left lung" in indexed
