"""
Read and parse completed benchmark run artifacts.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
from typing import Optional
from urllib.parse import urlparse

from ..config import RUNS_ROOT, SUBTITLES_ROOT
from ..models.runs import RunSummary, RunDetail, RunResult, SourceMetric, AggregateMetrics
from packages.benchmarks.ground_truth.vtt_reference import extract_vtt_clip_text
from packages.benchmarks.metrics.text_metrics import (
    char_error_rate,
    match_error_rate,
    word_error_rate,
    word_error_rate_normalized,
    word_information_lost,
    segment_level_wer,
)
from packages.ingest.source_resolver import extract_youtube_video_id

_WORD_RE = re.compile(r"\w+", flags=re.UNICODE)


def _infer_video_id(video_id: Optional[str], canonical_url: Optional[str]) -> Optional[str]:
    vid = (video_id or "").strip()
    if vid and not vid.startswith("src-"):
        return vid

    url = (canonical_url or "").strip()
    if not url:
        return None

    # YouTube URL
    yt_id = extract_youtube_video_id(url)
    if yt_id:
        return yt_id

    # Local cached WAV: .../runtime/audio_cache/<video_id>.wav
    try:
        parsed = urlparse(url)
        raw_path = parsed.path if parsed.scheme else url
        p = Path(raw_path)
        if p.suffix.lower() == ".wav" and p.parent.name == "audio_cache":
            stem = p.stem.strip()
            try:
                from . import library_service

                for item in library_service.list_items():
                    if stem == item.video_id or stem.endswith(f"_{item.video_id}"):
                        return item.video_id
            except Exception:
                pass
            return stem or None
    except Exception:
        return None

    return None


def _resolve_artifact_source_id(
    *,
    raw_metric_video_id: Optional[str],
    canonical_url: Optional[str],
    source_id_by_canonical: dict[str, str],
    fallback_source_ids: list[str],
) -> Optional[str]:
    raw = str(raw_metric_video_id or "").strip()
    if raw.startswith("src-"):
        return raw

    canon = str(canonical_url or "").strip()
    if canon and canon in source_id_by_canonical:
        return source_id_by_canonical[canon]

    if len(fallback_source_ids) == 1:
        return fallback_source_ids[0]
    return None


def _load_whisper_segments_from_artifacts(
    *,
    run_dir: Path,
    model_id: str,
    setting_id: str,
    source_id: Optional[str],
) -> list[dict] | None:
    sid = str(source_id or "").strip()
    if not sid:
        return None
    json_path = (
        run_dir
        / "streaming_artifacts"
        / model_id
        / setting_id
        / sid
        / f"{sid}_whisper.json"
    )
    if not json_path.exists():
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        segments = payload.get("transcription")
        if isinstance(segments, list) and segments:
            return segments
    except Exception:
        return None
    return None


def _tokenize_words_normalized(text: str | None) -> list[str]:
    return [token.lower() for token in _WORD_RE.findall(text or "")]


def _align_word_ops(ref_tokens: list[str], hyp_tokens: list[str]) -> list[tuple[str, str | None, str | None]]:
    r_len = len(ref_tokens)
    h_len = len(hyp_tokens)
    dp: list[list[int]] = [[0] * (h_len + 1) for _ in range(r_len + 1)]
    for i in range(r_len + 1):
        dp[i][0] = i
    for j in range(h_len + 1):
        dp[0][j] = j
    for i in range(1, r_len + 1):
        for j in range(1, h_len + 1):
            if ref_tokens[i - 1] == hyp_tokens[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])

    ops: list[tuple[str, str | None, str | None]] = []
    i = r_len
    j = h_len
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref_tokens[i - 1] == hyp_tokens[j - 1] and dp[i][j] == dp[i - 1][j - 1]:
            ops.append(("=", ref_tokens[i - 1], hyp_tokens[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            ops.append(("S", ref_tokens[i - 1], hyp_tokens[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            ops.append(("D", ref_tokens[i - 1], None))
            i -= 1
        else:
            ops.append(("I", None, hyp_tokens[j - 1]))
            j -= 1
    ops.reverse()
    return ops


def _segment_metrics_from_global_alignment(
    whisper_segments: list[dict],
    reference_text: str,
) -> list[dict] | None:
    normalized_ref = _tokenize_words_normalized(reference_text)
    if not normalized_ref:
        return None

    seg_rows: list[dict] = []
    hyp_tokens_flat: list[str] = []
    hyp_token_segment_idx: list[int] = []

    for seg_idx, seg in enumerate(whisper_segments):
        offsets = seg.get("offsets", {})
        from_ms = int(offsets.get("from", 0) or 0)
        to_ms = int(offsets.get("to", 0) or 0)
        if to_ms <= from_ms:
            continue
        hyp_text_raw = str(seg.get("text", "")).strip()
        seg_rows.append({
            "seg_idx": seg_idx,
            "from_ms": from_ms,
            "to_ms": to_ms,
            "hyp_text": hyp_text_raw,
        })
        hyp_tokens = _tokenize_words_normalized(hyp_text_raw)
        for token in hyp_tokens:
            hyp_tokens_flat.append(token)
            hyp_token_segment_idx.append(len(seg_rows) - 1)

    if not seg_rows or not hyp_tokens_flat:
        return None

    ops = _align_word_ops(normalized_ref, hyp_tokens_flat)
    ref_tokens_by_row: list[list[str]] = [[] for _ in seg_rows]

    hyp_cursor = 0
    last_row_idx = len(seg_rows) - 1
    for op, ref_word, hyp_word in ops:
        if op in ("=", "S", "I"):
            row_idx = hyp_token_segment_idx[hyp_cursor] if hyp_cursor < len(hyp_token_segment_idx) else last_row_idx
            if hyp_word is not None:
                hyp_cursor += 1
        else:  # D
            row_idx = hyp_token_segment_idx[hyp_cursor] if hyp_cursor < len(hyp_token_segment_idx) else last_row_idx
        if ref_word:
            ref_tokens_by_row[row_idx].append(ref_word)

    result: list[dict] = []
    for row_idx, row in enumerate(seg_rows):
        hyp_text = row["hyp_text"]
        ref_text = " ".join(ref_tokens_by_row[row_idx]).strip()
        seg_wer: float | None = None
        if ref_text and hyp_text:
            seg_wer = round(word_error_rate_normalized(ref_text, hyp_text), 4)
        result.append({
            "from_ms": row["from_ms"],
            "to_ms": row["to_ms"],
            "hyp_text": hyp_text,
            "ref_text": ref_text or None,
            "wer": seg_wer,
        })

    return result if result else None


def _enrich_source_metric(
    sm: dict,
    *,
    run_dir: Path,
    model_id: str,
    setting_id: str,
    sources_by_id: dict[str, str | None],
    source_id_by_canonical: dict[str, str],
    all_source_ids: list[str],
    sample_seconds: int,
) -> SourceMetric:
    canonical_url = sm.get("canonical_url") or sources_by_id.get(str(sm.get("video_id") or ""))
    resolved_video_id = _infer_video_id(sm.get("video_id"), canonical_url)
    transcript = sm.get("transcript")
    clip_start_seconds = sm.get("clip_start_seconds")
    clip_seconds = sm.get("clip_seconds") or sample_seconds

    reference_text = sm.get("reference_text")
    if not reference_text and transcript and resolved_video_id:
        reference_text = extract_vtt_clip_text(
            resolved_video_id,
            int(max(0, float(clip_start_seconds or 0))),
            int(max(0, float(clip_seconds or sample_seconds or 0))),
            SUBTITLES_ROOT,
        )

    wer = sm.get("wer")
    cer = sm.get("cer")
    wer_normalized = sm.get("wer_normalized")
    mer = sm.get("mer")
    wil = sm.get("wil")
    if transcript and reference_text:
        if wer is None:
            wer = round(word_error_rate(reference_text, transcript), 4)
        if cer is None:
            cer = round(char_error_rate(reference_text, transcript), 4)
        if wer_normalized is None:
            wer_normalized = round(word_error_rate_normalized(reference_text, transcript), 4)
        if mer is None:
            mer = round(match_error_rate(reference_text, transcript), 4)
        if wil is None:
            wil = round(word_information_lost(reference_text, transcript), 4)

    segment_metrics = sm.get("segment_metrics")
    if not segment_metrics and resolved_video_id:
        artifact_source_id = _resolve_artifact_source_id(
            raw_metric_video_id=sm.get("video_id"),
            canonical_url=canonical_url,
            source_id_by_canonical=source_id_by_canonical,
            fallback_source_ids=all_source_ids,
        )
        whisper_segments = _load_whisper_segments_from_artifacts(
            run_dir=run_dir,
            model_id=model_id,
            setting_id=setting_id,
            source_id=artifact_source_id,
        )
        if whisper_segments:
            try:
                if reference_text:
                    segment_metrics = _segment_metrics_from_global_alignment(whisper_segments, reference_text)
                if not segment_metrics:
                    segment_metrics = segment_level_wer(
                        whisper_segments,
                        resolved_video_id,
                        float(max(0, clip_start_seconds or 0)),
                        SUBTITLES_ROOT,
                    )
            except Exception:
                segment_metrics = None

    return SourceMetric(
        video_id=resolved_video_id,
        canonical_url=canonical_url,
        clip_start_seconds=clip_start_seconds,
        clip_seconds=clip_seconds,
        transcript=transcript,
        reference_text=reference_text,
        wer=wer,
        cer=cer,
        latency_ms=sm.get("latency_ms"),
        rtf=sm.get("rtf"),
        engine_elapsed_seconds=sm.get("engine_elapsed_seconds"),
        model_runtime_config=sm.get("model_runtime_config"),
        chunk_metrics=sm.get("chunk_metrics"),
        wer_normalized=wer_normalized,
        mer=mer,
        wil=wil,
        segment_metrics=segment_metrics,
    )


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
        sources_by_id: dict[str, str | None] = {
            str(s.get("source_id") or ""): s.get("canonical_url")
            for s in data.get("sources", [])
        }
        source_id_by_canonical: dict[str, str] = {
            str(s.get("canonical_url") or ""): str(s.get("source_id") or "")
            for s in data.get("sources", [])
            if s.get("canonical_url") and s.get("source_id")
        }
        all_source_ids: list[str] = [
            str(s.get("source_id") or "")
            for s in data.get("sources", [])
            if s.get("source_id")
        ]
        sample_seconds = int(data.get("sample_seconds") or 0)
        results = []
        for r in data.get("results", []):
            agg = r.get("aggregate", {})
            source_metrics = [
                _enrich_source_metric(
                    sm,
                    run_dir=run_dir,
                    model_id=r.get("model_id", ""),
                    setting_id=r.get("setting_id", ""),
                    sources_by_id=sources_by_id,
                    source_id_by_canonical=source_id_by_canonical,
                    all_source_ids=all_source_ids,
                    sample_seconds=sample_seconds,
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
