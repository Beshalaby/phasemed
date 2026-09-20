import io
import threading
import time

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.models import PatientModel, PatientObject
import backend.voice as voice
from backend.voice import resolve_command, status


CHEST_LABELS = [
    "lung_upper_lobe_left",
    "lung_lower_lobe_left",
    "lung_upper_lobe_right",
    "lung_middle_lobe_right",
    "lung_lower_lobe_right",
    "trachea",
    "esophagus",
    "heart",
    "aorta",
    "rib_left_4",
    "rib_right_4",
    "vertebrae_T5",
]
OBJECTS = [{"id": f"obj-{index}", "type": "anatomy", "label": label} for index, label in enumerate(CHEST_LABELS)]
OBJECTS.append({"id": "obj-volume", "type": "volume", "label": "1.25x1.25 LUNG"})
REGION_OBJECTS = OBJECTS + [
    {"id": "region-1", "type": "region", "label": "High-intensity region · unlabeled 01"},
    {"id": "region-2", "type": "region", "label": "High-intensity region · unlabeled 02"},
]
FINDING_OBJECTS = REGION_OBJECTS + [{"id": "finding-1", "type": "finding", "label": "spiculated nodule"}]


def labels_for(transcript: str) -> list[str]:
    return sorted(resolve_command(transcript, OBJECTS)["labels"])


def test_side_qualifier_selects_every_lobe_on_that_side():
    assert labels_for("highlight right lung") == ["lung_lower_lobe_right", "lung_middle_lobe_right", "lung_upper_lobe_right"]
    assert labels_for("highlight the left lung") == ["lung_lower_lobe_left", "lung_upper_lobe_left"]


def test_unsided_request_selects_both_sides():
    assert labels_for("highlight the lungs") == [
        "lung_lower_lobe_left",
        "lung_lower_lobe_right",
        "lung_middle_lobe_right",
        "lung_upper_lobe_left",
        "lung_upper_lobe_right",
    ]


def test_extra_qualifiers_narrow_to_one_lobe():
    assert labels_for("highlight the upper right lobe") == ["lung_upper_lobe_right"]


def test_synonyms_and_fillers_are_tolerated():
    assert labels_for("uh, highlight the windpipe please") == ["trachea"]
    assert labels_for("show me the heart") == ["heart"]


def test_spoken_numbers_select_the_numbered_structure():
    assert labels_for("highlight rib four on the right") == ["rib_right_4"]
    assert labels_for("highlight the fourth left rib") == ["rib_left_4"]


def test_a_spoken_number_must_agree_with_the_label():
    # rib_right_4 is the only numbered right rib in the fixture, so a different number matches nothing.
    assert labels_for("highlight rib seven on the right") == []


def test_clear_intent_takes_no_targets():
    result = resolve_command("clear the highlight", OBJECTS)
    assert result["intent"] == "clear"
    assert result["targets"] == []


def test_unmatched_anatomy_reports_no_targets():
    result = resolve_command("highlight the spleen", OBJECTS)
    assert result["targets"] == []
    assert result["summary"] == "No matching structure"


def test_volume_objects_are_never_targets():
    assert "1.25x1.25 LUNG" not in resolve_command("highlight the lungs", OBJECTS)["labels"]


def test_resolution_is_traceable():
    trace = resolve_command("highlight right lung", OBJECTS)["trace"]
    assert trace["normalised"] == "highlight right lung"
    assert "right" in trace["tokens"]
    assert trace["candidates"] and trace["candidates"][0]["score"] > 0


def test_local_engine_needs_no_api_key(monkeypatch):
    monkeypatch.delenv("PHASEMED_ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("PHASEMED_STT_PROVIDER", raising=False)
    monkeypatch.setattr(voice, "local_available", lambda: True)
    assert voice.config().provider == "local"
    assert status()["voice_input"] == "configured"
    assert status()["voice_engine"].startswith("local-whisper:")


def test_hosted_engine_is_the_fallback_when_no_local_model_is_installed(monkeypatch):
    monkeypatch.delenv("PHASEMED_STT_PROVIDER", raising=False)
    monkeypatch.setattr(voice, "local_available", lambda: False)
    monkeypatch.delenv("PHASEMED_ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert voice.config().provider == "none"
    assert status()["voice_input"] == "not_configured"
    monkeypatch.setenv("PHASEMED_ELEVENLABS_API_KEY", "sk_test")
    assert voice.config().provider == "elevenlabs"


def test_provider_can_be_pinned(monkeypatch):
    monkeypatch.setattr(voice, "local_available", lambda: True)
    monkeypatch.setenv("PHASEMED_ELEVENLABS_API_KEY", "sk_test")
    monkeypatch.setenv("PHASEMED_STT_PROVIDER", "elevenlabs")
    assert voice.config().provider == "elevenlabs"
    monkeypatch.setenv("PHASEMED_STT_PROVIDER", "local")
    assert voice.config().provider == "local"


def test_transcribe_without_any_engine_explains_the_options(monkeypatch):
    monkeypatch.setattr(voice, "local_available", lambda: False)
    monkeypatch.delenv("PHASEMED_ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="faster-whisper"):
        voice.transcribe(b"not-audio")


def test_vocabulary_prompt_carries_the_model_labels():
    prompt = voice.vocabulary_prompt(CHEST_LABELS)
    assert "trachea" in prompt and "lobe" in prompt and "highlight" in prompt
    assert len(prompt.split(",")) <= voice.VOCAB_PROMPT_WORDS + 1


def _model_in_isolated_runtime(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(main, "RUNTIME", runtime)
    monkeypatch.setattr(main, "STUDY_ROOT", runtime / "studies")
    monkeypatch.setattr(main, "MODEL_ROOT", runtime / "models")
    monkeypatch.setattr(main, "DB_PATH", runtime / "phasemed.sqlite3")
    main.STUDY_ROOT.mkdir(parents=True)
    main.MODEL_ROOT.mkdir(parents=True)
    model = PatientModel(
        id="model:test:v1",
        patient_id="patient-1",
        study_id="study-1",
        objects=[PatientObject(id=f"obj-{index}", type="anatomy", label=label) for index, label in enumerate(CHEST_LABELS)],
    )
    main.save_model(model)
    return model


def test_voice_command_endpoint_resolves_a_supplied_transcript(tmp_path, monkeypatch):
    model = _model_in_isolated_runtime(tmp_path, monkeypatch)
    client = TestClient(main.app)
    response = client.post(f"/api/patient-models/{model.id}/voice-command", data={"transcript": "highlight right lung"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "highlight"
    resolved = {obj.id: obj.label for obj in model.objects}
    assert payload["targets"]
    assert all(resolved[target].endswith("_right") for target in payload["targets"])


def test_voice_command_endpoint_requires_input(tmp_path, monkeypatch):
    model = _model_in_isolated_runtime(tmp_path, monkeypatch)
    client = TestClient(main.app)
    assert client.post(f"/api/patient-models/{model.id}/voice-command", data={"transcript": "   "}).status_code == 400


def test_voice_command_is_recorded_in_the_audit_log(tmp_path, monkeypatch):
    model = _model_in_isolated_runtime(tmp_path, monkeypatch)
    client = TestClient(main.app)
    client.post(f"/api/patient-models/{model.id}/voice-command", data={"transcript": "highlight the trachea"})
    events = client.get("/api/audit-events", params={"subject": model.id}).json()["events"]
    assert any(event["type"] == "voice.command" for event in events)


def test_health_reports_voice_capability():
    capabilities = TestClient(main.app).get("/api/health").json()["capabilities"]
    assert "voice_input" in capabilities


def test_local_transcription_never_writes_the_clip_to_disk(monkeypatch):
    """Recorded speech stays in memory; only a file-like object reaches the model."""
    seen: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            seen["audio"] = audio
            seen["prompt"] = kwargs.get("initial_prompt")
            segment = type("Segment", (), {"text": " highlight the trachea "})()
            return [segment], None

    monkeypatch.setattr(voice, "_load_local_model", lambda settings: FakeModel())
    monkeypatch.setattr(voice, "local_available", lambda: True)
    monkeypatch.delenv("PHASEMED_STT_PROVIDER", raising=False)
    text = voice.transcribe(b"fake-audio-bytes", filename="clip.webm", vocabulary=CHEST_LABELS)
    assert text == "highlight the trachea"
    assert isinstance(seen["audio"], io.BytesIO)
    assert "trachea" in (seen["prompt"] or "")


def test_transcription_is_serialised_per_model(monkeypatch):
    """Two clips must not decode concurrently through one CTranslate2 model."""
    overlap = {"max": 0, "live": 0}
    lock = threading.Lock()

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            with lock:
                overlap["live"] += 1
                overlap["max"] = max(overlap["max"], overlap["live"])
            time.sleep(0.05)
            with lock:
                overlap["live"] -= 1
            return [type("Segment", (), {"text": "highlight the heart"})()], None

    monkeypatch.setattr(voice, "_load_local_model", lambda settings: FakeModel())
    monkeypatch.setattr(voice, "local_available", lambda: True)
    monkeypatch.delenv("PHASEMED_STT_PROVIDER", raising=False)
    threads = [threading.Thread(target=lambda: voice.transcribe(b"clip")) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert overlap["max"] == 1


def test_view_commands_move_the_camera_not_the_model():
    for phrase, action in [
        ("zoom in", "zoom_in"),
        ("move closer", "zoom_in"),
        ("zoom out", "zoom_out"),
        ("pull back", "zoom_out"),
        ("reset the view", "reset_view"),
        ("stop spinning", "spin_off"),
        ("start rotating", "spin_on"),
    ]:
        result = resolve_command(phrase, OBJECTS)
        assert result["intent"] == "view", phrase
        assert result["action"] == action, phrase
        assert result["targets"] == []


def test_conjunctions_select_both_requests():
    assert labels_for("highlight the heart and the aorta") == ["aorta", "heart"]
    assert labels_for("highlight right lung and the trachea") == [
        "lung_lower_lobe_right",
        "lung_middle_lobe_right",
        "lung_upper_lobe_right",
        "trachea",
    ]


def test_a_side_fragment_keeps_both_sides():
    """"the left and right lung" must not let the merge swallow the second side."""
    assert labels_for("highlight the left and right lung") == [
        "lung_lower_lobe_left",
        "lung_lower_lobe_right",
        "lung_middle_lobe_right",
        "lung_upper_lobe_left",
        "lung_upper_lobe_right",
    ]


def test_each_clause_resolves_independently():
    """A side in one clause must not leak into the next."""
    labels = labels_for("highlight the left lung and the heart")
    assert "heart" in labels
    assert not any(label.endswith("_right") for label in labels)


def test_conjoined_targets_are_deduplicated():
    assert labels_for("highlight the heart and the heart") == ["heart"]


def test_abnormalities_select_reviewed_findings_when_present():
    result = resolve_command("highlight abnormalities", FINDING_OBJECTS)
    assert result["labels"] == ["spiculated nodule"]


def test_abnormalities_fall_back_to_unlabeled_regions():
    result = resolve_command("show me any lesions", REGION_OBJECTS)
    assert sorted(result["labels"]) == [
        "High-intensity region · unlabeled 01",
        "High-intensity region · unlabeled 02",
    ]


def test_abnormality_synonyms():
    for phrase in ["highlight the findings", "highlight any nodules", "highlight anything suspicious"]:
        assert resolve_command(phrase, FINDING_OBJECTS)["labels"] == ["spiculated nodule"], phrase


def test_everything_selects_all_non_volume_objects():
    result = resolve_command("highlight everything", REGION_OBJECTS)
    assert len(result["targets"]) == len(REGION_OBJECTS) - 1  # the volume is never a target


def test_long_selections_get_a_compact_summary():
    result = resolve_command("highlight everything", REGION_OBJECTS)
    assert "more" in result["summary"]


def test_clause_resolution_is_traceable():
    trace = resolve_command("highlight the heart and the aorta", OBJECTS)["trace"]
    assert [entry["clause"] for entry in trace["clauses"]] == ["highlight the heart", "the aorta"]
