#!/usr/bin/env python
"""
Zkopíruje VTT titulky ze starého projektu (aSTT-comparison) do nového (.runtime/library/subtitles/).
Spusť jednou — ušetří opětovné stahování přes yt-dlp.
"""
from __future__ import annotations
import shutil
import sys
from pathlib import Path

OLD_SUBTITLES = Path(r"C:\Users\adamf\OneDrive\Dokumenty\aSTT-comparison\.runtime\source_library\subtitles")
NEW_SUBTITLES = Path(__file__).parent.parent / "runtime" / "library" / "subtitles"

# 6 videí s CZ titulky
VIDEO_IDS = [
    "R3BsjbDtWrY",   # PlayStation VR2 Tech rozhovor
    "SQRKerJ22zw",   # Rozhovor s Capsem z G2 Esports
    "QyKlQLkaH0s",   # Rozhovor s Kaiserem z MAD Lions
    "s9F5qXVK4uU",   # Mezinárodní den autismu
    "3_sujNKFpPU",   # Cukrfree Podcast #24
    "t9j-7JxuZVU",   # Rozhovor s vývojářem Cyberpunk 2077
]


def main() -> int:
    if not OLD_SUBTITLES.exists():
        print(f"FAIL  Starý projekt nenalezen: {OLD_SUBTITLES}")
        return 1

    NEW_SUBTITLES.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0

    for vid in VIDEO_IDS:
        src = OLD_SUBTITLES / vid
        dst = NEW_SUBTITLES / vid
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
