#!/usr/bin/env python
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io

configure_console_io()

BASE_URL = "http://127.0.0.1:8012"
LOG_DIR = ROOT / "runtime" / "logs"
RUNS_ROOT = ROOT / "runtime" / "runs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_json(method: str, path: str, payload: dict | None = None, timeout: float = 20.0) -> Any:
    url = f"{BASE_URL}{path}"
    data: bytes | None = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _safe_excerpt(text: str, limit: int = 180) -> str:
    t = (text or "").strip().replace("\r", " ").replace("\n", " ")
    return t[:limit]


@dataclass
class ModelRunRow:
    model_id: str
    installed: bool
    status: str
    setting_id: str
    model_params_used: dict[str, Any] = field(default_factory=dict)
    job_id: str | None = None
    run_id: str | None = None
    transcript_len: int = 0
    transcript_excerpt: str = ""
    transcript_text: str = ""
    rtf: float | None = None
    latency_ms: float | None = None
    clip_start_seconds: float | None = None
    clip_seconds: float | None = None
    archive_transcript_id: str | None = None
    archive_save_error: str | None = None
    error: str | None = None


def _resolve_default_source_id() -> str:
    items = _http_json("GET", "/api/library/items", timeout=12.0)
    if not isinstance(items, list) or not items:
        raise RuntimeError("Library is empty. Add or import an audio source first.")

    test_first = [
        it for it in items
        if str(it.get("video_id", "")).startswith("local_")
        and "TEST" in str(it.get("title", "")).upper()
        and str(it.get("language", "")).lower().startswith("cs")
    ]
    if test_first:
        return str(test_first[0]["video_id"])

    cs_items = [it for it in items if str(it.get("language", "")).lower().startswith("cs")]
    if cs_items:
        return str(cs_items[0]["video_id"])

    return str(items[0]["video_id"])


def _pick_models(options_models: list[dict], installed_map: dict[str, bool], model_arg: str, all_installed: bool) -> list[str]:
    available = [str(m.get("id")) for m in options_models if m.get("id")]
    if all_installed:
        return [mid for mid in available if installed_map.get(mid, False)]

    if model_arg.strip():
        requested = [m.strip() for m in model_arg.split(",") if m.strip()]
        return [m for m in requested if m in available]

    # Default short path: only turbo smoke.
    return ["whisper_cpp_large_v3_turbo"] if "whisper_cpp_large_v3_turbo" in available else available[:1]


def _registry_defaults(registry_rows: list[dict]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in registry_rows:
        model_id = str(row.get("model_id") or "")
        if not model_id:
            continue
        defaults: dict[str, Any] = {}
        for p in row.get("params") or []:
            name = p.get("name")
            if not name:
                continue
            defaults[str(name)] = p.get("default")
        out[model_id] = defaults
    return out


def _load_params_file(path: str) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"params file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("params file must be a JSON object {model_id: {param: value}}")
    out: dict[str, dict[str, Any]] = {}
    for model_id, params in data.items():
        if isinstance(params, dict):
            out[str(model_id)] = dict(params)
    return out


def _build_model_params(
    *,
    model_id: str,
    defaults_map: dict[str, dict[str, Any]],
    file_overrides: dict[str, dict[str, Any]],
    threads: int | None,
    beam_size: int | None,
    best_of: int | None,
    language: str,
) -> dict[str, Any]:
    params = dict(defaults_map.get(model_id, {}))
    params.update(file_overrides.get(model_id, {}))

    if language.strip() and "language" in params:
        params["language"] = language.strip()

    if threads is not None:
        if "threads" in params:
            params["threads"] = int(threads)
        if "num_threads" in params:
            params["num_threads"] = int(threads)

    if beam_size is not None and "beam_size" in params:
        params["beam_size"] = int(beam_size)
    if best_of is not None and "best_of" in params:
        params["best_of"] = int(best_of)

    return params


def _poll_job(job_id: str, timeout_s: int) -> dict:
    start = time.time()
    while True:
        job = _http_json("GET", f"/api/benchmark/jobs/{job_id}", timeout=10.0)
        status = str(job.get("status") or "")
        if status in {"completed", "failed", "cancelled"}:
            return job
        if (time.time() - start) > timeout_s:
            try:
                _http_json("POST", f"/api/benchmark/jobs/{job_id}/cancel", payload={}, timeout=8.0)
            except Exception:
                pass
            return {
                "status": "timeout",
                "error": f"timeout after {timeout_s}s",
                "job_id": job_id,
            }
        time.sleep(1.2)


def _read_run_matrix_row(run_id: str, model_id: str, setting_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    matrix_path = RUNS_ROOT / run_id / "benchmark_matrix.json"
    if not matrix_path.exists():
        raise FileNotFoundError(f"benchmark_matrix.json not found: {matrix_path}")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    results = matrix.get("results") or []
    if not results:
        return {}, {}

    result = next(
        (
            r for r in results
            if str(r.get("model_id")) == model_id and str(r.get("setting_id")) == setting_id
        ),
        results[0],
    )
    source_metrics = result.get("source_metrics") or []
    sm0 = source_metrics[0] if source_metrics else {}
    return result, sm0


def _run_one_model(
    *,
    source_id: str,
    model_id: str,
    installed: bool,
    sample_s: int,
    start_s: int,
    timeout_s: int,
    setting_id: str,
    model_params: dict[str, Any],
    label_prefix: str,
) -> ModelRunRow:
    row = ModelRunRow(
        model_id=model_id,
        installed=installed,
        status="pending",
        setting_id=setting_id,
        model_params_used=dict(model_params),
    )
    if not installed:
        row.status = "skipped_not_installed"
        row.error = "model not installed"
        return row

    payload: dict[str, Any] = {
        "video_ids": [source_id],
        "model_ids": [model_id],
        "setting_ids": [setting_id],
        "evaluation_mode": "streaming",
        "sample_seconds": sample_s,
        "segment_start_seconds": start_s,
        "label": f"{label_prefix} {model_id}",
    }
    if model_params:
        payload["model_params"] = {model_id: model_params}

    try:
        job = _http_json("POST", "/api/benchmark/jobs", payload=payload, timeout=20.0)
    except Exception as exc:
        row.status = "create_failed"
        row.error = str(exc)
        return row

    row.job_id = str(job.get("job_id") or "")
    if not row.job_id:
        row.status = "create_failed"
        row.error = "missing job_id"
        return row

    final_job = _poll_job(row.job_id, timeout_s=timeout_s)
    job_status = str(final_job.get("status") or "")
    row.run_id = final_job.get("run_id")
    if job_status != "completed" or not row.run_id:
        row.status = "failed"
        row.error = str(final_job.get("error") or f"job status={job_status}")
        return row

    try:
        result0, sm0 = _read_run_matrix_row(row.run_id, model_id, setting_id)
    except Exception as exc:
        row.status = "completed_read_failed"
        row.error = str(exc)
        return row

    transcript = str(sm0.get("transcript") or "").strip()
    row.transcript_len = len(transcript)
    row.transcript_excerpt = _safe_excerpt(transcript)
    row.transcript_text = transcript
    row.rtf = sm0.get("rtf")
    if row.rtf is None:
        row.rtf = (result0.get("aggregate") or {}).get("rtf")
    row.latency_ms = sm0.get("latency_ms")
    if row.latency_ms is None:
        row.latency_ms = (result0.get("aggregate") or {}).get("latency_ms")
    row.clip_start_seconds = sm0.get("clip_start_seconds")
    row.clip_seconds = sm0.get("clip_seconds")
    row.error = sm0.get("error")

    if row.error:
        row.status = "blocked"
    elif transcript:
        row.status = "pass"
    else:
        row.status = "empty"
    return row


def _transcript_to_html(title: str, model_id: str, plain_text: str) -> str:
    lines = [ln.strip() for ln in plain_text.splitlines() if ln.strip()]
    body = "".join(f"<p>{html.escape(line)}</p>" for line in lines)
    header = (
        f"<p><strong>{html.escape(title)}</strong></p>"
        f"<p><em>Model: {html.escape(model_id)}</em></p><hr/>"
    )
    return header + body


def _save_transcript_to_archive(
    *,
    row: ModelRunRow,
    source_id: str,
    sample_s: int,
    start_s: int,
    title_prefix: str,
) -> str:
    text = row.transcript_text.strip()
    if not text:
        raise RuntimeError("empty transcript")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    title = f"{title_prefix}_{row.model_id}_start{start_s}s_len{sample_s}s_{stamp}"
    payload = {
        "title": title,
        "html": _transcript_to_html(title, row.model_id, text),
        "plain_text": text,
        "source_label": source_id,
        "model_id": row.model_id,
        "range_from": f"{start_s}s",
        "range_to": f"{start_s + sample_s}s",
    }
    saved = _http_json("POST", "/api/transcribe/transcripts", payload=payload, timeout=20.0)
    transcript_id = str(saved.get("transcript_id") or "").strip()
    if not transcript_id:
        raise RuntimeError("missing transcript_id in save response")
    return transcript_id


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Smoke Transcribe 13s Report")
    lines.append("")
    profile_label = str(payload.get("profile_label") or "").strip()
    profile_settings = payload.get("profile_settings") or {}
    if profile_label or profile_settings:
        lines.append("## Header - Transcription Settings")
        lines.append("")
        if profile_label:
            lines.append(f"- profile_label: `{profile_label}`")
        if profile_settings:
            lines.append("- model_settings:")
            for model_id in payload.get("models", []):
                params = profile_settings.get(model_id)
                if isinstance(params, dict):
                    lines.append(f"  - `{model_id}`: `{json.dumps(params, ensure_ascii=False)}`")
        lines.append("")
    lines.append(f"- generated_at_utc: `{payload.get('generated_at_utc')}`")
    lines.append(f"- source_id: `{payload.get('source_id')}`")
    lines.append(f"- sample_s: `{payload.get('sample_s')}`")
    lines.append(f"- start_s: `{payload.get('start_s')}`")
    lines.append(f"- setting_id: `{payload.get('setting_id')}`")
    lines.append(f"- param_source: `{payload.get('param_source')}`")
    lines.append(f"- threads_override: `{payload.get('threads_override')}`")
    lines.append(f"- beam_override: `{payload.get('beam_override')}`")
    lines.append(f"- best_of_override: `{payload.get('best_of_override')}`")
    lines.append(f"- language_override: `{payload.get('language_override')}`")
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| model | status | transcript_len | rtf | latency_ms | archive | error |")
    lines.append("|---|---|---:|---:|---:|---|---|")
    for row in payload.get("rows", []):
        err = (row.get("error") or "").replace("\n", " ").replace("|", "/")
        archive_note = row.get("archive_transcript_id") or row.get("archive_save_error") or ""
        archive_note = str(archive_note).replace("\n", " ").replace("|", "/")
        lines.append(
            f"| `{row.get('model_id')}` | `{row.get('status')}` | {row.get('transcript_len') or 0} | "
            f"{row.get('rtf') if row.get('rtf') is not None else ''} | "
            f"{row.get('latency_ms') if row.get('latency_ms') is not None else ''} | {archive_note[:60]} | {err[:140]} |"
        )
    lines.append("")
    lines.append("## Params Used")
    lines.append("")
    for row in payload.get("rows", []):
        params = row.get("model_params_used") or {}
        lines.append(f"- `{row.get('model_id')}`: `{json.dumps(params, ensure_ascii=False)}`")
    lines.append("")
    lines.append("## Excerpts")
    lines.append("")
    for row in payload.get("rows", []):
        excerpt = row.get("transcript_excerpt") or ""
        if not excerpt:
            continue
        lines.append(f"- `{row.get('model_id')}`: {excerpt}")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Physical 13s smoke test via /api/benchmark/jobs with per-model defaults from /api/models/registry."
    )
    parser.add_argument("--source-id", default="", help="Library source_id/video_id (default: auto-detect CZ TEST/local).")
    parser.add_argument("--sample-s", type=int, default=13)
    parser.add_argument("--start-s", type=int, default=0)
    parser.add_argument("--setting-id", default="balanced", help="Benchmark setting id (default: balanced).")
    parser.add_argument("--timeout-s", type=int, default=420)
    parser.add_argument("--models", default="", help="Comma-separated model IDs. Empty => whisper_cpp_large_v3_turbo only.")
    parser.add_argument("--all-installed", action="store_true", help="Run all installed models from /api/benchmark/options.")
    parser.add_argument("--params-file", default="", help="Optional JSON {model_id:{param:value}} overrides.")
    parser.add_argument("--profile-label", default="", help="Optional label for chosen params profile shown in report header.")
    parser.add_argument("--threads", type=int, default=None, help="Optional override for 'threads'/'num_threads' where supported.")
    parser.add_argument("--beam-size", type=int, default=None, help="Optional override for 'beam_size' where supported.")
    parser.add_argument("--best-of", type=int, default=None, help="Optional override for 'best_of' where supported.")
    parser.add_argument("--language", default="", help="Optional override for 'language' where supported.")
    parser.add_argument("--save-archive", action="store_true", help="Save PASS transcripts to /api/transcribe/transcripts.")
    parser.add_argument("--archive-title-prefix", default="CZ_TEST_SMOKE13", help="Title prefix used with --save-archive.")
    parser.add_argument("--verify-offset", action="store_true", help="Extra turbo run at start=60 and compare excerpt with primary run.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when any model is not PASS.")
    args = parser.parse_args()

    try:
        _http_json("GET", "/api/health", timeout=5.0)
    except (URLError, HTTPError) as exc:
        print(f"FAIL  Backend not reachable at {BASE_URL}: {exc}")
        return 2

    source_id = args.source_id.strip() or _resolve_default_source_id()
    options = _http_json("GET", "/api/benchmark/options", timeout=10.0)
    model_status = _http_json("GET", "/api/models", timeout=10.0)
    registry = _http_json("GET", "/api/models/registry", timeout=10.0)

    installed_map = {str(m.get("model_id")): bool(m.get("installed")) for m in (model_status or [])}
    models = _pick_models(options.get("models", []), installed_map, args.models, args.all_installed)
    if not models:
        print("FAIL  No matching models to run.")
        return 2

    defaults_map = _registry_defaults(registry if isinstance(registry, list) else [])
    file_overrides = _load_params_file(args.params_file)

    rows: list[ModelRunRow] = []
    print(f"Smoke source_id: {source_id}")
    print(f"Models: {', '.join(models)}")
    print(
        f"Settings: sample={args.sample_s}s start={args.start_s}s setting={args.setting_id} "
        f"| threads_override={args.threads} beam_override={args.beam_size} best_of_override={args.best_of} "
        f"language_override={(args.language or '-')}"
    )

    for idx, model_id in enumerate(models, start=1):
        model_params = _build_model_params(
            model_id=model_id,
            defaults_map=defaults_map,
            file_overrides=file_overrides,
            threads=args.threads,
            beam_size=args.beam_size,
            best_of=args.best_of,
            language=args.language,
        )
        print(f"[{idx}/{len(models)}] {model_id} ...")
        row = _run_one_model(
            source_id=source_id,
            model_id=model_id,
            installed=installed_map.get(model_id, False),
            sample_s=max(1, int(args.sample_s)),
            start_s=max(0, int(args.start_s)),
            timeout_s=max(30, int(args.timeout_s)),
            setting_id=str(args.setting_id),
            model_params=model_params,
            label_prefix="SMOKE13",
        )
        rows.append(row)
        print(
            f"  -> {row.status} | len={row.transcript_len} | run={row.run_id or '-'}"
            + (f" | err={row.error[:120]}" if row.error else "")
        )

    archive_saved = 0
    if args.save_archive:
        print("[archive-save] saving PASS transcripts to /api/transcribe/transcripts ...")
        for row in rows:
            if row.status != "pass" or not row.transcript_text.strip():
                continue
            try:
                transcript_id = _save_transcript_to_archive(
                    row=row,
                    source_id=source_id,
                    sample_s=max(1, int(args.sample_s)),
                    start_s=max(0, int(args.start_s)),
                    title_prefix=args.archive_title_prefix.strip() or "CZ_TEST_SMOKE13",
                )
                row.archive_transcript_id = transcript_id
                archive_saved += 1
                print(f"  -> saved {row.model_id}: {transcript_id}")
            except Exception as exc:
                row.archive_save_error = str(exc)
                print(f"  -> save failed {row.model_id}: {row.archive_save_error}")

    offset_check: dict[str, Any] | None = None
    if args.verify_offset and "whisper_cpp_large_v3_turbo" in models:
        print("[offset-check] whisper_cpp_large_v3_turbo at start=60 ...")
        model_params = _build_model_params(
            model_id="whisper_cpp_large_v3_turbo",
            defaults_map=defaults_map,
            file_overrides=file_overrides,
            threads=args.threads,
            beam_size=args.beam_size,
            best_of=args.best_of,
            language=args.language,
        )
        row60 = _run_one_model(
            source_id=source_id,
            model_id="whisper_cpp_large_v3_turbo",
            installed=installed_map.get("whisper_cpp_large_v3_turbo", False),
            sample_s=max(1, int(args.sample_s)),
            start_s=60,
            timeout_s=max(30, int(args.timeout_s)),
            setting_id=str(args.setting_id),
            model_params=model_params,
            label_prefix="SMOKE13-offset60",
        )
        first_primary = next((r for r in rows if r.model_id == "whisper_cpp_large_v3_turbo"), None)
        primary_excerpt = first_primary.transcript_excerpt if first_primary else ""
        different = bool(primary_excerpt and row60.transcript_excerpt and primary_excerpt != row60.transcript_excerpt)
        offset_check = {
            "primary_start_s": int(args.start_s),
            "offset_start_s": 60,
            "primary_status": first_primary.status if first_primary else None,
            "offset_status": row60.status,
            "primary_excerpt": primary_excerpt,
            "offset_excerpt": row60.transcript_excerpt,
            "excerpt_differs": different,
            "offset_run": asdict(row60),
        }
        print(f"  -> offset status={row60.status} | differs={different}")

    summary = {
        "pass": sum(1 for r in rows if r.status == "pass"),
        "blocked": sum(1 for r in rows if r.status == "blocked"),
        "empty": sum(1 for r in rows if r.status == "empty"),
        "failed": sum(1 for r in rows if r.status in {"failed", "create_failed", "completed_read_failed"}),
        "skipped_not_installed": sum(1 for r in rows if r.status == "skipped_not_installed"),
        "archive_saved": archive_saved,
    }

    payload: dict[str, Any] = {
        "generated_at_utc": _now_utc(),
        "base_url": BASE_URL,
        "source_id": source_id,
        "sample_s": int(args.sample_s),
        "start_s": int(args.start_s),
        "setting_id": str(args.setting_id),
        "param_source": "registry_defaults+params_file+cli_overrides",
        "threads_override": args.threads,
        "beam_override": args.beam_size,
        "best_of_override": args.best_of,
        "language_override": args.language or "",
        "save_archive": bool(args.save_archive),
        "models": models,
        "summary": summary,
        "rows": [asdict(r) for r in rows],
    }
    profile_label = args.profile_label.strip()
    if profile_label:
        payload["profile_label"] = profile_label
    if file_overrides:
        payload["profile_settings"] = file_overrides
    if offset_check is not None:
        payload["offset_check"] = offset_check

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = LOG_DIR / f"smoke_transcribe_13s_{stamp}.json"
    md_path = LOG_DIR / f"smoke_transcribe_13s_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(md_path, payload)

    print("")
    print(f"Report JSON: {json_path}")
    print(f"Report MD:   {md_path}")
    print(
        "Summary: "
        f"pass={summary['pass']} blocked={summary['blocked']} empty={summary['empty']} "
        f"failed={summary['failed']} skipped={summary['skipped_not_installed']} "
        f"archive_saved={summary['archive_saved']}"
    )

    if args.strict and any(r.status != "pass" for r in rows):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
