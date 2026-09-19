from pathlib import Path

import numpy as np
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from backend.compiler import compile_study
from backend.dicom import index_directory


SEGMENTATION_STORAGE = "1.2.840.10008.5.1.4.1.1.66.4"


def write_segmentation(path: Path) -> None:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = SEGMENTATION_STORAGE
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientName = "Seg^Patient"
    ds.PatientID = "SEG-001"
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.SOPClassUID = SEGMENTATION_STORAGE
    ds.Modality = "SEG"
    ds.StudyDate = "20260919"
    ds.StudyDescription = "Segmentation study"
    ds.Rows = 4
    ds.Columns = 4
    ds.NumberOfFrames = 1
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 1
    ds.BitsStored = 1
    ds.HighBit = 0
    ds.PixelRepresentation = 0
    ds.SegmentationType = "BINARY"
    ds.PixelSpacing = [1.0, 1.0]
    ds.SliceThickness = 2.0
    segment = Dataset()
    segment.SegmentNumber = 1
    segment.SegmentLabel = "Left lung"
    ds.SegmentSequence = Sequence([segment])
    identification = Dataset()
    identification.ReferencedSegmentNumber = 1
    frame = Dataset()
    frame.SegmentIdentificationSequence = Sequence([identification])
    ds.PerFrameFunctionalGroupsSequence = Sequence([frame])
    mask = np.zeros((4, 4), dtype=np.uint8)
    mask[1:3, 1:3] = 1
    ds.PixelData = np.packbits(mask.reshape(-1), bitorder="little").tobytes()
    ds.save_as(path)


def test_dicom_segmentation_becomes_reviewable_patient_object(tmp_path: Path):
    write_segmentation(tmp_path / "seg.dcm")
    study, _ = index_directory(tmp_path, "seg-study")
    model = compile_study("seg-study", tmp_path, lambda *_: None)
    lung = next(item for item in model.objects if item.label == "Left lung")
    assert study.modality == "SEG"
    assert lung.type == "anatomy"
    assert lung.geometry and lung.geometry.volume_mm3 == 8.0
    assert lung.geometry.segmentation_id
    assert lung.geometry.mesh_id and (tmp_path / lung.metadata["mesh_path"]).exists()
    assert lung.metadata["mesh_face_count"] == 16
    assert model.capabilities["validated_anatomy_segmentation"] == "available"
    assert model.capabilities["mesh_generation"] == "available"
