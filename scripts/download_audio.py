#!/usr/bin/env python
"""
Stáhne plné audio pro CZ videa v knihovně.
Výstup: runtime/audio_cache/{video_id}.wav  (16 kHz, mono, PCM-s16le)

Použití:
  python scripts/download_audio.py                    # vše cs
  python scripts/download_audio.py s9F5qXVK4uU mW9FC8BR_l4
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LIBRARY_ITEMS = ROOT / "runtime" / "library" / "items.json"
AUDIO_CACHE   = ROOT / "runtime" / "audio_cache"
SAMPLE_RATE   = 16000


def _duration_of_wav(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def _download_video(video_id: str, duration_s: float) -> None:
    out_path = AUDIO_CACHE / f"{video_id}.wav"
    if out_path.exists():
        dur = _duration_of_wav(out_path)
        print(f"  ✓ {video_id} — již v cache ({dur:.0f}s, {out_path.stat().st_size // 1024} kB)")
        return

    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"  ⬇ {video_id} ({duration_s:.0f}s) — stahuji...", flush=True)

    # yt-dlp -o - | ffmpeg -i pipe:0 → WAV
    ytdlp_cmd = [
        sys.executable, "-m", "yt_dlp",
        "--quiet", "--no-playlist",
        "--format", "bestaudio/best",
        "-o", "-",
        yt_url,
    ]
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        str(out_path),
    ]

    ytdlp = subprocess.Popen(ytdlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    ffmpeg = subprocess.Popen(
        ffmpeg_cmd,
        stdin=ytdlp.stdout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    ytdlp.stdout.close()  # type: ignore[union-attr]

    _, ffmpeg_err = ffmpeg.communicate()
    ytdlp.wait()

    if ffmpeg.returncode != 0 or not out_path.exists():
        print(f"  ✗ {video_id} SELHALO: {ffmpeg_err[-300:]}", file=sys.stderr)
        out_path.unlink(missing_ok=True)
        return

    dur = _duration_of_wav(out_path)
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"  ✓ {video_id} staženo — {dur:.0f}s, {size_mb:.1f} MB → {out_path.name}")


def main() -> int:
    AUDIO_CACHE.mkdir(parents=True, exist_ok=True)

    # Načti knihovnu
    if not LIBRARY_ITEMS.exists():
        print(f"ERROR: {LIBRARY_ITEMS} neexistuje", file=sys.stderr)
        return 1

    items: list[dict] = json.loads(LIBRARY_ITEMS.read_text(encoding="utf-8"))

    # Filtr: buď argumenty z CLI, nebo všechna cs videa
    if len(sys.argv) > 1:
        wanted = set(sys.argv[1:])
        targets = [i for i in items if i["video_id"] in wanted]
        if not targets:
            print(f"Žádné video z {wanted} nenalezeno v knihovně.", file=sys.stderr)
            return 1
    else:
        targets = [i for i in items if (i.get("language") or "").lower() == "cs"]

    if not targets:
        print("Žádná cs videa v knihovně.")
        return 0

    total_s = sum(i.get("duration_seconds") or 0 for i in targets)
    size_est_mb = total_s * SAMPLE_RATE * 2 / (1024 * 1024)
    print(f"Stáhnu {len(targets)} videí (celkem ~{total_s:.0f}s, odhad ~{size_est_mb:.0f} MB):")
    print(f"Cache: {AUDIO_CACHE}\n")

    for item in targets:
        _download_video(item["video_id"], item.get("duration_seconds") or 0)

    print("\nHotovo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
