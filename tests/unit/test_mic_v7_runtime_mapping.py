from __future__ import annotations

import json
import shutil
from pathlib import Path

from backend.app.services import mic_service
from backend.app.services.mic_v7_contract import apply_v7_event_contract


def test_runtime_mapping_status_reads_events_and_sequence_reports(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[2] / "runtime" / "_test_mic_v7_runtime_mapping"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)

    events_path = root / "logs" / "mic_sequence_events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    seq_root = root / "mic_sequences"
    seq_dir = seq_root / "seq_test"
    seq_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(mic_service, "MIC_EVENTS_LOG_PATH", events_path)
    monkeypatch.setattr(mic_service, "MIC_SEQUENCES_ROOT", seq_root)

    event = apply_v7_event_contract(
        {
            "ts": "2026-04-08T10:00:00+00:00",
            "event": "started",
            "session_id": "mic_a",
            "model_id": "whisper_cpp_small",
            "orchestrator_mode": "v7_cs_online",
            "run_id": "run_a",
            "sequence_id": "seq_test",
            "sequence_index": 1,
            "sequence_total": 3,
            "global_timeline_ms": 0.0,
            "reason_code": None,
        }
    )
    events_path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")

    report = {
        "sequence_token": "seq_test",
        "updated_at": "2026-04-08T10:00:03+00:00",
        "sequence_total": 3,
        "trials_count": 3,
        "summary": {},
        "trials": [
            {
                "seq_index": 1,
                "model_id": "whisper_cpp_small",
                "orchestrator_mode": "v7_cs_online",
                "status": "stopped",
                "contract_valid": True,
                "global_timeline_ms": 1000.0,
                "first_word_wall_ms": 6000.0,
                "drop_rate": 0.01,
                "rtf": 0.4,
            },
            {
                "seq_index": 2,
                "model_id": "faster_whisper_small_cs_int8",
                "orchestrator_mode": "v7_cs_online",
                "status": "stopped",
                "contract_valid": True,
                "global_timeline_ms": 2000.0,
                "first_word_wall_ms": 6500.0,
                "drop_rate": 0.01,
                "rtf": 0.5,
            },
            {
                "seq_index": 3,
                "model_id": "vosk_small_cs_0_4",
                "orchestrator_mode": "v7_cs_online",
                "status": "stopped",
                "contract_valid": True,
                "global_timeline_ms": 3000.0,
                "first_word_wall_ms": 7000.0,
                "drop_rate": 0.01,
                "rtf": 0.6,
            },
        ],
    }
    (seq_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        payload = mic_service.get_v7_runtime_mapping_status(max_reports=10, max_events=100)
        assert payload["status"] == "ok"
        assert payload["events_total"] == 1
        assert payload["events_v7_total"] == 1
        assert payload["sequence_reports_total"] == 1
        assert payload["timeline_fail_reports"] == 0
        assert payload["readiness_fail_reports"] == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_client_sequence_event_logs_ui_stop_reason_and_loop_plan(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[2] / "runtime" / "_test_mic_client_sequence_event"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)

    events_path = root / "logs" / "mic_sequence_events.jsonl"
    monkeypatch.setattr(mic_service, "MIC_EVENTS_LOG_PATH", events_path)

    try:
        result = mic_service.log_client_sequence_event(
            "client_sequence_trial_stop_requested",
            {
                "sequence_token": "seq_ui",
                "sequence_index": 1,
                "sequence_total": 6,
                "orchestrator_mode": "v7_cs_online",
                "model_id": "whisper_cpp_small",
                "mobile_loop_speech_s": 60,
                "mobile_loop_pause_s": 13,
                "client_stop_reason": "silence_no_new_text",
                "client_silence_stop_s": 15,
                "ui_message": "Auto sekvence: 15s bez nového přepisu.",
            },
        )

        assert result["ok"] is True
        row = json.loads(events_path.read_text(encoding="utf-8").splitlines()[-1])
        assert row["event"] == "client_sequence_trial_stop_requested"
        assert row["event_known"] is True
        assert row["contract_valid"] is True
        assert row["run_id"] == "run_client_seq_ui"
        assert row["sequence_id"] == "seq_ui"
        assert row["global_timeline_ms"] == 0.0
        assert row["client_event"] is True
        assert row["mobile_loop_speech_s"] == 60
        assert row["mobile_loop_pause_s"] == 13
        assert row["client_stop_reason"] == "silence_no_new_text"
        assert row["ui_message"] == "Auto sekvence: 15s bez nového přepisu."
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_sequence_pause_validation_flags_large_idle_gap() -> None:
    trials = [
        {
            "seq_index": 1,
            "model_id": "whisper_cpp_small",
            "sequence_timing": {"planned_pause_s": 13.0},
        },
        {
            "seq_index": 2,
            "model_id": "faster_whisper_small_cs_int8",
            "sequence_timing": {
                "planned_pause_s": 13.0,
                "observed_pause_after_prev_stop_s": 12.8,
            },
        },
        {
            "seq_index": 3,
            "model_id": "vosk_small_cs_0_4",
            "sequence_timing": {
                "planned_pause_s": 13.0,
                "observed_pause_after_prev_stop_s": 47.292,
            },
        },
    ]

    validation = mic_service._build_sequence_pause_validation(trials)

    assert validation["ok"] is False
    assert validation["checked_points"] == 2
    assert validation["planned_pause_s"] == 13.0
    assert validation["max_abs_deviation_s"] == 34.292
    assert validation["violations"] == [
        {
            "seq_index": 3,
            "model_id": "vosk_small_cs_0_4",
            "planned_pause_s": 13.0,
            "observed_pause_s": 47.292,
            "deviation_s": 34.292,
        }
    ]


def test_sequence_conclusion_summarizes_usable_and_failed_models() -> None:
    trials = [
        {
            "seq_index": 1,
            "model_id": "whisper_cpp_small",
            "trial_status": "fail",
            "stopped_at": "2026-04-25T16:05:33+00:00",
            "rtf": 2.5894,
            "drop_rate": 0.5774,
            "reason_code": "backpressure_drop",
        },
        {
            "seq_index": 2,
            "model_id": "vosk_small_cs_0_4",
            "trial_status": "borderline",
            "stopped_at": "2026-04-25T16:07:51+00:00",
            "rtf": 1.0004,
            "drop_rate": 0.0,
            "reason_code": None,
        },
        {
            "seq_index": 3,
            "model_id": "whisper_cpp_base",
            "trial_status": "borderline",
            "stopped_at": "2026-04-25T16:09:09+00:00",
            "rtf": 1.3216,
            "drop_rate": 0.1683,
            "reason_code": "backpressure_drop",
        },
    ]

    conclusion = mic_service._build_sequence_conclusion(
        trials,
        pause_validation={"ok": False, "max_abs_deviation_s": 34.292},
    )

    assert conclusion["best_model_id"] == "vosk_small_cs_0_4"
    assert conclusion["usable_models"][0]["model_id"] == "vosk_small_cs_0_4"
    assert conclusion["performance_failed_models"][0]["model_id"] == "whisper_cpp_small"
    assert conclusion["quality_not_reliable_models"][0]["model_id"] == "whisper_cpp_small"
    assert conclusion["quality_not_reliable_models"][1]["model_id"] == "whisper_cpp_base"
    assert "Pauzy mimo plán" in conclusion["notes"][0]
