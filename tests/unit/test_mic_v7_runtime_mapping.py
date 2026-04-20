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
