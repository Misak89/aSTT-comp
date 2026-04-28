from pathlib import Path
import shutil
import uuid

from backend.app.services import library_service


def test_local_file_url_resolves_prefixed_audio_cache_name(monkeypatch):
    root = (Path("runtime") / "unit_test_library_audio_resolution" / uuid.uuid4().hex).resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
        old_name = "mW9FC8BR_l4_test.wav"
        renamed = root / f"CZ_Psych_{old_name}"
        renamed.write_bytes(b"RIFF")
        old_url = (root / old_name).as_uri()

        monkeypatch.setattr(library_service, "AUDIO_CACHE_ROOT", root)
        monkeypatch.setattr(
            library_service,
            "_load_raw",
            lambda: [{
                "video_id": "local_ca8e8889",
                "title": "CZ_TEST",
                "url": old_url,
                "visible_in_menus": True,
            }],
        )

        assert library_service.resolve_audio_file_for_library_item("local_ca8e8889") == renamed

        item = library_service.list_items()[0]
        assert item.audio_cached is True
        assert item.audio_size_bytes == len(b"RIFF")
    finally:
        shutil.rmtree(root, ignore_errors=True)
