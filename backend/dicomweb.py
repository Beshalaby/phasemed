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
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class DICOMwebConfig:
    base_url: str | None
    username: str | None = None
    password: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

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
    payload, _ = request("studies", accept="application/dicom+json")
    decoded = json.loads(payload.decode("utf-8"))
    return decoded if isinstance(decoded, list) else []


def qido_series(study_uid: str) -> list[dict]:
    payload, _ = request(f"studies/{study_uid}/series", accept="application/dicom+json")
    decoded = json.loads(payload.decode("utf-8"))
    return decoded if isinstance(decoded, list) else []


def qido_instances(study_uid: str, series_uid: str) -> list[dict]:
    payload, _ = request(f"studies/{study_uid}/series/{series_uid}/instances", accept="application/dicom+json")
    decoded = json.loads(payload.decode("utf-8"))
    return decoded if isinstance(decoded, list) else []


def wado_metadata(study_uid: str, series_uid: str, instance_uid: str | None = None) -> list[dict] | dict:
    suffix = f"/instances/{instance_uid}/metadata" if instance_uid else "/metadata"
    payload, _ = request(f"studies/{study_uid}/series/{series_uid}{suffix}", accept="application/dicom+json")
    decoded = json.loads(payload.decode("utf-8"))
    return decoded


def wado_instance(study_uid: str, series_uid: str, instance_uid: str) -> bytes:
    payload, _ = request(
        f"studies/{study_uid}/series/{series_uid}/instances/{instance_uid}",
        accept="application/dicom",
    )
    return payload


def stow_dicom(data: bytes) -> dict:
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
    return {"dicomweb": "available" if settings.configured else "not_configured", "qido_rs": "available" if settings.configured else "not_configured", "wado_rs": "available" if settings.configured else "not_configured", "stow_rs": "available" if settings.configured else "not_configured", "c_store": cstore}
