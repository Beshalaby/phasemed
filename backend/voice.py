"""Speech-to-text for the hologram hold-to-talk control, plus a deterministic resolver.

Transcription runs locally by default: `faster-whisper` (CTranslate2) loads a small
Whisper model once and decodes the uploaded clip on the CPU, so the workstation keeps
working offline and no audio leaves the machine. An ElevenLabs key is an optional
fallback for hosts that would rather not carry the model files.

Whichever engine produces the text, the meaning is decided here by explicit rules --
no model call picks the structures, so the same words always select the same objects
and the decision can be replayed from the returned trace.
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
DEFAULT_STT_MODEL = "scribe_v1"
DEFAULT_LOCAL_MODEL = "base.en"
MAX_AUDIO_BYTES = 12 * 1024 * 1024

_local_model: Any = None
_local_model_key: tuple[str, str, str] | None = None
_local_lock = threading.Lock()
_transcribe_lock = threading.Lock()


@dataclass(frozen=True)
class VoiceConfig:
    api_key: str | None
    model_id: str
    local_model: str
    local_device: str
    local_compute_type: str
    provider: str

    @property
    def configured(self) -> bool:
        return self.provider != "none"


def local_available() -> bool:
    """True when faster-whisper is importable, without loading a model."""
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def config() -> VoiceConfig:
    api_key = os.getenv("PHASEMED_ELEVENLABS_API_KEY") or os.getenv("ELEVENLABS_API_KEY")
    requested = (os.getenv("PHASEMED_STT_PROVIDER") or "auto").strip().lower()
    if requested == "auto":
        # Local first: the workstation is local-first and a key should never be required.
        provider = "local" if local_available() else ("elevenlabs" if api_key else "none")
    elif requested == "local":
        provider = "local" if local_available() else "none"
    elif requested == "elevenlabs":
        provider = "elevenlabs" if api_key else "none"
    else:
        provider = "none"
    return VoiceConfig(
        api_key=api_key,
        model_id=os.getenv("PHASEMED_ELEVENLABS_STT_MODEL", DEFAULT_STT_MODEL),
        local_model=os.getenv("PHASEMED_WHISPER_MODEL", DEFAULT_LOCAL_MODEL),
        local_device=os.getenv("PHASEMED_WHISPER_DEVICE", "cpu"),
        local_compute_type=os.getenv("PHASEMED_WHISPER_COMPUTE_TYPE", "int8"),
        provider=provider,
    )


def status() -> dict[str, str]:
    settings = config()
    return {
        "voice_input": "configured" if settings.configured else "not_configured",
        "voice_engine": {"local": f"local-whisper:{settings.local_model}", "elevenlabs": f"elevenlabs:{settings.model_id}", "none": "not_configured"}[settings.provider],
    }


def _load_local_model(settings: VoiceConfig) -> Any:
    """Load the CTranslate2 Whisper model once and keep it resident."""
    global _local_model, _local_model_key
    key = (settings.local_model, settings.local_device, settings.local_compute_type)
    with _local_lock:
        if _local_model is not None and _local_model_key == key:
            return _local_model
        from faster_whisper import WhisperModel

        download_root = os.getenv("PHASEMED_WHISPER_CACHE_DIR") or None
        _local_model = WhisperModel(settings.local_model, device=settings.local_device, compute_type=settings.local_compute_type, download_root=download_root)
        _local_model_key = key
        return _local_model


VOCAB_PROMPT_WORDS = 64


def vocabulary_prompt(labels: Iterable[str]) -> str:
    """Bias the decoder towards words this PatientModel can actually be asked about.

    Whisper conditions on the prompt, so feeding it the open model's own anatomy
    vocabulary keeps "right lung" from being decoded as "ride long".
    """
    words: list[str] = []
    for word in ["highlight", "clear", "show", "mark", "zoom", "in", "out", "reset", "spin", "stop",
                 "abnormalities", "findings", "everything", "and", "left", "right", "upper", "middle", "lower", "lobe"]:
        words.append(word)
    for label in labels:
        for token in _label_tokens(label):
            if token not in words and not token.isdigit():
                words.append(token)
    return "Medical imaging commands. Vocabulary: " + ", ".join(words[:VOCAB_PROMPT_WORDS]) + "."


def warm_local() -> str:
    """Load the local model ahead of the first clip and report which one is resident."""
    settings = config()
    if settings.provider != "local":
        raise RuntimeError("Local speech-to-text is not the active engine")
    _load_local_model(settings)
    return settings.local_model


def transcribe_local(audio: bytes, *, suffix: str = ".webm", vocabulary: Iterable[str] | None = None) -> str:
    """Decode one clip with the bundled Whisper model.

    The clip is decoded straight from memory -- recorded speech is never written to
    disk -- and one model instance is serialised behind a lock, because a CTranslate2
    model must not run two transcriptions at once.
    """
    settings = config()
    model = _load_local_model(settings)
    prompt = vocabulary_prompt(vocabulary) if vocabulary else None
    with _transcribe_lock:
        segments, _info = model.transcribe(
            io.BytesIO(audio),
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=prompt,
            temperature=0.0,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


def _multipart(fields: dict[str, str], filename: str, content_type: str, audio: bytes) -> tuple[bytes, str]:
    boundary = f"----phasemed{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode()
    )
    parts.append(audio)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def transcribe_elevenlabs(audio: bytes, *, filename: str = "clip.webm", content_type: str = "audio/webm") -> str:
    """Optional hosted fallback; only used when a key is configured."""
    settings = config()
    if not settings.api_key:
        raise RuntimeError("ElevenLabs speech-to-text is not configured")
    body, content = _multipart({"model_id": settings.model_id}, filename, content_type, audio)
    request = Request(
        ELEVENLABS_STT_URL,
        data=body,
        method="POST",
        headers={"xi-api-key": settings.api_key, "Content-Type": content, "User-Agent": "Phasmed-local/0.1"},
    )
    try:
        with urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as error:  # surface the status without leaking the key
        raise RuntimeError(f"ElevenLabs returned HTTP {error.code}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError("ElevenLabs request failed") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("ElevenLabs returned an unreadable response") from error
    return str(payload.get("text") or "").strip()


def transcribe(audio: bytes, *, filename: str = "clip.webm", content_type: str = "audio/webm", vocabulary: Iterable[str] | None = None) -> str:
    """Transcribe one recorded clip with the configured engine."""
    settings = config()
    if not audio:
        raise RuntimeError("Empty audio clip")
    if len(audio) > MAX_AUDIO_BYTES:
        raise RuntimeError("Audio clip is too large")
    if settings.provider == "local":
        try:
            text = transcribe_local(audio, suffix=Path(filename).suffix or ".webm", vocabulary=vocabulary)
        except Exception as error:  # a missing model file or an undecodable clip
            raise RuntimeError(f"Local speech-to-text failed: {error}") from error
    elif settings.provider == "elevenlabs":
        text = transcribe_elevenlabs(audio, filename=filename, content_type=content_type)
    else:
        raise RuntimeError("No speech-to-text engine is available. Install faster-whisper or set PHASEMED_ELEVENLABS_API_KEY.")
    text = text.strip()
    if not text:
        raise RuntimeError("No speech was recognised in the clip")
    return text


# --- Deterministic command resolution -------------------------------------------------

HIGHLIGHT_VERBS = ("highlight", "mark", "colour", "color", "flag", "point out", "show me", "light up", "select")
CLEAR_VERBS = ("clear", "reset", "unhighlight", "remove highlight", "remove the highlight", "drop the highlight", "no highlight", "clear highlight")
# Spoken forms that do not appear verbatim in TotalSegmentator labels.
SYNONYMS: dict[str, str] = {
    "windpipe": "trachea",
    "wind pipe": "trachea",
    "airway": "trachea",
    "airways": "trachea",
    "backbone": "vertebrae",
    "spine": "vertebrae",
    "spinal column": "vertebrae",
    "vertebra": "vertebrae",
    "ribcage": "rib",
    "rib cage": "rib",
    "ribs": "rib",
    "collarbone": "clavicula",
    "collar bone": "clavicula",
    "shoulder blade": "scapula",
    "breastbone": "sternum",
    "breast bone": "sternum",
    "upper arm": "humerus",
    "gullet": "esophagus",
    "oesophagus": "esophagus",
    "food pipe": "esophagus",
    "thyroid": "thyroid_gland",
    "great vessels": "aorta",
    "vena cava": "vena_cava",
    "voice box": "trachea",
}
# View commands act on the camera instead of the model, so they carry no targets.
VIEW_COMMANDS: tuple[tuple[str, str], tuple[str, str], ...] = (
    (r"\b(?:zoom|move|come|get)\s+(?:in|closer)\b|\bcloser\b|\bzoom\s*in\b|\bmagnify\b|\benlarge\b|\bbigger\b", "zoom_in"),
    (r"\bzoom\s*out\b|\b(?:zoom|move|back)\s+(?:out|away|off)\b|\bfurther\s+(?:out|away)\b|\bsmaller\b|\bwider\b|\bpull\s+back\b", "zoom_out"),
    (r"\b(?:reset|recenter|re center|centre|center|fit)\s+(?:the\s+)?(?:view|camera|hologram)\b|\bfit\s+to\s+(?:screen|view)\b", "reset_view"),
    (r"\b(?:stop|pause|freeze|halt)\s+(?:the\s+)?(?:spin\w*|rotat\w*|turning)\b|\bhold\s+still\b|\bstop\s+moving\b", "spin_off"),
    (r"\b(?:start|resume|keep)\s+(?:the\s+)?(?:spin\w*|rotat\w*)\b|\b(?:spin|rotate)\s+(?:it|the\s+\w+)?\s*(?:again)?\b", "spin_on"),
)
VIEW_SUMMARIES = {
    "zoom_in": "Zoomed in",
    "zoom_out": "Zoomed out",
    "reset_view": "View reset",
    "spin_off": "Rotation paused",
    "spin_on": "Rotation running",
}
# Semantic groups: words that name a kind of object rather than one structure.
ABNORMAL_WORDS = ("abnormality", "abnormalities", "abnormal", "finding", "findings", "lesion", "lesions", "nodule", "nodules", "mass", "masses", "tumour", "tumor", "tumours", "tumors", "suspicious", "anything wrong", "region of interest", "regions of interest", "hot spot", "hot spots")
EVERYTHING_WORDS = ("everything", "all anatomy", "all structures", "whole model", "all objects", "the whole thing")
ABNORMAL_TYPES = ("finding", "lesion")
ABNORMAL_LABEL = re.compile(r"high.intensity|nodule|lesion|mass|tumou?r|unlabeled", re.IGNORECASE)
# Conjunctions that join two separate requests in one breath.
CLAUSE_SPLIT = re.compile(r"\s+(?:and|plus|also|as well as|along with|together with)\s+|\s*,\s*")

LATERAL = {"right": "right", "left": "left"}
STOPWORDS = {"the", "a", "an", "please", "can", "you", "my", "his", "her", "and", "of", "on", "in", "to", "for", "me", "just", "now", "s"}
FILLERS = {"um", "uh", "erm", "hmm", "okay", "ok", "so", "like"}


# Ribs and vertebrae carry numbers in their labels; speech gives them as words.
NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5", "sixth": "6",
    "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10", "eleventh": "11", "twelfth": "12",
}


def _normalise(text: str) -> str:
    lowered = str(text or "").lower()
    lowered = lowered.replace("’", "'").replace("-", " ")
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    for spoken, canonical in SYNONYMS.items():
        lowered = re.sub(rf"\b{re.escape(spoken)}\b", canonical.replace("_", " "), lowered)
    for word, digit in NUMBER_WORDS.items():
        lowered = re.sub(rf"\b{word}\b", digit, lowered)
    # "t five" and "t 5" both have to meet the label token "t5".
    lowered = re.sub(r"\b([a-z])\s+(\d{1,2})\b", r"\1\2", lowered)
    return lowered


def _stem(token: str) -> str:
    # Spoken plurals ("lungs", "lobes") have to meet singular label tokens ("lung", "lobe").
    if len(token) > 3 and token.endswith("es") and not token.endswith("ees"):
        return token[:-2] if token[:-2].endswith(("s", "x", "ch", "sh")) else token[:-1]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(text: str) -> list[str]:
    return [_stem(token) for token in _normalise(text).split(" ") if token and token not in STOPWORDS and token not in FILLERS]


def _label_tokens(label: str) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9]+", " ", str(label or "").lower())
    return [token for token in cleaned.split(" ") if token]


def _side_of(tokens: Iterable[str]) -> str | None:
    for token in tokens:
        if token in LATERAL:
            return LATERAL[token]
    return None


def _object_entries(objects: Iterable[Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for obj in objects:
        label = obj.get("label") if isinstance(obj, dict) else getattr(obj, "label", "")
        obj_id = obj.get("id") if isinstance(obj, dict) else getattr(obj, "id", "")
        obj_type = obj.get("type") if isinstance(obj, dict) else getattr(obj, "type", "")
        if not obj_id or obj_type == "volume":
            continue
        tokens = _label_tokens(label)
        entries.append({"id": obj_id, "label": label or obj_id, "type": obj_type or "", "tokens": tokens, "side": _side_of(tokens)})
    return entries


def _score(spoken: list[str], entry: dict[str, Any]) -> float:
    """Fraction of the object's own label tokens the speaker actually said.

    Anatomy words carry the score; a spoken side must agree with the label's side.
    """
    tokens = [_stem(token) for token in entry["tokens"] if token not in LATERAL]
    if not tokens:
        return 0.0
    spoken_side = _side_of(spoken)
    if spoken_side and entry["side"] and spoken_side != entry["side"]:
        return 0.0
    if spoken_side and not entry["side"]:
        return 0.0
    spoken_numbers = {token for token in spoken if token.isdigit()}
    label_numbers = {token for token in tokens if token.isdigit()}
    if spoken_numbers and label_numbers and not (spoken_numbers & label_numbers):
        return 0.0
    matched = sum(1 for token in tokens if token in spoken)
    if not matched:
        return 0.0
    coverage = matched / len(tokens)
    # A side that was asked for and agrees is extra evidence; an unasked side is not penalised.
    if spoken_side and entry["side"] == spoken_side:
        coverage += 0.25
    return coverage


def _view_action(text: str) -> str | None:
    for pattern, action in VIEW_COMMANDS:
        if re.search(pattern, text):
            return action
    return None


def _semantic_group(clause: str, entries: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]] | None:
    """Resolve a word that names a kind of object rather than one structure."""
    if any(re.search(rf"\b{re.escape(word)}\b", clause) for word in EVERYTHING_WORDS):
        return "everything", list(entries)
    if any(re.search(rf"\b{re.escape(word)}\b", clause) for word in ABNORMAL_WORDS):
        flagged = [entry for entry in entries if entry["type"] in ABNORMAL_TYPES]
        if not flagged:
            # No reviewed finding objects: fall back to the compiler's unlabeled regions,
            # which are what "abnormality" means on a model nobody has annotated yet.
            flagged = [entry for entry in entries if entry["type"] == "region" or ABNORMAL_LABEL.search(entry["label"])]
        return "abnormalities", flagged
    return None


def _resolve_clause(clause: str, entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (winners, scored candidates) for one clause of a command."""
    group = _semantic_group(clause, entries)
    if group is not None:
        _name, members = group
        return [{"id": entry["id"], "label": entry["label"], "score": 1.0} for entry in members], []
    spoken = _tokens(clause)
    scored = sorted(
        ({"id": entry["id"], "label": entry["label"], "score": _score(spoken, entry)} for entry in entries),
        key=lambda item: (-item["score"], item["label"]),
    )
    best = scored[0]["score"] if scored else 0.0
    if best <= 0:
        return [], [item for item in scored[:4] if item["score"] > 0]
    # Everything that matches the winning structure equally well comes along, so
    # "right lung" lights all three right lobes rather than an arbitrary one.
    return [item for item in scored if item["score"] >= best - 1e-9], [item for item in scored[:6] if item["score"] > 0]


def _clauses(text: str) -> list[str]:
    parts = [part.strip() for part in CLAUSE_SPLIT.split(text) if part.strip()]
    return parts or [text]


def _summarise(labels: list[str]) -> str:
    if len(labels) <= 4:
        return ", ".join(labels)
    return f"{labels[0]}, {labels[1]} and {len(labels) - 2} more"


def resolve_command(transcript: str, objects: Iterable[Any]) -> dict[str, Any]:
    """Map a transcript onto an action and PatientObject ids using explicit, replayable rules."""
    text = _normalise(transcript)
    entries = _object_entries(objects)
    trace: dict[str, Any] = {"normalised": text, "tokens": _tokens(transcript), "clauses": [], "candidates": []}

    action = _view_action(text)
    if action:
        trace["matched"] = "view command"
        return {"intent": "view", "action": action, "transcript": transcript, "targets": [], "labels": [], "summary": VIEW_SUMMARIES[action], "trace": trace}

    if any(verb in text for verb in CLEAR_VERBS):
        return {"intent": "clear", "action": None, "transcript": transcript, "targets": [], "labels": [], "summary": "Highlight cleared", "trace": trace}

    intent = "highlight" if any(verb in text for verb in HIGHLIGHT_VERBS) else "unknown"

    # "highlight right lung and spine" is two requests in one breath; each clause is
    # resolved on its own so a side in one cannot leak into the other.
    clauses = _clauses(text)
    winners: list[dict[str, Any]] = []
    seen: set[str] = set()
    index = 0
    while index < len(clauses):
        clause = clauses[index]
        matched, candidates = _resolve_clause(clause, entries)
        if not matched and index + 1 < len(clauses):
            # A fragment like "the left" only means something joined to what follows.
            # The next clause is still resolved on its own, so "the left and right lung"
            # keeps both sides instead of letting the merge swallow the second one.
            merged = f"{clause} {clauses[index + 1]}"
            matched, candidates = _resolve_clause(merged, entries)
            if matched:
                clause = merged
        trace["clauses"].append({"clause": clause, "labels": [item["label"] for item in matched]})
        trace["candidates"].extend(candidates)
        for item in matched:
            if item["id"] not in seen:
                seen.add(item["id"])
                winners.append(item)
        index += 1

    if not winners:
        return {"intent": "unknown", "action": None, "transcript": transcript, "targets": [], "labels": [], "summary": "No matching structure", "trace": trace}

    labels = [item["label"] for item in winners]
    return {
        "intent": "highlight" if intent in ("highlight", "unknown") else intent,
        "action": None,
        "transcript": transcript,
        "targets": [item["id"] for item in winners],
        "labels": labels,
        "summary": _summarise(labels),
        "trace": trace,
    }
