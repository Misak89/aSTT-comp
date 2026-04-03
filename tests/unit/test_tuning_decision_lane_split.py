from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

from backend.app.services import tuning_decision


def _fresh_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "runtime" / "_test_tuning_decision_lane"
    root.mkdir(parents=True, exist_ok=True)
    case = root / f"case_{uuid.uuid4().hex[:8]}"
    if case.exists():
        shutil.rmtree(case, ignore_errors=True)
    case.mkdir(parents=True, exist_ok=True)
    return case


def _write_status(
    root: Path,
    *,
    job_id: str,
    results: list[dict],
    reproducibility: list[dict] | None = None,
) -> None:
    job_dir = root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "job_id": job_id,
        "status": "completed",
        "results": results,
        "repro_validation": {"required_n": 1, "checked_top_k": 0, "passed": True, "missing_trial_idxs": []},
    }
    if reproducibility is not None:
        payload["reproducibility"] = reproducibility
    (job_dir / "status.json").write_text(json.dumps(payload), encoding="utf-8")


def test_decision_keeps_strict_live_pool_and_avoids_proxy_mix(monkeypatch):
    root = _fresh_root()
    job_id = "job_lane_strict"
    _write_status(
        root,
        job_id=job_id,
        results=[
            {
                "trial_idx": 1,
                "model_id": "whisper_cpp_small",
                "params": {"threads": 4},
                "wer": 0.16,
                "wer_soft": 0.14,
                "rtf": 0.88,
                "perceived_delay_s": 1.2,
                "success_rate": 1.0,
                "latency_lane": "strict_live",
                "latency_quality": "measured_live",
            },
            {
                "trial_idx": 2,
                "model_id": "whisper_cpp_small",
                "params": {"threads": 8},
                "wer": 0.08,
                "wer_soft": 0.07,
                "rtf": 0.82,
                "perceived_delay_s": 1.0,
                "success_rate": 1.0,
                "latency_lane": "batch_proxy",
                "latency_quality": "proxy_offline",
            },
            {
                "trial_idx": 3,
                "model_id": "whisper_cpp_small",
                "params": {"threads": 6},
                "wer": 0.14,
                "wer_soft": 0.12,
                "rtf": 0.90,
                "perceived_delay_s": 1.1,
                "success_rate": 1.0,
                "latency_lane": "strict_live",
                "latency_quality": "measured_live",
            },
        ],
    )
    monkeypatch.setattr(tuning_decision, "TUNING_ROOT", root)

    report = tuning_decision.get_job_decision_report(job_id, require_repro_n=1, top=5)
    assert report is not None
    assert report["selected_lane"] == "strict_live"
    assert report["selected_pool"] == "strict_live"
    assert report["best"]["trial_idx"] == 3
    assert [row["trial_idx"] for row in report["top"]] == [3, 1]
    assert all(row["latency_lane"] == "strict_live" for row in report["top"])
    assert report["lane_counts"]["strict_live"] == 2
    assert report["lane_counts"]["batch_proxy"] == 1


def test_decision_prefers_probe_online_when_strict_live_is_absent(monkeypatch):
    root = _fresh_root()
    job_id = "job_lane_probe"
    _write_status(
        root,
        job_id=job_id,
        results=[
            {
                "trial_idx": 1,
                "model_id": "whisper_cpp_small",
                "params": {"threads": 4},
                "wer": 0.15,
                "rtf": 0.90,
                "perceived_delay_s": 1.4,
                "success_rate": 1.0,
                "latency_quality": "probe_online",
            },
            {
                "trial_idx": 2,
                "model_id": "whisper_cpp_small",
                "params": {"threads": 8},
                "wer": 0.09,
                "rtf": 0.88,
                "perceived_delay_s": 1.1,
                "success_rate": 1.0,
                "latency_lane": "batch_proxy",
                "latency_quality": "proxy_offline",
            },
        ],
    )
    monkeypatch.setattr(tuning_decision, "TUNING_ROOT", root)

    report = tuning_decision.get_job_decision_report(job_id, require_repro_n=1, top=5)
    assert report is not None
    assert report["selected_lane"] == "probe_online"
    assert report["selected_pool"] == "probe_online_fallback"
    assert report["best"]["trial_idx"] == 1
    assert report["top"][0]["latency_lane"] == "probe_online"


def test_decision_normalizes_legacy_latency_fields_to_batch_proxy(monkeypatch):
    root = _fresh_root()
    job_id = "job_lane_legacy"
    _write_status(
        root,
        job_id=job_id,
        results=[
            {
                "trial_idx": 1,
                "model_id": "whisper_cpp_small",
                "params": {"threads": 4},
                "wer": 0.12,
                "rtf": 0.95,
                "perceived_delay_s": 2.0,
                "perceived_delay_quality": "low",
                "success_rate": 1.0,
                "latency_quality": "proxy_offline",
            },
            {
                "trial_idx": 2,
                "model_id": "whisper_cpp_small",
                "params": {"threads": 6},
                "wer": 0.11,
                "rtf": 0.93,
                "perceived_delay_s": 1.8,
                "perceived_delay_quality": "low",
                "success_rate": 1.0,
            },
        ],
    )
    monkeypatch.setattr(tuning_decision, "TUNING_ROOT", root)

    report = tuning_decision.get_job_decision_report(job_id, require_repro_n=1, top=5)
    assert report is not None
    assert report["selected_lane"] == "batch_proxy"
    assert report["selected_pool"] == "batch_proxy_fallback"
    assert all(row["latency_lane"] == "batch_proxy" for row in report["top"])
