"""
Read and parse completed benchmark run artifacts.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from ..config import RUNS_ROOT
from ..models.runs import RunSummary, RunDetail, RunResult, SourceMetric, AggregateMetrics


def list_runs() -> list[RunSummary]:
    out = []
    for run_dir in sorted(RUNS_ROOT.iterdir(), reverse=True):
        matrix_file = run_dir / "benchmark_matrix.json"
        if not matrix_file.exists():
            continue
        try:
            data = json.loads(matrix_file.read_text(encoding="utf-8"))
            out.append(RunSummary(
                run_id=data.get("run_id", run_dir.name),
                created_at_utc=data.get("created_at_utc", ""),
                evaluation_mode=data.get("evaluation_mode", ""),
                sample_seconds=data.get("sample_seconds", 0),
                source_count=data.get("source_count", 0),
                result_count=len(data.get("results", [])),
                label=data.get("label"),
            ))
        except Exception:
            continue
    return out


def get_run(run_id: str) -> Optional[RunDetail]:
    run_dir = RUNS_ROOT / run_id
    matrix_file = run_dir / "benchmark_matrix.json"
    if not matrix_file.exists():
        return None
    try:
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
                    model_runtime_config=sm.get("model_runtime_config"),
                    chunk_metrics=sm.get("chunk_metrics"),
                    wer_normalized=sm.get("wer_normalized"),
                    mer=sm.get("mer"),
                    wil=sm.get("wil"),
                    segment_metrics=sm.get("segment_metrics"),
                )
                for sm in r.get("source_metrics", [])
            ]
            results.append(RunResult(
                model_id=r.get("model_id", ""),
                model_label=r.get("model_label", ""),
                setting_id=r.get("setting_id", ""),
                setting_label=r.get("setting_label", ""),
                aggregate=AggregateMetrics(
                    score=agg.get("score"),
                    wer=agg.get("wer"),
                    cer=agg.get("cer"),
                    latency_ms=agg.get("latency_ms"),
                    rtf=agg.get("rtf"),
                    cpu_percent=agg.get("cpu_percent"),
                    ram_mb=agg.get("ram_mb"),
                    mer=agg.get("mer"),
                    wil=agg.get("wil"),
                    wer_normalized=agg.get("wer_normalized"),
                ),
                source_metrics=source_metrics,
                model_runtime_config=r.get("model_runtime_config"),
            ))
        return RunDetail(
            run_id=data.get("run_id", run_dir.name),
            created_at_utc=data.get("created_at_utc", ""),
            evaluation_mode=data.get("evaluation_mode", ""),
            sample_seconds=data.get("sample_seconds", 0),
            sources=data.get("sources", []),
            results=results,
            host_telemetry_summary=data.get("host_telemetry_summary"),
        )
    except Exception:
        return None


def read_run_file(run_id: str, filepath: str) -> Optional[str]:
    """Read a run artifact file with path-traversal protection."""
    run_dir = RUNS_ROOT / run_id
    # Resolve and ensure it stays within run_dir
    target = (run_dir / filepath).resolve()
    if not str(target).startswith(str(run_dir.resolve())):
        return None
    if not target.exists() or not target.is_file():
        return None
    return target.read_text(encoding="utf-8")
