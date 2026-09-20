"""Best-effort Elasticsearch indexing for the local Phasmed workspace.

The workstation remains local-first: SQLite and the persisted PatientModel files
are authoritative, while Elasticsearch is an asynchronous secondary index. Only
de-identified study/model metadata is sent; patient names, patient IDs, DICOM
UIDs, report excerpts, and absolute file paths never leave this process.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import threading
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import certifi

from .models import PatientModel

DEFAULT_INDEX = "phasemed"
MAX_QUEUE_SIZE = 256
_INDEX_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,199}$")


def _valid_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        return None
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        return None
    return value.strip().rstrip("/")


def _index_name(value: str | None) -> str:
    candidate = (value or DEFAULT_INDEX).strip().lower()
    return candidate if _INDEX_NAME.fullmatch(candidate) else DEFAULT_INDEX


@dataclass(frozen=True)
class ElasticsearchConfig:
    url: str | None
    api_key: str | None = field(repr=False)
    index: str = DEFAULT_INDEX
    timeout_s: float = 3.0

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key)


def config() -> ElasticsearchConfig:
    return ElasticsearchConfig(
        url=_valid_url(os.getenv("PHASEMED_ELASTICSEARCH_URL") or os.getenv("ELASTICSEARCH_URL")),
        api_key=(os.getenv("PHASEMED_ELASTICSEARCH_API_KEY") or os.getenv("ELASTICSEARCH_API_KEY") or "").strip() or None,
        index=_index_name(os.getenv("PHASEMED_ELASTICSEARCH_INDEX")),
    )


def _object_key(model_id: str, object_id: str) -> str:
    return hashlib.sha256(f"{model_id}:{object_id}".encode("utf-8")).hexdigest()[:24]


def study_document(study: dict[str, Any]) -> dict[str, Any]:
    """Build a searchable document without patient identifiers or DICOM UIDs."""
    description = str(study.get("description") or "")
    modality = str(study.get("modality") or "")
    status = str(study.get("status") or "")
    return {
        "entity_type": "study",
        "entity_id": str(study.get("id") or ""),
        "search_text": " ".join(value for value in (description, modality, status) if value),
        "description": description,
        "modality": modality,
        "status": status,
        "study_date": study.get("study_date"),
        "series_count": int(study.get("series_count") or 0),
        "image_count": int(study.get("image_count") or 0),
        "model_id": study.get("model_id"),
        "created_at": study.get("created_at"),
    }


def model_document(model: PatientModel) -> dict[str, Any]:
    labels = [str(obj.label) for obj in model.objects if obj.label]
    types = sorted({str(obj.type) for obj in model.objects})
    statuses = sorted({str(obj.review_status) for obj in model.objects})
    measurements = []
    for obj in model.objects:
        geometry = obj.geometry
        if geometry is None:
            continue
        measurements.append(
            f"{obj.label} volume {geometry.volume_mm3} surface {geometry.surface_area_mm2}"
        )
    return {
        "entity_type": "patient_model",
        "entity_id": model.id,
        "search_text": " ".join(labels + measurements),
        "study_id": model.study_id,
        "version": model.version,
        "object_count": len(model.objects),
        "object_labels": labels,
        "object_types": types,
        "review_statuses": statuses,
        "relationship_count": len(model.relationships),
        "context_count": len(model.context_bindings),
        "created_at": model.created_at,
        "pipeline_version": model.pipeline_version,
    }


class ElasticsearchBridge:
    """A small, non-blocking secondary index writer."""

    def __init__(self, settings: ElasticsearchConfig | None = None) -> None:
        self.settings = settings or config()
        self._queue: Queue[tuple[str, str, dict[str, Any]] | None] = Queue(maxsize=MAX_QUEUE_SIZE)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = "disabled" if not self.settings.configured else "starting"
        self._state_lock = threading.Lock()

    def start(self) -> None:
        if not self.settings.configured or self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="phasemed-elasticsearch", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        if self._thread:
            self._thread.join(timeout=1.5)
        self._thread = None

    def status(self) -> dict[str, str]:
        if not self.settings.configured:
            return {
                "elasticsearch": "not_configured",
                "elasticsearch_engine": "disabled",
                "elasticsearch_index": self.settings.index,
            }
        host = urlsplit(self.settings.url or "").hostname or "unknown"
        with self._state_lock:
            state = self._state
        return {
            "elasticsearch": "configured",
            "elasticsearch_engine": "elasticsearch",
            "elasticsearch_endpoint": host,
            "elasticsearch_index": self.settings.index,
            "elasticsearch_sync": state,
        }

    def index_study(self, study: dict[str, Any]) -> None:
        self._enqueue("study", str(study.get("id") or ""), study_document(study))

    def index_model(self, model: PatientModel) -> None:
        self._enqueue("patient_model", model.id, model_document(model))

    def _enqueue(self, entity_type: str, entity_id: str, document: dict[str, Any]) -> None:
        if not self.settings.configured or not entity_id:
            return
        self.start()
        try:
            self._queue.put_nowait((entity_type, entity_id, document))
        except Full:
            self._set_state("degraded")

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state

    def _run(self) -> None:
        try:
            self._ensure_index()
            self._set_state("ready")
        except Exception:
            self._set_state("degraded")
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.4)
            except Empty:
                continue
            if item is None:
                continue
            entity_type, entity_id, document = item
            try:
                self._index(entity_type, entity_id, document)
                if self.status().get("elasticsearch_sync") == "degraded":
                    self._set_state("ready")
            except Exception:
                self._set_state("degraded")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.settings.url or not self.settings.api_key:
            raise RuntimeError("Elasticsearch is not configured")
        endpoint = f"{self.settings.url}/{path.lstrip('/')}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "Authorization": f"ApiKey {self.settings.api_key}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(endpoint, data=data, headers=headers, method=method)
        context = ssl.create_default_context(cafile=certifi.where())
        with urlopen(request, timeout=self.settings.timeout_s, context=context) as response:
            body = response.read()
        return json.loads(body.decode("utf-8")) if body else {}

    def _ensure_index(self) -> None:
        try:
            self._request("HEAD", self.settings.index)
            return
        except HTTPError as error:
            if error.code != 404:
                raise
        self._request(
            "PUT",
            self.settings.index,
            {
                "settings": {"number_of_shards": 1, "number_of_replicas": 0},
                "mappings": {
                    "dynamic": "false",
                    "properties": {
                        "entity_type": {"type": "keyword"},
                        "entity_id": {"type": "keyword"},
                        "search_text": {"type": "text"},
                        "description": {"type": "text"},
                        "modality": {"type": "keyword"},
                        "status": {"type": "keyword"},
                        "object_labels": {"type": "keyword"},
                        "object_types": {"type": "keyword"},
                    },
                },
            },
        )

    def _index(self, entity_type: str, entity_id: str, document: dict[str, Any]) -> None:
        self._request(
            "PUT",
            f"{self.settings.index}/_doc/{quote(f'{entity_type}:{entity_id}', safe='')}",
            document,
        )
