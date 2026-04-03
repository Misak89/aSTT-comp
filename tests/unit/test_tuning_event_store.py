from __future__ import annotations

from pathlib import Path
import shutil
import uuid

from packages.common import tuning_event_store


def _fresh_job_dir() -> Path:
    root = Path(__file__).resolve().parents[2] / "runtime" / "_test_tuning_event_store"
    root.mkdir(parents=True, exist_ok=True)
    job_dir = root / f"job_{uuid.uuid4().hex[:8]}"
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def test_event_store_append_read_and_stats():
    job_dir = _fresh_job_dir()

    tuning_event_store.ensure_event_store(job_dir)
    seq1 = tuning_event_store.append_event(job_dir, event_type="worker_started", payload={"job_id": "x"})
    seq2 = tuning_event_store.append_event(job_dir, event_type="progress", payload={"message": "hello"})
    seq3 = tuning_event_store.append_event(job_dir, event_type="job_completed", payload={"completed_trials": 3})

    assert seq1 < seq2 < seq3

    all_events = tuning_event_store.read_events(job_dir, after_seq=0, limit=100)
    assert [e["event_type"] for e in all_events] == ["worker_started", "progress", "job_completed"]
    assert all_events[1]["payload"]["message"] == "hello"

    after_first = tuning_event_store.read_events(job_dir, after_seq=seq1, limit=100)
    assert [e["seq"] for e in after_first] == [seq2, seq3]

    only_progress = tuning_event_store.read_events(job_dir, after_seq=0, limit=100, event_type="progress")
    assert len(only_progress) == 1
    assert only_progress[0]["event_type"] == "progress"

    stats = tuning_event_store.get_event_stats(job_dir)
    assert stats["exists"] is True
    assert stats["count"] == 3
    assert stats["max_seq"] == seq3
    assert stats["event_types"]["progress"] == 1

