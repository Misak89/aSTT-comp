from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

from backend.app.services import tuning_service
from packages.common.tuning_event_store import append_event, ensure_event_store


def _fresh_tuning_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "runtime" / "_test_tuning_events_service"
    root.mkdir(parents=True, exist_ok=True)
    case = root / f"case_{uuid.uuid4().hex[:8]}"
    if case.exists():
        shutil.rmtree(case, ignore_errors=True)
    case.mkdir(parents=True, exist_ok=True)
    return case


def _write_status(job_dir: Path) -> None:
    payload = {
        "job_id": job_dir.name,
        "status": "completed",
        "model_id": "whisper_cpp_small",
        "model_ids": ["whisper_cpp_small"],
        "label": None,
        "created_at": "2026-04-03T00:00:00Z",
        "total_trials": 1,
        "completed_trials": 1,
        "results": [],
        "error": None,
        "best_trial_idx": None,
        "progress_message": None,
        "audio_ready": [],
    }
    (job_dir / "status.json").write_text(json.dumps(payload), encoding="utf-8")


def test_get_job_events_returns_incremental_payload(monkeypatch):
    tuning_root = _fresh_tuning_root()
    job_id = "job_events_1"
    job_dir = tuning_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    _write_status(job_dir)
    ensure_event_store(job_dir)
    s1 = append_event(job_dir, event_type="worker_started", payload={"job_id": job_id})
    s2 = append_event(job_dir, event_type="progress", payload={"message": "x"})
    s3 = append_event(job_dir, event_type="job_completed", payload={"completed_trials": 1})
    assert s1 < s2 < s3

    monkeypatch.setattr(tuning_service, "TUNING_ROOT", tuning_root)
    payload = tuning_service.get_job_events(job_id, after_seq=s1, limit=10)

    assert payload["job_id"] == job_id
    assert payload["after_seq"] == s1
    assert payload["next_after_seq"] == s3
    assert payload["count"] == 2
    assert [e["event_type"] for e in payload["events"]] == ["progress", "job_completed"]
    assert payload["stats"]["count"] == 3
    assert payload["validation"]["ok"] is True
    assert payload["validation"]["terminal_event"] == "job_completed"


def test_get_job_events_missing_job_returns_empty(monkeypatch):
    tuning_root = _fresh_tuning_root()
    monkeypatch.setattr(tuning_service, "TUNING_ROOT", tuning_root)
    assert tuning_service.get_job_events("missing_job") == {}
