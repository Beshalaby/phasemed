from __future__ import annotations

import re
from typing import Any

from .models import ContextBinding, PatientModel, SourceReference, now_iso


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, list):
        return " ".join(_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    return ""


def _code_text(resource: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("code", "coding", "category", "valueCodeableConcept", "reasonCode", "bodySite"):
        value = resource.get(key)
        if isinstance(value, dict):
            values.append(_text(value.get("text")))
            for coding in value.get("coding", []) if isinstance(value.get("coding"), list) else []:
                values.extend(str(coding.get(name, "")) for name in ("system", "code", "display"))
        elif isinstance(value, list):
            values.append(_text(value))
    return " ".join(item for item in values if item)


def normalize_context(payload: dict[str, Any], fallback_source: str = "context-import") -> list[dict[str, Any]]:
    """Normalize a small FHIR Bundle or a plain context list without an LLM."""
    if payload.get("resourceType") == "Bundle":
        records = [entry.get("resource", {}) for entry in payload.get("entry", [])]
    elif isinstance(payload.get("items"), list):
        records = payload["items"]
    else:
        records = [payload]
    normalized = []
    for index, resource in enumerate(records):
        if not isinstance(resource, dict):
            continue
        resource_type = resource.get("resourceType") or resource.get("type") or "context"
        identifier = resource.get("id") or f"{fallback_source}-{index + 1}"
        title = resource.get("title") or (resource.get("code", {}).get("text") if isinstance(resource.get("code"), dict) else None) or resource_type
        date = resource.get("effectiveDateTime") or resource.get("issued") or resource.get("date") or now_iso()
        text = resource.get("text") or resource.get("description") or resource.get("valueString") or resource.get("value") or _text(resource.get("content"))
        code_text = _code_text(resource)
        normalized.append({
            "id": str(identifier),
            "type": str(resource_type).lower(),
            "title": str(title),
            "date": str(date),
            "text": _text(text),
            "codes": code_text,
            "subject": _text(resource.get("subject")),
            "raw": resource,
        })
    return normalized


def bind_context(model: PatientModel, items: list[dict[str, Any]]) -> list[ContextBinding]:
    bindings: list[ContextBinding] = []
    for item in items:
        source = SourceReference(id=f"source:{item['id']}", type=item["type"], title=item["title"], date=item["date"], excerpt=item["text"][:280], uri=f"context://{item['id']}")
        haystack = f"{item['title']} {item['text']} {item.get('codes', '')} {item.get('subject', '')}".lower()
        candidates = []
        for obj in model.objects:
            terms = [obj.label.lower(), *(str(value).lower() for value in obj.metadata.values() if isinstance(value, str))]
            terminology = obj.terminology or {}
            code_match = bool(terminology.get("code") and terminology["code"].lower() in haystack)
            label_match = max((0.92 if len(term) > 2 and re.search(rf"\b{re.escape(term)}\b", haystack) else 0.0) for term in terms) if terms else 0.0
            token_match = 0.0
            label_tokens = [token for token in re.findall(r"[a-z0-9]+", obj.label.lower()) if len(token) > 2]
            token_hits = sum(token in haystack for token in label_tokens)
            if token_hits:
                token_match = min(0.82, 0.46 + 0.12 * token_hits)
            score = 0.98 if code_match else max(label_match, token_match)
            if score:
                candidates.append((score, obj))
        if candidates:
            score, obj = sorted(candidates, key=lambda value: value[0], reverse=True)[0]
            method = "structured" if score >= 0.98 else "text-extraction"
            bindings.append(ContextBinding(id=f"binding:{item['id']}:{obj.id}", context_item_id=item["id"], target_type="object", target_id=obj.id, relevance=round(score, 3), method=method, source=source, evidence=item["text"][:280], algorithm_version="context-engine-0.1", review_status="unreviewed"))
        else:
            study_hint = model.study_id.lower() in haystack or item["type"] == "imagingstudy"
            bindings.append(ContextBinding(id=f"binding:{item['id']}:{'study' if study_hint else 'patient'}", context_item_id=item["id"], target_type="study" if study_hint else "patient", target_id=model.study_id if study_hint else model.patient_id, relevance=0.5, method="manual", source=source, evidence=item["text"][:280], algorithm_version="context-engine-0.1", review_status="unreviewed"))
    model.context_bindings.extend(bindings)
    existing_source_ids = {source.id for source in model.sources}
    for binding in bindings:
        if binding.source.id not in existing_source_ids:
            model.sources.append(binding.source)
            existing_source_ids.add(binding.source.id)
        if binding.target_type == "object" and binding.target_id:
            obj = next((item for item in model.objects if item.id == binding.target_id), None)
            if obj:
                obj.context.append(binding)
                obj.sources.append(binding.source)
    return bindings
