from pathlib import Path

from backend.adapters import run_segmentation


def test_configured_segmentation_adapter_must_emit_dicom_seg(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    command = f'"{__import__("sys").executable}" -c "from pathlib import Path; Path(r\'{tmp_path}/not-a-seg.bin\').write_bytes(b\'not dicom\')"'
    monkeypatch.setenv("PHASEMED_SEGMENTATION_COMMAND", command)
    result = run_segmentation(tmp_path, "study-test")
    assert result["status"] == "failed"
    assert "DICOM SEG" in result["error"]
