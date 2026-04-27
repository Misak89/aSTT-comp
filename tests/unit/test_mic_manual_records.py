import json
import shutil
from pathlib import Path

from backend.app.services import mic_service


def _prepare_runtime_root(name: str) -> Path:
    root = Path("runtime") / "_test_mic_manual_records" / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    return root


def test_save_manual_record_uses_authoritative_transcript_from_linked_session(monkeypatch):
    root = _prepare_runtime_root("save_backfill")
    monkeypatch.setattr(mic_service, "MIC_SESSIONS_ROOT", root)

    try:
        (root / "mic_abc123.json").write_text(
            json.dumps({"session_id": "mic_abc123", "final": {"text": "Dobrý den, test přepisu."}}),
            encoding="utf-8",
        )

        result = mic_service.save_manual_record(
            model_id="whisper_cpp_base",
            metrics={"mic_session_id": "mic_abc123"},
            transcript="Snapshot se nesmí zobrazit.",
            source="web_mic_auto_error",
        )

        saved = json.loads(Path(result["history_path"]).read_text(encoding="utf-8"))

        assert saved["transcript"] == "Dobrý den, test přepisu."
        assert saved["metrics"]["transcript_source"] == "mic_ws_final"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_list_manual_records_uses_authoritative_transcript_from_linked_session(monkeypatch):
    root = _prepare_runtime_root("list_backfill")
    history = root / "history"
    history.mkdir(parents=True)
    monkeypatch.setattr(mic_service, "MIC_SESSIONS_ROOT", root)

    try:
        (root / "mic_def456.json").write_text(
            json.dumps(
                {
                    "session_id": "mic_def456",
                    "transcript": "",
                    "final": {"text": "Text doplněný z final payloadu."},
                }
            ),
            encoding="utf-8",
        )
        (history / "20260425_183719_whisper_cpp_base_manual.json").write_text(
            json.dumps(
                {
                    "session_id": "manual_old",
                    "snapshot_at": "2026-04-25T18:37:19+00:00",
                    "model_id": "whisper_cpp_base",
                    "transcript": "Snapshot se nesmí zobrazit.",
                    "metrics": {"mic_session_id": "mic_def456"},
                }
            ),
            encoding="utf-8",
        )

        records = mic_service.list_manual_records(limit=10)

        assert records[0]["transcript"] == "Text doplněný z final payloadu."
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_list_manual_records_clears_snapshot_when_linked_session_has_no_text(monkeypatch):
    root = _prepare_runtime_root("clear_snapshot")
    history = root / "history"
    history.mkdir(parents=True)
    monkeypatch.setattr(mic_service, "MIC_SESSIONS_ROOT", root)

    try:
        (root / "mic_empty.json").write_text(
            json.dumps({"session_id": "mic_empty", "transcript": "", "final": {"text": ""}}),
            encoding="utf-8",
        )
        (history / "20260425_183719_whisper_cpp_base_manual.json").write_text(
            json.dumps(
                {
                    "session_id": "manual_old",
                    "snapshot_at": "2026-04-25T18:37:19+00:00",
                    "model_id": "whisper_cpp_base",
                    "transcript": "Snapshot se nesmí zobrazit.",
                    "metrics": {"mic_session_id": "mic_empty"},
                }
            ),
            encoding="utf-8",
        )

        records = mic_service.list_manual_records(limit=10)

        assert records[0]["transcript"] == ""
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_list_manual_records_ignores_history_transcript_for_linked_session(monkeypatch):
    root = _prepare_runtime_root("ignore_history_snapshot")
    history = root / "history"
    history.mkdir(parents=True)
    monkeypatch.setattr(mic_service, "MIC_SESSIONS_ROOT", root)

    try:
        (history / "20260425_183719_mic_history_final.json").write_text(
            json.dumps(
                {
                    "session_id": "mic_history",
                    "phase": "final",
                    "transcript": "Text z historie se nesmí použít.",
                    "final": {"text": "Ani final z history snapshotu se nesmí použít."},
                }
            ),
            encoding="utf-8",
        )
        (history / "20260425_183720_whisper_cpp_base_manual.json").write_text(
            json.dumps(
                {
                    "session_id": "manual_old",
                    "snapshot_at": "2026-04-25T18:37:20+00:00",
                    "model_id": "whisper_cpp_base",
                    "transcript": "Snapshot se nesmí zobrazit.",
                    "metrics": {"mic_session_id": "mic_history"},
                }
            ),
            encoding="utf-8",
        )

        records = mic_service.list_manual_records(limit=10)

        assert records[0]["transcript"] == ""
        assert records[0]["metrics"]["transcript_source"] == "none"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_list_manual_records_keeps_manual_text_without_linked_session(monkeypatch):
    root = _prepare_runtime_root("manual_without_session")
    history = root / "history"
    history.mkdir(parents=True)
    monkeypatch.setattr(mic_service, "MIC_SESSIONS_ROOT", root)

    try:
        (history / "20260425_183719_user_manual.json").write_text(
            json.dumps(
                {
                    "session_id": "manual_only",
                    "snapshot_at": "2026-04-25T18:37:19+00:00",
                    "model_id": "manual_note",
                    "transcript": "Ručně zadaný text bez mic_session_id.",
                    "metrics": {},
                }
            ),
            encoding="utf-8",
        )

        records = mic_service.list_manual_records(limit=10)

        assert records[0]["transcript"] == "Ručně zadaný text bez mic_session_id."
    finally:
        shutil.rmtree(root, ignore_errors=True)
