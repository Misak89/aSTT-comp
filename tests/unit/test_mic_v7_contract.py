from __future__ import annotations

import pytest

from backend.app.services import mic_service
from backend.app.services.mic_v7_contract import (
    apply_v7_event_contract,
    compute_kpi_summary,
    evaluate_v7_readiness,
    validate_timeline_monotonic,
)


def test_apply_v7_event_contract_detects_missing_required_fields() -> None:
    payload = apply_v7_event_contract(
        {
            "event": "started",
            "orchestrator_mode": "v7_cs_online",
            "run_id": "run_x",
            "sequence_id": "seq_x",
            "sequence_index": 1,
            # sequence_total/global_timeline_ms intentionally missing
        }
    )
    assert payload["contract_valid"] is False
    assert "sequence_total" in payload["contract_missing_fields"]
    assert "global_timeline_ms" in payload["contract_missing_fields"]


def test_validate_timeline_monotonic_detects_regression() -> None:
    rows = [
        {"seq_index": 1, "global_timeline_ms": 10.0},
        {"seq_index": 2, "global_timeline_ms": 20.0},
        {"seq_index": 3, "global_timeline_ms": 15.0},
    ]
    validation = validate_timeline_monotonic(rows)
    assert validation["ok"] is False
    assert validation["regressions"] == 1
    assert validation["max_regression_ms"] == 5.0


def test_kpi_summary_and_readiness_flags_hard_latency_violation() -> None:
    trials = [
        {
            "model_id": "whisper_cpp_small",
            "orchestrator_mode": "v7_cs_online",
            "status": "stopped",
            "contract_valid": True,
            "global_timeline_ms": 1000.0,
            "seq_index": 1,
            "rtf": 0.4,
            "drop_rate": 0.01,
            "first_word_wall_ms": 7000.0,
            "worker_rss_peak_mb": 120.0,
        },
        {
            "model_id": "faster_whisper_small_cs_int8",
            "orchestrator_mode": "v7_cs_online",
            "status": "stopped",
            "contract_valid": True,
            "global_timeline_ms": 2000.0,
            "seq_index": 2,
            "rtf": 0.5,
            "drop_rate": 0.02,
            "first_word_wall_ms": 13000.0,
            "worker_rss_peak_mb": 180.0,
        },
    ]
    timeline = validate_timeline_monotonic(trials)
    kpi = compute_kpi_summary(trials, hard_limit_ms=12_000.0)
    readiness = evaluate_v7_readiness(
        trials=trials,
        timeline_validation=timeline,
        kpi_summary=kpi,
        min_models=2,
        require_v7_mode=True,
        require_finalized=True,
    )

    assert kpi["hard_limit_violations"] == 1
    assert readiness["pass"] is False
    assert "latency_hard_limit" in readiness["failed_checks"]


def test_mic_session_preflight_blocks_batch_only_model() -> None:
    session_id = mic_service.create_session(
        "qwen_asr_0_6b",
        {"mic_orchestrator_mode": "v7_cs_online"},
    )
    state = mic_service.get_session(session_id)
    assert state is not None
    assert state.preflight_ok is False
    assert "model_not_mic_capable" in (state.preflight_errors or [])
    with pytest.raises(RuntimeError, match="preflight_failed"):
        mic_service.start_recording(session_id)
