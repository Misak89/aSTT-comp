import base64
import json
import wave
from array import array
from pathlib import Path

from backend.app.services import latemic_service


def _pcm16_base64(sample_count: int = 1600) -> str:
    pcm = array("h", [0] * sample_count)
    return base64.b64encode(pcm.tobytes()).decode("ascii")


def _install_fake_runtime(monkeypatch, tmp_path):
    root = tmp_path / "late_mic"
    model_store = tmp_path / "model_store"
    root.mkdir()
    model_store.mkdir()
    monkeypatch.setattr(latemic_service, "LATE_MIC_ROOT", root)
    monkeypatch.setattr(latemic_service, "MODEL_STORE_ROOT", model_store)
    monkeypatch.setattr(latemic_service, "REGISTRY", {"vosk_small_cs_0_4": object()})
    return root


def test_latemic_segment_is_saved_and_transcribed_from_authoritative_wav(monkeypatch, tmp_path):
    _install_fake_runtime(monkeypatch, tmp_path)
    calls = []

    def fake_transcribe_latemic_segment(**kwargs):
        wav_path = Path(kwargs["wav_path"])
        calls.append(kwargs)
        with wave.open(str(wav_path), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 1600
        return {
            "transcript_text": "Dobrý den, toto je skutečný mic segment.",
            "engine_elapsed_seconds": 0.25,
            "rtf": 2.5,
            "latency_ms": 250,
        }

    monkeypatch.setattr(latemic_service, "transcribe_latemic_segment", fake_transcribe_latemic_segment)

    result = latemic_service.create_segment_transcription(
        model_id="vosk_small_cs_0_4",
        model_params={"chunk_seconds": 0.4},
        pcm16_base64=_pcm16_base64(),
        sample_rate=16000,
        lag_budget_s=10,
        target_segment_s=5,
        queue_wait_s=1.5,
        delete_policy="keep_all",
        client_meta={"source": "browser_mic"},
    )

    assert calls
    assert result["status"] == "ok"
    assert result["transcript"] == "Dobrý den, toto je skutečný mic segment."
    assert result["transcript_source"] == "latemic_segment"
    assert result["audio_authority"] == "real_mic_segment_wav"
    assert result["reference_text_used"] is False
    assert result["history_fallback_used"] is False
    assert result["queue_wait_s"] == 1.5
    assert result["max_visible_lag_s"] == 1.85
    assert result["wav_deleted"] is False
    assert result["wav_retained"] is True

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    saved = json.loads(Path(result["result_path"]).read_text(encoding="utf-8"))
    assert manifest["transcript_source_required"] == "latemic_segment"
    assert manifest["reference_text_allowed"] is False
    assert manifest["history_fallback_allowed"] is False
    assert saved["helper_result"]["transcript_text"] == result["transcript"]
    assert Path(result["wav_path"]).exists()


def test_latemic_after_transcript_deletes_successful_segment_wav(monkeypatch, tmp_path):
    _install_fake_runtime(monkeypatch, tmp_path)

    monkeypatch.setattr(
        latemic_service,
        "transcribe_latemic_segment",
        lambda **kwargs: {"transcript_text": "Text ze segmentu.", "engine_elapsed_seconds": 0.1},
    )

    result = latemic_service.create_segment_transcription(
        model_id="vosk_small_cs_0_4",
        pcm16_base64=_pcm16_base64(),
        sample_rate=16000,
        delete_policy="after_transcript",
    )

    assert result["status"] == "ok"
    assert result["wav_deleted"] is True
    assert result["wav_retained"] is False
    assert not Path(result["wav_path"]).exists()
    assert Path(result["result_path"]).exists()


def test_latemic_failed_transcription_keeps_empty_authoritative_result(monkeypatch, tmp_path):
    _install_fake_runtime(monkeypatch, tmp_path)

    def fail_transcribe(**kwargs):
        raise RuntimeError("model failed")

    monkeypatch.setattr(latemic_service, "transcribe_latemic_segment", fail_transcribe)

    result = latemic_service.create_segment_transcription(
        model_id="vosk_small_cs_0_4",
        pcm16_base64=_pcm16_base64(),
        sample_rate=16000,
        delete_policy="after_transcript",
    )

    assert result["status"] == "error"
    assert result["error"] == "model failed"
    assert result["transcript"] == ""
    assert result["transcript_source"] == "latemic_segment"
    assert result["reference_text_used"] is False
    assert result["history_fallback_used"] is False
    assert result["wav_deleted"] is False
    assert result["wav_retained"] is True
    assert Path(result["wav_path"]).exists()


def test_latemic_same_audio_requests_get_distinct_artifact_dirs(monkeypatch, tmp_path):
    _install_fake_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(
        latemic_service,
        "transcribe_latemic_segment",
        lambda **kwargs: {"transcript_text": "Stejný segment.", "engine_elapsed_seconds": 0.1},
    )
    payload = _pcm16_base64()

    first = latemic_service.create_segment_transcription(
        model_id="vosk_small_cs_0_4",
        pcm16_base64=payload,
        sample_rate=16000,
        delete_policy="keep_all",
    )
    second = latemic_service.create_segment_transcription(
        model_id="vosk_small_cs_0_4",
        pcm16_base64=payload,
        sample_rate=16000,
        delete_policy="keep_all",
    )

    assert first["segment_id"] != second["segment_id"]
    assert first["result_path"] != second["result_path"]
    assert Path(first["result_path"]).exists()
    assert Path(second["result_path"]).exists()


def test_latemic_delete_segment_wav_removes_only_audio(monkeypatch, tmp_path):
    _install_fake_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(
        latemic_service,
        "transcribe_latemic_segment",
        lambda **kwargs: {"transcript_text": "Audio bude smazáno.", "engine_elapsed_seconds": 0.1},
    )

    result = latemic_service.create_segment_transcription(
        model_id="vosk_small_cs_0_4",
        pcm16_base64=_pcm16_base64(),
        sample_rate=16000,
        delete_policy="keep_all",
    )
    deleted = latemic_service.delete_segment_wav(result["segment_id"])

    assert deleted["wav_existed"] is True
    assert deleted["wav_deleted"] is True
    assert deleted["wav_retained"] is False
    assert not Path(result["wav_path"]).exists()
    assert Path(result["result_path"]).exists()
    saved = json.loads(Path(result["result_path"]).read_text(encoding="utf-8"))
    assert saved["wav_deleted"] is True
    assert saved["wav_retained"] is False
