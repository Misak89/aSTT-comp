from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import random
import subprocess
import time
from typing import Any

from packages.benchmarks.ground_truth.reference_manifest import load_reference_lookup, lookup_reference_text
from packages.benchmarks.ground_truth.vtt_reference import extract_vtt_clip_text
from packages.benchmarks.metrics.text_metrics import char_error_rate, word_error_rate
from packages.benchmarks.runners.host_telemetry import HostTelemetryRecorder
from packages.adapters.qwen_asr_runner import QwenRunConfig, run_qwen_source
from packages.adapters.faster_whisper_runner import (
    FasterWhisperRunConfig,
    resolve_faster_whisper_model_path,
    run_faster_whisper_source,
)
from packages.adapters.sherpa_onnx_runner import (
    SherpaRunConfig,
    resolve_sherpa_model_bundle,
    run_sherpa_source,
)
from packages.adapters.vosk_runner import (
    VoskRunConfig,
    resolve_vosk_model_dir,
    run_vosk_source,
)
from packages.adapters.whisper_cpp_runner import (
    WhisperRunConfig,
    resolve_whisper_cli,
    resolve_whisper_model_file,
    run_whisper_source,
)
from packages.ingest.source_resolver import SourceEntry


@dataclass(frozen=True)
class ModelPreset:
    model_id: str
    label: str
    quality_factor: float
    speed_factor: float
    memory_factor: float
    diarization_factor: float
    cpu_factor: float


@dataclass(frozen=True)
class SettingPreset:
    setting_id: str
    label: str
    quality_multiplier: float
    speed_multiplier: float
    cpu_multiplier: float
    ram_multiplier: float


DEFAULT_MODELS: dict[str, ModelPreset] = {
    "whisper_cpp_base": ModelPreset(
        model_id="whisper_cpp_base",
        label="whisper.cpp base",
        quality_factor=0.82,
        speed_factor=1.00,
        memory_factor=1.00,
        diarization_factor=0.70,
        cpu_factor=1.00,
    ),
    "whisper_cpp_small": ModelPreset(
        model_id="whisper_cpp_small",
        label="whisper.cpp small",
        quality_factor=0.88,
        speed_factor=0.80,
        memory_factor=1.25,
        diarization_factor=0.74,
        cpu_factor=1.15,
    ),
    "whisper_cpp_large_v3": ModelPreset(
        model_id="whisper_cpp_large_v3",
        label="whisper.cpp large-v3",
        quality_factor=0.97,
        speed_factor=0.42,
        memory_factor=3.10,
        diarization_factor=0.81,
        cpu_factor=1.62,
    ),
    "whisper_cpp_large_v3_turbo": ModelPreset(
        model_id="whisper_cpp_large_v3_turbo",
        label="whisper.cpp large-v3-turbo",
        quality_factor=0.95,
        speed_factor=1.15,
        memory_factor=1.50,
        diarization_factor=0.80,
        cpu_factor=0.95,
    ),
    "sherpa_onnx_small": ModelPreset(
        model_id="sherpa_onnx_small",
        label="sherpa-onnx small",
        quality_factor=0.75,
        speed_factor=1.18,
        memory_factor=0.72,
        diarization_factor=0.66,
        cpu_factor=0.85,
    ),
    "sherpa_onnx_parakeet_cs_int8": ModelPreset(
        model_id="sherpa_onnx_parakeet_cs_int8",
        label="sherpa-onnx Parakeet 0.6B int8 (CZ)",
        quality_factor=0.90,
        speed_factor=0.78,
        memory_factor=1.35,
        diarization_factor=0.80,
        cpu_factor=1.10,
    ),
    "vosk_small_cs_0_4": ModelPreset(
        model_id="vosk_small_cs_0_4",
        label="VOSK small cs-0.4",
        quality_factor=0.72,
        speed_factor=1.35,
        memory_factor=0.48,
        diarization_factor=0.61,
        cpu_factor=0.68,
    ),
    "faster_whisper_small_cs_int8": ModelPreset(
        model_id="faster_whisper_small_cs_int8",
        label="faster-whisper small (CZ int8)",
        quality_factor=0.87,
        speed_factor=1.05,
        memory_factor=1.00,
        diarization_factor=0.76,
        cpu_factor=0.95,
    ),
    "faster_whisper_medium_cs_int8": ModelPreset(
        model_id="faster_whisper_medium_cs_int8",
        label="faster-whisper medium (CZ int8)",
        quality_factor=0.92,
        speed_factor=0.82,
        memory_factor=1.35,
        diarization_factor=0.79,
        cpu_factor=1.15,
    ),
    "qwen3_asr_0_6b": ModelPreset(
        model_id="qwen3_asr_0_6b",
        label="Qwen3-ASR 0.6B",
        quality_factor=0.90,
        speed_factor=0.72,
        memory_factor=1.55,
        diarization_factor=0.79,
        cpu_factor=1.25,
    ),
    "qwen3_asr_1_7b": ModelPreset(
        model_id="qwen3_asr_1_7b",
        label="Qwen3-ASR 1.7B",
        quality_factor=0.94,
        speed_factor=0.52,
        memory_factor=2.25,
        diarization_factor=0.84,
        cpu_factor=1.45,
    ),
}

DEFAULT_SETTINGS: dict[str, SettingPreset] = {
    "low_latency": SettingPreset(
        setting_id="low_latency",
        label="Low Latency",
        quality_multiplier=0.94,
        speed_multiplier=1.22,
        cpu_multiplier=0.88,
        ram_multiplier=0.92,
    ),
    "balanced": SettingPreset(
        setting_id="balanced",
        label="Balanced",
        quality_multiplier=1.00,
        speed_multiplier=1.00,
        cpu_multiplier=1.00,
        ram_multiplier=1.00,
    ),
    "high_accuracy": SettingPreset(
        setting_id="high_accuracy",
        label="High Accuracy",
        quality_multiplier=1.07,
        speed_multiplier=0.82,
        cpu_multiplier=1.18,
        ram_multiplier=1.22,
    ),
    "memory_saver": SettingPreset(
        setting_id="memory_saver",
        label="Memory Saver",
        quality_multiplier=0.92,
        speed_multiplier=1.08,
        cpu_multiplier=0.93,
        ram_multiplier=0.72,
    ),
}


@dataclass(frozen=True)
class ClipSelection:
    source: SourceEntry
    clip_start_seconds: int
    clip_seconds: int
    clip_end_seconds: int
    clip_seed: int
    source_duration_seconds: float | None
    segment_fingerprint: str | None = None
    selection_note: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            **self.source.to_record(),
            "clip_start_seconds": self.clip_start_seconds,
            "clip_seconds": self.clip_seconds,
            "clip_end_seconds": self.clip_end_seconds,
            "clip_seed": self.clip_seed,
            "source_duration_seconds": self.source_duration_seconds,
            "segment_fingerprint": self.segment_fingerprint,
            "selection_note": self.selection_note,
        }


def run_benchmark_matrix(
    *,
    sources: list[SourceEntry],
    model_ids: list[str],
    setting_ids: list[str],
    sample_seconds: int,
    run_root: str | Path = ".runtime/runs",
    model_store_root: str | Path = ".runtime/model_store",
    reference_manifest_path: str | Path | None = None,
    run_id: str | None = None,
    evaluation_mode: str = "synthetic",
    clip_selection_seed: int | None = None,
    clip_selection_strategy: str = "deterministic_v1",
    segment_start_seconds: int | None = None,
    segment_end_seconds: int | None = None,
    subtitles_root: Path | None = None,
    progress_callback=None,
) -> dict[str, object]:
    run_started_at_utc = datetime.now(UTC)
    run_started_perf = time.perf_counter()
    selected_models = [DEFAULT_MODELS[model_id] for model_id in model_ids if model_id in DEFAULT_MODELS]
    selected_settings = [DEFAULT_SETTINGS[setting_id] for setting_id in setting_ids if setting_id in DEFAULT_SETTINGS]

    if not selected_models:
        raise ValueError("No valid models selected")
    if not selected_settings:
        raise ValueError("No valid settings selected")
    if not sources:
        raise ValueError("No sources to benchmark")

    effective_sample_seconds = _effective_sample_seconds(
        sample_seconds=sample_seconds,
        segment_start_seconds=segment_start_seconds,
        segment_end_seconds=segment_end_seconds,
    )
    current_run_id = run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
    run_dir = Path(run_root) / current_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    selected_sources, effective_seed = _select_source_clips(
        sources=sources,
        sample_seconds=effective_sample_seconds,
        seed=clip_selection_seed,
        strategy=clip_selection_strategy,
        segment_start_seconds=segment_start_seconds,
        segment_end_seconds=segment_end_seconds,
    )

    host_telemetry_path = run_dir / "host_telemetry.json"
    host_telemetry_payload: dict[str, Any] | None = None
    timeline_events: list[dict[str, Any]] = []
    _append_timeline_event(
        events=timeline_events,
        run_started_at_utc=run_started_at_utc,
        run_started_perf=run_started_perf,
        event_kind="run_started",
        phase="run",
        model_id=None,
        setting_id=None,
        source_id=None,
        started_perf=run_started_perf,
        ended_perf=run_started_perf,
        metadata={
            "evaluation_mode": evaluation_mode,
            "model_count": len(selected_models),
            "setting_count": len(selected_settings),
            "source_count": len(selected_sources),
        },
    )
    telemetry = HostTelemetryRecorder(
        run_id=current_run_id,
        evaluation_mode=evaluation_mode,
        sampling_interval_seconds=1.0,
        output_path=host_telemetry_path,
    )
    telemetry.start()

    combos: list[dict[str, object]] = []
    run_error: str | None = None

    try:
        if evaluation_mode == "synthetic":
            for model in selected_models:
                for setting in selected_settings:
                    source_metrics: list[dict[str, object]] = []
                    for selection in selected_sources:
                        metric_started_perf = time.perf_counter()
                        metric = _simulate_metrics(selection, model, setting, sample_seconds=effective_sample_seconds)
                        metric_ended_perf = time.perf_counter()
                        source_metrics.append(metric)
                        _append_timeline_event(
                            events=timeline_events,
                            run_started_at_utc=run_started_at_utc,
                            run_started_perf=run_started_perf,
                            event_kind="source_metric",
                            phase="simulate",
                            model_id=model.model_id,
                            setting_id=setting.setting_id,
                            source_id=selection.source.source_id,
                            started_perf=metric_started_perf,
                            ended_perf=metric_ended_perf,
                            metadata={
                                "engine": "synthetic",
                                "clip_start_seconds": selection.clip_start_seconds,
                                "clip_end_seconds": selection.clip_end_seconds,
                                "sample_seconds": effective_sample_seconds,
                            },
                        )
                    aggregate = _aggregate_metrics(source_metrics)
                    aggregate["score"] = _score_combo(aggregate)

                    combos.append(
                        {
                            "model_id": model.model_id,
                            "model_label": model.label,
                            "setting_id": setting.setting_id,
                            "setting_label": setting.label,
                            "aggregate": aggregate,
                            "source_metrics": source_metrics,
                        }
                    )
        elif evaluation_mode == "real":
            combos = _run_real_matrix(
                selected_models=selected_models,
                selected_settings=selected_settings,
                selected_sources=selected_sources,
                sample_seconds=effective_sample_seconds,
                run_dir=run_dir,
                model_store_root=Path(model_store_root),
                reference_manifest_path=reference_manifest_path,
                timeline_events=timeline_events,
                run_started_at_utc=run_started_at_utc,
                run_started_perf=run_started_perf,
                subtitles_root=subtitles_root,
                progress_callback=progress_callback,
            )
        else:
            raise ValueError(f"Unsupported evaluation_mode: {evaluation_mode}")
    except Exception as exc:
        run_error = f"{type(exc).__name__}: {exc}"
        _append_timeline_event(
            events=timeline_events,
            run_started_at_utc=run_started_at_utc,
            run_started_perf=run_started_perf,
            event_kind="run_error",
            phase="run",
            model_id=None,
            setting_id=None,
            source_id=None,
            started_perf=time.perf_counter(),
            ended_perf=time.perf_counter(),
            metadata={"error": run_error},
        )
        raise
    finally:
        telemetry_stop_started_perf = time.perf_counter()
        try:
            host_telemetry_payload = telemetry.stop(
                run_status="failed" if run_error else "completed",
                error=run_error,
            )
        except Exception as telemetry_exc:
            host_telemetry_payload = {
                "telemetry_version": 1,
                "run_id": current_run_id,
                "evaluation_mode": evaluation_mode,
                "status": "collector_failed",
                "run_status": "failed" if run_error else "completed",
                "error": f"{type(telemetry_exc).__name__}: {telemetry_exc}",
            }
            host_telemetry_path.write_text(
                json.dumps(host_telemetry_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        telemetry_stop_ended_perf = time.perf_counter()
        _append_timeline_event(
            events=timeline_events,
            run_started_at_utc=run_started_at_utc,
            run_started_perf=run_started_perf,
            event_kind="telemetry_stop",
            phase="telemetry",
            model_id=None,
            setting_id=None,
            source_id=None,
            started_perf=telemetry_stop_started_perf,
            ended_perf=telemetry_stop_ended_perf,
            metadata={"telemetry_status": host_telemetry_payload.get("status") if isinstance(host_telemetry_payload, dict) else None},
        )

    combos.sort(key=lambda c: c["aggregate"]["score"], reverse=True)
    for index, combo in enumerate(combos, start=1):
        combo["rank"] = index

    payload = {
        "run_id": current_run_id,
        "created_at_utc": run_started_at_utc.isoformat(),
        "evaluation_mode": evaluation_mode,
        "sample_seconds": effective_sample_seconds,
        "source_count": len(selected_sources),
        "source_signature": _source_signature(sources),
        "clip_selection": {
            "strategy": clip_selection_strategy,
            "seed": effective_seed,
            "requested_segment_start_seconds": segment_start_seconds,
            "requested_segment_end_seconds": segment_end_seconds,
            "effective_segment_start_seconds": selected_sources[0].clip_start_seconds if selected_sources else None,
            "effective_segment_end_seconds": selected_sources[0].clip_end_seconds if selected_sources else None,
            "effective_segment_seconds": effective_sample_seconds,
            "source_manifest_path": str(run_dir / "source_manifest.json"),
        },
        "reference_manifest_path": str(reference_manifest_path) if reference_manifest_path else None,
        "sources": [selection.source.to_record() for selection in selected_sources],
        "sources_effective": [selection.to_record() for selection in selected_sources],
        "models": [asdict(model) for model in selected_models],
        "settings": [asdict(setting) for setting in selected_settings],
        "results": combos,
        "host_telemetry_path": str(host_telemetry_path),
        "host_telemetry_status": host_telemetry_payload.get("status") if isinstance(host_telemetry_payload, dict) else None,
        "host_telemetry_summary": host_telemetry_payload.get("summary") if isinstance(host_telemetry_payload, dict) else None,
        "clock_audit": host_telemetry_payload.get("clock_audit") if isinstance(host_telemetry_payload, dict) else None,
        "timeline_event_count": len(timeline_events),
    }

    output_path = run_dir / "benchmark_matrix.json"
    run_log_path = run_dir / "run.log"
    timeline_events_path = run_dir / "timeline_events.jsonl"
    immutable_summary_path = run_dir / "immutable_summary.json"
    source_manifest_path = _write_source_manifest(
        run_dir=run_dir,
        run_id=current_run_id,
        source_signature=payload["source_signature"],
        sample_seconds=sample_seconds,
        selection_strategy=clip_selection_strategy,
        selection_seed=effective_seed,
        selected_sources=selected_sources,
    )
    payload["source_manifest_path"] = str(source_manifest_path)
    payload["clip_selection"]["source_manifest_path"] = str(source_manifest_path)

    run_completed_perf = time.perf_counter()
    _append_timeline_event(
        events=timeline_events,
        run_started_at_utc=run_started_at_utc,
        run_started_perf=run_started_perf,
        event_kind="run_completed",
        phase="run",
        model_id=None,
        setting_id=None,
        source_id=None,
        started_perf=run_completed_perf,
        ended_perf=run_completed_perf,
        metadata={"run_status": "failed" if run_error else "completed"},
    )
    payload["timeline_event_count"] = len(timeline_events)
    _write_timeline_events(path=timeline_events_path, events=timeline_events)
    payload["timeline_events_path"] = str(timeline_events_path)
    payload["immutable_summary_path"] = str(immutable_summary_path)
    _write_run_log(run_log_path, payload)
    legend_path = run_dir / "legend_cs_short.json"
    payload["legend_cs_short"] = _write_legend_cs_short(legend_path)
    payload["legend_path"] = str(legend_path)
    payload["output_path"] = str(output_path)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    immutable_summary = _write_immutable_summary(
        path=immutable_summary_path,
        payload=payload,
        artifact_paths={
            "benchmark_matrix_path": output_path,
            "source_manifest_path": source_manifest_path,
            "host_telemetry_path": host_telemetry_path,
            "timeline_events_path": timeline_events_path,
            "run_log_path": run_log_path,
            "legend_path": legend_path,
        },
    )
    payload["immutable_summary_fingerprint"] = immutable_summary.get("summary_fingerprint")
    return payload


def run_real_source_once(
    *,
    source: SourceEntry,
    model_id: str,
    setting_id: str,
    sample_seconds: int,
    start_offset_seconds: int = 0,
    output_dir: str | Path,
    model_store_root: str | Path = ".runtime/model_store",
    reference_manifest_path: str | Path | None = None,
    clip_seed: int = 0,
    source_duration_seconds: float | None = None,
    segment_fingerprint: str | None = None,
    selection_note: str | None = None,
) -> dict[str, object]:
    model = DEFAULT_MODELS.get(model_id)
    if model is None:
        raise ValueError(f"Unsupported real-mode model: {model_id}")
    setting = DEFAULT_SETTINGS.get(setting_id)
    if setting is None:
        raise ValueError(f"Unsupported setting: {setting_id}")
    if source.origin_type != "local_file" or not source.exists:
        raise ValueError("real mode requires existing local_file sources only")

    effective_sample_seconds = max(1, int(sample_seconds))
    effective_start_offset_seconds = max(0, int(start_offset_seconds))
    selection = ClipSelection(
        source=source,
        clip_start_seconds=effective_start_offset_seconds,
        clip_seconds=effective_sample_seconds,
        clip_end_seconds=effective_start_offset_seconds + effective_sample_seconds,
        clip_seed=int(max(0, clip_seed)),
        source_duration_seconds=source_duration_seconds,
        segment_fingerprint=segment_fingerprint,
        selection_note=selection_note or "live_source_once",
    )
    combos = _run_real_matrix(
        selected_models=[model],
        selected_settings=[setting],
        selected_sources=[selection],
        sample_seconds=effective_sample_seconds,
        run_dir=Path(output_dir),
        model_store_root=Path(model_store_root),
        reference_manifest_path=reference_manifest_path,
    )
    if not combos:
        raise ValueError("No real-mode result returned for source")
    combo = combos[0]
    source_metrics = combo.get("source_metrics") if isinstance(combo.get("source_metrics"), list) else []
    if not source_metrics:
        raise ValueError("Real-mode result is missing source metrics")
    metric = dict(source_metrics[0])
    metric["aggregate"] = combo.get("aggregate")
    metric["model_id"] = combo.get("model_id")
    metric["model_label"] = combo.get("model_label")
    metric["setting_id"] = combo.get("setting_id")
    metric["setting_label"] = combo.get("setting_label")
    metric["engine"] = metric.get("engine") or combo.get("engine")
    return metric


def default_model_ids() -> list[str]:
    return ["whisper_cpp_base", "whisper_cpp_small", "sherpa_onnx_small", "vosk_small_cs_0_4"]


def default_setting_ids() -> list[str]:
    return ["low_latency", "balanced", "high_accuracy", "memory_saver"]


def _simulate_metrics(
    selection: ClipSelection,
    model: ModelPreset,
    setting: SettingPreset,
    *,
    sample_seconds: int,
) -> dict[str, object]:
    source = selection.source
    seed_payload = (
        f"{source.value}|{model.model_id}|{setting.setting_id}|"
        f"{sample_seconds}|{selection.clip_start_seconds}|{selection.clip_seed}"
    )
    seed = int(hashlib.sha256(seed_payload.encode("utf-8")).hexdigest()[:12], 16)
    rng = random.Random(seed)

    noise_small = rng.uniform(-0.02, 0.02)
    noise_medium = rng.uniform(0.90, 1.12)

    wer = _clamp(
        (0.30 - model.quality_factor * 0.18)
        + ((1.0 - setting.quality_multiplier) * 0.09)
        + max(0.0, noise_small),
        0.05,
        0.45,
    )
    cer = _clamp((wer * 0.55) + rng.uniform(0.008, 0.028), 0.03, 0.30)

    latency_ms = (
        1250.0
        / model.speed_factor
        / setting.speed_multiplier
        * noise_medium
    )
    rtf = _clamp((0.95 / model.speed_factor / setting.speed_multiplier) * noise_medium, 0.25, 3.0)
    engine_elapsed_seconds = max(0.001, float(sample_seconds) * float(rtf))

    cpu_percent = _clamp(
        36.0 * model.cpu_factor * setting.cpu_multiplier + rng.uniform(-4.0, 8.0),
        8.0,
        100.0,
    )
    ram_mb = _clamp(
        1650.0 * model.memory_factor * setting.ram_multiplier + rng.uniform(-120.0, 240.0),
        300.0,
        64000.0,
    )

    speaker_accuracy = _clamp(
        model.diarization_factor * setting.quality_multiplier + rng.uniform(-0.03, 0.03),
        0.35,
        0.99,
    )
    speaker_confusion = _clamp((1.0 - speaker_accuracy) * rng.uniform(0.60, 0.92), 0.01, 0.65)

    return {
        "source_id": source.source_id,
        "source_label": source.label,
        "origin_type": source.origin_type,
        "sample_seconds": sample_seconds,
        "clip_start_seconds": selection.clip_start_seconds,
        "clip_seconds": selection.clip_seconds,
        "clip_end_seconds": selection.clip_end_seconds,
        "clip_seed": selection.clip_seed,
        "source_duration_seconds": selection.source_duration_seconds,
        "canonical_url": source.canonical_url,
        "video_id": source.video_id,
        "segment_fingerprint": selection.segment_fingerprint,
        "wer": round(wer, 4),
        "cer": round(cer, 4),
        "latency_ms": round(latency_ms, 1),
        "engine_elapsed_seconds": round(engine_elapsed_seconds, 3),
        "rtf": round(rtf, 3),
        "cpu_percent": round(cpu_percent, 1),
        "ram_mb": round(ram_mb, 1),
        "speaker_attribution_accuracy": round(speaker_accuracy, 4),
        "speaker_confusion_rate": round(speaker_confusion, 4),
        "engine": "synthetic",
        "latency_mode": "synthetic_proxy_ms",
    }


def _aggregate_metrics(source_metrics: list[dict[str, object]]) -> dict[str, float | None]:
    count = len(source_metrics)
    if count == 0:
        raise ValueError("source_metrics cannot be empty")

    keys = [
        "wer",
        "cer",
        "latency_ms",
        "rtf",
        "cpu_percent",
        "ram_mb",
        "speaker_attribution_accuracy",
        "speaker_confusion_rate",
    ]

    metric_precision = {
        "cpu_percent": 1,
        "ram_mb": 1,
    }
    agg: dict[str, float | None] = {}
    for key in keys:
        numeric_values = [float(entry[key]) for entry in source_metrics if isinstance(entry.get(key), (int, float))]
        if numeric_values:
            agg[key] = round(sum(numeric_values) / len(numeric_values), metric_precision.get(key, 4))
        else:
            agg[key] = None
    return agg


def _score_combo(aggregate: dict[str, float | None]) -> float:
    weighted_sum = 0.0
    used_weight = 0.0

    def add_component(weight: float, value: float | None, mapper) -> None:
        nonlocal weighted_sum, used_weight
        if isinstance(value, (int, float)):
            weighted_sum += mapper(float(value)) * weight
            used_weight += weight

    add_component(45.0, aggregate.get("wer"), lambda v: max(0.0, 1.0 - min(1.0, max(0.0, v))))
    add_component(15.0, aggregate.get("cer"), lambda v: max(0.0, 1.0 - min(1.0, max(0.0, v))))
    add_component(20.0, aggregate.get("latency_ms"), lambda v: max(0.0, 1.0 - v / 2500.0))
    add_component(8.0, aggregate.get("rtf"), lambda v: max(0.0, 1.0 - v / 2.0))
    add_component(6.0, aggregate.get("ram_mb"), lambda v: max(0.0, 1.0 - v / 5000.0))
    add_component(6.0, aggregate.get("speaker_attribution_accuracy"), lambda v: min(1.0, max(0.0, v)))

    if used_weight == 0:
        return 0.0
    # normalize back to 0..100-ish range to keep score interpretation stable across partial metrics
    normalized = (weighted_sum / used_weight) * 100.0
    return round(normalized, 4)


def _write_run_log(path: Path, payload: dict[str, object]) -> None:
    top = payload["results"][0]
    clip_selection = payload.get("clip_selection", {})
    telemetry_status = payload.get("host_telemetry_status")
    telemetry_path = payload.get("host_telemetry_path")
    timeline_path = payload.get("timeline_events_path")
    immutable_summary_path = payload.get("immutable_summary_path")
    lines = [
        f"run_id={payload['run_id']}",
        f"evaluation_mode={payload['evaluation_mode']}",
        f"source_count={payload['source_count']}",
        f"source_signature={payload['source_signature']}",
        f"clip_selection_strategy={clip_selection.get('strategy')}",
        f"clip_selection_seed={clip_selection.get('seed')}",
        f"source_manifest={clip_selection.get('source_manifest_path')}",
        f"top_rank={top['model_id']}:{top['setting_id']} score={top['aggregate']['score']}",
    ]
    if telemetry_status is not None:
        lines.append(f"host_telemetry_status={telemetry_status}")
    if telemetry_path is not None:
        lines.append(f"host_telemetry_path={telemetry_path}")
    if timeline_path is not None:
        lines.append(f"timeline_events_path={timeline_path}")
    if immutable_summary_path is not None:
        lines.append(f"immutable_summary_path={immutable_summary_path}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_legend_cs_short(path: Path) -> dict[str, object]:
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "title": "Stručná legenda artefaktů a metrik benchmarku",
        "files": {
            "benchmark_request.json": "Přesná konfigurace, která byla spuštěna.",
            "source_resolution.json": "Vstupy před/po resolvu a záznamy stahování online zdrojů.",
            "source_manifest.json": "Deterministický výběr klipů (start/seed/délka).",
            "benchmark_matrix.json": "Kompletní výsledky všech kombinací model × nastavení.",
            "host_telemetry.json": "Host telemetry: stav CPU/RAM/disku před během po (system-level).",
            "timeline_events.jsonl": "Jednotná časová osa kroků benchmarku (UTC + monotonic elapsed).",
            "immutable_summary.json": "Souhrnný read-only artefakt s checksumy všech klíčových souborů.",
            "run.log": "Textový souhrn běhu a top kombinace.",
            "real_artifacts/*/*/*.txt": "Per-zdroj přepis (TXT) pro konkrétní kombinaci.",
            "real_artifacts/*/*/*.json": "Per-zdroj technické detaily adapteru (JSON).",
        },
        "metrics": {
            "latency_ms": "Latence použitá pro scoring (u offline modelů proxy přes elapsed).",
            "first_word_latency_ms": "Čas do prvního slova (pokud engine podporuje).",
            "first_word_wall_ms": "Wall-clock od startu decode do prvního slova.",
            "first_word_audio_ms": "Pozice prvního slova na časové ose audia.",
            "engine_elapsed_seconds": "Celkový čas zpracování klipu modelem.",
            "rtf": "Real-time factor (čas přepisu / délka klipu).",
            "cpu_percent": "Průměrné/odhadované CPU vytížení běhu modelu (%).",
            "ram_mb": "RAM během běhu modelu (MB).",
            "wer": "Word Error Rate (jen pokud je dostupná reference).",
            "cer": "Character Error Rate (jen pokud je dostupná reference).",
            "latency_mode": "Jak je latence získaná (true first-text vs proxy).",
        },
        "note": "REAL režim = měření na skutečném běhu adapterů nad audiem. SYNTHETIC režim = simulované metriky pro technické smoke testy; neporovnávej je s REAL výsledky.",
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _append_timeline_event(
    *,
    events: list[dict[str, Any]],
    run_started_at_utc: datetime,
    run_started_perf: float,
    event_kind: str,
    phase: str,
    model_id: str | None,
    setting_id: str | None,
    source_id: str | None,
    started_perf: float,
    ended_perf: float,
    metadata: dict[str, Any] | None = None,
) -> None:
    started_elapsed_ms = max(0.0, (float(started_perf) - float(run_started_perf)) * 1000.0)
    ended_elapsed_ms = max(started_elapsed_ms, (float(ended_perf) - float(run_started_perf)) * 1000.0)
    event_time = run_started_at_utc + timedelta(milliseconds=ended_elapsed_ms)
    events.append(
        {
            "event_index": len(events) + 1,
            "event_kind": event_kind,
            "phase": phase,
            "event_time_utc": event_time.isoformat(),
            "elapsed_start_ms": round(started_elapsed_ms, 3),
            "elapsed_end_ms": round(ended_elapsed_ms, 3),
            "elapsed_duration_ms": round(max(0.0, ended_elapsed_ms - started_elapsed_ms), 3),
            "model_id": model_id,
            "setting_id": setting_id,
            "source_id": source_id,
            "metadata": metadata or {},
        }
    )


def _write_timeline_events(*, path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(event, ensure_ascii=False)
        for event in sorted(events, key=lambda item: float(item.get("elapsed_end_ms") or 0.0))
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _write_immutable_summary(
    *,
    path: Path,
    payload: dict[str, Any],
    artifact_paths: dict[str, Path],
) -> dict[str, Any]:
    artifact_index = [_artifact_index_record(key=key, path=value) for key, value in artifact_paths.items()]
    top_result = None
    results = payload.get("results")
    if isinstance(results, list) and results and isinstance(results[0], dict):
        first = results[0]
        aggregate = first.get("aggregate") if isinstance(first.get("aggregate"), dict) else {}
        top_result = {
            "model_id": first.get("model_id"),
            "setting_id": first.get("setting_id"),
            "score": aggregate.get("score"),
            "wer": aggregate.get("wer"),
            "cer": aggregate.get("cer"),
            "latency_ms": aggregate.get("latency_ms"),
            "rtf": aggregate.get("rtf"),
            "cpu_percent": aggregate.get("cpu_percent"),
            "ram_mb": aggregate.get("ram_mb"),
        }

    summary: dict[str, Any] = {
        "summary_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "run_id": payload.get("run_id"),
        "evaluation_mode": payload.get("evaluation_mode"),
        "source_count": payload.get("source_count"),
        "result_count": len(results) if isinstance(results, list) else 0,
        "source_signature": payload.get("source_signature"),
        "clip_selection": payload.get("clip_selection"),
        "timeline_event_count": payload.get("timeline_event_count"),
        "clock_audit": payload.get("clock_audit"),
        "top_result": top_result,
        "artifact_index": artifact_index,
        "immutability_guard": {
            "rule": "summary is derived-only and never rewrites source artifacts",
            "all_artifacts_present": all(bool(item.get("exists")) for item in artifact_index),
        },
    }
    summary_fingerprint = hashlib.sha256(
        json.dumps(summary, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    summary["summary_fingerprint"] = f"sha256:{summary_fingerprint}"
    path.parent.mkdir(parents=True, exist_ok=True)
    _set_writable(path)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    _set_read_only(path)
    return summary


def _artifact_index_record(*, key: str, path: Path) -> dict[str, Any]:
    exists = path.exists() and path.is_file()
    record: dict[str, Any] = {
        "artifact_key": key,
        "path": str(path),
        "exists": exists,
    }
    if not exists:
        return record
    try:
        stat = path.stat()
        record["size_bytes"] = int(stat.st_size)
        record["modified_at_utc"] = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
    except Exception:
        pass
    checksum = _sha256_file(path)
    if checksum:
        record["sha256"] = checksum
    return record


def _sha256_file(path: Path) -> str | None:
    try:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return None


def _set_read_only(path: Path) -> None:
    try:
        path.chmod(0o444)
    except Exception:
        # Best-effort only; some filesystems may not support chmod semantics.
        return


def _set_writable(path: Path) -> None:
    if not path.exists():
        return
    try:
        path.chmod(0o644)
    except Exception:
        return


def _source_signature(sources: list[SourceEntry]) -> str:
    base = "\n".join(f"{s.origin_type}|{s.canonical_url or s.value}" for s in sources)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _effective_sample_seconds(
    *,
    sample_seconds: int,
    segment_start_seconds: int | None,
    segment_end_seconds: int | None,
) -> int:
    if segment_start_seconds is not None and segment_end_seconds is not None and segment_end_seconds > segment_start_seconds:
        return int(max(1, segment_end_seconds - segment_start_seconds))
    return int(max(1, sample_seconds))


def _select_source_clips(
    *,
    sources: list[SourceEntry],
    sample_seconds: int,
    seed: int | None,
    strategy: str,
    segment_start_seconds: int | None = None,
    segment_end_seconds: int | None = None,
) -> tuple[list[ClipSelection], int]:
    effective_seed = _resolve_clip_seed(seed=seed, sources=sources, sample_seconds=sample_seconds)
    selected: list[ClipSelection] = []

    for index, source in enumerate(sources, start=1):
        source_seed = _source_clip_seed(
            effective_seed=effective_seed,
            source=source,
            source_index=index,
            sample_seconds=sample_seconds,
        )
        duration_seconds = _probe_source_duration_seconds(source)
        clip_start_seconds = int(max(0, segment_start_seconds or 0))
        clip_end_seconds = clip_start_seconds + int(sample_seconds)
        selection_note: str | None = None

        if segment_start_seconds is not None:
            if duration_seconds is not None and float(clip_end_seconds) > float(duration_seconds) + 0.001:
                raise ValueError(
                    f"Requested segment {clip_start_seconds}-{clip_end_seconds}s exceeds source duration {duration_seconds}s for {source.source_id}"
                )
            selection_note = "explicit_segment"
        elif strategy == "start_zero":
            selection_note = "forced_start_zero"
        elif strategy == "deterministic_v1":
            if duration_seconds is None:
                selection_note = "duration_unknown_start_zero"
            else:
                max_offset = max(0, int(duration_seconds - float(sample_seconds)))
                if max_offset > 0:
                    rng = random.Random(source_seed)
                    clip_start_seconds = rng.randint(0, max_offset)
                    clip_end_seconds = clip_start_seconds + int(sample_seconds)
        else:
            raise ValueError(f"Unsupported clip_selection_strategy: {strategy}")

        selected.append(
            ClipSelection(
                source=source,
                clip_start_seconds=int(max(0, clip_start_seconds)),
                clip_seconds=sample_seconds,
                clip_end_seconds=int(max(sample_seconds, clip_end_seconds)),
                clip_seed=source_seed,
                source_duration_seconds=duration_seconds,
                segment_fingerprint=_compute_segment_fingerprint(
                    source=source,
                    clip_start_seconds=int(max(0, clip_start_seconds)),
                    clip_seconds=sample_seconds,
                ),
                selection_note=selection_note,
            )
        )

    return selected, effective_seed


def _resolve_clip_seed(*, seed: int | None, sources: list[SourceEntry], sample_seconds: int) -> int:
    if isinstance(seed, int):
        return seed
    payload = f"{sample_seconds}|{_source_signature(sources)}"
    return int(hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8], 16)


def _source_clip_seed(
    *,
    effective_seed: int,
    source: SourceEntry,
    source_index: int,
    sample_seconds: int,
) -> int:
    payload = f"{effective_seed}|{sample_seconds}|{source_index}|{source.source_id}|{source.canonical_url or source.value}"
    return int(hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8], 16)


def _probe_source_duration_seconds(source: SourceEntry) -> float | None:
    if source.origin_type != "local_file":
        return None

    path = Path(source.value)
    if not path.exists() or not path.is_file():
        return None

    if path.suffix.lower() == ".wav":
        try:
            import wave

            with wave.open(str(path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if frames > 0 and rate > 0:
                    return round(frames / float(rate), 3)
        except Exception:
            pass

    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode == 0:
            value = (proc.stdout or "").strip()
            if value:
                duration = float(value)
                if duration > 0:
                    return round(duration, 3)
    except Exception:
        return None
    return None


def _compute_segment_fingerprint(
    *,
    source: SourceEntry,
    clip_start_seconds: int,
    clip_seconds: int,
) -> str | None:
    if source.origin_type != "local_file" or not source.exists:
        return None

    path = Path(source.value)
    if not path.exists() or not path.is_file():
        return None

    if path.suffix.lower() == ".wav":
        return _compute_wav_segment_fingerprint(
            path=path,
            clip_start_seconds=clip_start_seconds,
            clip_seconds=clip_seconds,
        )
    return _compute_ffmpeg_segment_fingerprint(
        path=path,
        clip_start_seconds=clip_start_seconds,
        clip_seconds=clip_seconds,
    )


def _compute_wav_segment_fingerprint(
    *,
    path: Path,
    clip_start_seconds: int,
    clip_seconds: int,
) -> str | None:
    try:
        import wave

        with wave.open(str(path), "rb") as wf:
            sample_rate = wf.getframerate()
            if sample_rate <= 0:
                return None
            start_frame = max(0, int(clip_start_seconds * sample_rate))
            frame_count = max(1, int(clip_seconds * sample_rate))
            total_frames = wf.getnframes()
            if start_frame >= total_frames:
                return None
            wf.setpos(start_frame)
            frames = wf.readframes(frame_count)
            hasher = hashlib.sha256()
            hasher.update(f"wav|{wf.getnchannels()}|{wf.getsampwidth()}|{sample_rate}|{clip_start_seconds}|{clip_seconds}".encode("utf-8"))
            hasher.update(frames)
            return f"sha256:{hasher.hexdigest()}"
    except Exception:
        return None


def _compute_ffmpeg_segment_fingerprint(
    *,
    path: Path,
    clip_start_seconds: int,
    clip_seconds: int,
) -> str | None:
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                str(max(0, int(clip_start_seconds))),
                "-t",
                str(max(1, int(clip_seconds))),
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "s16le",
                "-",
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
    except Exception:
        return None

    if proc.returncode != 0 or not proc.stdout:
        return None

    hasher = hashlib.sha256()
    hasher.update(f"s16le|16000|1|{clip_start_seconds}|{clip_seconds}".encode("utf-8"))
    hasher.update(proc.stdout)
    return f"sha256:{hasher.hexdigest()}"


def _write_source_manifest(
    *,
    run_dir: Path,
    run_id: str,
    source_signature: str,
    sample_seconds: int,
    selection_strategy: str,
    selection_seed: int,
    selected_sources: list[ClipSelection],
) -> Path:
    payload = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_signature": source_signature,
        "sample_seconds": sample_seconds,
        "selection_strategy": selection_strategy,
        "selection_seed": selection_seed,
        "sources": [selection.to_record() for selection in selected_sources],
    }
    path = run_dir / "source_manifest.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(value, max_value))


def _run_real_matrix(
    *,
    selected_models: list[ModelPreset],
    selected_settings: list[SettingPreset],
    selected_sources: list[ClipSelection],
    sample_seconds: int,
    run_dir: Path,
    model_store_root: Path,
    reference_manifest_path: str | Path | None,
    timeline_events: list[dict[str, Any]] | None = None,
    run_started_at_utc: datetime | None = None,
    run_started_perf: float | None = None,
    subtitles_root: Path | None = None,
    progress_callback=None,
) -> list[dict[str, object]]:
    supported_models = {
        "whisper_cpp_base",
        "whisper_cpp_small",
        "whisper_cpp_large_v3",
        "sherpa_onnx_small",
        "vosk_small_cs_0_4",
        "faster_whisper_small_cs_int8",
        "faster_whisper_medium_cs_int8",
        "qwen3_asr_0_6b",
        "qwen3_asr_1_7b",
    }
    unsupported = [model.model_id for model in selected_models if model.model_id not in supported_models]
    if unsupported:
        raise ValueError(
            "real mode supports whisper_cpp_{base,small,large_v3}, sherpa_onnx_small, faster_whisper_{small,medium}_cs_int8, vosk_small_cs_0_4 and qwen3_asr_{0_6b,1_7b}. Unsupported: "
            + ", ".join(unsupported)
        )

    if any(selection.source.origin_type != "local_file" or not selection.source.exists for selection in selected_sources):
        raise ValueError("real mode requires existing local_file sources only")

    artifacts_root = run_dir / "real_artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    reference_lookup = load_reference_lookup(reference_manifest_path)
    timeline_enabled = (
        isinstance(timeline_events, list)
        and isinstance(run_started_at_utc, datetime)
        and isinstance(run_started_perf, (int, float))
    )

    combos: list[dict[str, object]] = []
    whisper_bin = resolve_whisper_cli(model_store_root)
    for model in selected_models:
        whisper_model_path = _resolve_whisper_model_path(model_store_root, model.model_id)
        if whisper_model_path is not None:
            if not whisper_bin:
                raise ValueError("whisper runtime not found. Install whisper-cli or set WHISPER_CPP_BIN.")
            if not whisper_model_path.exists():
                raise ValueError(f"whisper model file not found: {whisper_model_path}")

        sherpa_bundle = None
        if model.model_id in {"sherpa_onnx_small", "sherpa_onnx_parakeet_cs_int8"}:
            preferred_language = "cs" if model.model_id == "sherpa_onnx_parakeet_cs_int8" else None
            sherpa_bundle = resolve_sherpa_model_bundle(
                model_store_root,
                preferred_language=preferred_language,
            )
            if sherpa_bundle is None:
                raise ValueError(
                    "No sherpa-onnx transducer bundle found. Expected tokens+encoder+decoder+joiner .onnx files "
                    "under .runtime/model_store/sherpa_onnx_small or similar."
                )

        qwen_model_path = _resolve_qwen_model_path(model_store_root, model.model_id)
        if qwen_model_path is not None and not qwen_model_path.exists():
            raise ValueError(f"Qwen model directory not found: {qwen_model_path}")
        vosk_model_path = _resolve_vosk_model_path(model_store_root, model.model_id)
        if model.model_id == "vosk_small_cs_0_4" and vosk_model_path is None:
            raise ValueError(
                "VOSK model directory not found. Expected conf/model.conf + am/final.mdl under .runtime/model_store/vosk_small_cs_0_4."
            )
        if vosk_model_path is not None and not vosk_model_path.exists():
            raise ValueError(f"VOSK model directory not found: {vosk_model_path}")
        faster_model_path = _resolve_faster_whisper_model_path(model_store_root, model.model_id)
        if model.model_id in {"faster_whisper_small_cs_int8", "faster_whisper_medium_cs_int8"} and faster_model_path is None:
            raise ValueError(
                f"faster-whisper CTranslate2 model missing for {model.model_id}. "
                f"Expected runtime/model_store/{model.model_id}/model.bin"
            )
        if faster_model_path is not None and not faster_model_path.exists():
            raise ValueError(f"faster-whisper model directory not found: {faster_model_path}")

        for setting in selected_settings:
            source_metrics: list[dict[str, object]] = []
            for selection in selected_sources:
                source = selection.source
                source_out = artifacts_root / f"{model.model_id}__{setting.setting_id}" / source.source_id
                metric_started_perf = time.perf_counter()
                try:
                    runtime_cfg_snapshot: dict[str, Any] | None = None
                    if whisper_model_path is not None:
                        whisper_cfg = _setting_to_whisper_config(
                            setting_id=setting.setting_id,
                            whisper_bin=str(whisper_bin),
                            model_path=str(whisper_model_path),
                        )
                        runtime_cfg_snapshot = asdict(whisper_cfg)
                        metrics = run_whisper_source(
                            source=source,
                            sample_seconds=sample_seconds,
                            start_offset_seconds=selection.clip_start_seconds,
                            output_dir=source_out,
                            config=whisper_cfg,
                        )
                    elif model.model_id in {"sherpa_onnx_small", "sherpa_onnx_parakeet_cs_int8"}:
                        if sherpa_bundle is None:
                            raise ValueError("Sherpa bundle unexpectedly missing after validation.")
                        sherpa_cfg = _setting_to_sherpa_config(
                            setting_id=setting.setting_id,
                            tokens=sherpa_bundle.tokens,
                            encoder=sherpa_bundle.encoder,
                            decoder=sherpa_bundle.decoder,
                            joiner=sherpa_bundle.joiner,
                        )
                        runtime_cfg_snapshot = asdict(sherpa_cfg)
                        metrics = run_sherpa_source(
                            source=source,
                            sample_seconds=sample_seconds,
                            start_offset_seconds=selection.clip_start_seconds,
                            output_dir=source_out,
                            config=sherpa_cfg,
                        )
                    elif model.model_id in {"faster_whisper_small_cs_int8", "faster_whisper_medium_cs_int8"}:
                        if faster_model_path is None:
                            raise ValueError("faster-whisper model unexpectedly missing after validation.")
                        faster_cfg = _setting_to_faster_whisper_config(
                            setting_id=setting.setting_id,
                            model_id=model.model_id,
                            model_path=str(faster_model_path),
                        )
                        runtime_cfg_snapshot = asdict(faster_cfg)
                        metrics = run_faster_whisper_source(
                            source=source,
                            sample_seconds=sample_seconds,
                            start_offset_seconds=selection.clip_start_seconds,
                            output_dir=source_out,
                            config=faster_cfg,
                        )
                    elif model.model_id in {"qwen3_asr_0_6b", "qwen3_asr_1_7b"}:
                        qwen_cfg = _setting_to_qwen_config(
                            setting_id=setting.setting_id,
                            model_id=model.model_id,
                            model_path=str(qwen_model_path),
                        )
                        runtime_cfg_snapshot = asdict(qwen_cfg)
                        metrics = run_qwen_source(
                            source=source,
                            sample_seconds=sample_seconds,
                            start_offset_seconds=selection.clip_start_seconds,
                            output_dir=source_out,
                            config=qwen_cfg,
                        )
                    elif model.model_id == "vosk_small_cs_0_4":
                        vosk_cfg = _setting_to_vosk_config(
                            setting_id=setting.setting_id,
                            model_path=str(vosk_model_path),
                        )
                        runtime_cfg_snapshot = asdict(vosk_cfg)
                        metrics = run_vosk_source(
                            source=source,
                            sample_seconds=sample_seconds,
                            start_offset_seconds=selection.clip_start_seconds,
                            output_dir=source_out,
                            config=vosk_cfg,
                        )
                    else:
                        raise ValueError(f"Unsupported model in real mode: {model.model_id}")
                except Exception as exc:
                    if timeline_enabled:
                        _append_timeline_event(
                            events=timeline_events,
                            run_started_at_utc=run_started_at_utc,
                            run_started_perf=float(run_started_perf),
                            event_kind="source_metric_error",
                            phase="real_adapter",
                            model_id=model.model_id,
                            setting_id=setting.setting_id,
                            source_id=source.source_id,
                            started_perf=metric_started_perf,
                            ended_perf=time.perf_counter(),
                            metadata={"error": f"{type(exc).__name__}: {exc}"},
                        )
                    raise
                metric_ended_perf = time.perf_counter()

                metrics["clip_start_seconds"] = selection.clip_start_seconds
                metrics["clip_seconds"] = selection.clip_seconds
                metrics["clip_end_seconds"] = selection.clip_end_seconds
                metrics["clip_seed"] = selection.clip_seed
                metrics["source_duration_seconds"] = selection.source_duration_seconds
                metrics["canonical_url"] = source.canonical_url
                metrics["video_id"] = source.video_id
                metrics["segment_fingerprint"] = selection.segment_fingerprint
                _apply_reference_scoring(
                    source=source,
                    metrics=metrics,
                    reference_lookup=reference_lookup,
                    subtitles_root=subtitles_root,
                    sample_seconds=sample_seconds,
                )
                source_metrics.append(metrics)
                if timeline_enabled:
                    _append_timeline_event(
                        events=timeline_events,
                        run_started_at_utc=run_started_at_utc,
                        run_started_perf=float(run_started_perf),
                        event_kind="source_metric",
                        phase="real_adapter",
                        model_id=model.model_id,
                        setting_id=setting.setting_id,
                        source_id=source.source_id,
                        started_perf=metric_started_perf,
                        ended_perf=metric_ended_perf,
                        metadata={
                            "engine": metrics.get("engine"),
                            "clip_start_seconds": selection.clip_start_seconds,
                            "clip_end_seconds": selection.clip_end_seconds,
                            "sample_seconds": sample_seconds,
                        },
                    )

            aggregate = _aggregate_metrics(source_metrics)
            aggregate["score"] = _score_combo(aggregate)
            combo: dict[str, Any] = {
                "model_id": model.model_id,
                "model_label": model.label,
                "setting_id": setting.setting_id,
                "setting_label": setting.label,
                "aggregate": aggregate,
                "source_metrics": source_metrics,
                "engine": source_metrics[0].get("engine", "unknown") if source_metrics else "unknown",
            }
            if runtime_cfg_snapshot is not None:
                combo["model_runtime_config"] = runtime_cfg_snapshot
            combos.append(combo)
    return combos


def _setting_to_whisper_config(*, setting_id: str, whisper_bin: str, model_path: str) -> WhisperRunConfig:
    threads_cpu = max(1, (os_cpu_count() or 4) // 2)
    if setting_id == "low_latency":
        return WhisperRunConfig(
            whisper_bin=whisper_bin,
            model_path=model_path,
            threads=max(1, threads_cpu),
            beam_size=1,
            best_of=1,
            no_fallback=True,
        )
    if setting_id == "high_accuracy":
        return WhisperRunConfig(
            whisper_bin=whisper_bin,
            model_path=model_path,
            threads=max(1, threads_cpu),
            beam_size=5,
            best_of=5,
            no_fallback=False,
        )
    if setting_id == "memory_saver":
        return WhisperRunConfig(
            whisper_bin=whisper_bin,
            model_path=model_path,
            threads=1,
            beam_size=1,
            best_of=1,
            no_fallback=True,
        )
    return WhisperRunConfig(
        whisper_bin=whisper_bin,
        model_path=model_path,
        threads=max(1, threads_cpu),
        beam_size=3,
        best_of=3,
        no_fallback=True,
    )


def _resolve_whisper_model_path(model_store_root: Path, model_id: str) -> Path | None:
    return resolve_whisper_model_file(model_store_root, model_id)


def _setting_to_sherpa_config(
    *,
    setting_id: str,
    tokens: str,
    encoder: str,
    decoder: str,
    joiner: str,
) -> SherpaRunConfig:
    cpu_total = max(1, os_cpu_count() or 4)
    if setting_id == "memory_saver":
        threads = 1
    elif setting_id == "low_latency":
        threads = max(1, cpu_total // 2)
    elif setting_id == "high_accuracy":
        threads = max(1, min(cpu_total, 8))
    else:
        threads = max(1, min(cpu_total, 6))

    return SherpaRunConfig(
        tokens=tokens,
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        provider="cpu",
        num_threads=threads,
        sample_rate=16000,
        feature_dim=80,
        decoding_method="greedy_search",
    )


def _resolve_qwen_model_path(model_store_root: Path, model_id: str) -> Path | None:
    if model_id == "qwen3_asr_0_6b":
        return model_store_root / "qwen3_asr_0_6b"
    if model_id == "qwen3_asr_1_7b":
        return model_store_root / "qwen3_asr_1_7b"
    return None


def _resolve_vosk_model_path(model_store_root: Path, model_id: str) -> Path | None:
    if model_id != "vosk_small_cs_0_4":
        return None
    return resolve_vosk_model_dir(model_store_root)


def _resolve_faster_whisper_model_path(model_store_root: Path, model_id: str) -> Path | None:
    if not model_id.startswith("faster_whisper_"):
        return None
    return resolve_faster_whisper_model_path(model_store_root, model_id)


def _setting_to_qwen_config(*, setting_id: str, model_id: str, model_path: str) -> QwenRunConfig:
    if setting_id == "low_latency":
        return QwenRunConfig(
            model_path=model_path,
            language="Czech",
            device_map="cpu",
            dtype="float32",
            max_new_tokens=128 if model_id == "qwen3_asr_0_6b" else 160,
            cache_model=True,
        )
    if setting_id == "high_accuracy":
        return QwenRunConfig(
            model_path=model_path,
            language="Czech",
            device_map="cpu",
            dtype="float32",
            max_new_tokens=512,
            cache_model=True,
        )
    if setting_id == "memory_saver":
        return QwenRunConfig(
            model_path=model_path,
            language="Czech",
            device_map="cpu",
            dtype="float32",
            max_new_tokens=128,
            cache_model=True,
        )
    return QwenRunConfig(
        model_path=model_path,
        language="Czech",
        device_map="cpu",
        dtype="float32",
        max_new_tokens=256,
        cache_model=True,
    )


def _setting_to_vosk_config(*, setting_id: str, model_path: str) -> VoskRunConfig:
    if setting_id == "low_latency":
        return VoskRunConfig(model_dir=model_path, sample_rate=16000, chunk_seconds=0.12, set_words=False)
    if setting_id == "high_accuracy":
        return VoskRunConfig(model_dir=model_path, sample_rate=16000, chunk_seconds=0.28, set_words=False)
    if setting_id == "memory_saver":
        return VoskRunConfig(model_dir=model_path, sample_rate=16000, chunk_seconds=0.20, set_words=False)
    return VoskRunConfig(model_dir=model_path, sample_rate=16000, chunk_seconds=0.20, set_words=False)


def _setting_to_faster_whisper_config(*, setting_id: str, model_id: str, model_path: str) -> FasterWhisperRunConfig:
    cpu_total = max(1, os_cpu_count() or 4)
    is_medium = "medium" in model_id
    low_threads = max(1, min(cpu_total, 4 if is_medium else 6))
    bal_threads = max(1, min(cpu_total, 5 if is_medium else 6))
    acc_threads = max(1, min(cpu_total, 6 if is_medium else 8))
    if setting_id == "low_latency":
        return FasterWhisperRunConfig(
            model_path=model_path,
            language="cs",
            threads=low_threads,
            beam_size=1,
            best_of=1,
            device="cpu",
            compute_type="int8",
        )
    if setting_id == "high_accuracy":
        return FasterWhisperRunConfig(
            model_path=model_path,
            language="cs",
            threads=acc_threads,
            beam_size=3,
            best_of=3,
            device="cpu",
            compute_type="int8",
        )
    if setting_id == "memory_saver":
        return FasterWhisperRunConfig(
            model_path=model_path,
            language="cs",
            threads=max(1, min(cpu_total, 3 if is_medium else 4)),
            beam_size=1,
            best_of=1,
            device="cpu",
            compute_type="int8",
        )
    return FasterWhisperRunConfig(
        model_path=model_path,
        language="cs",
        threads=bal_threads,
        beam_size=2,
        best_of=2,
        device="cpu",
        compute_type="int8",
    )


def _apply_reference_scoring(
    *,
    source: SourceEntry,
    metrics: dict[str, object],
    reference_lookup: dict[str, str],
    subtitles_root: Path | None = None,
    sample_seconds: int = 30,
) -> None:
    reference_text = lookup_reference_text(source, reference_lookup)

    # VTT fallback: use subtitle file for the clip window if no manifest match
    if not reference_text and subtitles_root is not None:
        video_id = str(metrics.get("video_id") or "")
        clip_start = float(metrics.get("clip_start_seconds") or 0)
        clip_secs = float(metrics.get("clip_seconds") or sample_seconds)
        if video_id:
            reference_text = extract_vtt_clip_text(
                video_id=video_id,
                clip_start_s=clip_start,
                clip_end_s=clip_start + clip_secs,
                subtitles_root=subtitles_root,
            )
            if reference_text:
                metrics["reference_source"] = "vtt"

    if not reference_text:
        return
    hypothesis = str(metrics.get("transcript_text", ""))
    metrics["wer"] = round(word_error_rate(reference_text, hypothesis), 4)
    metrics["cer"] = round(char_error_rate(reference_text, hypothesis), 4)
    metrics["reference_text"] = reference_text


def os_cpu_count() -> int | None:
    try:
        import os

        return os.cpu_count()
    except Exception:
        return None
