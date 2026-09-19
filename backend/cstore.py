"""Opt-in local DICOM C-STORE SCP.

The receiver is deliberately disabled unless ``PHASEMED_CSTORE_PORT`` is set.
When enabled it binds to ``PHASEMED_CSTORE_HOST`` (localhost by default),
accepts storage presentation contexts, and writes each received instance to an
immutable staging directory grouped by StudyInstanceUID. It does not expose a
network listener accidentally and does not overwrite an existing SOP instance.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path


class CStoreReceiver:
    def __init__(self, root: Path, host: str, port: int, ae_title: str) -> None:
        self.root = root
        self.host = host
        self.port = port
        self.ae_title = ae_title[:16]
        self.server = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.server is not None

    def start(self) -> None:
        try:
            from pynetdicom import AE, AllStoragePresentationContexts, evt
        except ImportError as exc:
            raise RuntimeError("pynetdicom is required for C-STORE") from exc

        ae = AE(ae_title=self.ae_title)
        ae.supported_contexts = AllStoragePresentationContexts

        def handle_store(event):
            dataset = event.dataset
            dataset.file_meta = event.file_meta
            study_uid = str(getattr(dataset, "StudyInstanceUID", "unknown-study"))
            sop_uid = str(getattr(dataset, "SOPInstanceUID", uuid.uuid4().hex))
            destination = self.root / "incoming" / study_uid
            destination.mkdir(parents=True, exist_ok=True)
            path = destination / f"{sop_uid}.dcm"
            with self._lock:
                if not path.exists():
                    dataset.save_as(path, write_like_original=False)
            return 0x0000

        self.server = ae.start_server((self.host, self.port), block=False, evt_handlers=[(evt.EVT_C_STORE, handle_store)])

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server = None

    def status(self) -> dict[str, object]:
        return {"enabled": True, "running": self.running, "host": self.host, "port": self.port, "ae_title": self.ae_title, "staging_path": str(self.root / "incoming")}


def configured_receiver(root: Path) -> CStoreReceiver | None:
    value = os.getenv("PHASEMED_CSTORE_PORT")
    if not value:
        return None
    try:
        port = int(value)
    except ValueError as exc:
        raise RuntimeError("PHASEMED_CSTORE_PORT must be an integer") from exc
    host = os.getenv("PHASEMED_CSTORE_HOST", "127.0.0.1")
    ae_title = os.getenv("PHASEMED_CSTORE_AE_TITLE", "PHASEMED")
    return CStoreReceiver(root, host, port, ae_title)
