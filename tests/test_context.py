from backend.context import bind_context, normalize_context
from backend.models import PatientModel, PatientObject


def test_context_bundle_normalizes_and_binds_to_specific_object():
    model = PatientModel(id="model:test", patient_id="P1", study_id="S1", objects=[PatientObject(id="nodule", type="finding", label="Pulmonary nodule", metadata={"region": "right upper lobe"})])
    items = normalize_context({"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "DiagnosticReport", "id": "r1", "code": {"text": "Pulmonary nodule follow-up"}, "text": "Pulmonary nodule in right upper lobe measured 8 mm."}}]})
    bindings = bind_context(model, items)
    assert items[0]["type"] == "diagnosticreport"
    assert bindings[0].target_type == "object"
    assert bindings[0].target_id == "nodule"
    assert model.objects[0].context[0].source.id == "source:r1"
