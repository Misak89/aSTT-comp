from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from packages.ingest.source_resolver import SourceEntry


@dataclass(frozen=True)
class ReferenceLookup:
    by_source_id: dict[str, str]
    by_source_value: dict[str, str]
    source_value_by_id: dict[str, str | None]


def load_reference_lookup(manifest_path: str | Path | None) -> ReferenceLookup:
    if not manifest_path:
        return ReferenceLookup(by_source_id={}, by_source_value={}, source_value_by_id={})

    path = Path(manifest_path)
    if not path.exists():
        return ReferenceLookup(by_source_id={}, by_source_value={}, source_value_by_id={})

    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_entries = payload.get("entries", [])
    if not isinstance(raw_entries, list):
        return ReferenceLookup(by_source_id={}, by_source_value={}, source_value_by_id={})

    by_source_id: dict[str, str] = {}
    by_source_value: dict[str, str] = {}
    source_value_by_id: dict[str, str | None] = {}

    for item in raw_entries:
        if not isinstance(item, dict):
            continue

        reference_text = item.get("reference_text")
        if not isinstance(reference_text, str) or not reference_text.strip():
            continue
        reference_text = reference_text.strip()

        source_id = item.get("source_id")
        source_id_key = source_id.strip() if isinstance(source_id, str) and source_id.strip() else None

        normalized_source_value: str | None = None
        source_value = item.get("source_value")
        if isinstance(source_value, str) and source_value.strip():
            normalized_source_value = _normalize_source_value(source_value)
            by_source_value[normalized_source_value] = reference_text

        if source_id_key:
            by_source_id[source_id_key] = reference_text
            source_value_by_id[source_id_key] = normalized_source_value

    return ReferenceLookup(
        by_source_id=by_source_id,
        by_source_value=by_source_value,
        source_value_by_id=source_value_by_id,
    )


def lookup_reference_text(source: SourceEntry, lookup: ReferenceLookup) -> str | None:
    normalized_value = _normalize_source_value(source.value)
    by_value = lookup.by_source_value.get(normalized_value)
    if by_value:
        return by_value

    by_id = lookup.by_source_id.get(source.source_id)
    if not by_id:
        return None

    expected_value = lookup.source_value_by_id.get(source.source_id)
    if expected_value is None:
        # Backward-compatible fallback when source_value is not specified in manifest.
        return by_id
    if expected_value == normalized_value:
        return by_id
    return None


def _normalize_source_value(value: str) -> str:
    raw = (value or "").strip()
    if "://" in raw:
        return raw
    return str(Path(raw).resolve()).replace("\\", "/").lower()
