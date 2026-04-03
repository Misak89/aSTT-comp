from __future__ import annotations

from packages.common.tuning_event_store import summarize_event_sequence


def test_event_validation_ok_sequence():
    events = [
        {"seq": 1, "event_type": "worker_started", "payload": {"job_id": "a"}},
        {"seq": 2, "event_type": "status_changed", "payload": {"status": "running"}},
        {"seq": 3, "event_type": "progress", "payload": {"message": "x"}},
        {"seq": 4, "event_type": "trial_result", "payload": {"trial_idx": 1}},
        {"seq": 5, "event_type": "job_completed", "payload": {"completed_trials": 1}},
    ]
    out = summarize_event_sequence(events, expected_terminal="job_completed")
    assert out["ok"] is True
    assert out["terminal_event"] == "job_completed"
    assert out["post_terminal_events"] == 0
    assert out["event_type_counts"]["progress"] == 1
    assert out["status_values"] == ["running"]


def test_event_validation_detects_post_terminal_and_multiple_terminal():
    events = [
        {"seq": 1, "event_type": "worker_started", "payload": {}},
        {"seq": 2, "event_type": "job_failed", "payload": {"error": "boom"}},
        {"seq": 3, "event_type": "progress", "payload": {"message": "late"}},
        {"seq": 4, "event_type": "job_cancelled", "payload": {}},
    ]
    out = summarize_event_sequence(events)
    assert out["ok"] is False
    assert out["terminal_event"] == "job_failed"
    assert out["post_terminal_events"] == 2
    assert any(issue.startswith("multiple_terminal_events") for issue in out["issues"])
    assert any(issue.startswith("post_terminal_events:") for issue in out["issues"])

