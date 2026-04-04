from __future__ import annotations

from pathlib import Path
import shutil
import uuid

import pytest

from backend.app.models.library import SegmentBundlePreviewRequest
from backend.app.services import library_service


def _fresh_segments_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "runtime" / "_test_library_segment_bundle"
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"seg_{uuid.uuid4().hex[:8]}"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_preview_preset_bundle_10m():
    req = SegmentBundlePreviewRequest(
        source_id="upload_long_001",
        source_type="upload",
        mode="preset",
        audio_duration_seconds=3700,
        pause_aware=False,
        preset_minutes=10,
    )
    bundle = library_service.preview_segment_bundle(req)

    assert bundle.mode == "preset"
    assert bundle.points_seconds == [600.0, 1200.0, 1800.0, 2400.0, 3000.0, 3600.0]
    assert len(bundle.segments) == 7
    assert bundle.segments[0].start_s == 0.0
    assert bundle.segments[-1].end_s == 3700.0


def test_preview_manual_bundle_respects_max_points():
    req = SegmentBundlePreviewRequest(
        source_id="upload_long_002",
        source_type="upload",
        mode="manual",
        audio_duration_seconds=10000,
        pause_aware=False,
        manual_points_seconds=[float(i * 30) for i in range(1, 23)],
    )
    with pytest.raises(ValueError, match="max 21 points"):
        library_service.preview_segment_bundle(req)


def test_upsert_and_get_segment_bundle_roundtrip_preserves_created_at(monkeypatch: pytest.MonkeyPatch):
    root = _fresh_segments_root()
    monkeypatch.setattr(library_service, "_SEGMENTS_ROOT", root)

    req_first = SegmentBundlePreviewRequest(
        source_id="upload_long_roundtrip",
        source_type="upload",
        mode="manual",
        audio_duration_seconds=500.0,
        pause_aware=False,
        manual_points_seconds=[120.0, 300.0],
    )
    first = library_service.upsert_segment_bundle("upload_long_roundtrip", req_first)
    loaded = library_service.get_segment_bundle("upload_long_roundtrip")

    assert loaded.source_id == "upload_long_roundtrip"
    assert loaded.points_seconds == [120.0, 300.0]
    assert loaded.created_at == first.created_at

    req_second = SegmentBundlePreviewRequest(
        source_id="upload_long_roundtrip",
        source_type="upload",
        mode="manual",
        audio_duration_seconds=500.0,
        pause_aware=False,
        manual_points_seconds=[100.0, 250.0, 420.0],
    )
    second = library_service.upsert_segment_bundle("upload_long_roundtrip", req_second)
    assert second.created_at == first.created_at
    assert second.points_seconds == [100.0, 250.0, 420.0]


def test_preview_pause_aware_upload_not_supported():
    req = SegmentBundlePreviewRequest(
        source_id="upload_pause_001",
        source_type="upload",
        mode="manual",
        audio_duration_seconds=300.0,
        pause_aware=True,
        manual_points_seconds=[60.0, 120.0],
    )
    with pytest.raises(ValueError, match="source_type=library_item"):
        library_service.preview_segment_bundle(req)


def test_preview_pause_aware_library_item_applies_snapped_points(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(library_service, "_library_item_exists", lambda source_id: True)
    monkeypatch.setattr(library_service, "resolve_audio_file_for_library_item", lambda source_id: Path("dummy.wav"))

    def fake_snap(*, source_audio_path: Path, points: list[float], duration_s: float, tolerance_s: float, silence_dbfs: float, min_silence_ms: int):
        assert source_audio_path == Path("dummy.wav")
        assert points == [100.0, 200.0]
        return [101.0, 198.5], [
            {"original": 100.0, "snapped": 101.0, "delta_ms": 1000.0, "changed": True},
            {"original": 200.0, "snapped": 198.5, "delta_ms": -1500.0, "changed": True},
        ]

    monkeypatch.setattr(library_service, "_pause_aware_snap_points", fake_snap)

    req = SegmentBundlePreviewRequest(
        source_id="vid_pause_001",
        source_type="library_item",
        mode="manual",
        audio_duration_seconds=300.0,
        pause_aware=True,
        manual_points_seconds=[100.0, 200.0],
    )
    bundle = library_service.preview_segment_bundle(req)
    assert bundle.points_seconds == [101.0, 198.5]
    assert bundle.segments[0].snapped is True
    assert bundle.segments[0].snapped_from_s == 100.0
