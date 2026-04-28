#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app.config import AUDIO_CACHE_ROOT, MODEL_STORE_ROOT, SUBTITLES_ROOT
from backend.app.services import library_service
from packages.adapters.model_readiness import collect_model_readiness, summarize_readiness
from packages.common.console_io import configure_console_io
from packages.common.network_access import strict_offline_enabled
from packages.common.runtime_paths import runtime_subpath
from scripts.download_audio import _download_video

configure_console_io()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _subtitle_files(video_id: str) -> list[Path]:
    sub_dir = SUBTITLES_ROOT / video_id
    if not sub_dir.exists():
        return []
    return [p for p in sub_dir.iterdir() if p.is_file() and p.suffix.lower() in {".vtt", ".srt", ".txt", ".md"}]


def _select_items(raw_items: list[dict], *, video_ids: list[str], only_cs: bool) -> list[dict]:
    if video_ids:
        wanted = set(video_ids)
        return [item for item in raw_items if str(item.get("video_id")) in wanted]
    if only_cs:
        return [item for item in raw_items if str(item.get("language", "")).lower() == "cs"]
    return raw_items


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline preseed: inventory + optional fill for audio/subtitles/model_store readiness."
    )
    parser.add_argument("--video-ids", nargs="*", default=[], help="Target video_ids (default: all, or only-cs with --only-cs).")
    parser.add_argument("--only-cs", action="store_true", help="Use only Czech videos from runtime/library/items.json.")
    parser.add_argument("--download-audio", action="store_true", help="Download missing runtime/audio_cache/<video_id>.wav.")
    parser.add_argument("--download-subtitles", action="store_true", help="Download missing subtitles via yt-dlp.")
    parser.add_argument(
        "--report-path",
        default=str(runtime_subpath("offline", "preseed_report.json")),
        help="Path to JSON report.",
    )
    parser.add_argument("--fail-on-missing", action="store_true", help="Exit 1 when any required offline asset is missing.")
    args = parser.parse_args()

    raw_items = library_service._load_raw()  # noqa: SLF001 - intentional internal read for batch prep
    targets = _select_items(raw_items, video_ids=list(args.video_ids or []), only_cs=bool(args.only_cs))

    if not targets:
        print("No matching library items found.")
        return 1

    AUDIO_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    SUBTITLES_ROOT.mkdir(parents=True, exist_ok=True)
    MODEL_STORE_ROOT.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    missing_audio = 0
    missing_subtitles = 0

    print(f"Targets: {len(targets)} video(s)")
    print(f"strict_offline_enabled={strict_offline_enabled()}")

    for item in targets:
        video_id = str(item.get("video_id") or "")
        url = str(item.get("url") or "")
        duration_s = float(item.get("duration_seconds") or 0.0)
        audio_path = library_service.resolve_audio_cache_file_for_library_item(video_id, extensions=(".wav",))
        if audio_path is None:
            audio_path = AUDIO_CACHE_ROOT / library_service.audio_cache_filename_for_library_item(
                video_id,
                str(item.get("title") or ""),
                ".wav",
            )

        subtitle_paths = _subtitle_files(video_id)
        subtitles_ok = bool(subtitle_paths)
        if not subtitles_ok and args.download_subtitles and url:
            res = library_service.download_subtitles(video_id, url)
            subtitles_ok = bool(res.get("ok")) and bool(_subtitle_files(video_id))
            subtitle_paths = _subtitle_files(video_id)

        audio_ok = audio_path.exists()
        if not audio_ok and args.download_audio:
            _download_video(video_id, duration_s, str(item.get("title") or ""))
            audio_ok = audio_path.exists()

        if not audio_ok:
            missing_audio += 1
        if not subtitles_ok:
            missing_subtitles += 1

        rows.append(
            {
                "video_id": video_id,
                "url": url,
                "duration_seconds": duration_s,
                "audio_cached": audio_ok,
                "audio_path": str(audio_path),
                "subtitles_ready": subtitles_ok,
                "subtitle_files": [p.name for p in subtitle_paths],
            }
        )
        print(
            f" - {video_id}: audio={'OK' if audio_ok else 'MISS'} | subtitles={'OK' if subtitles_ok else 'MISS'}"
        )

    model_records = collect_model_readiness(MODEL_STORE_ROOT)
    readiness_summary = summarize_readiness(model_records)

    report = {
        "generated_at_utc": _now_utc(),
        "strict_offline_enabled": strict_offline_enabled(),
        "target_count": len(rows),
        "missing_audio_count": missing_audio,
        "missing_subtitles_count": missing_subtitles,
        "assets": rows,
        "model_readiness_summary": readiness_summary,
        "model_readiness": model_records,
    }

    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nSummary:")
    print(f" - missing_audio_count={missing_audio}")
    print(f" - missing_subtitles_count={missing_subtitles}")
    print(
        " - model_ready_for_real={}/{}".format(
            readiness_summary.get("ready_for_real_count", 0),
            readiness_summary.get("model_count", 0),
        )
    )
    print(f" - report={report_path}")

    if args.fail_on_missing and (missing_audio > 0 or missing_subtitles > 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
