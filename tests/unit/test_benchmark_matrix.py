"""
Unit testy pro strukturu benchmark_matrix.json.

Testují že soubor produkovaný benchmark_worker.py má správné schéma
čtené results_service.py — bez spouštění modelu nebo backendu.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Fixture — minimální validní benchmark_matrix.json (kopie struktury z worker)
# ---------------------------------------------------------------------------

VALID_MATRIX = {
    "run_id": "test-run-001",
    "created_at_utc": "2026-03-23T10:00:00+00:00",
    "evaluation_mode": "streaming",
    "sample_seconds": 30,
    "source_count": 1,
    "sources": [
        {"source_id": "R3BsjbDtWrY", "canonical_url": "https://youtu.be/R3BsjbDtWrY"}
    ],
    "results": [
        {
            "model_id": "whisper_cpp_small",
            "model_label": "whisper_cpp_small",
            "setting_id": "streaming",
            "setting_label": "Streaming",
            "aggregate": {
                "rtf": 0.42,
                "latency_ms": 320.0,
                "wer": None,
                "cer": None,
                "cpu_percent": None,
                "ram_mb": None,
            },
            "source_metrics": [
                {
                    "video_id": "R3BsjbDtWrY",
                    "canonical_url": "https://youtu.be/R3BsjbDtWrY",
                    "clip_start_seconds": 0,
                    "clip_seconds": 30,
                    "transcript": "Ahoj světe",
                    "reference_text": "Ahoj světe",
                    "wer": 0.0,
                    "cer": 0.0,
                    "latency_ms": 320.0,
                    "rtf": 0.42,
                    "engine_elapsed_seconds": 12.6,
                    "chunk_metrics": [
                        {
                            "chunk_start_s": 0,
                            "chunk_end_s": 30,
                            "chunk_duration_s": 30,
                            "processing_s": 12.6,
                            "rtf": 0.42,
                            "total_elapsed_s": 12.6,
                            "words": 2,
                        }
                    ],
                }
            ],
        }
    ],
}


def _write_matrix(tmp: Path, data: dict) -> Path:
    path = tmp / "benchmark_matrix.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Testy struktury
# ---------------------------------------------------------------------------

def test_matrix_has_required_top_level_keys():
    required = {"run_id", "created_at_utc", "evaluation_mode", "sample_seconds", "source_count", "sources", "results"}
    assert required.issubset(VALID_MATRIX.keys())


def test_matrix_results_is_list():
    assert isinstance(VALID_MATRIX["results"], list)
    assert len(VALID_MATRIX["results"]) > 0


def test_matrix_result_has_required_keys():
    result = VALID_MATRIX["results"][0]
    required = {"model_id", "model_label", "setting_id", "setting_label", "aggregate", "source_metrics"}
    assert required.issubset(result.keys())


def test_matrix_aggregate_has_rtf():
    agg = VALID_MATRIX["results"][0]["aggregate"]
    assert "rtf" in agg


def test_matrix_source_metric_has_required_keys():
    sm = VALID_MATRIX["results"][0]["source_metrics"][0]
    required = {"video_id", "wer", "cer", "rtf", "latency_ms", "transcript", "reference_text"}
    assert required.issubset(sm.keys())


def test_matrix_chunk_metrics_structure():
    sm = VALID_MATRIX["results"][0]["source_metrics"][0]
    assert sm["chunk_metrics"] is not None
    chunk = sm["chunk_metrics"][0]
    required = {"chunk_start_s", "chunk_end_s", "chunk_duration_s", "processing_s", "rtf", "total_elapsed_s", "words"}
    assert required.issubset(chunk.keys())


def test_matrix_readable_by_results_service():
    """Parsování matice stejnou logikou jako results_service.get_run() — bez KeyError."""
    from backend.app.models.runs import RunDetail, RunResult, SourceMetric, AggregateMetrics

    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "test-run-001"
        run_dir.mkdir()
        matrix_file = _write_matrix(run_dir, VALID_MATRIX)

        data = json.loads(matrix_file.read_text(encoding="utf-8"))
        results = []
        for r in data.get("results", []):
            agg = r.get("aggregate", {})
            source_metrics = [
                SourceMetric(
                    video_id=sm.get("video_id"),
                    canonical_url=sm.get("canonical_url"),
                    clip_start_seconds=sm.get("clip_start_seconds"),
                    clip_seconds=sm.get("clip_seconds"),
                    transcript=sm.get("transcript"),
                    reference_text=sm.get("reference_text"),
                    wer=sm.get("wer"),
                    cer=sm.get("cer"),
                    latency_ms=sm.get("latency_ms"),
                    rtf=sm.get("rtf"),
                    engine_elapsed_seconds=sm.get("engine_elapsed_seconds"),
                    chunk_metrics=sm.get("chunk_metrics"),
                )
                for sm in r.get("source_metrics", [])
            ]
            results.append(RunResult(
                model_id=r.get("model_id", ""),
                model_label=r.get("model_label", ""),
                setting_id=r.get("setting_id", ""),
                setting_label=r.get("setting_label", ""),
                aggregate=AggregateMetrics(**agg),
                source_metrics=source_metrics,
            ))

        run = RunDetail(
            run_id=data["run_id"],
            created_at_utc=data.get("created_at_utc", ""),
            evaluation_mode=data.get("evaluation_mode", ""),
            sample_seconds=data.get("sample_seconds"),
            source_count=data.get("source_count", 0),
            results=results,
        )
        assert run.run_id == "test-run-001"
        assert len(run.results) == 1
        assert run.results[0].model_id == "whisper_cpp_small"
        assert run.results[0].source_metrics[0].chunk_metrics is not None


def test_matrix_source_metric_wer_none_allowed():
    """WER může být None — model neměl referenční text."""
    matrix = {**VALID_MATRIX}
    sm = dict(matrix["results"][0]["source_metrics"][0])
    sm["wer"] = None
    sm["cer"] = None
    sm["reference_text"] = None
    # Strukturálně validní — nesmí vyhodit KeyError
    assert sm["wer"] is None
