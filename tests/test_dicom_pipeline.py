from pathlib import Path

import numpy as np
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

from backend.compiler import compile_study
from backend.dicom import index_directory


def write_dicom(path: Path, study_uid: str, series_uid: str, index: int) -> None:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientName = "Test^Patient"
    ds.PatientID = "TEST-001"
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.Modality = "CT"
    ds.StudyDate = "20260919"
    ds.StudyDescription = "Test chest"
    ds.Rows = 8
    ds.Columns = 8
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.InstanceNumber = index
    ds.ImagePositionPatient = [0, 0, float(index)]
    ds.PixelData = (np.arange(64, dtype=np.uint16) + index).tobytes()
    ds.save_as(path)


def test_dicom_index_and_compile(tmp_path: Path):
    study_uid = generate_uid(); series_uid = generate_uid()
    for index in range(3):
        write_dicom(tmp_path / f"slice-{index}.dcm", study_uid, series_uid, index)
    study, series = index_directory(tmp_path, "study-test")
    assert study.image_count == 3
    assert study.modality == "CT"
    assert series[0]["instance_count"] == 3
    updates = []
    model = compile_study("study-test", tmp_path, lambda stage, progress, message: updates.append((stage, progress, message)))
    assert model.study_id == "study-test"
    assert model.objects[0].geometry is not None
    assert any(item.type == "region" for item in model.objects)
    assert model.capabilities["unlabeled_intensity_regions"] == "available"
    assert model.capabilities["dicom_ingestion"] == "available"
    assert updates[-1][1] == 100
