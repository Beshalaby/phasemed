"""Optional DICOMweb and C-STORE adapters for the local workstation.

The local file pipeline remains the default. When an Orthanc or another
WADO-RS/QIDO-RS/STOW-RS endpoint is configured, these helpers provide a thin,
standards-shaped integration surface without making a network call by
accident. Secrets and endpoint URLs are read from environment variables and
are never returned in full by the capability endpoint.
"""

from __future__ import annotations

import json
import os
import io
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from pathlib import Path

import pydicom
from pydicom.datadict import dictionary_VR, tag_for_keyword
from pydicom.tag import Tag


@dataclass(frozen=True)
class DICOMwebConfig:
    base_url: str | None
    username: str | None = None
    password: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    @property
    def local(self) -> bool:
        return (self.base_url or "").startswith("local://")

    @property
    def safe_base(self) -> str | None:
        if not self.base_url:
            return None
        return self.base_url.rstrip("/")


def config() -> DICOMwebConfig:
    return DICOMwebConfig(
        base_url=os.getenv("PHASEMED_DICOMWEB_URL") or os.getenv("ORTHANC_URL"),
        username=os.getenv("PHASEMED_DICOMWEB_USER") or os.getenv("ORTHANC_USER"),
        password=os.getenv("PHASEMED_DICOMWEB_PASSWORD") or os.getenv("ORTHANC_PASSWORD"),
    )


def _local_root() -> Path:
    configured = os.getenv("PHASEMED_DICOMWEB_SOURCE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    runtime = Path(os.getenv("PHASEMED_RUNTIME_DIR", ".runtime")).expanduser()
    return (runtime / "source-studies").resolve()


def _local_catalog() -> dict[str, dict[str, list[Path]]]:
    """Index the local source directory using the same DICOMweb-shaped objects."""
    catalog: dict[str, dict[str, list[Path]]] = {}
    root = _local_root()
    if not root.exists():
        return catalog
    for path in root.rglob("*"):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        try:
            dataset = pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
            study_uid = str(getattr(dataset, "StudyInstanceUID", ""))
            series_uid = str(getattr(dataset, "SeriesInstanceUID", ""))
        except Exception:
            continue
        if study_uid and series_uid:
            catalog.setdefault(study_uid, {}).setdefault(series_uid, []).append(path)
    return catalog


def _json_element(dataset, keyword: str) -> dict | None:
    tag_value = tag_for_keyword(keyword)
    if tag_value is None or Tag(tag_value) not in dataset:
        return None
    element = dataset[Tag(tag_value)]
    value = element.value
    values = list(value) if isinstance(value, (list, tuple)) else [value]
    if element.VR == "PN":
        values = [{"Alphabetic": str(item)} for item in values]
    else:
        values = [item if isinstance(item, (str, int, float, bool)) else str(item) for item in values]
    return {"vr": element.VR or dictionary_VR(Tag(tag_value)) or "UN", "Value": values}


def _metadata(path: Path) -> dict:
    dataset = pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
    result = {}
    for keyword in (
        "PatientName", "PatientID", "StudyInstanceUID", "StudyDate", "StudyDescription", "Modality",
        "SeriesInstanceUID", "SeriesNumber", "SeriesDescription", "SOPInstanceUID", "SOPClassUID",
        "InstanceNumber", "Rows", "Columns", "PixelSpacing", "SliceThickness", "ImagePositionPatient",
        "ImageOrientationPatient",
    ):
        element = _json_element(dataset, keyword)
        if element:
            tag_value = tag_for_keyword(keyword)
            result[f"{Tag(tag_value).group:04X}{Tag(tag_value).element:04X}"] = element
    return result


def _local_instances(study_uid: str, series_uid: str | None = None) -> list[Path]:
    catalog = _local_catalog()
    series = catalog.get(study_uid, {})
    paths = [path for uid, values in series.items() if series_uid is None or uid == series_uid for path in values]
    return sorted(paths, key=lambda path: str(path))


def _headers(accept: str) -> dict[str, str]:
    return {"Accept": accept, "User-Agent": "Phasmed-local/0.1"}


def request(path: str, *, accept: str = "application/dicom+json", method: str = "GET", body: bytes | None = None, content_type: str | None = None) -> tuple[bytes, str]:
    settings = config()
    if not settings.configured or not settings.safe_base:
        raise RuntimeError("DICOMweb endpoint is not configured")
    url = f"{settings.safe_base}/{path.lstrip('/')}"
    headers = _headers(accept)
    if content_type:
        headers["Content-Type"] = content_type
    auth = (settings.username, settings.password)
    if auth[0] and auth[1]:
        import base64
        headers["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    try:
        with urlopen(Request(url, data=body, headers=headers, method=method), timeout=20) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"DICOMweb request failed: {exc}") from exc


def qido_studies() -> list[dict]:
    settings = config()
    if settings.local:
        studies = []
        for study_uid, series in _local_catalog().items():
            instances = _local_instances(study_uid)
            if instances:
                studies.append(_metadata(instances[0]))
        return studies
    payload, _ = request("studies", accept="application/dicom+json")
    decoded = json.loads(payload.decode("utf-8"))
    return decoded if isinstance(decoded, list) else []


def qido_series(study_uid: str) -> list[dict]:
    settings = config()
    if settings.local:
        return [_metadata(paths[0]) for paths in _local_catalog().get(study_uid, {}).values() if paths]
    payload, _ = request(f"studies/{study_uid}/series", accept="application/dicom+json")
    decoded = json.loads(payload.decode("utf-8"))
    return decoded if isinstance(decoded, list) else []


def qido_instances(study_uid: str, series_uid: str) -> list[dict]:
    settings = config()
    if settings.local:
        return [_metadata(path) for path in _local_instances(study_uid, series_uid)]
    payload, _ = request(f"studies/{study_uid}/series/{series_uid}/instances", accept="application/dicom+json")
    decoded = json.loads(payload.decode("utf-8"))
    return decoded if isinstance(decoded, list) else []


def wado_metadata(study_uid: str, series_uid: str, instance_uid: str | None = None) -> list[dict] | dict:
    settings = config()
    if settings.local:
        paths = _local_instances(study_uid, series_uid)
        if instance_uid:
            return next((_metadata(path) for path in paths if _json_element(pydicom.dcmread(str(path), stop_before_pixels=True, force=False), "SOPInstanceUID")["Value"][0] == instance_uid), {})
        return [_metadata(path) for path in paths]
    suffix = f"/instances/{instance_uid}/metadata" if instance_uid else "/metadata"
    payload, _ = request(f"studies/{study_uid}/series/{series_uid}{suffix}", accept="application/dicom+json")
    decoded = json.loads(payload.decode("utf-8"))
    return decoded


def wado_instance(study_uid: str, series_uid: str, instance_uid: str) -> bytes:
    settings = config()
    if settings.local:
        for path in _local_instances(study_uid, series_uid):
            dataset = pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
            if str(getattr(dataset, "SOPInstanceUID", "")) == instance_uid:
                return path.read_bytes()
        raise RuntimeError("Local DICOMweb instance not found")
    payload, _ = request(
        f"studies/{study_uid}/series/{series_uid}/instances/{instance_uid}",
        accept="application/dicom",
    )
    return payload


def stow_dicom(data: bytes) -> dict:
    settings = config()
    if settings.local:
        try:
            dataset = pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True, force=False)
            study_uid = str(getattr(dataset, "StudyInstanceUID", ""))
            instance_uid = str(getattr(dataset, "SOPInstanceUID", ""))
            if not study_uid or not instance_uid:
                raise ValueError("DICOM object is missing StudyInstanceUID or SOPInstanceUID")
            destination = _local_root() / "stow" / study_uid
            destination.mkdir(parents=True, exist_ok=True)
            (destination / f"{instance_uid}.dcm").write_bytes(data)
            return {"response": {"stored": [instance_uid], "study": study_uid}, "content_type": "application/dicom+json"}
        except Exception as exc:
            raise RuntimeError(f"Local DICOMweb STOW failed: {exc}") from exc
    payload, content_type = request("studies", accept="application/dicom+json", method="POST", body=data, content_type="application/dicom")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        decoded = {"raw": payload.decode("utf-8", errors="replace")}
    return {"response": decoded, "content_type": content_type}


def capability_status() -> dict[str, str]:
    settings = config()
    try:
        import pynetdicom  # type: ignore
        cstore = "available" if os.getenv("PHASEMED_CSTORE_PORT") else "not_configured"
    except ImportError:
        cstore = "not_installed"
    source_available = settings.configured and (not settings.local or _local_root().exists())
    return {"dicomweb": "available" if source_available else "not_configured", "qido_rs": "available" if source_available else "not_configured", "wado_rs": "available" if source_available else "not_configured", "stow_rs": "available" if source_available else "not_configured", "local_dicom": "available" if _local_root().exists() else "not_configured", "c_store": cstore}
