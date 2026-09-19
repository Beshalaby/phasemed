from __future__ import annotations

import io
import json
import os
import struct
import zlib
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import pydicom
from pydicom.dataset import Dataset

from .models import StudySummary


def _value(ds: Dataset, name: str, default: str | None = None) -> str | None:
    value = getattr(ds, name, default)
    if value is None:
        return default
    return str(value)


def _float_pair(ds: Dataset, name: str, default: tuple[float, float] = (1.0, 1.0)) -> tuple[float, float]:
    value = getattr(ds, name, None)
    if value is None or len(value) < 2:
        return default
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return default


def read_metadata(path: Path) -> Dataset | None:
    try:
        return pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
    except Exception:
        try:
            return pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
        except Exception:
            return None


def discover_dicom_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file() and path.name not in {".DS_Store"}]


def index_directory(root: Path, study_id: str) -> tuple[StudySummary, list[dict]]:
    grouped: dict[str, list[tuple[Path, Dataset]]] = defaultdict(list)
    skipped = 0
    for path in discover_dicom_files(root):
        ds = read_metadata(path)
        if not ds or not _value(ds, "StudyInstanceUID"):
            skipped += 1
            continue
        grouped[_value(ds, "StudyInstanceUID")].append((path, ds))
    if not grouped:
        raise ValueError("No readable DICOM instances found")
    study_uid, primary_instances = max(grouped.items(), key=lambda item: len(item[1]))
    all_instances = [item for group in grouped.values() for item in group]
    first = primary_instances[0][1]
    series: dict[str, list[tuple[Path, Dataset]]] = defaultdict(list)
    for item in all_instances:
        series[_value(item[1], "SeriesInstanceUID", "unknown")].append(item)
    study = StudySummary(
        id=study_id,
        patient_id=_value(first, "PatientID"),
        patient_name=_value(first, "PatientName"),
        study_instance_uid=study_uid,
        study_date=_value(first, "StudyDate"),
        description=_value(first, "StudyDescription"),
        modality=next((value for _, ds in primary_instances if (value := _value(ds, "Modality")) and value != "SEG"), None) or _value(first, "Modality"),
        series_count=len(series),
        image_count=len(all_instances),
    )
    rows = []
    for series_uid, values in series.items():
        sample = values[0][1]
        rows.append({
            "series_instance_uid": series_uid,
            "study_instance_uid": _value(sample, "StudyInstanceUID"),
            "series_number": _value(sample, "SeriesNumber"),
            "description": _value(sample, "SeriesDescription"),
            "modality": _value(sample, "Modality"),
            "rows": int(getattr(sample, "Rows", 0) or 0),
            "columns": int(getattr(sample, "Columns", 0) or 0),
            "pixel_spacing": _float_pair(sample, "PixelSpacing"),
            "slice_thickness": float(getattr(sample, "SliceThickness", 1.0) or 1.0),
            "instance_count": len(values),
            "instances": [
                {"path": str(path.relative_to(root)), "sop_instance_uid": _value(ds, "SOPInstanceUID"), "instance_number": _value(ds, "InstanceNumber"), "z": _z_position(ds), "position": _position(ds), "orientation": _orientation(ds)}
                for path, ds in sorted(values, key=lambda item: (_z_position(item[1]), _value(item[1], "InstanceNumber", "0")))
            ],
        })
    return study, rows


def _z_position(ds: Dataset) -> float:
    position = getattr(ds, "ImagePositionPatient", None)
    if position and len(position) >= 3:
        try:
            return float(position[2])
        except (TypeError, ValueError):
            pass
    try:
        return float(getattr(ds, "SliceLocation", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _position(ds: Dataset) -> tuple[float, float, float] | None:
    value = getattr(ds, "ImagePositionPatient", None)
    if value is None or len(value) < 3:
        return None
    try:
        return float(value[0]), float(value[1]), float(value[2])
    except (TypeError, ValueError):
        return None


def _orientation(ds: Dataset) -> tuple[float, float, float, float, float, float] | None:
    value = getattr(ds, "ImageOrientationPatient", None)
    if value is None or len(value) < 6:
        return None
    try:
        return tuple(float(item) for item in value[:6])  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def extract_pixels(root: Path, instances: list[dict]) -> list[dict]:
    """Return lightweight pixel statistics for the selected series.

    This keeps the compiler useful without claiming clinical segmentation. Full
    pixel arrays are not sent to the browser; only deterministic statistics and
    source coordinates are returned.
    """
    stats = []
    for item in instances:
        path = root / item["path"]
        try:
            ds = pydicom.dcmread(str(path), force=False)
            arr = scaled_pixel_array(ds)
            stats.append({"path": item["path"], "min": float(arr.min()), "max": float(arr.max()), "mean": float(arr.mean()), "rows": int(arr.shape[-2]), "columns": int(arr.shape[-1])})
        except Exception as exc:
            stats.append({"path": item["path"], "error": str(exc)})
    return stats


def scaled_pixel_array(ds: Dataset):
    """Read a DICOM pixel array in modality units when rescale tags exist."""
    import numpy as np

    array = ds.pixel_array
    slope = float(getattr(ds, "RescaleSlope", 1.0) or 1.0)
    intercept = float(getattr(ds, "RescaleIntercept", 0.0) or 0.0)
    if slope != 1.0 or intercept != 0.0:
        array = array.astype(np.float32) * slope + intercept
    return array


def grayscale_png(root: Path, instance: dict, window_center: float | None = None, window_width: float | None = None) -> bytes:
    """Encode one readable DICOM frame as a dependency-free grayscale PNG."""
    import numpy as np

    path = root / instance["path"]
    ds = pydicom.dcmread(str(path), force=False)
    array = scaled_pixel_array(ds)
    if array.ndim > 2:
        array = array[0]
    return grayscale_array_png(array, window_center, window_width)


def grayscale_array_png(array, window_center: float | None = None, window_width: float | None = None) -> bytes:
    """Encode a 2-D numpy array as a contrast-stretched grayscale PNG."""
    import numpy as np

    if array.ndim > 2:
        array = array[0]
    values = array.astype(np.float32)
    if window_center is not None and window_width is not None and window_width > 0:
        low = float(window_center) - float(window_width) / 2.0
        high = float(window_center) + float(window_width) / 2.0
    else:
        low, high = float(values.min()), float(values.max())
    if high <= low:
        pixels = np.zeros(values.shape, dtype=np.uint8)
    else:
        pixels = np.clip((values - low) * 255.0 / (high - low), 0, 255).astype(np.uint8)
    raw = b"".join(b"\x00" + pixels[row].tobytes() for row in range(pixels.shape[0]))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", pixels.shape[1], pixels.shape[0], 8, 0, 0, 0, 0)
    return header + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def load_series_volume(root: Path, series: dict):
    """Load a readable single-frame series into a z,y,x numpy volume.

    Multi-frame objects are supported when pydicom exposes a 3-D pixel array.
    Incompatible or unreadable instances are skipped and reported by the
    caller through the returned list length rather than being fabricated.
    """
    import numpy as np

    frames = []
    for instance in series.get("instances", []):
        try:
            ds = pydicom.dcmread(str(root / instance["path"]), force=False)
            arr = scaled_pixel_array(ds)
            if arr.ndim == 2:
                frames.append(arr)
            elif arr.ndim == 3:
                frames.extend(list(arr))
        except Exception:
            continue
    if not frames:
        return None
    shapes = {tuple(frame.shape) for frame in frames}
    if len(shapes) != 1:
        return None
    return np.stack(frames, axis=0)


def volume_plane_png(root: Path, series: dict, plane: str = "axial", index: int | None = None, window_center: float | None = None, window_width: float | None = None) -> tuple[bytes, dict]:
    """Render an axial/coronal/sagittal source plane and its index metadata."""
    volume = load_series_volume(root, series)
    if volume is None:
        raise ValueError("Series pixels could not be reconstructed")
    plane = plane.lower()
    if plane == "axial":
        count = volume.shape[0]
        slice_index = max(0, min(index if index is not None else count // 2, count - 1))
        image = volume[slice_index]
    elif plane == "coronal":
        count = volume.shape[1]
        slice_index = max(0, min(index if index is not None else count // 2, count - 1))
        image = volume[:, slice_index, :]
    elif plane == "sagittal":
        count = volume.shape[2]
        slice_index = max(0, min(index if index is not None else count // 2, count - 1))
        image = volume[:, :, slice_index]
    else:
        raise ValueError("plane must be axial, coronal, or sagittal")
    if plane != "axial":
        # Superior at the top for reformats; axial rows already run anterior to posterior.
        image = image[::-1, ...]
    return grayscale_array_png(image, window_center, window_width), {"plane": plane, "index": slice_index, "count": count, "shape": list(volume.shape), "window_center": window_center, "window_width": window_width}


def unpack_upload(data: bytes, filename: str, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    if filename.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                target = (destination / member.filename).resolve()
                try:
                    target.relative_to(destination.resolve())
                except ValueError:
                    raise ValueError("Unsafe ZIP path")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
    else:
        (destination / Path(filename).name).write_bytes(data)
    return destination
