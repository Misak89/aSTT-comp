from pathlib import Path
import shutil
import uuid

from packages.ingest.source_resolver import local_path_from_file_url, parse_source_entries


def test_file_url_keeps_windows_drive_separator():
    path = local_path_from_file_url("file:///C:/Users/adamf/audio/test.wav")

    assert path is not None
    assert str(path).startswith("C:")
    assert not str(path).startswith("C:Users")


def test_parse_existing_file_url_as_local_file():
    root = Path("runtime") / "unit_test_source_resolver" / uuid.uuid4().hex
    try:
        root.mkdir(parents=True, exist_ok=True)
        wav = (root / "sample.wav").resolve()
        wav.write_bytes(b"RIFF")

        entries = parse_source_entries([wav.as_uri()], max_sources=1)

        assert len(entries) == 1
        assert entries[0].origin_type == "local_file"
        assert entries[0].exists is True
        assert Path(entries[0].value) == wav
    finally:
        shutil.rmtree(root, ignore_errors=True)
