#!/usr/bin/env python
"""
Zkopíruje VTT titulky ze starého projektu (aSTT-comparison) do nového runtime/library/subtitles/.
Spusť jednou — ušetří opětovné stahování přes yt-dlp.
"""
from __future__ import annotations
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io
from packages.common.runtime_paths import runtime_subpath

configure_console_io()

# 6 videí s CZ titulky
VIDEO_IDS = [
    "R3BsjbDtWrY",   # PlayStation VR2 Tech rozhovor
    "SQRKerJ22zw",   # Rozhovor s Capsem z G2 Esports
    "QyKlQLkaH0s",   # Rozhovor s Kaiserem z MAD Lions
    "s9F5qXVK4uU",   # Mezinárodní den autismu
    "3_sujNKFpPU",   # Cukrfree Podcast #24
    "t9j-7JxuZVU",   # Rozhovor s vývojářem Cyberpunk 2077
]


def _resolve_source_subtitles(source_root: Path) -> Path | None:
    candidates = (
        source_root / ".runtime" / "source_library" / "subtitles",
        source_root / "runtime" / "source_library" / "subtitles",
        source_root / "source_library" / "subtitles",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy legacy VTT subtitles into runtime/library/subtitles.")
    parser.add_argument(
        "--source-root",
        default=str(ROOT.parent / "aSTT-comparison"),
        help="Path to legacy aSTT-comparison root (default: ../aSTT-comparison).",
    )
    parser.add_argument(
        "--target-subtitles",
        default=str(runtime_subpath("library", "subtitles")),
        help="Target subtitles directory (default: runtime/library/subtitles).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    source_root = Path(args.source_root).expanduser().resolve()
    old_subtitles = _resolve_source_subtitles(source_root)
    new_subtitles = Path(args.target_subtitles).expanduser().resolve()

    if old_subtitles is None:
        print(f"FAIL  Nenalezen source subtitles root pod: {source_root}")
        print("      Očekávám jednu z variant: .runtime/source_library/subtitles | runtime/source_library/subtitles | source_library/subtitles")
        return 1

    new_subtitles.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0

    for vid in VIDEO_IDS:
        src = old_subtitles / vid
        dst = new_subtitles / vid
        if not src.exists():
            print(f"WARN  {vid}: zdrojová složka neexistuje ({src})")
            continue
        files = list(src.iterdir())
        if not files:
            print(f"WARN  {vid}: prázdná složka")
            continue
        dst.mkdir(parents=True, exist_ok=True)
        for f in files:
            dst_f = dst / f.name
            if dst_f.exists():
                skipped += 1
                continue
            shutil.copy2(f, dst_f)
            print(f"OK    {vid}/{f.name}")
            copied += 1

    print(f"\nHotovo: {copied} souborů zkopírováno, {skipped} přeskočeno (již existují)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
