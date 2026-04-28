from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import uuid

from packages.common.runtime_paths import runtime_subpath

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "v7_readiness_checklist.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("v7_readiness_checklist", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _workspace_tmp_dir(prefix: str) -> Path:
    root = runtime_subpath("_test_v7_readiness_checklist")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{prefix}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_resolve_sequence_token_latest_uses_newest_report():
    mod = _load_module()
    tmp_path = _workspace_tmp_dir("latest")
    try:
        seq_a = tmp_path / "seq_a"
        seq_b = tmp_path / "seq_b"
        seq_a.mkdir(parents=True)
        seq_b.mkdir(parents=True)
        (seq_a / "report.json").write_text("{}", encoding="utf-8")
        (seq_b / "report.json").write_text("{}", encoding="utf-8")

        import os
        import time

        older = time.time() - 30.0
        newer = time.time() - 10.0
        os.utime(seq_a / "report.json", (older, older))
        os.utime(seq_b / "report.json", (newer, newer))

        assert mod.resolve_sequence_token("latest", tmp_path) == "seq_b"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_evaluate_sequence_fails_when_trials_not_finalized_or_latency_missing():
    mod = _load_module()
    report = {
        "contract": {"schema": "astt.mic.v7.event", "version": "1.0.0"},
        "timeline_validation": {"ok": True, "checked_points": 3, "issues": []},
        "kpi": {"hard_limit_violations": 0},
        "trials": [
            {
                "seq_index": 1,
                "model_id": "m1",
                "orchestrator_mode": "v7_cs_online",
                "global_timeline_ms": 1.0,
                "started_at": "2026-04-20T00:00:01+00:00",
                "stopped_at": None,
                "latency_ms": None,
            },
            {
                "seq_index": 2,
                "model_id": "m2",
                "orchestrator_mode": "v7_cs_online",
                "global_timeline_ms": 2.0,
                "started_at": "2026-04-20T00:00:02+00:00",
                "stopped_at": None,
                "latency_ms": None,
            },
            {
                "seq_index": 3,
                "model_id": "m3",
                "orchestrator_mode": "v7_cs_online",
                "global_timeline_ms": 3.0,
                "started_at": "2026-04-20T00:00:03+00:00",
                "stopped_at": None,
                "latency_ms": None,
            },
        ],
    }
    events = mod.EventStats(
        scanned_lines=10,
        matched_events=3,
        matched_v7_events=3,
        invalid_contract_events=0,
        unknown_reason_events=0,
    )

    result = mod.evaluate_sequence(
        sequence_token="seq_x",
        report=report,
        csv_exists=True,
        events=events,
        min_models=3,
        max_models=5,
        require_v7_mode=True,
        require_finalized=True,
    )
    assert result["overall_pass"] is False
    assert "all_trials_finalized" in result["failed_checks"]
    assert "latency_available_all_trials" in result["failed_checks"]


def test_collect_event_stats_counts_invalid_contract_and_unknown_reason():
    mod = _load_module()
    tmp_path = _workspace_tmp_dir("events")
    token = "seq_token_1"
    events_path = tmp_path / "events.jsonl"
    try:
        valid_event = {
            "event_name": "started",
            "event_schema": "astt.mic.v7.event",
            "event_version": "1.0.0",
            "timestamp_utc": "2026-04-20T20:00:00+00:00",
            "orchestrator_mode": "v7_cs_online",
            "run_id": "run_1",
            "sequence_id": token,
            "sequence_index": 1,
            "sequence_total": 3,
            "global_timeline_ms": 1.1,
            "reason_code": "no_tokens",
        }
        invalid_event = {
            "event_name": "started",
            "event_schema": "astt.mic.v7.event",
            "event_version": "1.0.0",
            "timestamp_utc": "2026-04-20T20:00:01+00:00",
            "orchestrator_mode": "v7_cs_online",
            "sequence_id": token,
            "sequence_index": 2,
            "sequence_total": 3,
            "global_timeline_ms": 2.2,
            "reason_code": "my_custom_reason",
        }
        other_seq_event = {
            "event_name": "started",
            "event_schema": "astt.mic.v7.event",
            "event_version": "1.0.0",
            "timestamp_utc": "2026-04-20T20:00:02+00:00",
            "orchestrator_mode": "v7_cs_online",
            "run_id": "run_3",
            "sequence_id": "other",
            "sequence_index": 1,
            "sequence_total": 1,
            "global_timeline_ms": 1.0,
            "reason_code": "no_tokens",
        }
        lines = [valid_event, invalid_event, other_seq_event]
        events_path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines), encoding="utf-8")

        stats = mod.collect_event_stats(sequence_token=token, events_log_path=events_path)
        assert stats.scanned_lines == 3
        assert stats.matched_events == 2
        assert stats.matched_v7_events == 2
        assert stats.invalid_contract_events >= 1
        assert stats.unknown_reason_events >= 1
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
