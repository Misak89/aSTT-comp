"""
Smoke testy Fáze 3: subprocess worker, scénáře, streaming simulátor.
"""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


def test_benchmark_worker_exists():
    assert (ROOT / "scripts" / "benchmark_worker.py").exists()


def test_streaming_simulator_exists():
    assert (ROOT / "packages" / "benchmarks" / "runners" / "streaming_simulator.py").exists()


def test_scenario_service_exists():
    assert (ROOT / "backend" / "app" / "services" / "scenario_service.py").exists()


def test_streaming_simulator_importable():
    from packages.benchmarks.runners.streaming_simulator import StreamingResult, simulate_streaming
    r = StreamingResult()
    assert r.transcript == ""
    assert r.chunks_sent == 0


def test_scenario_model():
    from backend.app.models.benchmark import Scenario
    sc = Scenario(
        scenario_id="test_scenario",
        clip_seconds=120,
        clip_seed=42,
        model_ids=["whisper_cpp_small"],
        setting_ids=["balanced"],
        video_ids=["R3BsjbDtWrY"],
    )
    assert sc.scenario_id == "test_scenario"
    assert sc.clip_seconds == 120


def test_benchmark_job_video_ids():
    """BenchmarkJobRequest musí přijmout video_ids."""
    from backend.app.models.benchmark import BenchmarkJobRequest
    req = BenchmarkJobRequest(video_ids=["R3BsjbDtWrY"], model_ids=["whisper_cpp_small"])
    assert req.video_ids == ["R3BsjbDtWrY"]


def test_benchmark_job_requires_sources():
    """BenchmarkJobRequest bez video_ids i sources musí selhat."""
    import pytest
    from pydantic import ValidationError
    from backend.app.models.benchmark import BenchmarkJobRequest
    with pytest.raises(ValidationError):
        BenchmarkJobRequest()


def test_benchmark_status_has_conditions_clean():
    from backend.app.models.benchmark import BenchmarkJobStatus
    status = BenchmarkJobStatus(
        job_id="x", status="pending", created_at="2026-01-01T00:00:00Z",
        conditions_clean=True,
    )
    assert status.conditions_clean is True
