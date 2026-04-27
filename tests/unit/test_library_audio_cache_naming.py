import json
import shutil
import uuid
from pathlib import Path

from backend.app.services import library_service


def _case_root() -> Path:
    root = Path("runtime") / "unit_test_audio_cache_naming" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_audio_cache_prefixed_filename_and_resolve(monkeypatch):
    root = _case_root()
    audio_root = root / "audio_cache"
    library_root = root / "library"
    audio_root.mkdir()
    library_root.mkdir()
    try:
        items_file = library_root / "items.json"
        items_file.write_text(
            json.dumps(
                [
                    {
                        "video_id": "s9F5qXVK4uU",
                        "title": "CZ_Mezinárodní den zvýšení povědomí",
                        "url": "https://www.youtube.com/watch?v=s9F5qXVK4uU",
                        "language": "cs",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        wav = audio_root / library_service.audio_cache_filename_for_library_item(
            "s9F5qXVK4uU",
            "CZ_Mezinárodní den zvýšení povědomí",
        )
        wav.write_bytes(b"RIFF")

        monkeypatch.setattr(library_service, "AUDIO_CACHE_ROOT", audio_root)
        monkeypatch.setattr(library_service, "_ITEMS_FILE", items_file)

        assert wav.name == "CZ_Mezin_s9F5qXVK4uU.wav"
        assert library_service.resolve_audio_cache_file_for_library_item("s9F5qXVK4uU") == wav

        item = library_service.list_items()[0]
        assert item.audio_cached is True
        assert item.audio_size_bytes == 4
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_audio_cache_resolves_old_prefix_after_title_change(monkeypatch):
    root = _case_root()
    audio_root = root / "audio_cache"
    library_root = root / "library"
    audio_root.mkdir()
    library_root.mkdir()
    try:
        items_file = library_root / "items.json"
        items_file.write_text(
            json.dumps(
                [
                    {
                        "video_id": "mW9FC8BR_l4",
                        "title": "CZ_Nový název",
                        "url": "https://www.youtube.com/watch?v=mW9FC8BR_l4",
                        "language": "cs",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        wav = audio_root / "CZ_Stary_mW9FC8BR_l4.wav"
        wav.write_bytes(b"RIFF")

        monkeypatch.setattr(library_service, "AUDIO_CACHE_ROOT", audio_root)
        monkeypatch.setattr(library_service, "_ITEMS_FILE", items_file)

        assert library_service.resolve_audio_cache_file_for_library_item("mW9FC8BR_l4") == wav
    finally:
        shutil.rmtree(root, ignore_errors=True)
