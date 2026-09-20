from pathlib import Path

from fastapi.testclient import TestClient

import backend.main as main


def design(endpoint="binary"):
    payload = {
        "title": "Response test",
        "indication": "Solid tumor",
        "phase": "Phase 2",
        "endpoint": endpoint,
        "control_label": "Control",
        "treatment_label": "Drug",
        "alpha": 0.05,
        "target_power": 0.8,
        "dropout_rate": 0.1,
        "allocation_ratio": 1,
        "interim_fraction": 0.5,
        "simulations": 250,
        "seed": 123,
    }
    if endpoint == "binary":
        payload.update(control_rate=0.3, treatment_rate=0.48)
    elif endpoint == "continuous":
        payload.update(control_mean=10, treatment_mean=12, standard_deviation=5)
    else:
        payload.update(hazard_ratio=0.7, event_rate=0.65)
    return payload


def test_trial_studio_simulation_persists_a_reproducible_artifact(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(main, "RUNTIME", runtime)
    monkeypatch.setattr(main, "TRIAL_ROOT", runtime / "trial-runs")
    client = TestClient(main.app)

    response = client.post("/api/trial-studio/simulate", json=design())
    assert response.status_code == 200
    result = response.json()
    assert result["provenance"]["seed"] == 123
    assert result["synthetic_cohort"]["rows"]
    assert client.get(f"/api/trial-studio/runs/{result['run_id']}").json()["run_id"] == result["run_id"]
    assert client.get("/api/trial-studio/runs").json()[0]["run_id"] == result["run_id"]


def test_trial_studio_supports_continuous_and_survival_endpoints():
    client = TestClient(main.app)
    for endpoint in ("continuous", "time_to_event"):
        response = client.post("/api/trial-studio/simulate", json=design(endpoint))
        assert response.status_code == 200
        assert response.json()["design"]["endpoint"] == endpoint


def test_trial_studio_randomization_is_seeded():
    client = TestClient(main.app)
    payload = {"participant_ids": ["P1", "P2", "P3", "P4"], "allocation_ratio": 1, "seed": 19}
    first = client.post("/api/trial-studio/randomize", json=payload).json()
    second = client.post("/api/trial-studio/randomize", json=payload).json()
    assert first["assignments"] == second["assignments"]
    assert first["counts"] == {"Control": 2, "Investigational": 2}


def test_trial_studio_route_is_served():
    response = TestClient(main.app).get("/trial-studio")
    assert response.status_code == 200
    assert "Make a trial design" in response.text
    assert "id=\"designForm\"" in response.text
